//! health-pub — publishes this pod's health onto the fabric, so a pod's state
//! is knowable without reaching it.
//!
//! WHY THIS IS A DUMB PIPE. Computing a pod's health means asking docker what is
//! running, which means the docker socket, which means a container that can
//! trivially escape to root on the host. That is far too much privilege for a
//! thing whose job is to report. So the privileged half stays on the host, where
//! it already lives: a systemd timer runs `octo-pod status --json` and writes a
//! file. This process reads that file and publishes it. It holds no socket, no
//! docker access and no ability to change anything.
//!
//! WHY IT IS A PUBLISHER LIKE ANY OTHER. It gets a certificate from the pen CA
//! and an ACL grant from the same declaration that mints them for an arm. There
//! is nothing new in the trust model: it writes one key, and the router enforces
//! that the same way it enforces an arm's namespace.
//!
//! WHAT IT DELIBERATELY DOES NOT DO. It never buffers, never retries a failed
//! publish, and never replays. Counters in the payload are cumulative since
//! boot, so a single fresh message conveys everything that happened during an
//! outage — `repairs: 412` after a week of silence says what 412 buffered
//! messages would have said. Nothing to replay means no queue to bound and no
//! stale state to mistake for current.
//!
//! License: Apache-2.0

use std::path::PathBuf;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use clap::Parser;
use octo_arm_common::SessionConfig;
use tracing::{error, info, warn};

#[derive(Parser, Debug, Clone)]
#[command(
    name = "health-pub",
    about = "octo-pod: publish pod health onto the fabric"
)]
struct Args {
    /// The key this pod publishes on. One key per pod, never one per metric:
    /// keyexpr cardinality is an O(N) cost on every broad read, and a key per
    /// field multiplies the fleet's key count by the number of fields.
    #[arg(long, env = "HEALTH_KEY")]
    key: String,

    /// Written by the host-side timer. Read fresh on every publish.
    #[arg(
        long,
        env = "HEALTH_STATUS_FILE",
        default_value = "/var/lib/octo-pod/health.json"
    )]
    status_file: PathBuf,

    /// Base publish interval.
    #[arg(long, env = "HEALTH_INTERVAL_SECS", default_value_t = 60)]
    interval_secs: u64,

    /// A reading older than this is reported as stale rather than as current.
    /// The publisher does not know why the writer stopped; it only knows the
    /// file has not moved, and says so instead of implying currency.
    #[arg(long, env = "HEALTH_STALE_AFTER_SECS", default_value_t = 180)]
    stale_after_secs: u64,

    #[arg(long, env = "INGEST_ENDPOINT")]
    endpoint: String,
    #[arg(long, env = "INGEST_TLS_CERT")]
    cert: Option<String>,
    #[arg(long, env = "INGEST_TLS_KEY")]
    key_file: Option<String>,
    #[arg(long, env = "INGEST_TLS_CA")]
    root_ca: Option<String>,
}

