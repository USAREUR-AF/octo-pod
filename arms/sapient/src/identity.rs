//! Who this pod is on a SAPIENT network, and the ids it stamps on what it sends.
//!
//! The `node_id` is minted once and kept. A node that registers under a new UUID after
//! every restart is a new node to the peer every time: the DMM accumulates ghosts, each
//! holding whatever state it was given, and nothing on either side says they are the same
//! sensor. So it is written to the arm's state directory on first use and read back
//! afterwards — the same reasoning that keeps the pod's slot anchor out of the binary.
//!
//! It is a PUBLIC identifier, stamped on every message and visible to the peer, so it
//! lives beside the arm's configuration at 0644 rather than with its key material.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use tracing::info;

/// Crockford base32, which is what a ULID is written in: no I, L, O or U, so an id read
/// aloud or copied off a screen cannot become a different id.
const CROCKFORD: &[u8; 32] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/// The file under the arm's state directory that holds the node id.
const NODE_ID_FILE: &str = "node-id";

/// Read this node's id, minting and persisting one on first use.
pub fn node_id(state_dir: &Path) -> Result<String> {
    let path = state_dir.join(NODE_ID_FILE);
    if let Ok(text) = fs::read_to_string(&path) {
        let existing = text.trim().to_string();
        if !existing.is_empty() {
            return Ok(existing);
        }
    }

    let minted = uuid::Uuid::new_v4().to_string();
    fs::create_dir_all(state_dir)
        .with_context(|| format!("creating the arm state directory `{}`", state_dir.display()))?;
    fs::write(&path, format!("{minted}\n")).with_context(|| {
        format!(
            "writing the node id to `{}` — without it this arm registers as a new node on \
             every restart, and the peer accumulates one ghost per restart",
            path.display()
        )
    })?;
    info!(node_id = %minted, path = %path.display(), "minted this node's SAPIENT id");
    Ok(minted)
}

/// A ULID: 48 bits of millisecond timestamp then 80 bits of randomness, in 26 Crockford
/// characters. The standard requires one on every report, and requires it to sort in the
/// order the reports were made — which is the whole reason it is not a UUID.
pub fn ulid() -> String {
    let ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    // The entropy source is the same one uuid::new_v4 uses; taking ten of its bytes
    // avoids a second random-number dependency for a value that only has to be unique
    // within a millisecond.
    let entropy = uuid::Uuid::new_v4();
    let rand = &entropy.as_bytes()[..10];

    let mut bits = [0u8; 16];
    bits[..6].copy_from_slice(&ms.to_be_bytes()[2..]);
    bits[6..].copy_from_slice(rand);
    encode_crockford(&bits)
}

/// 128 bits to 26 Crockford characters, most significant first.
fn encode_crockford(bytes: &[u8; 16]) -> String {
    let mut value = u128::from_be_bytes(*bytes);
    let mut out = [0u8; 26];
    for slot in out.iter_mut().rev() {
        *slot = CROCKFORD[(value & 0x1f) as usize];
        value >>= 5;
    }
    String::from_utf8(out.to_vec()).expect("Crockford alphabet is ASCII")
}

/// Where the arm keeps what it must remember across restarts. One directory per arm, so
/// two arms on one pod cannot read each other's identity.
pub fn state_dir(arm_id: &str) -> PathBuf {
    PathBuf::from("/var/lib/octo-arm").join(arm_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("octo-sapient-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        d
    }

    #[test]
    fn the_node_id_survives_a_restart() {
        let dir = tmp("persist");
        let first = node_id(&dir).unwrap();
        let second = node_id(&dir).unwrap();
        assert_eq!(first, second, "a restart must not mint a new node");
        assert_eq!(
            first.len(),
            36,
            "a v4 UUID, as the standard's is_uuid demands"
        );
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn an_empty_file_is_treated_as_no_id_rather_than_an_empty_one() {
        // A truncated write (a pod that lost power mid-write) must not register the pod
        // under the empty string, which the peer would reject on every connect forever.
        let dir = tmp("empty");
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join(NODE_ID_FILE), "   \n").unwrap();
        let id = node_id(&dir).unwrap();
        assert_eq!(id.len(), 36);
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn ulids_are_26_crockford_characters() {
        let u = ulid();
        assert_eq!(u.len(), 26);
        assert!(
            u.bytes().all(|b| CROCKFORD.contains(&b)),
            "not Crockford base32: {u}"
        );
    }

    #[test]
    fn ulids_sort_in_the_order_they_were_made() {
        // The property the standard wants from a ULID, and the one a UUID does not have:
        // reports compare in time order as plain strings.
        let first = ulid();
        std::thread::sleep(std::time::Duration::from_millis(3));
        let second = ulid();
        assert!(first < second, "{first} should sort before {second}");
    }

    #[test]
    fn ulids_made_in_the_same_millisecond_still_differ() {
        let mut seen = std::collections::HashSet::new();
        for _ in 0..1000 {
            assert!(seen.insert(ulid()), "a ULID repeated within one run");
        }
    }

    #[test]
    fn encoding_is_most_significant_first() {
        assert_eq!(encode_crockford(&[0u8; 16]), "0".repeat(26));
        let mut one = [0u8; 16];
        one[15] = 1;
        assert!(encode_crockford(&one).ends_with('1'));
        assert!(encode_crockford(&one).starts_with('0'));
    }

    #[test]
    fn state_dirs_are_per_arm() {
        assert_ne!(state_dir("sapient1"), state_dir("other"));
        assert!(state_dir("sapient1").ends_with("sapient1"));
    }
}
