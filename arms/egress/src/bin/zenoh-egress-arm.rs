// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! zenoh-egress-arm: deliver released downlink keys into a partner's OWN Zenoh router,
//! instead of a raw socket.
//!
//! `egress-arm` delivers to a consumer that does not speak Zenoh, the way it would connect
//! to the sensor itself (a datagram, a dialled stream, or a listener it dials). A partner
//! that already runs their own Zenoh mesh needs none of that: the gateway's job here is
//! just to dial their router in client mode, under the certificate THEY issued us, and
//! publish under the prefix THEY granted. No framing, no protocol translation.
//!
//! Two legs, both retried forever rather than opened once (an audit sink's lesson applies to a partner's
//! router exactly as it does to our own fabric: at boot, or after it restarts, the other
//! side routinely comes up after this arm does):
//!
//! - **pen**: this arm's own pod-CA certificate, read-only on exactly its granted
//!   `downlink/<key>` entries, same gate every other egress arm enforces;
//! - **partner**: the certificate and CA the partner issued, client mode only (this arm
//!   never becomes a peer or router on their mesh), confined by the declared, allow-listed
//!   prefix in [`octo_egress::zenoh_dest`].
//!
//! Keys move namespace exactly once on the way out: pen `downlink/K` becomes partner
//! `<prefix>/K`, the fabric key `K` preserved verbatim beneath the granted prefix, the same
//! convention used for a partner's own namespace on an ingress bundle.
//!
//! Failure behavior:
//! - a declaration it refuses (keys, or the prefix not in `--allow-prefix`): opens nothing,
//!   writes a `refused` audit line when it can, exit 2;
//! - either leg not up yet: retried every 5 s ([`octo_egress::session::OPEN_RETRY`]) rather
//!   than exiting, because an unreachable partner at startup is the common case, not a
//!   fault;
//! - a publish to the partner that fails: counted (`forward_errors`); the data waits in the
//!   Outbox and ages out rather than going late, exactly as `egress-arm` does for a stalled
//!   socket destination. `Session::put` returning `Ok` is not proof of delivery, only that
//!   zenoh accepted it locally — the partner's own ACL can still discard it silently, which
//!   is exactly why BEFORE YOU START in the ingress bundle asks the partner to confirm what
//!   they actually receive.

use std::path::Path;
use std::process::ExitCode;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::Context;
use clap::Parser;
use octo_arm_common::{Outbox, Pending, Policy, SessionConfig, Stats};
use octo_egress::audit::AuditLog;
use octo_egress::gate::{fabric_key, pen_key, DownlinkGate};
use octo_egress::session::Leg;
use octo_egress::zenoh_dest::{PrefixAllowlist, ZenohDestination};
use serde_json::json;
use tracing::{error, info, warn};
use zenoh::key_expr::OwnedKeyExpr;

/// Exit code for a declaration the arm refuses to run with.
const REFUSED: u8 = 2;
/// Idle wake-up, so the drain loop notices state changes on a quiet pen.
const IDLE: Duration = Duration::from_millis(500);

