// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! downlink-bridge: the uplink bridge, run in reverse.
//!
//! The uplink bridge (`uplink-bridge`) subscribes on the pen to the namespaces in
//! `bridge_allow` and republishes them upstream under the pod's slot anchor; its fabric
//! session never subscribes. This bridge subscribes upstream, as the pod's slot identity,
//! ONLY to the keys in `downlink_allow`, and republishes them on the pen under
//! `downlink/<key>`; its fabric session never publishes and its pen session never
//! subscribes.
//!
//! Three gates stand between a fabric key and the pen, and this bridge is the middle one:
//! the fabric router's read grant for the slot identity, then `downlink_allow` (checked
//! when the bridge starts and again on every sample), then the pen ACL that decides which
//! egress arm may read each downlink key.
//!
//! Failure behavior:
//! - a `downlink_allow` entry that is too broad, reserved, or not a key expression: logged
//!   as an error and never subscribed; the accepted entries still run;
//! - nothing released: opens no fabric session at all and idles until stopped, exit 0;
//! - a router that is not up yet: both sessions retry every 5 s until they open;
//! - a sample whose key falls outside the gate: dropped and counted (`dropped_gate`);
//! - a pen put that fails: counted (`forward_errors`), not retried, so nothing arrives late;
//! - an upstream subscription that ends: the bridge exits non-zero so its supervisor
//!   restarts it into a clean set of subscriptions.

use std::process::ExitCode;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use clap::Parser;
use octo_egress::gate::{pen_key, DownlinkGate};
use octo_egress::session::Leg;
use tracing::{error, info, warn};
use zenoh::key_expr::OwnedKeyExpr;
use zenoh::sample::SampleKind;

#[derive(Parser, Debug)]
#[command(
    name = "downlink-bridge",
    about = "Bring released fabric keys down onto the pod-local router, gated by downlink_allow"
)]
struct Args {
    /// Fabric router, e.g. tls/router:7447.
    #[arg(long, env = "DOWNLINK_FABRIC_ENDPOINT")]
    fabric_endpoint: String,
    /// The pod's slot certificate on the fabric (PEM).
    #[arg(long, env = "DOWNLINK_FABRIC_CERT")]
    fabric_cert: String,
    /// Its private key (PEM).
    #[arg(long, env = "DOWNLINK_FABRIC_KEY")]
    fabric_key: String,
    /// The fabric CA (PEM).
    #[arg(long, env = "DOWNLINK_FABRIC_ROOT_CA")]
    fabric_root_ca: String,
    /// Verify the fabric router's certificate name.
    #[arg(long, env = "DOWNLINK_VERIFY_NAME_FABRIC", default_value_t = true, action = clap::ArgAction::Set)]
    verify_name_fabric: bool,
    /// Pen router, e.g. tls/gw-pen:7447.
    #[arg(long, env = "DOWNLINK_PEN_ENDPOINT")]
    pen_endpoint: String,
    /// This bridge's pod-CA certificate (CN octo-downlink-bridge).
    #[arg(long, env = "DOWNLINK_PEN_CERT")]
    pen_cert: String,
    /// Its private key (PEM).
    #[arg(long, env = "DOWNLINK_PEN_KEY")]
    pen_key: String,
    /// The pod CA (PEM).
    #[arg(long, env = "DOWNLINK_PEN_ROOT_CA")]
    pen_root_ca: String,
    /// `downlink_allow`: comma-separated fabric keys released downward. Empty is valid.
    #[arg(long, env = "DOWNLINK_ALLOW", default_value = "")]
    allow: String,
    /// Seconds between stats lines.
    #[arg(long, env = "DOWNLINK_STATS_SECS", default_value_t = 60)]
    stats_secs: u64,
}

#[derive(Default)]
struct Counters {
    forwarded: AtomicU64,
    forward_errors: AtomicU64,
    dropped_gate: AtomicU64,
    deletes: AtomicU64,
}

