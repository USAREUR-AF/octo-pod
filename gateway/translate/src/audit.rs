// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The gateway's local audit record: which captured sample became which published one.
//!
//! One NDJSON file, appended and flushed per line, on the pod's own disk, in the shape of
//! the egress arms' record (`arms/egress/src/audit.rs`): an RFC 3339 `ts`, a `kind`, and
//! payload hashes as `sha256:<hex>`, the form GOAT's audit subscriber uses. A
//! `translated` line holds the hash of the source payload and the hash of the result, so
//! it joins the pen audit sink's event for the captured sample to the sink's event for
//! the converted one. A `failed` line holds the source hash and the engine's error.
//!
//! Failure behavior: a line that cannot be written is reported to the caller, which
//! counts it (`audit_errors`) and keeps converting. A full disk shows as a rising
//! counter, not a stopped feed.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::SystemTime;

use serde_json::{json, Value};

/// An append-only NDJSON audit file for one gateway.
pub struct AuditLog {
    path: PathBuf,
    file: Mutex<File>,
    id: String,
}

/// `sha256:<hex>` over a payload.
#[must_use]
pub fn payload_hash(payload: &[u8]) -> String {
    let digest = ring::digest::digest(&ring::digest::SHA256, payload);
    format!("sha256:{}", hex::encode(digest.as_ref()))
}

/// The sample a result came from.
#[derive(Debug, Clone)]
pub struct Source<'a> {
    /// The pen key the sample was read on.
    pub key: &'a str,
    /// The sample's Zenoh timestamp, when the pen stamped one.
    pub timestamp: Option<&'a str>,
    /// `sha256:<hex>` of the captured bytes.
    pub hash: &'a str,
    /// The source format.
    pub format: &'a str,
}

impl AuditLog {
    /// Open (or create) `<dir>/<id>-translate.ndjson` for appending.
    ///
    /// # Errors
    ///
    /// Fails if the directory cannot be created or the file opened.
    pub fn open(dir: &Path, id: &str) -> io::Result<Self> {
        fs::create_dir_all(dir)?;
        let path = dir.join(format!("{id}-translate.ndjson"));
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        Ok(Self {
            path,
            file: Mutex::new(file),
            id: id.to_string(),
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
            map.insert("gateway".into(), json!(self.id));
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

    /// One converted sample, published.
    ///
    /// # Errors
    ///
    /// Fails if the line cannot be written; the caller counts it.
    pub fn translated(
        &self,
        seq: u64,
        source: &Source<'_>,
        to: &str,
        key: &str,
        result: &[u8],
    ) -> io::Result<()> {
        self.write(json!({
            "kind": "translated",
            "seq": seq,
            "source_key": source.key,
            "source_ts": source.timestamp,
            "source_hash": source.hash,
            "from": source.format,
            "to": to,
            "key": key,
            "len": result.len(),
            "payload_hash": payload_hash(result),
        }))
    }

    /// One sample that produced no output for one target: `failed` (the engine refused
    /// it) or `skipped` (the target has no field for what it carries).
    ///
    /// # Errors
    ///
    /// Fails if the line cannot be written; the caller counts it.
    pub fn not_translated(
        &self,
        kind: &str,
        source: &Source<'_>,
        to: &str,
        error: &str,
        detail: &str,
    ) -> io::Result<()> {
        self.write(json!({
            "kind": kind,
            "source_key": source.key,
            "source_ts": source.timestamp,
            "source_hash": source.hash,
            "from": source.format,
            "to": to,
            "error": error,
            "detail": detail,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_translated_line_joins_the_source_hash_to_the_result_hash() {
        let dir = std::env::temp_dir().join(format!("octo-translate-audit-{}", std::process::id()));
        let log = AuditLog::open(&dir, "xl1").unwrap();
        let source = Source {
            key: "raw/radar1/sapient/192-0-2-50",
            timestamp: None,
            hash: &payload_hash(b"abc"),
            format: "sapient",
        };
        log.translated(1, &source, "cot-xml", "raw/xl1/cot-xml/k", b"<event/>")
            .unwrap();
        log.not_translated(
            "failed",
            &source,
            "geojson",
            "no-kind",
            "encode: no-kind: x",
        )
        .unwrap();
        log.event("summary", json!({"translated": 1})).unwrap();

        let text = fs::read_to_string(log.path()).unwrap();
        let lines: Vec<Value> = text
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[0]["kind"], "translated");
        assert_eq!(lines[0]["gateway"], "xl1");
        assert_eq!(
            lines[0]["source_hash"],
            "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(lines[0]["payload_hash"], payload_hash(b"<event/>"));
        assert_eq!(lines[0]["source_ts"], Value::Null);
        assert_eq!(lines[1]["kind"], "failed");
        assert_eq!(lines[1]["error"], "no-kind");
        assert_eq!(lines[2]["translated"], 1);
        assert!(log.path().ends_with("xl1-translate.ndjson"));
        fs::remove_dir_all(dir).unwrap();
    }
}
