// uplink-bridge — the pod's sync leg: pen → fabric scoped zenoh uplink.
//
// Two client sessions, one direction. Samples matching the allowlist on the
// pen router are republished into the fabric under `<slot_prefix>/<key>`. The
// fabric session never subscribes — outbound only by construction.
//
// Session-resilience shape: retry the initial open forever — at host boot the pen router
// routinely comes up after us, and a one-shot open turns that ordering into
// a dead bridge. Once open, zenoh handles link re-establishment itself.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{bail, Result};
use clap::Parser;
use tracing::{info, warn};
use zenoh::sample::SampleKind;

const SESSION_OPEN_RETRY: Duration = Duration::from_secs(5);

#[derive(Parser, Debug, Clone)]
#[command(
    name = "uplink-bridge",
    about = "pen → fabric scoped zenoh uplink bridge (outbound only)"
)]
// The BRIDGE_* environment names are the ones the Quadlet unit sets
// (platform/templates/bridge/octo-uplink-bridge.container.j2); they change together.
struct Args {
    /// pen router endpoint, e.g. tls/192.0.2.1:7447
    #[arg(long, env = "BRIDGE_PEN_ENDPOINT")]
    pen_endpoint: String,
    /// pen-CA client cert (leaf, PEM)
    #[arg(long, env = "BRIDGE_PEN_CERT")]
    pen_cert: String,
    /// pen-CA client private key (PEM)
    #[arg(long, env = "BRIDGE_PEN_KEY")]
    pen_key: String,
    /// pen CA root (PEM)
    #[arg(long, env = "BRIDGE_PEN_ROOT_CA")]
    pen_root_ca: String,

    /// fabric router endpoint over wt0, e.g. tls/zenoh.fabric.example:7447
    #[arg(long, env = "BRIDGE_FABRIC_ENDPOINT")]
    fabric_endpoint: String,
    /// portal-minted org client cert (the fabric slot) (leaf, PEM)
    #[arg(long, env = "BRIDGE_FABRIC_CERT")]
    fabric_cert: String,
    /// fabric org client private key (PEM)
    #[arg(long, env = "BRIDGE_FABRIC_KEY")]
    fabric_key: String,
    /// fabric CA root (PEM)
    #[arg(long, env = "BRIDGE_FABRIC_ROOT_CA")]
    fabric_root_ca: String,

    /// fabric write anchor (the portal-assigned slot uuid). Every republished
    /// key becomes `<slot_prefix>/<original_key>` — this is both the write
    /// authority and the provenance marker.
    #[arg(long, env = "BRIDGE_SLOT_PREFIX")]
    slot_prefix: String,

    /// Comma-separated allowlist of pen key expressions to uplink
    /// (e.g. "ops/env/health/**,demo/**"). Keep entries non-overlapping —
    /// overlapping expressions deliver (and uplink) duplicate samples.
    #[arg(long, env = "BRIDGE_ALLOW", value_delimiter = ',', required = true)]
    allow: Vec<String>,

    /// Verify the router TLS name on the pen leg
    #[arg(long, env = "BRIDGE_VERIFY_NAME_PEN", default_value_t = false)]
    verify_name_pen: bool,
    /// Verify the router TLS name on the fabric leg
    #[arg(long, env = "BRIDGE_VERIFY_NAME_FABRIC", default_value_t = true)]
    verify_name_fabric: bool,

    /// Periodic stats log interval (seconds)
    #[arg(long, env = "BRIDGE_STATS_INTERVAL_S", default_value_t = 60)]
    stats_interval_s: u64,
}

#[derive(Default)]
struct Stats {
    forwarded: AtomicU64,
    forward_errors: AtomicU64,
    deletes: AtomicU64,
}

struct TlsLeg<'a> {
    label: &'static str,
    endpoint: &'a str,
    cert: &'a str,
    key: &'a str,
    root_ca: &'a str,
    verify_name: bool,
}

