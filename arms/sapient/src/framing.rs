//! SAPIENT frame extraction — a 4-byte length prefix whose BYTE ORDER IS DISCOVERED.
//!
//! The standard fixes the prefix at four bytes and says nothing an implementation can be
//! held to about its order, and real peers differ. The alternative to discovering it is a
//! per-site setting derived from a packet capture, which makes every new peer wait on
//! someone with tcpdump and turns a fact about the wire into a fact an operator has to
//! carry. So the arm reads the first prefix both ways and keeps the reading that can be
//! true:
//!
//! 1. A length over the cap is not a length. A 200-byte message prefixed little-endian
//!    reads `c8 00 00 00`, which big-endian is 3.3 GB — so one order is usually absurd on
//!    its face, and that alone settles it.
//! 2. When both readings are plausible — small messages whose length bytes are nearly
//!    symmetric — the tie is broken by asking whether the payload actually decodes.
//!
//! Once settled the order is fixed for the connection: a stream that changed prefix order
//! mid-flight would be a desync, not a peer being helpful. `SAPIENT_FRAMING` pins it and
//! skips detection entirely, for a peer whose first message is ambiguous both ways.
//!
//! Capture-first: frames are returned as raw bytes with the prefix stripped, exactly as
//! `tcp-ingest`'s `len32le` mode does. Decoding is the session's business, and only as
//! far as the session needs.

use anyhow::{bail, Result};
use octo_arm_common::framing::MAX_FRAME;

/// Which way round the 4-byte length prefix is read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ByteOrder {
    Le,
    Be,
}

impl ByteOrder {
    pub fn parse(s: &str) -> Result<Self> {
        Ok(match s {
            "len32le" => Self::Le,
            "len32be" => Self::Be,
            other => bail!("unknown framing `{other}` (len32le|len32be, or empty to detect)"),
        })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Le => "len32le",
            Self::Be => "len32be",
        }
    }

    fn read(self, hdr: [u8; 4]) -> usize {
        match self {
            Self::Le => u32::from_le_bytes(hdr) as usize,
            Self::Be => u32::from_be_bytes(hdr) as usize,
        }
    }
}

/// Incremental frame extractor that settles the byte order on the first frame.
pub struct FrameBuf {
    order: Option<ByteOrder>,
    /// True when the operator pinned the order, so a frame that fails to decode is
    /// reported as a bad frame rather than quietly re-read the other way round.
    pinned: bool,
    buf: Vec<u8>,
}

impl FrameBuf {
    /// `None` detects on the first frame; `Some` pins and never detects.
    pub fn new(pinned: Option<ByteOrder>) -> Self {
        Self {
            order: pinned,
            pinned: pinned.is_some(),
            buf: Vec::new(),
        }
    }

    pub fn order(&self) -> Option<ByteOrder> {
        self.order
    }

    pub fn extend(&mut self, bytes: &[u8]) {
        self.buf.extend_from_slice(bytes);
    }

    /// Pop the next complete frame, or `None` if more bytes are needed.
    ///
    /// `decodes` is asked only to break a tie between two plausible readings, so a
    /// session can pass its own message parser without this module knowing the schema.
    /// An error is unrecoverable for the connection: the stream is desynchronised and no
    /// amount of further reading fixes it.
    pub fn next_frame(&mut self, decodes: &dyn Fn(&[u8]) -> bool) -> Result<Option<Vec<u8>>> {
        if self.buf.len() < 4 {
            return Ok(None);
        }
        let hdr: [u8; 4] = self.buf[..4].try_into().expect("4 bytes checked");

        let order = match self.order {
            Some(o) => o,
            None => match self.detect(hdr, decodes)? {
                Some(o) => {
                    self.order = Some(o);
                    o
                }
                // Both readings are still possible and the longer one has not arrived;
                // wait rather than guess.
                None => return Ok(None),
            },
        };

        let len = order.read(hdr);
        if len > MAX_FRAME {
            bail!(
                "length-prefixed frame of {len} bytes exceeds cap {MAX_FRAME} — wrong byte \
                 order for this peer? It is reading as {}",
                order.as_str()
            );
        }
        if self.buf.len() < 4 + len {
            return Ok(None);
        }
        self.buf.drain(..4);
        Ok(Some(self.buf.drain(..len).collect()))
    }

