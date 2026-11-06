// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The framing a stream-reading arm applies to every byte a peer sends:
//! `octo_arm_common::framing::FrameBuf`, used by `tcp-ingest` and `tcp-connect`.
//!
//! Input: one selector byte, then the bytes as they arrive on the socket. The low two
//! bits of the selector pick the framing mode. The other six pick a read size, because a
//! socket can return the same stream split at any point.
//!
//! Besides "never panics", the target checks what an arm depends on:
//! - the frames do not depend on how the stream was split into reads;
//! - no frame is over the cap, and a newline frame holds no newline;
//! - `Framing::encode` is the inverse of the reader.

#![no_main]

use libfuzzer_sys::fuzz_target;
use octo_arm_common::framing::{FrameBuf, Framing, MAX_FRAME};

const MODES: [Framing; 4] = [
    Framing::Stream,
    Framing::Newline,
    Framing::Len32Be,
    Framing::Len32Le,
];

/// Read `stream` the way an arm does: add each read to the buffer, then take every
/// complete frame. Returns the frames and whether the framing refused the stream, which
/// closes the connection.
fn read(framing: Framing, stream: &[u8], read_size: usize) -> (Vec<Vec<u8>>, bool) {
    let mut buf = FrameBuf::new(framing);
    let mut frames = Vec::new();
    for chunk in stream.chunks(read_size.max(1)) {
        buf.extend(chunk);
        loop {
            match buf.next_frame() {
                Ok(Some(frame)) => frames.push(frame),
                Ok(None) => break,
                Err(_) => return (frames, true),
            }
        }
    }
    (frames, false)
}

fuzz_target!(|data: &[u8]| {
    let Some((&selector, stream)) = data.split_first() else {
        return;
    };
    let framing = MODES[usize::from(selector & 0b11)];
    let read_size = usize::from(selector >> 2) + 1;

    let (whole, whole_refused) = read(framing, stream, stream.len());
    let (split, split_refused) = read(framing, stream, read_size);

    if framing == Framing::Stream {
        // No boundaries: what comes out is what went in, in order.
        assert_eq!(whole.concat(), stream);
        assert_eq!(split.concat(), stream);
        assert!(
            !whole_refused && !split_refused,
            "stream mode refused bytes"
        );
        return;
    }

    assert_eq!(
        whole, split,
        "{framing:?}: the frames depend on how the stream was split into reads"
    );
    assert_eq!(
        whole_refused, split_refused,
        "{framing:?}: refusal depends on read size"
    );
    for frame in &whole {
        assert!(frame.len() <= MAX_FRAME, "{framing:?}: frame over the cap");
        if framing == Framing::Newline {
            assert!(!frame.contains(&b'\n'), "a newline frame holds a newline");
        }
    }

    // Write back every frame the writer accepts. The reader must return exactly those
    // frames, and for a length prefix the bytes must be the ones that were read.
    let writable: Vec<&Vec<u8>> = whole.iter().filter(|f| framing.encode(f).is_ok()).collect();
    let mut rewritten = Vec::new();
    for frame in &writable {
        if let Ok(bytes) = framing.encode(frame) {
            rewritten.extend_from_slice(&bytes);
        }
    }
    let (reread, reread_refused) = read(framing, &rewritten, read_size);
    assert!(
        !reread_refused,
        "{framing:?}: the reader refused what the writer wrote"
    );
    assert_eq!(
        reread.len(),
        writable.len(),
        "{framing:?}: units merged or split"
    );
    assert!(reread.iter().zip(&writable).all(|(a, b)| a == *b));
    if framing != Framing::Newline {
        assert_eq!(
            writable.len(),
            whole.len(),
            "a length-prefixed frame cannot be written"
        );
        assert_eq!(
            Some(rewritten.as_slice()),
            stream.get(..rewritten.len()),
            "{framing:?}: the writer does not reproduce the bytes the reader consumed"
        );
    }

    // The writer is the reader's inverse: a unit it accepts reads back as that unit, once.
    if let Ok(encoded) = framing.encode(stream) {
        let (back, refused) = read(framing, &encoded, read_size);
        assert!(!refused, "{framing:?}: the reader refused an encoded unit");
        assert_eq!(
            back.len(),
            1,
            "{framing:?}: one unit read back as {}",
            back.len()
        );
        assert_eq!(back[0], stream);
    }
});
