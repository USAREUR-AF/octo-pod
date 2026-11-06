// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Egress mode: this arm as a `sapient://` destination of the pod's egress.
//!
//! Declared with fabric keys (`SAPIENT_EGRESS_KEYS`), the arm is one of the pod's egress
//! destinations, held to the same rules as `egress-arm`:
//!
//! - every key passes the downlink gate's scope rule, and the arm subscribes only to the
//!   `downlink/...` pen keys the downlink bridge writes for those keys;
//! - it dials exactly one literal `IP:PORT`, which must be in the declared allow list;
//! - it is the sensor side of the session (`asm`): it registers with the controller and
//!   sends it what was released, and nothing else;
//! - it writes nothing to the pen: no `-egress` mirror of what it sends (that came down
//!   from the fabric, and the pen is a way back up), no capture of the controller's frames
//!   (the return path, read by the session only) and no lifecycle records. Each send and
//!   each session event is a line in a local audit file instead, as `egress-arm` records
//!   its sends. Its pen grant is therefore read-only, exactly an egress destination's.
//!
//! The data must already be SAPIENT: each sample is one `SapientMessage` carrying a
//! `DetectionReport` or an `Alert`. The arm converts nothing; it re-addresses each message
//! under its own registered node id and frames it for the wire.
//!
//! License: Apache-2.0

use std::fmt;
use std::io;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use octo_egress::audit::AuditLog;
use octo_egress::dest::{Destination, DestinationAllowlist};
use octo_egress::gate::{fabric_key, pen_key, DownlinkGate};
use serde_json::Value;
use zenoh::key_expr::keyexpr;

use crate::session::Role;

/// Everything checked before the arm may open anything.
#[derive(Debug)]
pub struct EgressPlan {
    /// The one address the arm dials.
    pub dest: SocketAddr,
    /// The pen keys it subscribes to, one per declared fabric key.
    pub pen_keys: Vec<String>,
    /// The declared keys, checked again on every sample.
    pub gate: Arc<DownlinkGate>,
}

/// Whether `pen` is the downlink form of a key `gate` admits.
#[must_use]
pub fn permits_pen_key(gate: &DownlinkGate, pen: &str) -> bool {
    fabric_key(pen)
        .as_deref()
        .and_then(|k| keyexpr::new(k).ok())
        .is_some_and(|k| gate.permits(k))
}

/// The declaration the arm was started with, as far as egress mode is concerned.
#[derive(Debug, Clone, Copy)]
pub struct Declared<'a> {
    /// `SAPIENT_EGRESS_KEYS`: fabric keys, comma-separated. Empty means not egress mode.
    pub egress_keys: &'a str,
    /// `SAPIENT_RELAY_KEY`: the single pen key of the non-egress relay.
    pub relay_key: &'a str,
    /// The parsed role.
    pub role: Role,
    /// `--target`, the peer to dial.
    pub target: Option<&'a str>,
    /// `--listen`, a port to accept on.
    pub listen: Option<u16>,
    /// `SAPIENT_ALLOW_DEST`: literal `IP:PORT` entries, comma-separated.
    pub allow_dest: &'a str,
}

/// Decide whether the arm runs in egress mode and, if it does, check its declaration.
///
/// Returns `Ok(None)` when no egress keys are declared: the arm then runs as an ordinary
/// session arm.
///
/// # Errors
///
/// Returns why the declaration is refused. A refused arm opens nothing.
pub fn plan(d: &Declared<'_>) -> Result<Option<EgressPlan>, String> {
    if d.egress_keys.trim().is_empty() {
        return Ok(None);
    }
    if !d.relay_key.trim().is_empty() {
        return Err("give SAPIENT_EGRESS_KEYS or SAPIENT_RELAY_KEY, not both".into());
    }
    if d.role != Role::Asm {
        return Err(format!(
            "a sapient:// destination registers with the controller as a sensor node, so its \
             role must be asm, not {}",
            d.role
        ));
    }
    if d.listen.is_some() {
        return Err("a sapient:// destination dials the controller; --listen is refused".into());
    }
    let Some(target) = d.target else {
        return Err("a sapient:// destination needs --target <IP>:<PORT>".into());
    };
    let dest = Destination::parse(&format!("tcp://{}", target.trim()))
        .map_err(|e| format!("sapient://{}: {e}", target.trim()))?;
    DestinationAllowlist::from_csv(d.allow_dest)
        .map_err(|e| e.to_string())?
        .admit(&dest)
        .map_err(|e| e.to_string())?;
    let keys: Vec<String> = d
        .egress_keys
        .split(',')
        .map(|k| k.trim().to_string())
        .filter(|k| !k.is_empty())
        .collect();
    let (gate, refused) = DownlinkGate::new(&keys);
    if !refused.is_empty() {
        let why: Vec<String> = refused.iter().map(ToString::to_string).collect();
        return Err(format!("declared keys refused: {}", why.join("; ")));
    }
    if gate.is_empty() {
        return Err("no keys declared".into());
    }
    let pen_keys = gate.entries().iter().map(|k| pen_key(k.as_str())).collect();
    Ok(Some(EgressPlan {
        dest: dest.addr,
        pen_keys,
        gate: Arc::new(gate),
    }))
}

/// The local record of what this destination sent, shared by the session and `main`.
#[derive(Clone)]
pub struct AuditHook {
    log: Arc<AuditLog>,
    seq: Arc<AtomicU64>,
}

impl fmt::Debug for AuditHook {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AuditHook")
            .field("path", &self.log.path())
            .finish_non_exhaustive()
    }
}