#[tokio::main]
async fn main() -> ExitCode {
    octo_arm_common::init_tracing();
    ExitCode::from(
        bridge(Args::parse(), async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await,
    )
}

/// The whole bridge, until `shutdown` resolves (exit 0) or an upstream subscription ends
/// (exit 1, so the supervisor restarts it into a clean set of subscriptions).
async fn bridge(args: Args, shutdown: impl std::future::Future<Output = ()>) -> u8 {
    let entries: Vec<String> = args
        .allow
        .split(',')
        .map(|s| s.trim().to_string())
        .collect();
    let (gate, refused) = DownlinkGate::new(&entries);
    for r in &refused {
        error!(refusal = %r, "REFUSED downlink_allow entry: not subscribed");
    }
    let refused_count = refused.len() as u64;
    info!(
        released = ?gate.entries().iter().map(|k| k.as_str()).collect::<Vec<_>>(),
        refused = refused_count,
        "downlink-bridge starting (fabric -> pen only)"
    );
    let counters = Arc::new(Counters::default());
    let stats = spawn_stats(args.stats_secs, counters.clone(), refused_count);

    if gate.is_empty() {
        info!("nothing released downward: no fabric session is opened");
        shutdown.await;
        stats.abort();
        return 0;
    }

    let pen = Leg {
        label: "pen",
        endpoint: args.pen_endpoint.clone(),
        cert: args.pen_cert.clone(),
        key: args.pen_key.clone(),
        root_ca: args.pen_root_ca.clone(),
        verify_name: false, // octo-pod's pen idiom
    }
    .open_forever()
    .await;
    let fabric = Leg {
        label: "fabric",
        endpoint: args.fabric_endpoint.clone(),
        cert: args.fabric_cert.clone(),
        key: args.fabric_key.clone(),
        root_ca: args.fabric_root_ca.clone(),
        verify_name: args.verify_name_fabric,
    }
    .open_forever()
    .await;

    let gate = Arc::new(gate);
    let mut tasks = tokio::task::JoinSet::new();
    for entry in gate.entries().to_vec() {
        tasks.spawn(forward(
            entry,
            fabric.clone(),
            pen.clone(),
            gate.clone(),
            counters.clone(),
        ));
    }

    let code = tokio::select! {
        () = shutdown => { info!("shutdown signal received"); 0 }
        Some(_) = tasks.join_next() => {
            // Non-zero so the supervisor restarts us into a clean set of subscriptions.
            error!("a subscriber task ended; exiting for a supervised restart");
            1
        }
    };
    tasks.abort_all();
    stats.abort();
    let _ = fabric.close().await;
    let _ = pen.close().await;
    code
}

/// Forward one released entry: subscribe upstream, republish each sample on the pen.
async fn forward(
    entry: OwnedKeyExpr,
    fabric: zenoh::Session,
    pen: zenoh::Session,
    gate: Arc<DownlinkGate>,
    counters: Arc<Counters>,
) {
    let sub = match fabric.declare_subscriber(entry.clone()).await {
        Ok(s) => s,
        Err(e) => {
            error!(key_expr = %entry, error = %e, "declare_subscriber failed");
            return;
        }
    };
    info!(key_expr = %entry, "subscribed upstream");
    while let Ok(sample) = sub.recv_async().await {
        let key = sample.key_expr();
        // Belt and braces: a sample must still match the gate it came through.
        if !gate.permits(key) {
            counters.dropped_gate.fetch_add(1, Ordering::Relaxed);
            warn!(key = %key, "sample outside downlink_allow dropped");
            continue;
        }
        let pen_key = pen_key(key.as_str());
        let result = match sample.kind() {
            SampleKind::Put => {
                pen.put(&pen_key, sample.payload().to_bytes().to_vec())
                    .encoding(sample.encoding().clone())
                    .await
            }
            SampleKind::Delete => {
                counters.deletes.fetch_add(1, Ordering::Relaxed);
                pen.delete(&pen_key).await
            }
        };
        match result {
            Ok(()) => counters.forwarded.fetch_add(1, Ordering::Relaxed),
            Err(e) => {
                warn!(pen_key = %pen_key, error = %e, "pen put failed");
                counters.forward_errors.fetch_add(1, Ordering::Relaxed)
            }
        };
    }
    warn!(key_expr = %entry, "upstream subscriber closed");
}

fn spawn_stats(secs: u64, c: Arc<Counters>, refused: u64) -> tokio::task::JoinHandle<()> {
    let period = Duration::from_secs(secs.max(1));
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(period).await;
            info!(
                forwarded = c.forwarded.load(Ordering::Relaxed),
                forward_errors = c.forward_errors.load(Ordering::Relaxed),
                dropped_gate = c.dropped_gate.load(Ordering::Relaxed),
                deletes = c.deletes.load(Ordering::Relaxed),
                refused_entries = refused,
                "downlink stats"
            );
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use octo_arm_common::testing::{free_tcp_port, next_sample, TestPen, ARRIVES, QUIET};
    use tokio::sync::oneshot;

    const KEY: &str = "894aee26d8146f54a0e949eb66acc40c/sensors/radar1/asterix/data";
    const OTHER: &str = "894aee26d8146f54a0e949eb66acc40c/sensors/radar2/asterix/data";

    fn args(fabric: &str, pen: &str, allow: &str) -> Args {
        Args::try_parse_from([
            "downlink-bridge",
            "--fabric-endpoint",
            fabric,
            "--fabric-cert",
            "/unused/slot.crt",
            "--fabric-key",
            "/unused/slot.key",
            "--fabric-root-ca",
            "/unused/fabric-ca.pem",
            "--pen-endpoint",
            pen,
            "--pen-cert",
            "/unused/pen.crt",
            "--pen-key",
            "/unused/pen.key",
            "--pen-root-ca",
            "/unused/pen-ca.pem",
            "--allow",
            allow,
        ])
        .unwrap()
    }

    fn start(args: Args) -> (oneshot::Sender<()>, tokio::task::JoinHandle<u8>) {
        let (stop, stopped) = oneshot::channel::<()>();
        let task = tokio::spawn(bridge(args, async move {
            let _ = stopped.await;
        }));
        (stop, task)
    }

    type Sub =
        zenoh::pubsub::Subscriber<zenoh::handlers::FifoChannelHandler<zenoh::sample::Sample>>;

    /// Publish upstream until the bridge has subscribed and the sample is on the pen.
    async fn put_until_down(fabric: &TestPen, pen: &Sub) -> zenoh::sample::Sample {
        let deadline = tokio::time::Instant::now() + ARRIVES;
        loop {
            fabric
                .session
                .put(KEY, b"\x30\x00\x03".to_vec())
                .encoding(zenoh::bytes::Encoding::APPLICATION_OCTET_STREAM)
                .await
                .unwrap();
            if let Some(s) = next_sample(pen, Duration::from_millis(250)).await {
                return s;
            }
            assert!(tokio::time::Instant::now() < deadline, "never came down");
        }
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn only_released_keys_come_down_and_only_under_downlink() {
        let (fabric, pen) = (TestPen::start().await, TestPen::start().await);
        let down = pen.subscribe("**").await;
        // `**` is refused (it would reach the whole fabric); the released key still runs.
        let (stop, task) = start(args(&fabric.endpoint, &pen.endpoint, &format!("**,{KEY}")));

        let s = put_until_down(&fabric, &down).await;
        assert_eq!(s.key_expr().as_str(), format!("downlink/{KEY}"));
        assert_eq!(s.payload().to_bytes().as_ref(), b"\x30\x00\x03");
        while next_sample(&down, Duration::from_millis(200))
            .await
            .is_some()
        {}

        for key in [OTHER, "ops/env/health/pod-1", "downlink/x"] {
            fabric.session.put(key, b"no".to_vec()).await.unwrap();
        }
        assert!(next_sample(&down, QUIET).await.is_none());

        fabric.session.delete(KEY).await.unwrap();
        let gone = next_sample(&down, ARRIVES).await.expect("delete came down");
        assert_eq!(gone.kind(), SampleKind::Delete);
        assert_eq!(gone.key_expr().as_str(), format!("downlink/{KEY}"));

        stop.send(()).unwrap();
        assert_eq!(task.await.unwrap(), 0);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn nothing_published_on_the_pen_goes_up_through_this_bridge() {
        let (fabric, pen) = (TestPen::start().await, TestPen::start().await);
        let down = pen.subscribe("**").await;
        let up = fabric.subscribe("**").await;
        let (stop, task) = start(args(&fabric.endpoint, &pen.endpoint, KEY));
        put_until_down(&fabric, &down).await;
        while next_sample(&up, Duration::from_millis(200)).await.is_some() {}

        for key in [
            format!("downlink/{KEY}"),
            KEY.to_string(),
            "raw/octo/radar/x".into(),
        ] {
            pen.session.put(key, b"up?".to_vec()).await.unwrap();
        }
        assert!(
            next_sample(&up, QUIET).await.is_none(),
            "the pen leg must never subscribe"
        );
        stop.send(()).unwrap();
        assert_eq!(task.await.unwrap(), 0);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn nothing_released_opens_no_session_and_stops_cleanly() {
        // Neither router exists: opening either leg would never return.
        let nowhere = format!("tcp/127.0.0.1:{}", free_tcp_port());
        for allow in ["", "**", "ops/**,attest/x"] {
            let (stop, task) = start(args(&nowhere, &nowhere, allow));
            tokio::time::sleep(Duration::from_millis(100)).await;
            stop.send(()).unwrap();
            let code = tokio::time::timeout(Duration::from_secs(5), task)
                .await
                .expect("stops without opening anything")
                .unwrap();
            assert_eq!(code, 0, "{allow}");
        }
    }
}
