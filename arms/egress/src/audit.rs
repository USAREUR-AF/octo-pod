//! The egress arm's local audit record: what was sent where, and when.
//!
//! One NDJSON file per arm, appended and flushed per line, on the pod's own disk, so the
//! record exists whether or not anything upstream is reachable. Each `sent` line names the
//! destination, the fabric key the bytes came from, the pen key they were read on, the
//! length and a SHA-256 of the payload. The hash uses the same `sha256:<hex>` form as
//! GOAT's audit subscriber, so a line here can be joined to the fabric-side audit event for
//! the same bytes. Periodic `summary` lines carry the arm's counters.
//!
//! Failure behavior: a line that cannot be written is reported to the caller, which counts
//! it (`audit_errors`) and keeps sending. The record is evidence of what left the pod, not
//! a gate on it; a full disk shows up as a rising counter, not a stopped feed.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::SystemTime;

use serde_json::{json, Value};

/// An append-only NDJSON audit file for one egress arm.
pub struct AuditLog {
    path: PathBuf,
    file: Mutex<File>,
    arm: String,
    destination: String,
}

/// `sha256:<hex>` over a payload.
#[must_use]
pub fn payload_hash(payload: &[u8]) -> String {
    let digest = ring::digest::digest(&ring::digest::SHA256, payload);
    format!("sha256:{}", hex::encode(digest.as_ref()))
}

impl AuditLog {
    /// Open (or create) `<dir>/<arm>-egress.ndjson` for appending.
    ///
    /// # Errors
    ///
    /// Fails if the directory cannot be created or the file opened.
    pub fn open(dir: &Path, arm: &str, destination: &str) -> io::Result<Self> {
        fs::create_dir_all(dir)?;
        let path = dir.join(format!("{arm}-egress.ndjson"));
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        Ok(Self {
            path,
            file: Mutex::new(file),
            arm: arm.to_string(),
            destination: destination.to_string(),
        })
    }

    /// Where the record is kept.
    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }

    fn write(&self, mut line: Value) -> io::Result<()> {
        if let Value::Object(map) = &mut line {
            map.insert(
                "ts".into(),
                json!(humantime::format_rfc3339_millis(SystemTime::now()).to_string()),
            );
            map.insert("arm".into(), json!(self.arm));
            map.insert("destination".into(), json!(self.destination));
        }
        let mut text = line.to_string();
        text.push('\n');
        let mut file = self
            .file
            .lock()
            .map_err(|_| io::Error::other("audit file lock poisoned"))?;
        file.write_all(text.as_bytes())?;
        file.flush()
    }

    /// One delivered sample.
    ///
    /// # Errors
    ///
    /// Fails if the line cannot be written; the caller counts it.
    pub fn sent(
        &self,
        seq: u64,
        fabric_key: &str,
        pen_key: &str,
        payload: &[u8],
    ) -> io::Result<()> {
        self.write(json!({
            "kind": "sent",
            "seq": seq,
            "fabric_key": fabric_key,
            "pen_key": pen_key,
            "len": payload.len(),
            "payload_hash": payload_hash(payload),
        }))
    }

    /// One sample queued to the consumers connected to a `tcp-server://` destination.
    ///
    /// # Errors
    ///
    /// Fails if the line cannot be written; the caller counts it.
    pub fn served(
        &self,
        seq: u64,
        fabric_key: &str,
        pen_key: &str,
        payload: &[u8],
        consumers: usize,
    ) -> io::Result<()> {
        self.write(json!({
            "kind": "sent",
            "seq": seq,
            "fabric_key": fabric_key,
            "pen_key": pen_key,
            "len": payload.len(),
            "payload_hash": payload_hash(payload),
            "consumers": consumers,
        }))
    }

    /// Any other event: start, refusal, summary.
    ///
    /// # Errors
    ///
    /// Fails if the line cannot be written.
    pub fn event(&self, kind: &str, detail: Value) -> io::Result<()> {
        let mut line = json!({ "kind": kind });
        if let (Value::Object(map), Value::Object(extra)) = (&mut line, detail) {
            map.extend(extra);
        }
        self.write(line)
    }
}

/// Consumer events from a `tcp-server://` destination go into the same record.
impl crate::serve::Events for AuditLog {
    fn event(&self, kind: &str, detail: Value) {
        if let Err(e) = AuditLog::event(self, kind, detail) {
            tracing::warn!(error = %e, kind, "audit line not written");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_one_parseable_line_per_send() {
        let dir = std::env::temp_dir().join(format!("octo-egress-audit-{}", std::process::id()));
        let log = AuditLog::open(&dir, "a", "udp://10.1.2.3:4001").unwrap();
        log.sent(1, "release/L/k", "downlink/L/k", b"abc").unwrap();
        log.event("summary", json!({"sent_total": 1})).unwrap();
        let text = fs::read_to_string(log.path()).unwrap();
        let lines: Vec<Value> = text
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0]["kind"], "sent");
        assert_eq!(lines[0]["destination"], "udp://10.1.2.3:4001");
        assert_eq!(
            lines[0]["payload_hash"],
            "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(lines[1]["sent_total"], 1);
        fs::remove_dir_all(dir).unwrap();
    }
}