impl AuditHook {
    /// Open `<dir>/<arm>-egress.ndjson` for appending.
    ///
    /// # Errors
    ///
    /// Fails if the directory cannot be created or the file opened.
    pub fn open(dir: &Path, arm: &str, destination: &str) -> io::Result<Self> {
        Ok(Self {
            log: Arc::new(AuditLog::open(dir, arm, destination)?),
            seq: Arc::new(AtomicU64::new(0)),
        })
    }

    /// Record one message relayed to the controller. The hash is over the payload as it
    /// came down from the fabric, so it matches the downlink audit sink's record of the
    /// same sample; what went on the wire differs only in node id, timestamp and framing.
    ///
    /// # Errors
    ///
    /// Fails if the line cannot be written; the caller counts it and the send stands.
    pub fn sent(&self, pen: &str, payload: &[u8]) -> io::Result<()> {
        let seq = self.seq.fetch_add(1, Ordering::Relaxed) + 1;
        let fabric = fabric_key(pen).unwrap_or_default();
        self.log.sent(seq, &fabric, pen, payload)
    }

    /// Any other event: start, refusal.
    ///
    /// # Errors
    ///
    /// Fails if the line cannot be written.
    pub fn event(&self, kind: &str, detail: Value) -> io::Result<()> {
        self.log.event(kind, detail)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn declared<'a>(keys: &'a str, target: Option<&'a str>, allow: &'a str) -> Declared<'a> {
        Declared {
            egress_keys: keys,
            relay_key: "",
            role: Role::Asm,
            target,
            listen: None,
            allow_dest: allow,
        }
    }

    #[test]
    fn no_egress_keys_is_not_egress_mode() {
        let d = declared("", Some("192.0.2.10:48000"), "");
        assert!(plan(&d).expect("not refused").is_none());
    }

    #[test]
    fn a_complete_declaration_is_planned() {
        let d = declared(
            "aa11/sapient/**, release/c2/tracks",
            Some("192.0.2.10:48000"),
            "192.0.2.10:48000",
        );
        let p = plan(&d).expect("not refused").expect("egress mode");
        assert_eq!(p.dest.to_string(), "192.0.2.10:48000");
        assert_eq!(
            p.pen_keys,
            vec![
                "downlink/aa11/sapient/**".to_string(),
                "downlink/release/c2/tracks".to_string()
            ]
        );
        assert!(permits_pen_key(&p.gate, "downlink/aa11/sapient/radar1"));
        assert!(!permits_pen_key(&p.gate, "downlink/bb22/sapient/radar1"));
        assert!(!permits_pen_key(&p.gate, "raw/octo/sapient/192.0.2.10"));
    }

    #[test]
    fn mixing_with_the_relay_key_is_refused() {
        let mut d = declared(
            "aa11/sapient/**",
            Some("192.0.2.10:48000"),
            "192.0.2.10:48000",
        );
        d.relay_key = "relay/out/**";
        assert!(plan(&d).is_err());
    }

    #[test]
    fn only_the_sensor_role_is_accepted() {
        for role in [Role::Dmm, Role::Client, Role::Auto] {
            let mut d = declared(
                "aa11/sapient/**",
                Some("192.0.2.10:48000"),
                "192.0.2.10:48000",
            );
            d.role = role;
            assert!(plan(&d).is_err(), "{role} must be refused");
        }
    }

    #[test]
    fn listening_is_refused_and_a_target_is_required() {
        let mut d = declared(
            "aa11/sapient/**",
            Some("192.0.2.10:48000"),
            "192.0.2.10:48000",
        );
        d.listen = Some(48000);
        assert!(plan(&d).is_err());
        let d = declared("aa11/sapient/**", None, "192.0.2.10:48000");
        assert!(plan(&d).is_err());
    }

    #[test]
    fn the_target_must_be_literal_and_allowed() {
        let d = declared(
            "aa11/sapient/**",
            Some("controller.example:48000"),
            "192.0.2.10:48000",
        );
        assert!(plan(&d).is_err(), "a name is not a literal address");
        let d = declared(
            "aa11/sapient/**",
            Some("192.0.2.11:48000"),
            "192.0.2.10:48000",
        );
        assert!(plan(&d).is_err(), "not in the allow list");
        let d = declared("aa11/sapient/**", Some("192.0.2.10:48000"), "");
        assert!(plan(&d).is_err(), "an empty allow list allows nothing");
    }

    #[test]
    fn keys_outside_the_scope_rule_are_refused() {
        for keys in [
            "**",
            "*/sapient",
            "ops/x",
            "downlink/aa11/x",
            "release/**",
            "@/x",
        ] {
            let d = declared(keys, Some("192.0.2.10:48000"), "192.0.2.10:48000");
            assert!(plan(&d).is_err(), "{keys} must be refused");
        }
    }

    #[test]
    fn sends_are_audited_with_the_fabric_key() {
        let dir = std::env::temp_dir().join(format!("sapient-egress-audit-{}", std::process::id()));
        let hook = AuditHook::open(&dir, "c2-sapient", "sapient://192.0.2.10:48000").expect("open");
        hook.sent("downlink/aa11/sapient/radar1", b"payload")
            .expect("write");
        let text = std::fs::read_to_string(dir.join("c2-sapient-egress.ndjson")).expect("read");
        let line: Value = serde_json::from_str(text.trim()).expect("one json line");
        assert_eq!(line["kind"], "sent");
        assert_eq!(line["seq"], 1);
        assert_eq!(line["fabric_key"], "aa11/sapient/radar1");
        assert_eq!(line["pen_key"], "downlink/aa11/sapient/radar1");
        assert_eq!(line["destination"], "sapient://192.0.2.10:48000");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