/// A stable per-pod offset into the publish interval, derived from the key.
///
/// WITHOUT THIS THE FLEET SYNCHRONISES. Every pod on a fixed cadence publishes
/// on the same second, which is a periodic spike rather than a smooth load — and
/// it is far worse in the case that matters: when a partition heals, every pod
/// reconnects and publishes at once, precisely when the fabric can least absorb
/// it. Derived from the key rather than randomised so it is stable across
/// restarts, and needs no coordination with anyone.
fn jitter_offset(key: &str, interval: u64) -> u64 {
    if interval == 0 {
        return 0;
    }
    // FNV-1a. Not cryptographic, and does not need to be: the only property
    // required is that different pods land on different offsets.
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in key.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h % interval
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// The payload, read fresh each time. Never cached: a cached reading republished
/// after the writer has died is a false green, which is the one failure this
/// whole surface exists to prevent.
fn payload(args: &Args) -> String {
    let ts = now_secs();
    match std::fs::read_to_string(&args.status_file) {
        Ok(body) => {
            let age = std::fs::metadata(&args.status_file)
                .and_then(|m| m.modified())
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|d| ts.saturating_sub(d.as_secs()))
                .unwrap_or(u64::MAX);

            // HONEST-UNKNOWN BEATS FALSE-GREEN. A stale file means the writer
            // stopped; the pod may be perfectly healthy or may be failing in a
            // way that also stopped the writer. Reporting the last known state
            // as current cannot distinguish those, so it is reported as stale
            // and the reading travels with its age.
            let stale = age > args.stale_after_secs;
            format!(
                r#"{{"schema":"octo-pod.health.v0","ts":{ts},"reading_age_s":{age},"stale":{stale},"status":{body}}}"#
            )
        }
        Err(e) => {
            warn!(error = %e, path = %args.status_file.display(),
                  "no status file — publishing unavailable rather than nothing");
            // Publishing *something* matters: silence is indistinguishable from
            // the pod being gone, and the reader must be able to tell "this pod
            // cannot describe itself" from "this pod is not there".
            format!(
                r#"{{"schema":"octo-pod.health.v0","ts":{ts},"stale":true,"status":null,"unavailable":"{}"}}"#,
                e.to_string().replace('"', "'")
            )
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let args = Args::parse();
    let cfg = SessionConfig {
        endpoint: args.endpoint.clone(),
        cert: args.cert.clone(),
        key: args.key_file.clone(),
        root_ca: args.root_ca.clone(),
    };

    let session = cfg.open().await.context("opening the pen session")?;
    let publisher = session
        .declare_publisher(args.key.clone())
        .await
        .map_err(|e| anyhow::anyhow!("declaring publisher on {}: {e}", args.key))?;

    let offset = jitter_offset(&args.key, args.interval_secs);
    info!(
        key = %args.key,
        interval_s = args.interval_secs,
        jitter_s = offset,
        status_file = %args.status_file.display(),
        "health publisher up"
    );

    // Spread the first publish across the interval, so a fleet restarting
    // together does not all report on the same second.
    tokio::time::sleep(Duration::from_secs(offset)).await;

    let mut ticker = tokio::time::interval(Duration::from_secs(args.interval_secs.max(1)));
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

    loop {
        tokio::select! {
            _ = ticker.tick() => {
                // A failed publish is dropped, not retried and not queued. The
                // next tick carries cumulative counters, so nothing is lost that
                // a retry would have recovered.
                if let Err(e) = publisher.put(payload(&args)).await {
                    error!(error = %e, "publish failed — dropped, next tick carries the same counters");
                }
            }
            _ = tokio::signal::ctrl_c() => {
                info!("stopping");
                return Ok(());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::jitter_offset;

    #[test]
    fn offset_is_within_the_interval() {
        for key in [
            "ops/pod/health/a",
            "ops/pod/health/b",
            "ops/pod/health/pod-example-01",
        ] {
            assert!(
                jitter_offset(key, 60) < 60,
                "{key} landed outside the interval"
            );
        }
    }

    #[test]
    fn offset_is_stable_for_a_key() {
        // Stability is the point: a pod that restarts must not move to a new
        // slot, or a restarting fleet re-synchronises on the new offsets.
        let a = jitter_offset("ops/pod/health/pod-example-01", 60);
        let b = jitter_offset("ops/pod/health/pod-example-01", 60);
        assert_eq!(a, b);
    }

    #[test]
    fn offsets_spread_across_the_interval() {
        // The failure this guards is every pod landing on the same second. With
        // 200 pods over a 60s interval, a hash that spreads at all should touch
        // most slots; one that does not would collapse to a handful.
        let mut seen = std::collections::HashSet::new();
        for i in 0..200 {
            seen.insert(jitter_offset(&format!("ops/pod/health/pod-{i:03}"), 60));
        }
        assert!(
            seen.len() > 40,
            "only {} distinct offsets from 200 pods",
            seen.len()
        );
    }

    #[test]
    fn zero_interval_does_not_divide_by_zero() {
        assert_eq!(jitter_offset("ops/pod/health/x", 0), 0);
    }
}
