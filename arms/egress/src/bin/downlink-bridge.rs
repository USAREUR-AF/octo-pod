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
    let args = Args::parse();
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
        let _ = tokio::signal::ctrl_c().await;
        stats.abort();
        return ExitCode::SUCCESS;
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
        _ = tokio::signal::ctrl_c() => { info!("shutdown signal received"); ExitCode::SUCCESS }
        Some(_) = tasks.join_next() => {
            // Non-zero so the supervisor restarts us into a clean set of subscriptions.
            error!("a subscriber task ended; exiting for a supervised restart");
            ExitCode::FAILURE
        }
    };
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
