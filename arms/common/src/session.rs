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
        zenoh::open(config)
            .await
            .map_err(|e| anyhow::anyhow!("zenoh::open failed: {e}"))
            .context("opening zenoh session")
    }
}