    /// Which reading of this header can be true. `None` means "not yet decidable".
    fn detect(&self, hdr: [u8; 4], decodes: &dyn Fn(&[u8]) -> bool) -> Result<Option<ByteOrder>> {
        let candidates: Vec<(ByteOrder, usize)> = [ByteOrder::Le, ByteOrder::Be]
            .into_iter()
            .map(|o| (o, o.read(hdr)))
            .filter(|(_, len)| *len > 0 && *len <= MAX_FRAME)
            .collect();

        match candidates.len() {
            0 => bail!(
                "first 4 bytes are {hdr:02x?}, which is neither a little-endian nor a \
                 big-endian length within the {MAX_FRAME}-byte cap — is this peer speaking \
                 length-prefixed SAPIENT at all?"
            ),
            1 => Ok(Some(candidates[0].0)),
            // Both plausible: ask which payload decodes. Whichever frame is complete
            // first is the one that can be tested; a reading whose payload has not fully
            // arrived cannot be ruled out yet, so we wait for it rather than pick the
            // short one by default.
            _ => {
                for (order, len) in &candidates {
                    if self.buf.len() >= 4 + len && decodes(&self.buf[4..4 + len]) {
                        return Ok(Some(*order));
                    }
                }
                let longest = candidates.iter().map(|(_, l)| *l).max().unwrap_or(0);
                if self.buf.len() >= 4 + longest {
                    bail!(
                        "first 4 bytes are {hdr:02x?} and neither reading decodes as a \
                         SapientMessage — pin the order with SAPIENT_FRAMING if this peer \
                         is known good"
                    );
                }
                Ok(None)
            }
        }
    }