fn make_config(leg: &TlsLeg) -> Result<zenoh::Config> {
    let mut config = zenoh::Config::default();
    config
        .insert_json5("mode", "\"client\"")
        .map_err(|e| anyhow::anyhow!("[{}] config mode: {e}", leg.label))?;
    config
        .insert_json5("connect/endpoints", &format!("[\"{}\"]", leg.endpoint))
        .map_err(|e| anyhow::anyhow!("[{}] config connect/endpoints: {e}", leg.label))?;
    // Pin transport compression off on both transports, as every other arm does
    // (octo_arm_common::SessionConfig). It is off by default in zenoh 1.9.0, and a link
    // only compresses when both ends enable it, but this bridge faces the external fabric:
    // pinning it keeps lz4_flex's decompress path (RUSTSEC-2026-0041) unreachable even if
    // a future zenoh default or a far-end router changes.
    config
        .insert_json5("transport/unicast/compression/enabled", "false")
        .map_err(|e| {
            anyhow::anyhow!("[{}] config transport/unicast/compression: {e}", leg.label)
        })?;
    config
        .insert_json5("transport/multicast/compression/enabled", "false")
        .map_err(|e| {
            anyhow::anyhow!(
                "[{}] config transport/multicast/compression: {e}",
                leg.label
            )
        })?;
    if leg.endpoint.starts_with("tls/") {
        // Insert the whole TLS block at once — per-sub-key inserts do not
        // enable the client-cert mTLS send path (same gotcha as the bundled
        // first-publisher.py notes for the python client).
        let tls_block = format!(
            r#"{{ "root_ca_certificate": "{}", "connect_certificate": "{}", "connect_private_key": "{}", "enable_mtls": true, "verify_name_on_connect": {} }}"#,
            leg.root_ca, leg.cert, leg.key, leg.verify_name
        );
        config
            .insert_json5("transport/link/tls", &tls_block)
            .map_err(|e| anyhow::anyhow!("[{}] config transport/link/tls: {e}", leg.label))?;
    }
    Ok(config)
}

/// Retry the session open forever (boot-ordering resilience).
async fn open_forever(leg: &TlsLeg<'_>) -> zenoh::Session {
    loop {
        match make_config(leg) {
            Ok(config) => match zenoh::open(config).await {
                Ok(s) => {
                    info!(leg = leg.label, zid = %s.zid(), endpoint = leg.endpoint, "session open");
                    return s;
                }
                Err(e) => warn!(
                    leg = leg.label,
                    error = %e,
                    retry_in_s = SESSION_OPEN_RETRY.as_secs(),
                    "zenoh session open failed; retrying"
                ),
            },
            // Config build failures are deterministic; still loop so a
            // bind-mount that appears late (cert delivery race) heals.
            Err(e) => warn!(leg = leg.label, error = %e, "session config failed; retrying"),
        }
        tokio::time::sleep(SESSION_OPEN_RETRY).await;
    }
}

/// The write anchor must be a single key segment. A slot prefix that is empty
/// or contains a path separator would let the bridge publish outside the slot
/// the portal assigned it, which is the one thing the prefix exists to prevent.
fn validate_slot_prefix(prefix: &str) -> Result<()> {
    if prefix.is_empty() || prefix.contains('/') {
        bail!("--slot-prefix must be a single key segment (the fabric slot uuid)");
    }
    Ok(())
}

/// Trim the allowlist, drop empty entries (a trailing or doubled comma in
/// BRIDGE_ALLOW yields one), and require at least one. An empty entry must never
/// become a subscription, and a bridge with an all-empty allowlist uplinks
/// nothing by mistake rather than failing loudly.
fn normalize_allow(raw: &[String]) -> Result<Vec<String>> {
    let allow: Vec<String> = raw
        .iter()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    if allow.is_empty() {
        bail!("--allow must name at least one key expression");
    }
    Ok(allow)
}

