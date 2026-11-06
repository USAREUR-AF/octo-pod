// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The SAPIENT arm's framing: a 4-byte length prefix whose byte order the arm discovers
//! from the first frame (`sapient::framing::FrameBuf`), with the session's own tie-breaker
//! (`sapient::wire::decodes`). Every byte a SAPIENT peer sends goes through it first.
//!
//! Input: one selector byte, then the stream. The selector picks detection or a pinned
//! byte order, and a read size.
//!
//! Besides "never panics", the target checks:
//! - no frame is over the cap;
//! - a pinned order is never detected around, and a detected order never changes;
//! - when both readings of the first prefix are possible lengths, the first frame is one
//!   the session's decoder accepts.

#![no_main]

use libfuzzer_sys::fuzz_target;
use octo_arm_common::framing::MAX_FRAME;
use sapient::framing::{ByteOrder, FrameBuf};
use sapient::wire;

/// Both readings of the first prefix are lengths the arm would accept, so only the
/// decoder can choose between them.
fn is_tie(stream: &[u8]) -> bool {
    let Some(&[a, b, c, d]) = stream.get(..4) else {
        return false;
    };
    let plausible = |n: u32| usize::try_from(n).is_ok_and(|n| n > 0 && n <= MAX_FRAME);
    plausible(u32::from_le_bytes([a, b, c, d])) && plausible(u32::from_be_bytes([a, b, c, d]))
}

fuzz_target!(|data: &[u8]| {
    let Some((&selector, stream)) = data.split_first() else {
        return;
    };
    let pinned = match selector % 3 {
        0 => None,
        1 => Some(ByteOrder::Le),
        _ => Some(ByteOrder::Be),
    };
    let read_size = usize::from(selector / 3) + 1;
    let tie = pinned.is_none() && is_tie(stream);

    let mut buf = FrameBuf::new(pinned);
    let mut settled = pinned;
    let mut frames = 0usize;
    for chunk in stream.chunks(read_size) {
        buf.extend(chunk);
        loop {
            let frame = match buf.next_frame(&wire::decodes) {
                Ok(Some(frame)) => frame,
                Ok(None) => break,
                // The session closes the connection on a framing error.
                Err(_) => return,
            };
            assert!(frame.len() <= MAX_FRAME, "frame over the cap");
            if frames == 0 && tie {
                assert!(
                    wire::decodes(&frame),
                    "a tie was settled on a frame the session's decoder refuses"
                );
            }
            frames += 1;
            assert!(
                buf.order().is_some(),
                "a frame was returned with no byte order"
            );
            if let Some(order) = settled {
                assert_eq!(
                    buf.order(),
                    Some(order),
                    "the byte order changed mid-stream"
                );
            }
            settled = buf.order();
        }
        assert_eq!(buf.is_pinned(), pinned.is_some());
        if pinned.is_some() {
            assert_eq!(buf.order(), pinned, "a pinned order was replaced");
        }
    }
});
