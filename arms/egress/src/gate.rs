// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The downlink gate: which fabric keys may come down onto the pen.
//!
//! The mirror of the uplink bridge's `bridge_allow`. Upstream, `bridge_allow` names the pen
//! namespaces the uplink bridge may forward to the fabric. Downstream, `downlink_allow`
//! names the fabric keys the downlink bridge may subscribe to and republish on the pen.
//!
//! An entry is any fabric key the pod's slot can read, held to one scope rule so that no
//! entry can reach further than a named publisher or release lane:
//!
//! - it is a canonical Zenoh key expression;
//! - its first chunk is literal: it names a publisher (a slot anchor) or a lane root, so
//!   `**` and `*/x`, the whole fabric, are refused;
//! - under `release/`, the lane chunk is literal too: `release/**` would be every lane;
//! - it never starts in GOAT's reserved roots (`ops`, `attest`, `system`), the pod's own
//!   `downlink` namespace, or Zenoh's admin space (`@`).
//!
//! What may leave the pod is decided per destination, by the pod: a destination is
//! declared with exact keys, released, and every send is audited. This gate is the bridge's
//! half of that, checked when it starts and again on every sample.
//!
//! Keys move namespace exactly once, at the bridge: fabric `K` becomes pen `downlink/K`.
//! Egress arms are granted pen keys, never fabric keys, so a destination can only ever read
//! what the gate let down.

use std::fmt;

use zenoh::key_expr::{keyexpr, OwnedKeyExpr};

/// The pen namespace every downlinked key lives under.
pub const PEN_ROOT: &str = "downlink";

/// First chunks no entry may start with: GOAT's reserved roots and the pod's own namespace.
const RESERVED: [&str; 4] = ["ops", "attest", "system", PEN_ROOT];

/// Why a declared entry was refused. Refusal is per entry: one bad line does not stop the
/// accepted ones, and it is reported loudly instead of being subscribed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Refusal {
    /// Not a canonical Zenoh key expression.
    NotAKeyExpr(String),
    /// A wildcard where a publisher or lane must be named.
    TooBroad(String),
    /// A reserved root, the pod's own namespace, or Zenoh's admin space.
    Reserved(String),
}

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Refusal::NotAKeyExpr(e) => write!(f, "{e:?} is not a canonical key expression"),
            Refusal::TooBroad(e) => write!(
                f,
                "{e:?} must start with a literal publisher or release lane, not a wildcard"
            ),
            Refusal::Reserved(e) => write!(
                f,
                "{e:?} starts in a reserved namespace (ops, attest, system, downlink or @)"
            ),
        }
    }
}

fn is_wild(chunk: &str) -> bool {
    chunk.contains('*') || chunk.contains('$')
}

/// Check one entry against the scope rule.
///
/// # Errors
///
/// Returns the refusal when the entry is malformed, too broad or reserved.
pub fn check_entry(raw: &str) -> Result<OwnedKeyExpr, Refusal> {
    let ke =
        OwnedKeyExpr::try_from(raw.to_string()).map_err(|_| Refusal::NotAKeyExpr(raw.into()))?;
    let mut chunks = ke.as_str().split('/');
    let first = chunks.next().unwrap_or_default();
    if first.starts_with('@') || RESERVED.contains(&first) {
        return Err(Refusal::Reserved(raw.into()));
    }
    if is_wild(first) {
        return Err(Refusal::TooBroad(raw.into()));
    }
    if first == "release" && chunks.next().is_none_or(is_wild) {
        return Err(Refusal::TooBroad(raw.into()));
    }
    Ok(ke)
}

/// The pen key a fabric key or expression is republished on: `downlink/<fabric>`.
#[must_use]
pub fn pen_key(fabric: &str) -> String {
    format!("{PEN_ROOT}/{fabric}")
}

/// The inverse of [`pen_key`], for an egress arm's audit record. `None` outside `downlink/`.
#[must_use]
pub fn fabric_key(pen: &str) -> Option<String> {
    pen.strip_prefix(PEN_ROOT)
        .and_then(|rest| rest.strip_prefix('/'))
        .filter(|rest| !rest.is_empty())
        .map(str::to_string)
}

/// The validated `downlink_allow` list.
#[derive(Debug, Clone, Default)]
pub struct DownlinkGate {
    allow: Vec<OwnedKeyExpr>,
}

impl DownlinkGate {
    /// Build the gate from declared entries. Returns the gate holding only the accepted
    /// entries, plus every refusal. An empty gate is legitimate: a pod that has released
    /// nothing downward.
    #[must_use]
    pub fn new(entries: &[String]) -> (Self, Vec<Refusal>) {
        let mut allow = Vec::new();
        let mut refused = Vec::new();
        for raw in entries.iter().map(|e| e.trim()).filter(|e| !e.is_empty()) {
            match check_entry(raw) {
                Ok(ke) if !allow.contains(&ke) => allow.push(ke),
                Ok(_) => {}
                Err(r) => refused.push(r),
            }
        }
        (Self { allow }, refused)
    }

