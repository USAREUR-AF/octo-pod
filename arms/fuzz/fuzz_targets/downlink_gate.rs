// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The downlink gate (`octo_egress::gate`): which fabric keys may come down onto the pen,
//! and which samples an egress arm may send on.
//!
//! Input: UTF-8 text. A line that starts with `+` is a declared `downlink_allow` entry.
//! Every other line is the key a sample arrived on, on the pen (`downlink/<fabric key>`).
//!
//! Besides "never panics", the target checks the gate's scope rule and that the arms
//! agree on it:
//! - an accepted entry starts with a literal publisher or release lane, never a reserved
//!   root or the admin space;
//! - `egress-arm` and `zenoh-egress-arm` (`fabric_key`, then `OwnedKeyExpr`, then
//!   `permits`) and the sapient arm's egress mode (`permits_pen_key`) admit the same keys;
//! - an admitted key never reaches past the publisher or lane an entry names.

#![no_main]

use libfuzzer_sys::fuzz_target;
use octo_egress::gate::{check_entry, fabric_key, pen_key, DownlinkGate};
use zenoh::key_expr::OwnedKeyExpr;

/// The first chunks no entry may start with (`RESERVED` in `egress/src/gate.rs`).
const RESERVED: [&str; 4] = ["ops", "attest", "system", "downlink"];

fn is_wild(chunk: &str) -> bool {
    chunk.contains('*') || chunk.contains('$')
}

/// The publisher, and for a release key the lane, that a key or entry is scoped to.
fn scope(key: &str) -> (&str, Option<&str>) {
    let mut chunks = key.split('/');
    let first = chunks.next().unwrap_or_default();
    let lane = if first == "release" {
        chunks.next()
    } else {
        None
    };
    (first, lane)
}

fuzz_target!(|data: &[u8]| {
    let Ok(text) = std::str::from_utf8(data) else {
        return;
    };
    let (declared, arrived): (Vec<&str>, Vec<&str>) =
        text.lines().partition(|line| line.starts_with('+'));
    let declared: Vec<String> = declared
        .iter()
        .map(|line| line.trim_start_matches('+').to_string())
        .collect();
    let (gate, _refused) = DownlinkGate::new(&declared);

    for entry in gate.entries() {
        let (first, lane) = scope(entry.as_str());
        assert!(
            !first.starts_with('@') && !RESERVED.contains(&first),
            "accepted a reserved root: {entry}"
        );
        assert!(
            !is_wild(first),
            "accepted an entry that starts with a wildcard: {entry}"
        );
        if first == "release" {
            assert!(
                lane.is_some_and(|l| !is_wild(l)),
                "accepted every release lane: {entry}"
            );
        }
        assert!(
            check_entry(entry.as_str()).is_ok(),
            "an accepted entry fails a recheck"
        );
    }

    for pen in arrived {
        let fabric = fabric_key(pen);
        if let Some(fabric) = &fabric {
            assert_eq!(pen_key(fabric), pen, "pen_key does not invert fabric_key");
        }
        // egress-arm.rs and zenoh-egress-arm.rs, on every sample.
        let declared = fabric
            .clone()
            .and_then(|f| OwnedKeyExpr::try_from(f).ok())
            .is_some_and(|f| gate.permits(&f));
        // sapient/src/egress.rs, on every relayed sample.
        assert_eq!(
            sapient::egress::permits_pen_key(&gate, pen),
            declared,
            "the egress arms and the sapient arm disagree on {pen:?}"
        );
        if declared {
            let fabric = fabric.unwrap_or_default();
            let (first, lane) = scope(&fabric);
            assert!(
                gate.entries().iter().any(|e| {
                    let (e_first, e_lane) = scope(e.as_str());
                    e_first == first && (e_first != "release" || e_lane == lane)
                }),
                "{fabric:?} was admitted outside every declared publisher and lane"
            );
        }
    }
});
