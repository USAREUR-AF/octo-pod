// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! TCP frame extraction — how a byte stream becomes ingestible units.
//!
//! Capture-first discipline: a framing mode makes each *unit* of the wire
//! protocol one Zenoh sample, without decoding it. Which mode a real feed
//! needs is confirmed the first time the listener sees real bytes — start with `stream` when unsure; nothing
//! is lost, only unit boundaries.

use anyhow::{bail, Result};

/// Sanity cap for length-prefixed frames: a garbage length must not make
/// the arm allocate unbounded memory. Generous vs any real sensor message.
pub const MAX_FRAME: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Framing {
    /// Forward each socket read as-is. No boundary knowledge; pure capture.
    Stream,
    /// Split on `\n` (classic line protocols, NDJSON, CoT streams).
    Newline,
    /// 4-byte big-endian length prefix, then payload.
    Len32Be,
    /// 4-byte little-endian length prefix, then payload (common in
    /// protobuf-over-TCP stacks; confirm per feed against real bytes).
    Len32Le,
}

impl Framing {
    pub fn parse(s: &str) -> Result<Self> {
        Ok(match s {
            "stream" => Self::Stream,
            "newline" => Self::Newline,
            "len32be" => Self::Len32Be,
            "len32le" => Self::Len32Le,
            other => bail!("unknown framing `{other}` (stream|newline|len32be|len32le)"),
        })
    }

    /// Frame one unit for a consumer: the inverse of [`FrameBuf`], so what this writes a
    /// `FrameBuf` in the same mode reads back as exactly that unit, once.
    ///
    /// A unit this framing cannot represent is refused rather than written, because the
    /// consumer would misread it and the error would surface on someone else's network:
    /// a newline-framed unit containing a newline arrives as several units, one ending in
    /// a carriage return loses it to the line-ending strip, and a length-prefixed unit over
    /// [`MAX_FRAME`] is refused by any reader holding the same bound. `stream` carries no
    /// boundaries, so it has none to violate.
    pub fn encode(&self, payload: &[u8]) -> Result<Vec<u8>> {
        match self {
            Self::Stream => Ok(payload.to_vec()),
            Self::Newline => {
                if let Some(pos) = payload.iter().position(|&b| b == b'\n') {
                    bail!(
                        "unit contains a newline at byte {pos}; newline framing would deliver \
                         it as more than one unit"
                    );
                }
                if payload.last() == Some(&b'\r') {
                    bail!(
                        "unit ends in a carriage return, which a newline reader strips as part \
                         of the line ending"
                    );
                }
                let mut out = Vec::with_capacity(payload.len() + 1);
                out.extend_from_slice(payload);
                out.push(b'\n');
                Ok(out)
            }
            Self::Len32Be | Self::Len32Le => {
                if payload.len() > MAX_FRAME {
                    bail!(
                        "unit of {} bytes exceeds the {MAX_FRAME}-byte frame cap a reader enforces",
                        payload.len()
                    );
                }
                let len = payload.len() as u32;
                let header = if *self == Self::Len32Be {
                    len.to_be_bytes()
                } else {
                    len.to_le_bytes()
                };
                let mut out = Vec::with_capacity(4 + payload.len());
                out.extend_from_slice(&header);
                out.extend_from_slice(payload);
                Ok(out)
            }
        }
    }
}

/// Incremental frame extractor over an append-only buffer.
///
/// Feed bytes with `extend`, then drain complete frames with `next_frame`.
/// `Stream` mode returns everything buffered as one frame per call.
pub struct FrameBuf {
    framing: Framing,
    buf: Vec<u8>,
}

impl FrameBuf {
    pub fn new(framing: Framing) -> Self {
        Self {
            framing,
            buf: Vec::new(),
        }
    }

    pub fn extend(&mut self, bytes: &[u8]) {
        self.buf.extend_from_slice(bytes);
    }