    /// The accepted entries, each to be subscribed upstream.
    #[must_use]
    pub fn entries(&self) -> &[OwnedKeyExpr] {
        &self.allow
    }

    /// True when nothing has been released downward.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.allow.is_empty()
    }

    /// Does a concrete fabric key fall inside an accepted entry?
    #[must_use]
    pub fn permits(&self, key: &keyexpr) -> bool {
        self.allow.iter().any(|e| e.includes(key))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RADAR_1: &str = "894aee26d8146f54a0e949eb66acc40c/sensors/radar1/asterix/data";
    const RADAR_2: &str = "894aee26d8146f54a0e949eb66acc40c/sensors/radar2/asterix/data";
    const LANE_KEY: &str = "release/REL-ALL/c43671680793b76e9f9688e3911d8f23/echidna/v1";

    fn ke(s: &str) -> OwnedKeyExpr {
        OwnedKeyExpr::try_from(s.to_string()).unwrap()
    }

    #[test]
    fn nothing_released_permits_nothing() {
        let (gate, refused) = DownlinkGate::new(&[]);
        assert!(gate.is_empty() && refused.is_empty());
        assert!(!gate.permits(&ke(RADAR_1)));
    }

    #[test]
    fn a_publishers_key_comes_down_and_its_neighbour_does_not() {
        let (gate, refused) = DownlinkGate::new(&[RADAR_1.to_string()]);
        assert!(refused.is_empty(), "{refused:?}");
        assert!(gate.permits(&ke(RADAR_1)));
        assert!(!gate.permits(&ke(RADAR_2)), "only the named key comes down");
        assert_eq!(pen_key(RADAR_1), format!("downlink/{RADAR_1}"));
        assert_eq!(fabric_key(&pen_key(RADAR_1)).unwrap(), RADAR_1);
    }

    #[test]
    fn release_lane_keys_still_work() {
        let (gate, refused) =
            DownlinkGate::new(&[LANE_KEY.to_string(), "release/REL-ALL/**".to_string()]);
        assert!(refused.is_empty(), "{refused:?}");
        assert!(gate.permits(&ke(LANE_KEY)));
        assert_eq!(pen_key("release/REL-ALL/**"), "downlink/release/REL-ALL/**");
    }

    #[test]
    fn a_publisher_wildcard_reaches_that_publisher_only() {
        let (gate, _) =
            DownlinkGate::new(&["894aee26d8146f54a0e949eb66acc40c/sensors/**".to_string()]);
        assert!(gate.permits(&ke(RADAR_1)) && gate.permits(&ke(RADAR_2)));
        assert!(!gate.permits(&ke("0405fc463154cb88502bc25fa9f0989a/sensors/x")));
    }

    #[test]
    fn the_whole_fabric_every_lane_and_reserved_roots_are_refused() {
        let entries = [
            "**",
            "*/sensors/x",
            "a$*/x",
            "release/**",
            "release/*/x",
            "ops/audit/x",
            "attest/x",
            "system/x",
            "downlink/x",
            "@/router/x",
        ]
        .map(String::from);
        let (gate, refused) = DownlinkGate::new(&entries);
        assert!(gate.is_empty(), "{:?}", gate.entries());
        assert_eq!(refused.len(), entries.len(), "{refused:?}");
        assert!(refused[..5]
            .iter()
            .all(|r| matches!(r, Refusal::TooBroad(_))));
        assert!(refused[5..]
            .iter()
            .all(|r| matches!(r, Refusal::Reserved(_))));
    }

    #[test]
    fn malformed_entries_are_refused_and_the_rest_still_run() {
        let (gate, refused) = DownlinkGate::new(&["a//b".to_string(), RADAR_1.to_string()]);
        assert!(matches!(refused[0], Refusal::NotAKeyExpr(_)));
        assert_eq!(gate.entries().len(), 1);
    }

    #[test]
    fn fabric_key_is_none_outside_the_pen_root() {
        assert_eq!(fabric_key("downlinkx/a"), None);
        assert_eq!(fabric_key("downlink/"), None);
        assert_eq!(fabric_key("raw/octo/x"), None);
    }

    #[test]
    fn duplicates_collapse() {
        let (gate, _) = DownlinkGate::new(&[RADAR_1.to_string(), RADAR_1.to_string()]);
        assert_eq!(gate.entries().len(), 1);
    }
}