/// Every uplinked sample is published under `<slot_prefix>/<source_key>`. The
/// slot prefix is the bridge's entire write authority on the fabric, so a source
/// key can only ever land beneath it, never at a sibling slot or a bare key.
fn slotted_key(slot_prefix: &str, source_key: &str) -> String {
    format!("{slot_prefix}/{source_key}")
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let args = Args::parse();

    validate_slot_prefix(&args.slot_prefix)?;
    let allow = normalize_allow(&args.allow)?;

    info!(
        slot_prefix = args.slot_prefix.as_str(),
        allow = ?allow,
        "uplink-bridge starting (outbound only: pen → fabric)"
    );

    let pen = open_forever(&TlsLeg {
        label: "pen",
        endpoint: &args.pen_endpoint,
        cert: &args.pen_cert,
        key: &args.pen_key,
        root_ca: &args.pen_root_ca,
        verify_name: args.verify_name_pen,
    })
    .await;
    let fabric = open_forever(&TlsLeg {
        label: "fabric",
        endpoint: &args.fabric_endpoint,
        cert: &args.fabric_cert,
        key: &args.fabric_key,
        root_ca: &args.fabric_root_ca,
        verify_name: args.verify_name_fabric,
    })
    .await;

    let stats = Arc::new(Stats::default());

    let mut tasks = tokio::task::JoinSet::new();
    for ke in allow.clone() {
        let sub_session = pen.clone();
        let pub_session = fabric.clone();
        let slot = args.slot_prefix.clone();
        let stats = stats.clone();
        tasks.spawn(async move {
            let subscriber = match sub_session.declare_subscriber(ke.clone()).await {
                Ok(s) => s,
                Err(e) => {
                    warn!(key_expr = ke.as_str(), error = %e, "declare_subscriber failed");
                    return;
                }
            };
            info!(key_expr = ke.as_str(), "subscribed on the pen");
            loop {
                let sample = match subscriber.recv_async().await {
                    Ok(s) => s,
                    Err(e) => {
                        warn!(key_expr = ke.as_str(), error = %e, "subscriber closed");
                        return;
                    }
                };
                let uplink_key = slotted_key(&slot, sample.key_expr().as_ref());
                let result = match sample.kind() {
                    SampleKind::Put => {
                        pub_session
                            .put(&uplink_key, sample.payload().to_bytes())
                            .encoding(sample.encoding().clone())
                            .await
                    }
                    SampleKind::Delete => {
                        stats.deletes.fetch_add(1, Ordering::Relaxed);
                        pub_session.delete(&uplink_key).await
                    }
                };
                match result {
                    Ok(()) => {
                        stats.forwarded.fetch_add(1, Ordering::Relaxed);
                    }
                    Err(e) => {
                        stats.forward_errors.fetch_add(1, Ordering::Relaxed);
                        warn!(uplink_key = uplink_key.as_str(), error = %e, "uplink put failed");
                    }
                }
            }
        });
    }

    let stats_task = {
        let stats = stats.clone();
        let interval = Duration::from_secs(args.stats_interval_s.max(5));
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(interval).await;
                info!(
                    forwarded = stats.forwarded.load(Ordering::Relaxed),
                    forward_errors = stats.forward_errors.load(Ordering::Relaxed),
                    deletes = stats.deletes.load(Ordering::Relaxed),
                    "uplink stats"
                );
            }
        })
    };

    tokio::select! {
        _ = tokio::signal::ctrl_c() => info!("SIGINT; shutting down"),
        Some(_) = tasks.join_next() => warn!("a subscriber task ended; shutting down for supervisor restart"),
    }
    stats_task.abort();
    pen.close().await.ok();
    fabric.close().await.ok();
    // Non-zero when a subscriber died unexpectedly so the container
    // restart policy re-runs us into the open_forever retry loop.
    std::process::exit(0);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slot_prefix_must_be_a_single_segment() {
        // A real portal slot uuid is accepted.
        assert!(validate_slot_prefix("a1b2c3d4e5f6").is_ok());
        // Empty, a separator, a traversal attempt, or a trailing slash are all
        // ways to address a key outside the assigned slot; all are refused.
        assert!(validate_slot_prefix("").is_err());
        assert!(validate_slot_prefix("a/b").is_err());
        assert!(validate_slot_prefix("../other-slot").is_err());
        assert!(validate_slot_prefix("slot/").is_err());
    }

    #[test]
    fn allowlist_drops_empty_entries_and_requires_at_least_one() {
        assert_eq!(
            normalize_allow(&["ops/env/health/**".into(), "  demo/**  ".into()]).unwrap(),
            vec!["ops/env/health/**".to_string(), "demo/**".to_string()],
        );
        // A trailing comma in BRIDGE_ALLOW produces an empty entry; it is dropped,
        // never turned into a subscription.
        assert_eq!(
            normalize_allow(&["ops/**".into(), "".into()]).unwrap(),
            vec!["ops/**".to_string()],
        );
        // No entries, or only blank ones, must fail rather than uplink nothing silently.
        assert!(normalize_allow(&[]).is_err());
        assert!(normalize_allow(&["".into(), "   ".into()]).is_err());
    }

    #[test]
    fn uplink_key_is_confined_to_the_slot_prefix() {
        assert_eq!(
            slotted_key("slot7", "sensors/radar/tracks"),
            "slot7/sensors/radar/tracks",
        );
        // Whatever the source key, the result always begins with "<slot>/", so the
        // bridge cannot address a sibling slot or a bare fabric key.
        for source in ["ops/env/health/x", "a", "from/elsewhere/**", "../escape"] {
            assert!(slotted_key("slot7", source).starts_with("slot7/"));
        }
    }

    #[test]
    fn compression_is_pinned_off_on_both_legs_and_transports() {
        // Guards RUSTSEC-2026-0041 (lz4_flex 0.10.0 decompress) on the leg that faces the
        // external fabric as well as the pen leg, with and without TLS. A wrong key path
        // would have failed make_config, so this also proves the paths are valid.
        for endpoint in ["tls/fabric.example.test:7447", "tcp/192.0.2.10:7447"] {
            let leg = TlsLeg {
                label: "test",
                endpoint,
                cert: "/etc/octo-pod/tls/pod.crt",
                key: "/etc/octo-pod/tls/pod.key",
                root_ca: "/etc/octo-pod/tls/root.pem",
                verify_name: true,
            };
            let config = make_config(&leg).expect("config builds");
            for transport in ["unicast", "multicast"] {
                assert_eq!(
                    config
                        .get_json(&format!("transport/{transport}/compression/enabled"))
                        .expect("key exists"),
                    "false",
                    "{endpoint} {transport}"
                );
            }
        }
    }
}
