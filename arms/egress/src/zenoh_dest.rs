// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Where a zenoh egress arm republishes: a partner's own Zenoh router, dialled in client
//! mode under a certificate THEY issued us, confined to the prefix THEY granted.
//!
//! The mirror of an ingress partner bundle, run in reverse. There, we are the router and a
//! partner dials in with a certificate we issued, confined to a prefix we grant (write-only,
//! default-deny outside it). Here, the partner is the router and we dial in with a
//! certificate they issued, confined to a prefix they grant. Same discipline both ways:
//! client mode only, never a peer; and a declared, literal prefix checked before anything is
//! published, so a bad value cannot publish under some other partner's namespace by mistake.
//!
//! A partner's own router endpoint is deliberately not required to be a literal address:
//! our own remote endpoints are commonly DNS-pinned names (`/etc/hosts`), and
//! `downlink-bridge`'s `fabric_endpoint` already allows that. The prefix is different: it is
//! the one thing standing between "this partner's namespace" and "every other partner's
//! namespace" on their router, so it stays literal and allow-listed.

use std::fmt;

use zenoh::key_expr::OwnedKeyExpr;

/// A destination or allow list the arm refuses to run with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DestError(pub String);

impl fmt::Display for DestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DestError {}

/// The partner router this arm dials, and the prefix it may publish under there.
#[derive(Debug, Clone)]
pub struct ZenohDestination {
    /// `tls/host:port` (the deployed posture) or `tcp/...` (plaintext, local testing only).
    pub endpoint: String,
    /// The granted prefix every republished key is written beneath.
    pub prefix: OwnedKeyExpr,
}

impl ZenohDestination {
    /// Validate the declared endpoint scheme and the granted prefix.
    ///
    /// # Errors
    ///
    /// Fails when the endpoint does not start `tls/` or `tcp/`, the prefix is not a
    /// canonical key expression, the prefix is wildcarded (a partner is granted one literal
    /// root, never `**`), or the prefix is not in the declared allow list.
    pub fn new(endpoint: &str, prefix: &str, allow: &PrefixAllowlist) -> Result<Self, DestError> {
        if !endpoint.starts_with("tls/") && !endpoint.starts_with("tcp/") {
            return Err(DestError(format!(
                "{endpoint:?} must start with tls/ or tcp/"
            )));
        }
        let prefix_ke = OwnedKeyExpr::try_from(prefix.to_string())
            .map_err(|_| DestError(format!("{prefix:?} is not a canonical key expression")))?;
        if prefix_ke.as_str().contains('*') || prefix_ke.as_str().contains('$') {
            return Err(DestError(format!(
                "{prefix:?} must be a literal root, not a wildcard: a partner is granted one fixed namespace"
            )));
        }
        allow.admit(prefix_ke.as_str())?;
        Ok(Self {
            endpoint: endpoint.to_string(),
            prefix: prefix_ke,
        })
    }

    /// The external key a pen-side fabric key is republished on: `<prefix>/<fabric key>`.
    #[must_use]
    pub fn external_key(&self, fabric: &str) -> String {
        format!("{}/{fabric}", self.prefix.as_str())
    }
}

impl fmt::Display for ZenohDestination {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}#{}", self.endpoint, self.prefix.as_str())
    }
}

/// The declared allow list: exact prefixes. Empty allows nothing.
#[derive(Debug, Clone, Default)]
pub struct PrefixAllowlist {
    allowed: Vec<String>,
}

impl PrefixAllowlist {
    /// Parse a comma-separated list of literal prefixes.
    #[must_use]
    pub fn from_csv(spec: &str) -> Self {
        let allowed = spec
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        Self { allowed }
    }

    /// Check the declared prefix before anything is opened.
    ///
    /// # Errors
    ///
    /// Fails, so the arm exits without sending, when the prefix is not listed.
    pub fn admit(&self, prefix: &str) -> Result<(), DestError> {
        if self.allowed.iter().any(|a| a == prefix) {
            Ok(())
        } else {
            Err(DestError(format!(
                "prefix {prefix:?} is not in the declared allow list {:?}",
                self.allowed
            )))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn allow(prefixes: &[&str]) -> PrefixAllowlist {
        PrefixAllowlist::from_csv(&prefixes.join(","))
    }

    #[test]
    fn a_listed_prefix_builds_and_remaps_keys_beneath_it() {
        let dest = ZenohDestination::new(
            "tls/10.20.30.1:7447",
            "raw/goat/radar1",
            &allow(&["raw/goat/radar1"]),
        )
        .unwrap();
        assert_eq!(
            dest.external_key("release/REL-ALL/anchor/radar1/v1"),
            "raw/goat/radar1/release/REL-ALL/anchor/radar1/v1"
        );
    }

    #[test]
    fn a_prefix_missing_from_the_allow_list_is_refused() {
        let why = ZenohDestination::new(
            "tls/10.20.30.1:7447",
            "raw/goat/radar2",
            &allow(&["raw/goat/radar1"]),
        )
        .err()
        .unwrap();
        assert!(why.to_string().contains("not in the declared allow list"));
    }

    #[test]
    fn an_empty_allow_list_refuses_every_prefix() {
        assert!(
            ZenohDestination::new("tls/10.20.30.1:7447", "raw/goat/radar1", &allow(&[])).is_err()
        );
    }

    #[test]
    fn a_wildcard_prefix_is_refused_even_if_listed() {
        let why = ZenohDestination::new(
            "tls/10.20.30.1:7447",
            "raw/goat/**",
            &allow(&["raw/goat/**"]),
        )
        .err()
        .unwrap();
        assert!(why.to_string().contains("literal root"));
    }

    #[test]
    fn an_endpoint_without_a_zenoh_scheme_is_refused() {
        let why = ZenohDestination::new(
            "10.20.30.1:7447",
            "raw/goat/radar1",
            &allow(&["raw/goat/radar1"]),
        )
        .err()
        .unwrap();
        assert!(why.to_string().contains("must start with tls/ or tcp/"));
    }
}