    /// True when the operator pinned the order rather than letting it be discovered.
    pub fn is_pinned(&self) -> bool {
        self.pinned
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Stand-in for the session's parser: our test payloads all start with 0x0a, which a
    /// SapientMessage does (field 1, length-delimited), and noise does not.
    fn decodes(b: &[u8]) -> bool {
        b.first() == Some(&0x0a)
    }

    fn framed(order: ByteOrder, payload: &[u8]) -> Vec<u8> {
        let len = payload.len() as u32;
        let mut out = match order {
            ByteOrder::Le => len.to_le_bytes().to_vec(),
            ByteOrder::Be => len.to_be_bytes().to_vec(),
        };
        out.extend_from_slice(payload);
        out
    }

    #[test]
    fn little_endian_is_detected_from_an_absurd_big_endian_reading() {
        let mut fb = FrameBuf::new(None);
        fb.extend(&framed(ByteOrder::Le, b"\x0ahello"));
        assert_eq!(fb.next_frame(&decodes).unwrap().unwrap(), b"\x0ahello");
        assert_eq!(fb.order(), Some(ByteOrder::Le));
    }

    #[test]
    fn big_endian_is_detected_the_same_way() {
        let mut fb = FrameBuf::new(None);
        fb.extend(&framed(ByteOrder::Be, b"\x0ahello"));
        assert_eq!(fb.next_frame(&decodes).unwrap().unwrap(), b"\x0ahello");
        assert_eq!(fb.order(), Some(ByteOrder::Be));
    }

    #[test]
    fn the_order_is_settled_once_and_reused() {
        let mut fb = FrameBuf::new(None);
        fb.extend(&framed(ByteOrder::Be, b"\x0aone"));
        fb.extend(&framed(ByteOrder::Be, b"\x0atwo"));
        assert_eq!(fb.next_frame(&decodes).unwrap().unwrap(), b"\x0aone");
        assert_eq!(fb.next_frame(&decodes).unwrap().unwrap(), b"\x0atwo");
        assert_eq!(fb.order(), Some(ByteOrder::Be));
    }

    #[test]
    fn a_pinned_order_is_never_detected_around() {
        // These bytes read 1 little-endian and 16_777_216 big-endian, so detection would
        // choose little-endian. Pinned big-endian, the arm must hold the operator's
        // choice and wait for a frame that will never come, rather than quietly disagree.
        let mut fb = FrameBuf::new(Some(ByteOrder::Be));
        fb.extend(&[0x01, 0x00, 0x00, 0x00, 0x0a]);
        assert!(fb.next_frame(&decodes).unwrap().is_none());
        assert_eq!(fb.order(), Some(ByteOrder::Be));
        assert!(fb.is_pinned());
    }

    #[test]
    fn a_frame_split_across_reads_is_reassembled() {
        let wire = framed(ByteOrder::Le, b"\x0aabcdef");
        let mut fb = FrameBuf::new(None);
        fb.extend(&wire[..2]);
        assert!(fb.next_frame(&decodes).unwrap().is_none());
        fb.extend(&wire[2..6]);
        assert!(fb.next_frame(&decodes).unwrap().is_none());
        fb.extend(&wire[6..]);
        assert_eq!(fb.next_frame(&decodes).unwrap().unwrap(), b"\x0aabcdef");
    }

    #[test]
    fn a_reading_past_the_cap_settles_the_order_without_decoding() {
        // [0, 0, 0, 12] reads 12 big-endian and 201_326_592 little-endian, which is past
        // the cap, so only one reading is possible and the decoder is never asked: this
        // one refuses everything and the frame still comes back.
        let payload = b"\x0apayload-one";
        let mut fb = FrameBuf::new(None);
        fb.extend(&framed(ByteOrder::Be, payload));
        assert_eq!(fb.next_frame(&|_| false).unwrap().unwrap(), payload);
        assert_eq!(fb.order(), Some(ByteOrder::Be));
    }

    #[test]
    fn a_tie_is_broken_by_asking_which_payload_decodes() {
        // [0, 1, 2, 0] reads 66_048 big-endian and 131_328 little-endian, both under the
        // cap, so only the decoder can settle the order.
        let hdr = [0u8, 1, 2, 0];
        let mut payload = vec![0u8; 66_048];
        payload[0] = 0x0a;
        let mut wire = hdr.to_vec();
        wire.extend_from_slice(&payload);
        let mut fb = FrameBuf::new(None);
        fb.extend(&wire);
        assert_eq!(fb.next_frame(&decodes).unwrap().unwrap(), payload);
        assert_eq!(fb.order(), Some(ByteOrder::Be));

        // The same header with both readings complete and neither decoding is refused,
        // not settled by whichever reading happened to be tried first.
        let mut noise = hdr.to_vec();
        noise.resize(4 + 131_328, 0);
        let mut fb = FrameBuf::new(None);
        fb.extend(&noise);
        let err = fb.next_frame(&decodes).unwrap_err();
        assert!(
            format!("{err:#}").contains("neither reading decodes"),
            "{err:#}"
        );
    }

    #[test]
    fn a_header_that_is_no_length_at_all_is_fatal() {
        let mut fb = FrameBuf::new(None);
        // Huge both ways: 0xff in the high bytes of each reading.
        fb.extend(&[0xff, 0xff, 0xff, 0xff, 0x00]);
        let err = fb.next_frame(&decodes).unwrap_err();
        assert!(format!("{err:#}").contains("neither"), "{err:#}");
    }

    #[test]
    fn an_oversize_frame_under_a_pinned_order_names_the_order() {
        let mut fb = FrameBuf::new(Some(ByteOrder::Be));
        fb.extend(&[0x7f, 0xff, 0xff, 0xff, 0x00]);
        let err = fb.next_frame(&decodes).unwrap_err();
        let msg = format!("{err:#}");
        assert!(msg.contains("len32be"), "{msg}");
    }

    #[test]
    fn framing_names_parse_and_round_trip() {
        assert_eq!(ByteOrder::parse("len32le").unwrap(), ByteOrder::Le);
        assert_eq!(ByteOrder::parse("len32be").unwrap(), ByteOrder::Be);
        assert_eq!(ByteOrder::Le.as_str(), "len32le");
        assert!(ByteOrder::parse("stream").is_err());
    }
}