    /// Pop the next complete frame, or None if more bytes are needed.
    /// Errors are unrecoverable for the connection (oversize frame).
    pub fn next_frame(&mut self) -> Result<Option<Vec<u8>>> {
        match self.framing {
            Framing::Stream => {
                if self.buf.is_empty() {
                    Ok(None)
                } else {
                    Ok(Some(std::mem::take(&mut self.buf)))
                }
            }
            Framing::Newline => match self.buf.iter().position(|&b| b == b'\n') {
                // The same bound the length-prefixed modes hold: a peer that never sends a
                // newline must not grow the buffer until the process is OOM-killed.
                None if self.buf.len() > MAX_FRAME => bail!(
                    "{} bytes buffered with no newline exceeds cap {MAX_FRAME} - wrong framing mode for this feed?",
                    self.buf.len()
                ),
                None => Ok(None),
                Some(pos) if pos > MAX_FRAME => bail!("newline frame exceeds cap {MAX_FRAME}"),
                Some(pos) => {
                    let mut line: Vec<u8> = self.buf.drain(..=pos).collect();
                    line.pop(); // strip '\n'
                    if line.last() == Some(&b'\r') {
                        line.pop();
                    }
                    Ok(Some(line))
                }
            },
            Framing::Len32Be | Framing::Len32Le => {
                if self.buf.len() < 4 {
                    return Ok(None);
                }
                let hdr: [u8; 4] = self.buf[..4].try_into().expect("4 bytes checked");
                let len = match self.framing {
                    Framing::Len32Be => u32::from_be_bytes(hdr) as usize,
                    _ => u32::from_le_bytes(hdr) as usize,
                };
                if len > MAX_FRAME {
                    bail!("length-prefixed frame of {len} bytes exceeds cap {MAX_FRAME} — wrong framing mode for this feed?");
                }
                if self.buf.len() < 4 + len {
                    return Ok(None);
                }
                self.buf.drain(..4);
                Ok(Some(self.buf.drain(..len).collect()))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn newline_frames() {
        let mut fb = FrameBuf::new(Framing::Newline);
        fb.extend(b"one\r\ntwo\npart");
        assert_eq!(fb.next_frame().unwrap().unwrap(), b"one");
        assert_eq!(fb.next_frame().unwrap().unwrap(), b"two");
        assert!(fb.next_frame().unwrap().is_none());
        fb.extend(b"ial\n");
        assert_eq!(fb.next_frame().unwrap().unwrap(), b"partial");
    }

    #[test]
    fn len32be_frames_across_reads() {
        let mut fb = FrameBuf::new(Framing::Len32Be);
        fb.extend(&[0, 0, 0, 3]);
        assert!(fb.next_frame().unwrap().is_none());
        fb.extend(b"abc\x00\x00\x00\x02de");
        assert_eq!(fb.next_frame().unwrap().unwrap(), b"abc");
        assert_eq!(fb.next_frame().unwrap().unwrap(), b"de");
        assert!(fb.next_frame().unwrap().is_none());
    }

    #[test]
    fn len32le_frames() {
        let mut fb = FrameBuf::new(Framing::Len32Le);
        fb.extend(&[2, 0, 0, 0]);
        fb.extend(b"hi");
        assert_eq!(fb.next_frame().unwrap().unwrap(), b"hi");
    }

    #[test]
    fn oversize_length_is_an_error() {
        let mut fb = FrameBuf::new(Framing::Len32Be);
        fb.extend(&[0xFF, 0xFF, 0xFF, 0xFF]);
        assert!(fb.next_frame().is_err());
    }

    #[test]
    fn a_line_that_never_ends_is_an_error_past_the_cap() {
        let mut fb = FrameBuf::new(Framing::Newline);
        fb.extend(&vec![b'x'; MAX_FRAME]);
        assert!(
            fb.next_frame().unwrap().is_none(),
            "at the cap it still waits"
        );
        fb.extend(b"x");
        assert!(fb.next_frame().is_err(), "one byte past the cap is refused");
    }

    #[test]
    fn a_line_of_exactly_the_cap_still_reads() {
        let mut fb = FrameBuf::new(Framing::Newline);
        let mut bytes = vec![b'x'; MAX_FRAME];
        bytes.push(b'\n');
        fb.extend(&bytes);
        assert_eq!(fb.next_frame().unwrap().unwrap().len(), MAX_FRAME);
    }

    #[test]
    fn an_oversize_complete_line_is_refused() {
        let mut fb = FrameBuf::new(Framing::Newline);
        let mut bytes = vec![b'x'; MAX_FRAME + 1];
        bytes.push(b'\n');
        fb.extend(&bytes);
        assert!(fb.next_frame().is_err());
    }

    #[test]
    fn stream_passthrough() {
        let mut fb = FrameBuf::new(Framing::Stream);
        assert!(fb.next_frame().unwrap().is_none());
        fb.extend(b"chunk");
        assert_eq!(fb.next_frame().unwrap().unwrap(), b"chunk");
        assert!(fb.next_frame().unwrap().is_none());
    }

    #[test]
    fn framing_parse() {
        assert_eq!(Framing::parse("newline").unwrap(), Framing::Newline);
        assert!(Framing::parse("bogus").is_err());
    }

    /// The property a writing arm depends on: the consumer's reader, built the same way,
    /// gets back exactly the unit that was written, and only that unit.
    #[test]
    fn every_framing_reads_back_what_it_writes() {
        let unit = b"a unit\twith bytes \x00\x01\xff";
        for framing in [
            Framing::Stream,
            Framing::Newline,
            Framing::Len32Be,
            Framing::Len32Le,
        ] {
            let mut fb = FrameBuf::new(framing);
            fb.extend(&framing.encode(unit).unwrap());
            assert_eq!(fb.next_frame().unwrap().unwrap(), unit, "{framing:?}");
            assert!(
                fb.next_frame().unwrap().is_none(),
                "{framing:?} wrote more than one unit"
            );
        }
    }

    #[test]
    fn consecutive_units_stay_separate() {
        for framing in [Framing::Newline, Framing::Len32Be, Framing::Len32Le] {
            let mut fb = FrameBuf::new(framing);
            fb.extend(&framing.encode(b"one").unwrap());
            fb.extend(&framing.encode(b"two").unwrap());
            assert_eq!(fb.next_frame().unwrap().unwrap(), b"one", "{framing:?}");
            assert_eq!(fb.next_frame().unwrap().unwrap(), b"two", "{framing:?}");
        }
    }

    #[test]
    fn a_newline_unit_that_would_split_or_lose_a_byte_is_refused() {
        assert!(Framing::Newline.encode(b"one\ntwo").is_err());
        assert!(Framing::Newline.encode(b"line\r").is_err());
        assert!(Framing::Newline.encode(b"a \r in the middle").is_ok());
    }

    #[test]
    fn a_unit_over_the_frame_cap_is_refused() {
        let unit = vec![0u8; MAX_FRAME + 1];
        assert!(Framing::Len32Be.encode(&unit).is_err());
        assert!(Framing::Len32Le.encode(&unit).is_err());
    }

    #[test]
    fn the_length_prefix_is_in_the_declared_byte_order() {
        assert_eq!(&Framing::Len32Be.encode(b"ab").unwrap()[..4], &[0, 0, 0, 2]);
        assert_eq!(&Framing::Len32Le.encode(b"ab").unwrap()[..4], &[2, 0, 0, 0]);
    }
}
