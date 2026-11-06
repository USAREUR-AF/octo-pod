// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
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

    run(Args::parse(), async {
        tokio::signal::ctrl_c().await.context("waiting for ctrl-c")
    })
    .await
}

/// The whole publisher: open the pen session, publish a fresh reading every interval
/// until `shutdown` resolves. `main` passes ctrl-c; a test passes its own trigger.
async fn run(args: Args, shutdown: impl std::future::Future<Output = Result<()>>) -> Result<()> {
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
    tokio::pin!(shutdown);

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
            stopped = &mut shutdown => {
                info!("stopping");
                return stopped;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use octo_arm_common::testing::{next_sample, TestPen, ARRIVES};
    use serde_json::Value;
    use std::fs::File;
    use tokio::sync::oneshot;

    fn args(endpoint: &str, status_file: &std::path::Path, interval: u64) -> Args {
        Args::try_parse_from([
            "health-pub",
            "--key",
            "ops/pod/health/pod-example-01",
            "--status-file",
            status_file.to_str().expect("utf-8 path"),
            "--interval-secs",
            &interval.to_string(),
            "--stale-after-secs",
            "180",
            "--endpoint",
            endpoint,
        ])
        .expect("args")
    }

    fn parse(payload: &str) -> Value {
        serde_json::from_str(payload).unwrap_or_else(|e| panic!("not JSON ({e}): {payload}"))
    }

    fn scratch(name: &str) -> std::path::PathBuf {
        let dir =
            std::env::temp_dir().join(format!("health-pub-test-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("scratch dir");
        dir.join("health.json")
    }

    #[test]
    fn a_fresh_reading_is_published_as_current_with_its_age() {
        let file = scratch("fresh");
        std::fs::write(&file, r#"{"arms":{"udp-ingest":"running"},"repairs":3}"#).expect("write");
        let v = parse(&payload(&args("tcp/127.0.0.1:1", &file, 60)));
        assert_eq!(v["schema"], "octo-pod.health.v0");
        assert_eq!(v["stale"], false);
        assert!(v["reading_age_s"].as_u64().expect("age") <= 5);
        assert_eq!(v["status"]["repairs"], 3);
        assert!(v["ts"].as_u64().expect("ts") > 1_700_000_000);
    }

    #[test]
    fn a_reading_the_writer_stopped_refreshing_is_reported_stale_not_current() {
        let file = scratch("stale");
        std::fs::write(&file, r#"{"ok":true}"#).expect("write");
        let ten_minutes_ago = SystemTime::now() - Duration::from_secs(600);
        File::options()
            .write(true)
            .open(&file)
            .and_then(|f| f.set_modified(ten_minutes_ago))
            .expect("age the file");
        let v = parse(&payload(&args("tcp/127.0.0.1:1", &file, 60)));
        assert_eq!(v["stale"], true);
        assert!(v["reading_age_s"].as_u64().expect("age") >= 600);
        // The old reading still travels, marked for what it is.
        assert_eq!(v["status"]["ok"], true);
    }

    #[test]
    fn a_missing_status_file_publishes_unavailable_not_nothing() {
        let file = scratch("missing").with_file_name("never-written.json");
        let v = parse(&payload(&args("tcp/127.0.0.1:1", &file, 60)));
        assert_eq!(v["stale"], true);
        assert_eq!(v["status"], Value::Null);
        let why = v["unavailable"].as_str().expect("reason");
        assert!(!why.is_empty() && !why.contains('"'), "{why}");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn each_publish_reads_the_file_fresh_and_stopping_is_clean() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("ops/pod/health/**").await;
        let file = scratch("live");
        std::fs::write(&file, r#"{"reading":1}"#).expect("write");
        // A one-second interval has a zero offset, so the first publish is immediate.
        let (stop, stopped) = oneshot::channel::<()>();
        let task = tokio::spawn(run(args(&pen.endpoint, &file, 1), async move {
            let _ = stopped.await;
            Ok(())
        }));

        let first = next_sample(&sub, ARRIVES).await.expect("first reading");
        assert_eq!(first.key_expr().as_str(), "ops/pod/health/pod-example-01");
        let body = String::from_utf8(first.payload().to_bytes().to_vec()).expect("utf-8");
        assert_eq!(parse(&body)["status"]["reading"], 1);

        std::fs::write(&file, r#"{"reading":2}"#).expect("rewrite");
        let deadline = tokio::time::Instant::now() + ARRIVES;
        loop {
            let s = next_sample(&sub, ARRIVES).await.expect("next reading");
            let body = String::from_utf8(s.payload().to_bytes().to_vec()).expect("utf-8");
            if parse(&body)["status"]["reading"] == 2 {
                break;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "never re-read the file"
            );
        }

        stop.send(()).expect("stop");
        task.await.expect("join").expect("clean shutdown");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn no_pen_fails_startup_loudly() {
        let file = scratch("nopen");
        let closed = format!(
            "tcp/127.0.0.1:{}",
            octo_arm_common::testing::free_tcp_port()
        );
        let err = run(args(&closed, &file, 60), async { Ok(()) })
            .await
            .expect_err("no pen");
        assert!(
            format!("{err:#}").contains("opening the pen session"),
            "{err:#}"
        );
    }

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
