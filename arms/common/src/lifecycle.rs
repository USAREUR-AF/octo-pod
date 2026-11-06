// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Lifecycle records: what a writing arm publishes on `<label>-session/<peer-ip>`.
//!
//! One flat JSON object per transition. Every writing arm carries `event`, `label`,
//! `peer`, `endpoint` and `detail`, so one consumer reads any arm's lifecycle without
//! knowing which arm wrote it. An arm with more to say about a session builds its own
//! object with these five first.
//!
//! The escaping lives here because it is identical everywhere, and it is the part that is
//! wrong in quiet ways when it is written more than once: a detail carrying an OS error
//! message with a quote in it produces a record no consumer can parse.

use std::net::{IpAddr, Ipv4Addr, SocketAddr};

/// The key chunk for a record from before a socket exists. `0.0.0.0` is not a peer that
/// could ever answer, so `<label>-session/0-0-0-0` reads unambiguously as "no peer yet" —
/// and a failure to reach a peer is recorded somewhere rather than nowhere.
pub const NO_PEER: SocketAddr = SocketAddr::new(IpAddr::V4(Ipv4Addr::UNSPECIFIED), 0);

/// Escape a string for a JSON string literal.
pub fn json_esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

/// One lifecycle record with the five shared fields. `peer` is `None` before a socket
/// exists and renders as `-`.
pub fn record(
    event: &str,
    label: &str,
    peer: Option<SocketAddr>,
    endpoint: &str,
    detail: &str,
) -> String {
    let peer_text = match peer {
        Some(p) => p.to_string(),
        None => "-".to_string(),
    };
    format!(
        r#"{{"event":"{}","label":"{}","peer":"{}","endpoint":"{}","detail":"{}"}}"#,
        json_esc(event),
        json_esc(label),
        json_esc(&peer_text),
        json_esc(endpoint),
        json_esc(detail),
    )
}

/// Whether the n-th occurrence of a repeating fault deserves a record or a log line: the
/// first, then every hundredth. A peer or translator failing at rate must not turn the pen
/// or the log into a copy of its own output, and the count in each report says how many
/// were not reported individually.
pub fn worth_reporting(count: u64) -> bool {
    count == 1 || count.is_multiple_of(100)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escaping_survives_quotes_backslashes_and_control_characters() {
        assert_eq!(json_esc("a\"b\\c\nd\te"), "a\\\"b\\\\c\\nd\\te");
        assert_eq!(json_esc("\u{1}"), "\\u0001");
    }

    #[test]
    fn a_record_carries_the_five_shared_fields_in_one_flat_object() {
        let peer: SocketAddr = "10.20.0.50:48062".parse().unwrap();
        let json = record("connected", "tcp", Some(peer), "10.20.0.50:48062", "ok");
        assert_eq!(
            json,
            r#"{"event":"connected","label":"tcp","peer":"10.20.0.50:48062","endpoint":"10.20.0.50:48062","detail":"ok"}"#
        );
    }

    /// A dial that never reached anyone still produces a record, keyed where an operator
    /// looks for it.
    #[test]
    fn a_record_from_before_a_socket_has_no_peer() {
        let json = record("dial-failed", "tcp", None, "10.20.0.50:48062", "refused");
        assert!(json.contains(r#""peer":"-""#), "{json}");
    }

    #[test]
    fn a_detail_with_a_quote_still_yields_one_parseable_object() {
        let json = record("closed", "tcp", None, "x", r#"os error "104""#);
        assert!(json.contains(r#"os error \"104\""#), "{json}");
        assert_eq!(json.matches('{').count(), 1, "{json}");
    }

    #[test]
    fn repeating_faults_are_reported_first_then_sparingly() {
        let reported: Vec<u64> = (1..=250).filter(|n| worth_reporting(*n)).collect();
        assert_eq!(reported, vec![1, 100, 200]);
    }

    #[test]
    fn the_sentinel_keys_as_zeros() {
        assert_eq!(crate::source::source_chunk(&NO_PEER), "0-0-0-0");
    }
}
