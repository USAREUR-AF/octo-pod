// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The downlink bridge's fabric leg: a Zenoh client session that verifies the router's
//! name, retried until it opens.
//!
//! `octo_arm_common::SessionConfig` is the pen idiom: mutual TLS with name verification
//! off, because the pen router's certificate binds addresses, not the names containers
//! dial. The bridge's fabric leg must verify the router it dials, as the uplink bridge's
//! fabric leg does, so it uses this instead. The open is retried forever: at boot
//! a router routinely comes up after its clients, and a one-shot open turns that ordering
//! into a dead bridge.

use std::time::Duration;

use tracing::{info, warn};

/// How long to wait between failed opens.
pub const OPEN_RETRY: Duration = Duration::from_secs(5);

/// One mTLS client leg.
#[derive(Debug, Clone)]
pub struct Leg {
    /// For logs: "fabric" or "pen".
    pub label: &'static str,
    /// `tls/host:port`.
    pub endpoint: String,
    /// Client certificate (PEM path).
    pub cert: String,
    /// Client private key (PEM path).
    pub key: String,
    /// The CA the router's certificate must chain to (PEM path).
    pub root_ca: String,
    /// Check the router's certificate name against the endpoint host.
    pub verify_name: bool,
}

impl Leg {
    /// Build the Zenoh config. The TLS block is inserted whole: per-key inserts do not
    /// enable the client-certificate send path.
    ///
    /// # Errors
    ///
    /// Fails if Zenoh rejects the configuration.
    pub fn config(&self) -> anyhow::Result<zenoh::Config> {
        let mut config = zenoh::Config::default();
        config
            .insert_json5("mode", "\"client\"")
            .map_err(|e| anyhow::anyhow!("[{}] mode: {e}", self.label))?;
        config
            .insert_json5("connect/endpoints", &format!("[\"{}\"]", self.endpoint))
            .map_err(|e| anyhow::anyhow!("[{}] connect/endpoints: {e}", self.label))?;
        // Pin transport compression off (see octo_arm_common::SessionConfig): defense in
        // depth for lz4_flex 0.10.0 (RUSTSEC-2026-0041), reached only on a compressed link.
        config
            .insert_json5("transport/unicast/compression/enabled", "false")
            .map_err(|e| anyhow::anyhow!("[{}] transport/unicast/compression: {e}", self.label))?;
        config
            .insert_json5("transport/multicast/compression/enabled", "false")
            .map_err(|e| {
                anyhow::anyhow!("[{}] transport/multicast/compression: {e}", self.label)
            })?;
        let tls = format!(
            r#"{{ "root_ca_certificate": "{}", "connect_certificate": "{}", "connect_private_key": "{}", "enable_mtls": true, "verify_name_on_connect": {} }}"#,
            self.root_ca, self.cert, self.key, self.verify_name
        );
        config
            .insert_json5("transport/link/tls", &tls)
            .map_err(|e| anyhow::anyhow!("[{}] transport/link/tls: {e}", self.label))?;
        Ok(config)
    }

    /// Open the session, retrying every [`OPEN_RETRY`] until it opens.
    pub async fn open_forever(&self) -> zenoh::Session {
        loop {
            match self.config() {
                Ok(config) => match zenoh::open(config).await {
                    Ok(s) => {
                        info!(leg = self.label, zid = %s.zid(), endpoint = %self.endpoint, "session open");
                        return s;
                    }
                    Err(e) => warn!(leg = self.label, error = %e, "session open failed; retrying"),
                },
                Err(e) => warn!(leg = self.label, error = %e, "session config failed; retrying"),
            }
            tokio::time::sleep(OPEN_RETRY).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn leg(verify_name: bool) -> Leg {
        Leg {
            label: "fabric",
            endpoint: "tls/router.example.test:7447".into(),
            cert: "/etc/octo-pod/slot-cert.pem".into(),
            key: "/etc/octo-pod/slot-key.pem".into(),
            root_ca: "/etc/octo-pod/fabric-ca.pem".into(),
            verify_name,
        }
    }

    #[test]
    fn the_tls_block_carries_mtls_and_the_name_check_as_declared() {
        for verify in [true, false] {
            let config = leg(verify).config().unwrap();
            let tls: serde_json::Value =
                serde_json::from_str(&config.get_json("transport/link/tls").unwrap()).unwrap();
            assert_eq!(tls["enable_mtls"], true);
            assert_eq!(tls["verify_name_on_connect"], verify);
            assert_eq!(tls["connect_certificate"], "/etc/octo-pod/slot-cert.pem");
            assert_eq!(tls["root_ca_certificate"], "/etc/octo-pod/fabric-ca.pem");
        }
    }

    #[test]
    fn the_leg_is_a_client_of_exactly_its_endpoint() {
        let config = leg(true).config().unwrap();
        assert_eq!(config.get_json("mode").unwrap(), "\"client\"");
        let endpoints: Vec<String> =
            serde_json::from_str(&config.get_json("connect/endpoints").unwrap()).unwrap();
        assert_eq!(endpoints, vec!["tls/router.example.test:7447".to_string()]);
    }

    #[test]
    fn compression_is_pinned_off_on_both_transports() {
        // Also proves the two config key paths are valid: insert_json5 would have failed
        // in config() otherwise. Guards RUSTSEC-2026-0041 (lz4_flex 0.10.0 decompress).
        let config = leg(true).config().unwrap();
        assert_eq!(
            config
                .get_json("transport/unicast/compression/enabled")
                .unwrap(),
            "false"
        );
        assert_eq!(
            config
                .get_json("transport/multicast/compression/enabled")
                .unwrap(),
            "false"
        );
    }
}
