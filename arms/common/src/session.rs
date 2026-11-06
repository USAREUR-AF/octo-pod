// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Zenoh client-session setup — the one home for the mTLS idiom.
//!
//! The same session idiom as GOAT's bridges and audit subscriber: `mode=client`, TLS selected by the
//! `tls/` endpoint scheme, `enable_mtls` so the client presents its leaf,
//! hostname verification off (router certs SAN-bind host IPs, not the
//! names containers dial through).

use anyhow::{Context, Result};

/// Where and as whom an arm connects.
#[derive(Debug, Clone)]
pub struct SessionConfig {
    /// `tls/<host>:<port>` for mTLS (the deployed posture); `tcp/...` is
    /// plaintext for local testing only.
    pub endpoint: String,
    /// PEM paths; required when the endpoint scheme is `tls/`. The leaf's
    /// CN / URI-SAN is the identity the router ACL scopes on.
    pub cert: Option<String>,
    pub key: Option<String>,
    pub root_ca: Option<String>,
}

impl SessionConfig {
    /// Open the session. Fails loudly (never silently) when TLS material
    /// is missing for a `tls/` endpoint.
    pub async fn open(&self) -> Result<zenoh::Session> {
        let config = self.config()?;
        zenoh::open(config)
            .await
            .map_err(|e| anyhow::anyhow!("zenoh::open failed: {e}"))
            .context("opening zenoh session")
    }

    /// The Zenoh configuration [`open`](Self::open) uses. Fails when a `tls/` endpoint
    /// is missing any of its three PEM paths, rather than falling back to plaintext.
    pub fn config(&self) -> Result<zenoh::Config> {
        let mut config = zenoh::Config::default();
        config
            .insert_json5("mode", "\"client\"")
            .map_err(|e| anyhow::anyhow!("config mode: {e}"))?;
        config
            .insert_json5("connect/endpoints", &format!("[\"{}\"]", self.endpoint))
            .map_err(|e| anyhow::anyhow!("config connect/endpoints: {e}"))?;
        // Pin transport compression off on both unicast and multicast. It is off by
        // default, so this changes nothing today; it is defense in depth for lz4_flex
        // 0.10.0 (RUSTSEC-2026-0041), whose decompress path Zenoh reaches only when a
        // link negotiates compression. Pinning it here means a later default change
        // cannot silently open that path.
        config
            .insert_json5("transport/unicast/compression/enabled", "false")
            .map_err(|e| anyhow::anyhow!("config transport/unicast/compression: {e}"))?;
        config
            .insert_json5("transport/multicast/compression/enabled", "false")
            .map_err(|e| anyhow::anyhow!("config transport/multicast/compression: {e}"))?;
        if self.endpoint.starts_with("tls/") {
            let cert = self
                .cert
                .as_ref()
                .context("cert required for tls/ endpoint")?;
            let key = self
                .key
                .as_ref()
                .context("key required for tls/ endpoint")?;
            let root_ca = self
                .root_ca
                .as_ref()
                .context("root CA required for tls/ endpoint")?;
            let tls_block = format!(
                r#"{{ "root_ca_certificate": "{root_ca}", "connect_certificate": "{cert}", "connect_private_key": "{key}", "enable_mtls": true, "verify_name_on_connect": false }}"#
            );
            config
                .insert_json5("transport/link/tls", &tls_block)
                .map_err(|e| anyhow::anyhow!("config transport/link/tls: {e}"))?;
        }
        Ok(config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::TestPen;

    fn tls(cert: Option<&str>, key: Option<&str>, root_ca: Option<&str>) -> SessionConfig {
        SessionConfig {
            endpoint: "tls/192.0.2.10:7447".into(),
            cert: cert.map(Into::into),
            key: key.map(Into::into),
            root_ca: root_ca.map(Into::into),
        }
    }

    fn json(config: &zenoh::Config, key: &str) -> serde_json::Value {
        serde_json::from_str(&config.get_json(key).expect(key)).expect(key)
    }

    #[test]
    fn a_tls_endpoint_missing_any_pem_path_is_refused_not_downgraded() {
        let all = (Some("/c.pem"), Some("/k.pem"), Some("/ca.pem"));
        for (cert, key, root_ca, missing) in [
            (None, all.1, all.2, "cert required"),
            (all.0, None, all.2, "key required"),
            (all.0, all.1, None, "root CA required"),
        ] {
            let err = tls(cert, key, root_ca).config().unwrap_err().to_string();
            assert!(err.contains(missing), "{missing}: got {err}");
        }
    }

    #[test]
    fn a_tls_endpoint_is_mutual_tls_with_the_declared_material() {
        let config = tls(Some("/c.pem"), Some("/k.pem"), Some("/ca.pem"))
            .config()
            .unwrap();
        let block = json(&config, "transport/link/tls");
        assert_eq!(block["enable_mtls"], true);
        assert_eq!(block["connect_certificate"], "/c.pem");
        assert_eq!(block["connect_private_key"], "/k.pem");
        assert_eq!(block["root_ca_certificate"], "/ca.pem");
        // The pen router's certificate binds addresses, not the names containers dial.
        assert_eq!(block["verify_name_on_connect"], false);
        assert_eq!(json(&config, "mode"), "client");
        assert_eq!(
            json(&config, "connect/endpoints"),
            serde_json::json!(["tls/192.0.2.10:7447"])
        );
    }

    #[test]
    fn compression_is_pinned_off_for_plaintext_and_tls_alike() {
        let plain = SessionConfig {
            endpoint: "tcp/127.0.0.1:7447".into(),
            cert: None,
            key: None,
            root_ca: None,
        };
        for config in [
            plain.config().unwrap(),
            tls(Some("/c.pem"), Some("/k.pem"), Some("/ca.pem"))
                .config()
                .unwrap(),
        ] {
            for key in [
                "transport/unicast/compression/enabled",
                "transport/multicast/compression/enabled",
            ] {
                assert_eq!(json(&config, key), false, "{key}");
            }
        }
    }

    #[test]
    fn a_plaintext_endpoint_needs_no_pem_material_and_carries_no_tls_block() {
        let plain = SessionConfig {
            endpoint: "tcp/127.0.0.1:7447".into(),
            cert: None,
            key: None,
            root_ca: None,
        };
        let config = plain.config().unwrap();
        assert_ne!(json(&config, "transport/link/tls")["enable_mtls"], true);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn open_reaches_a_listening_pen() {
        let pen = TestPen::start().await;
        let session = pen.client().open().await.expect("open");
        assert!(crate::outbox::pen_reachable(&session).await);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn open_fails_loudly_when_nothing_listens() {
        let closed = SessionConfig {
            endpoint: format!("tcp/127.0.0.1:{}", crate::testing::free_tcp_port()),
            cert: None,
            key: None,
            root_ca: None,
        };
        let err = tokio::time::timeout(std::time::Duration::from_secs(30), closed.open())
            .await
            .expect("open must fail, not hang")
            .expect_err("nothing listens");
        assert!(
            format!("{err:#}").contains("opening zenoh session"),
            "{err:#}"
        );
    }
}
