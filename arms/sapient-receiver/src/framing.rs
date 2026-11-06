// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The SAPIENT wire frame: a 4-byte little-endian length prefix, then that many bytes of
//! a serialized SapientMessage. This is the one framing the pod's sapient arm emits
//! (`framing::ByteOrder::Le` there), so a receiver that reads le32 reads the pod exactly.
//!
//! A receiver only ever needs to READ le32 and WRITE le32: byte-order discovery belongs
//! to a capture arm that does not yet know its peer, not to a node that has registered
//! one. Keeping this narrow is deliberate.

use anyhow::{bail, Result};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

/// The largest frame we will assemble. A SAPIENT message is kilobytes; a prefix past this
/// is a desync or a hostile peer, not a length, and allocating on it is how a receiver is
/// made to eat memory. Refuse rather than trust the number.
pub const MAX_FRAME: u32 = 8 * 1024 * 1024;

/// Read one frame. `Ok(None)` means the peer closed cleanly between frames (end of
/// session), which is not an error. A close mid-frame, or an impossible length, is.
pub async fn read_frame<R>(r: &mut R) -> Result<Option<Vec<u8>>>
where
    R: AsyncReadExt + Unpin,
{
    let mut hdr = [0u8; 4];
    if r.read(&mut hdr[..1]).await? == 0 {
        return Ok(None);
    }
    r.read_exact(&mut hdr[1..]).await?;
    let len = u32::from_le_bytes(hdr);
    if len == 0 || len > MAX_FRAME {
        bail!("frame length {len} is not a SAPIENT message (expected 1..={MAX_FRAME})");
    }
    let mut body = vec![0u8; len as usize];
    // A short read here IS an error: the length promised bytes the peer did not send.
    r.read_exact(&mut body).await?;
    Ok(Some(body))
}

/// Write one frame: the 4-byte little-endian length, then the body, then flush so the
/// peer sees the whole message now rather than when the buffer happens to fill.
pub async fn write_frame<W>(w: &mut W, body: &[u8]) -> Result<()>
where
    W: AsyncWriteExt + Unpin,
{
    let len =
        u32::try_from(body.len()).map_err(|_| anyhow::anyhow!("message too large to frame"))?;
    w.write_all(&len.to_le_bytes()).await?;
    w.write_all(body).await?;
    w.flush().await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[tokio::test]
    async fn round_trips_a_body() {
        let mut buf = Vec::new();
        write_frame(&mut buf, b"hello sapient").await.unwrap();
        let mut cur = Cursor::new(buf);
        let got = read_frame(&mut cur).await.unwrap();
        assert_eq!(got.as_deref(), Some(&b"hello sapient"[..]));
        // A second read past the one frame is a clean end, not an error.
        assert_eq!(read_frame(&mut cur).await.unwrap(), None);
    }

    #[tokio::test]
    async fn refuses_a_zero_length_frame() {
        // An empty SapientMessage is not a message anyone sent; a zero prefix is a desync.
        let mut cur = Cursor::new(0u32.to_le_bytes().to_vec());
        let err = read_frame(&mut cur).await.unwrap_err().to_string();
        assert!(err.contains("frame length 0"), "{err}");
    }

    #[tokio::test]
    async fn a_body_shorter_than_its_prefix_is_an_error() {
        let mut bytes = 8u32.to_le_bytes().to_vec();
        bytes.extend_from_slice(b"four");
        assert!(read_frame(&mut Cursor::new(bytes)).await.is_err());
    }

    #[tokio::test]
    async fn refuses_an_absurd_length() {
        let mut cur = Cursor::new((MAX_FRAME + 1).to_le_bytes().to_vec());
        assert!(read_frame(&mut cur).await.is_err());
    }

    #[tokio::test]
    async fn truncated_headers_are_errors() {
        for length in 1..4 {
            assert!(read_frame(&mut Cursor::new(vec![1; length])).await.is_err());
        }
    }

    #[tokio::test]
    async fn empty_stream_is_none_not_error() {
        let mut cur = Cursor::new(Vec::new());
        assert_eq!(read_frame(&mut cur).await.unwrap(), None);
    }
}