#[derive(Parser, Debug)]
#[command(
    name = "zenoh-egress-arm",
    about = "Deliver one partner's released downlink keys into their own Zenoh router"
)]
struct Args {
    /// This destination's id; names the audit file and appears in every record.
    #[arg(long, env = "ZENOH_EGRESS_ID")]
    id: String,
    /// Fabric keys this destination was declared with, comma-separated.
    #[arg(long, env = "ZENOH_EGRESS_KEYS")]
    keys: String,
    /// The granted prefix on the partner's router every republished key is written beneath.
    #[arg(long, env = "ZENOH_EGRESS_PREFIX")]
    prefix: String,
    /// The declared allow list: literal prefixes. Empty allows nothing.
    #[arg(long, env = "ZENOH_EGRESS_ALLOW_PREFIX", default_value = "")]
    allow_prefix: String,
    /// Our own pen router endpoint, e.g. tls/127.0.0.1:7447.
    #[arg(
        long,
        env = "ZENOH_EGRESS_PEN_ENDPOINT",
        default_value = "tls/127.0.0.1:7447"
    )]
    pen_endpoint: String,
    /// This arm's pod-CA certificate (PEM).
    #[arg(long, env = "ZENOH_EGRESS_PEN_CERT")]
    pen_cert: Option<String>,
    /// Its private key (PEM).
    #[arg(long, env = "ZENOH_EGRESS_PEN_KEY")]
    pen_key: Option<String>,
    /// The pod CA (PEM).
    #[arg(long, env = "ZENOH_EGRESS_PEN_ROOT_CA")]
    pen_root_ca: Option<String>,
    /// The partner's router, e.g. tls/10.20.30.1:7447.
    #[arg(long, env = "ZENOH_EGRESS_PARTNER_ENDPOINT")]
    partner_endpoint: String,
    /// The certificate the partner issued us (PEM).
    #[arg(long, env = "ZENOH_EGRESS_PARTNER_CERT")]
    partner_cert: String,
    /// Its private key (PEM).
    #[arg(long, env = "ZENOH_EGRESS_PARTNER_KEY")]
    partner_key: String,
    /// The partner's own CA, so we can verify their router (PEM).
    #[arg(long, env = "ZENOH_EGRESS_PARTNER_ROOT_CA")]
    partner_root_ca: String,
    /// Verify the partner router's certificate name against the endpoint host. Default on:
    /// unlike our own pen (whose certs bind addresses, not names), a partner's router is
    /// not ours to assume that about.
    #[arg(
        long,
        env = "ZENOH_EGRESS_VERIFY_NAME_PARTNER",
        default_value_t = true,
        action = clap::ArgAction::Set
    )]
    verify_name_partner: bool,
    /// Where the audit record is kept.
    #[arg(
        long,
        env = "ZENOH_EGRESS_AUDIT_DIR",
        default_value = "/var/lib/octo-egress"
    )]
    audit_dir: String,
    /// Seconds between stats lines and audit summaries.
    #[arg(long, env = "ZENOH_EGRESS_STATS_SECS", default_value_t = 60)]
    stats_secs: u64,
}

/// Counters this arm has no shared name for.
#[derive(Default)]
struct ZenohEgressCounters {
    dropped_key: AtomicU64,
    audit_errors: AtomicU64,
}

/// Everything the arm checked before it opened anything.
struct Plan {
    dest: ZenohDestination,
    gate: DownlinkGate,
    pen_keys: Vec<String>,
}

/// Validate the declaration. Any refusal means the arm never opens a session.
fn plan(args: &Args) -> Result<Plan, String> {
    let allow = PrefixAllowlist::from_csv(&args.allow_prefix);
    let dest = ZenohDestination::new(&args.partner_endpoint, &args.prefix, &allow)
        .map_err(|e| e.to_string())?;
    let keys: Vec<String> = args.keys.split(',').map(|k| k.trim().to_string()).collect();
    let (gate, refused) = DownlinkGate::new(&keys);
    if !refused.is_empty() {
        let why: Vec<String> = refused.iter().map(ToString::to_string).collect();
        return Err(format!("declared keys refused: {}", why.join("; ")));
    }
    if gate.is_empty() {
        return Err("no keys declared".into());
    }
    let pen_keys = gate.entries().iter().map(|k| pen_key(k.as_str())).collect();
    Ok(Plan {
        dest,
        gate,
        pen_keys,
    })
}

#[tokio::main]
async fn main() -> ExitCode {
    octo_arm_common::init_tracing();
    ExitCode::from(
        arm(Args::parse(), async {
            tokio::signal::ctrl_c().await.context("waiting for ctrl-c")
        })
        .await,
    )
}

/// Plan, then run until `shutdown` resolves. Returns the exit code: 0 stopped, 1 failed
/// (for the supervisor to restart), [`REFUSED`] for a declaration it will not serve.
async fn arm(args: Args, shutdown: impl std::future::Future<Output = anyhow::Result<()>>) -> u8 {
    let plan = match plan(&args) {
        Ok(p) => p,
        Err(why) => {
            // Fail closed: nothing is opened, nothing is sent.
            error!(id = %args.id, prefix = %args.prefix, reason = %why, "REFUSING TO START");
            if let Ok(audit) = AuditLog::open(Path::new(&args.audit_dir), &args.id, &args.prefix) {
                let _ = audit.event("refused", json!({ "reason": why }));
            }
            return REFUSED;
        }
    };
    match run(args, plan, shutdown).await {
        Ok(()) => 0,
        Err(e) => {
            error!(error = %format!("{e:#}"), "zenoh-egress-arm stopped");
            1
        }
    }
}

#[allow(clippy::too_many_lines)] // one linear setup; splitting it hides the order
async fn run(
    args: Args,
    plan: Plan,
    shutdown: impl std::future::Future<Output = anyhow::Result<()>>,
) -> anyhow::Result<()> {
    let audit = Arc::new(
        AuditLog::open(Path::new(&args.audit_dir), &args.id, &plan.dest.to_string())
            .context("opening the audit record")?,
    );
    audit.event(
        "start",
        json!({ "fabric_keys": plan.gate.entries().iter().map(|k| k.as_str()).collect::<Vec<_>>(),
                "pen_keys": plan.pen_keys,
                "partner_prefix": plan.dest.prefix.as_str() }),
    )?;
    info!(id = %args.id, dest = %plan.dest, pen_keys = ?plan.pen_keys, audit = %audit.path().display(), "zenoh-egress-arm starting");

    let pen = SessionConfig {
        endpoint: args.pen_endpoint.clone(),
        cert: args.pen_cert.clone(),
        key: args.pen_key.clone(),
        root_ca: args.pen_root_ca.clone(),
    }
    .open()
    .await?;
    let partner = Leg {
        label: "partner",
        endpoint: args.partner_endpoint.clone(),
        cert: args.partner_cert.clone(),
        key: args.partner_key.clone(),
        root_ca: args.partner_root_ca.clone(),
        verify_name: args.verify_name_partner,
    }
    .open_forever()
    .await;

    let stats = Arc::new(Stats::default());
    let outbox = Arc::new(Outbox::new(Policy::default(), stats.clone()));
    let counters = Arc::new(ZenohEgressCounters::default());

    // Subscribe to exactly the granted pen keys. A sample that is somehow outside the
    // declared set (a broader grant, a router bug) is dropped and counted here too.
    let mut subscribers = Vec::new();
    for pen_k in &plan.pen_keys {
        let (gate, outbox, stats, counters) = (
            plan.gate.clone(),
            outbox.clone(),
            stats.clone(),
            counters.clone(),
        );
        let sub = pen
            .declare_subscriber(pen_k.clone())
            .callback(move |sample| {
                Stats::bump(&stats.received);
                let key = sample.key_expr().as_str().to_string();
                let declared = fabric_key(&key)
                    .and_then(|f| OwnedKeyExpr::try_from(f).ok())
                    .is_some_and(|f| gate.permits(&f));
                if !declared {
                    counters.dropped_key.fetch_add(1, Ordering::Relaxed);
                    return;
                }
                outbox.push(Pending::new(
                    key,
                    sample.payload().to_bytes().to_vec(),
                    sample.encoding().clone(),
                ));
            })
            .await
            .map_err(|e| anyhow::anyhow!("declare_subscriber {pen_k}: {e}"))?;
        subscribers.push(sub);
    }

    let stats_task = spawn_stats(
        &args,
        &plan,
        stats.clone(),
        counters.clone(),
        audit.clone(),
        outbox.clone(),
    );
    let mut seq: u64 = 0;
    let drain = async {
        loop {
            if outbox.is_empty() {
                outbox.wait(IDLE).await;
            }
            while let Some(item) = outbox.pop_fresh() {
                let fabric = fabric_key(&item.key).unwrap_or_default();
                let external = plan.dest.external_key(&fabric);
                match partner
                    .put(&external, item.payload.clone())
                    .encoding(item.encoding.clone())
                    .await
                {
                    Ok(()) => {
                        seq += 1;
                        Stats::bump(&stats.forwarded);
                        // `pen_key` is where the bytes were read. Where they went is
                        // the line's destination (`<endpoint>#<prefix>`) plus the fabric key.
                        if audit.sent(seq, &fabric, &item.key, &item.payload).is_err() {
                            counters.audit_errors.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                    Err(e) => {
                        Stats::bump(&stats.forward_errors);
                        warn!(dest = %plan.dest, external = %external, error = %e, "partner put failed; held data ages out rather than going late");
                    }
                }
            }
        }
    };
    tokio::select! {
        () = drain => {}
        r = shutdown => { r?; info!("shutdown signal received"); }
    }
    stats_task.abort();
    drop(subscribers);
    let _ = partner.close().await;
    Ok(())
}

fn spawn_stats(
    args: &Args,
    plan: &Plan,
    stats: Arc<Stats>,
    counters: Arc<ZenohEgressCounters>,
    audit: Arc<AuditLog>,
    outbox: Arc<Outbox>,
) -> tokio::task::JoinHandle<()> {
    let period = Duration::from_secs(args.stats_secs.max(1));
    let dest = plan.dest.to_string();
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(period).await;
            let line = json!({
                "received": stats.received.load(Ordering::Relaxed),
                "sent": stats.forwarded.load(Ordering::Relaxed),
                "send_errors": stats.forward_errors.load(Ordering::Relaxed),
                "dropped_key": counters.dropped_key.load(Ordering::Relaxed),
                "dropped_stale": stats.dropped_stale.load(Ordering::Relaxed),
                "dropped_overflow": stats.dropped_overflow.load(Ordering::Relaxed),
                "audit_errors": counters.audit_errors.load(Ordering::Relaxed),
                "pending": outbox.len(),
            });
            info!(dest = %dest, stats = %line, "zenoh-egress stats");
            if audit.event("summary", line).is_err() {
                counters.audit_errors.fetch_add(1, Ordering::Relaxed);
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &str = "894aee26d8146f54a0e949eb66acc40c/sensors/radar1/asterix/data";

    fn args(prefix: &str, allow_prefix: &str, keys: &str) -> Args {
        Args::try_parse_from([
            "zenoh-egress-arm",
            "--id",
            "c2",
            "--keys",
            keys,
            "--prefix",
            prefix,
            "--allow-prefix",
            allow_prefix,
            "--partner-endpoint",
            "tls/10.20.30.1:7447",
            "--partner-cert",
            "/etc/octo-pod/c2-leg-cert.pem",
            "--partner-key",
            "/etc/octo-pod/c2-leg-key.pem",
            "--partner-root-ca",
            "/etc/octo-pod/c2-root-ca.pem",
        ])
        .unwrap()
    }

    use octo_arm_common::testing::{free_tcp_port, next_sample, TestPen, ARRIVES, QUIET};
    use serde_json::Value;
    use tokio::sync::oneshot;

    const PAYLOAD: &[u8] = b"{\"track\":7}";

    fn scratch(name: &str) -> String {
        let dir = std::env::temp_dir().join(format!("zenoh-egress-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir.to_string_lossy().into_owned()
    }

    fn audit_lines(dir: &str) -> Vec<Value> {
        std::fs::read_to_string(Path::new(dir).join("c2-egress.ndjson"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).expect("every audit line is JSON"))
            .collect()
    }

    /// The real arm between a pen and a partner router, both on loopback.
    fn live(pen: &str, partner: &str, dir: &str) -> Args {
        Args::try_parse_from([
            "zenoh-egress-arm",
            "--id",
            "c2",
            "--keys",
            KEY,
            "--prefix",
            "c2",
            "--allow-prefix",
            "c2",
            "--pen-endpoint",
            pen,
            "--partner-endpoint",
            partner,
            "--partner-cert",
            "/unused/leg.crt",
            "--partner-key",
            "/unused/leg.key",
            "--partner-root-ca",
            "/unused/root.pem",
            "--audit-dir",
            dir,
            "--stats-secs",
            "1",
        ])
        .unwrap()
    }

    fn start(args: Args) -> (oneshot::Sender<()>, tokio::task::JoinHandle<u8>) {
        let (stop, stopped) = oneshot::channel::<()>();
        let task = tokio::spawn(arm(args, async move {
            let _ = stopped.await;
            Ok(())
        }));
        (stop, task)
    }

    type Sub =
        zenoh::pubsub::Subscriber<zenoh::handlers::FifoChannelHandler<zenoh::sample::Sample>>;

    async fn publish_until_delivered(
        pen: &TestPen,
        partner: &Sub,
        within: Duration,
    ) -> zenoh::sample::Sample {
        let deadline = tokio::time::Instant::now() + within;
        loop {
            pen.session
                .put(format!("downlink/{KEY}"), PAYLOAD.to_vec())
                .encoding(zenoh::bytes::Encoding::APPLICATION_JSON)
                .await
                .unwrap();
            if let Some(s) = next_sample(partner, Duration::from_millis(250)).await {
                return s;
            }
            assert!(tokio::time::Instant::now() < deadline, "never delivered");
        }
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_released_sample_lands_under_the_granted_prefix_and_nothing_else_does() {
        let (pen, partner) = (TestPen::start().await, TestPen::start().await);
        let theirs = partner.subscribe("**").await;
        let dir = scratch("live");
        let (stop, task) = start(live(&pen.endpoint, &partner.endpoint, &dir));

        let s = publish_until_delivered(&pen, &theirs, ARRIVES).await;
        assert_eq!(s.key_expr().as_str(), format!("c2/{KEY}"));
        assert_eq!(s.payload().to_bytes().as_ref(), PAYLOAD);
        assert_eq!(*s.encoding(), zenoh::bytes::Encoding::APPLICATION_JSON);

        // A downlink key this partner was not released, and a pen key outside downlink/.
        while next_sample(&theirs, Duration::from_millis(200))
            .await
            .is_some()
        {}
        for key in [
            "downlink/894aee26d8146f54a0e949eb66acc40c/sensors/radar2/asterix/data",
            KEY,
        ] {
            pen.session.put(key, b"no".to_vec()).await.unwrap();
        }
        assert!(next_sample(&theirs, QUIET).await.is_none());

        tokio::time::sleep(Duration::from_millis(1_100)).await;
        stop.send(()).unwrap();
        assert_eq!(task.await.unwrap(), 0);
        let lines = audit_lines(&dir);
        assert_eq!(lines[0]["kind"], "start");
        assert_eq!(lines[0]["partner_prefix"], "c2");
        let sent = lines
            .iter()
            .find(|l| l["kind"] == "sent")
            .expect("a sent line");
        assert_eq!(sent["fabric_key"], KEY);
        // Where the bytes were read, not where they went (regression for 721885c).
        assert_eq!(sent["pen_key"], format!("downlink/{KEY}"));
        assert_eq!(sent["destination"], format!("{}#c2", partner.endpoint));
        assert_eq!(
            sent["payload_hash"],
            octo_egress::audit::payload_hash(PAYLOAD)
        );
        let summary = lines
            .iter()
            .find(|l| l["kind"] == "summary")
            .expect("a summary");
        assert!(summary["sent"].as_u64().unwrap() >= 1);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_partner_that_is_down_at_start_is_redialled_until_it_answers() {
        let pen = TestPen::start().await;
        let port = free_tcp_port();
        let dir = scratch("late");
        let (stop, task) = start(live(&pen.endpoint, &format!("tcp/127.0.0.1:{port}"), &dir));

        tokio::time::sleep(Duration::from_millis(500)).await;
        let partner = TestPen::start_on(port).await;
        let theirs = partner.subscribe("**").await;
        let s = publish_until_delivered(&pen, &theirs, octo_egress::session::OPEN_RETRY + ARRIVES)
            .await;
        assert_eq!(s.key_expr().as_str(), format!("c2/{KEY}"));
        stop.send(()).unwrap();
        assert_eq!(task.await.unwrap(), 0);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_prefix_outside_the_allow_list_exits_2_and_records_why() {
        let dir = scratch("refused");
        let mut a = live("tcp/127.0.0.1:1", "tcp/127.0.0.1:1", &dir);
        a.allow_prefix = "someone-else".into();
        assert_eq!(arm(a, async { Ok(()) }).await, REFUSED);
        let lines = audit_lines(&dir);
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0]["kind"], "refused");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_pen_that_will_not_open_exits_1_for_the_supervisor() {
        let dir = scratch("nopen");
        let nowhere = format!("tcp/127.0.0.1:{}", free_tcp_port());
        assert_eq!(
            arm(live(&nowhere, &nowhere, &dir), async { Ok(()) }).await,
            1
        );
    }

    #[test]
    fn a_listed_prefix_with_a_released_key_is_planned_onto_its_pen_key() {
        let p = plan(&args("raw/goat/radar1", "raw/goat/radar1", KEY)).unwrap();
        assert_eq!(p.dest.prefix.as_str(), "raw/goat/radar1");
        assert_eq!(p.pen_keys, vec![format!("downlink/{KEY}")]);
    }

    #[test]
    fn a_release_lane_key_still_plans() {
        let lane = "release/REL-ALL/c43671680793b76e9f9688e3911d8f23/echidna/**";
        let p = plan(&args("raw/goat/radar1", "raw/goat/radar1", lane)).unwrap();
        assert_eq!(p.pen_keys, vec![format!("downlink/{lane}")]);
    }

    #[test]
    fn a_prefix_missing_from_the_allow_list_is_refused() {
        let why = plan(&args("raw/goat/radar9", "raw/goat/radar1", KEY))
            .err()
            .unwrap();
        assert!(why.contains("not in the declared allow list"), "{why}");
    }

    #[test]
    fn an_empty_allow_list_refuses_every_prefix() {
        assert!(plan(&args("raw/goat/radar1", "", KEY)).is_err());
    }

    #[test]
    fn a_wildcard_prefix_is_refused() {
        let why = plan(&args("raw/goat/**", "raw/goat/**", KEY))
            .err()
            .unwrap();
        assert!(why.contains("literal root"), "{why}");
    }

    #[test]
    fn a_key_reaching_the_whole_fabric_refuses_the_whole_declaration() {
        let keys = format!("{KEY},**");
        let why = plan(&args("raw/goat/radar1", "raw/goat/radar1", &keys))
            .err()
            .unwrap();
        assert!(why.contains("declared keys refused"), "{why}");
    }

    #[test]
    fn a_declaration_with_no_keys_is_refused() {
        let why = plan(&args("raw/goat/radar1", "raw/goat/radar1", " , "))
            .err()
            .unwrap();
        assert!(why.contains("no keys declared"), "{why}");
    }

    #[test]
    fn the_external_key_keeps_the_fabric_key_verbatim_under_the_prefix() {
        let p = plan(&args("raw/goat/radar1", "raw/goat/radar1", KEY)).unwrap();
        assert_eq!(p.dest.external_key(KEY), format!("raw/goat/radar1/{KEY}"));
    }
}
