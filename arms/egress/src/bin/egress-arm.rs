// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! egress-arm: one octo-pod arm, run in reverse, per external destination.
//!
//! An inbound octo-pod arm takes a sensor's data the way the sensor offers it, gates on
//! the sender's address, and publishes into the pen under its own pod-CA certificate. This
//! arm reads from the pen under its own pod-CA certificate, and the pen's ACL lets it read
//! ONLY the downlink keys its destination was released for. It delivers each sample's
//! bytes, unchanged, the way the consumer expects to get them from the sensor:
//!
//! - `udp://IP:PORT`: one datagram per sample, from a socket connected to that address;
//! - `tcp://IP:PORT`: a stream on a connection this arm dials; the connected peer must be
//!   exactly the declared address, or the connection is closed unused;
//! - `tcp-server://IP:PORT`: a stream to each consumer that dials this arm, listening on
//!   the pod's own address; only `--peers` are served (see `octo_egress::serve`).
//!
//! It refuses to start, and opens nothing, unless the destination is a literal socket
//! address in the declared allow list, every declared key passes the downlink gate's scope
//! rule, and a `tcp-server://` destination names at least one peer. A sample on any key
//! outside its declared set is dropped and counted.
//!
//! What it shares with the inbound arms comes from `octo-arm-common`: the pen session
//! idiom, and the bounded Outbox (age, count and byte bounds; stale data is dropped and
//! counted, never sent late). Every send is written to a local NDJSON audit record (see
//! `octo_egress::audit`), and so is every consumer that connects, is refused, replaced or
//! leaves.
//!
//! Failure behavior:
//! - a declaration it refuses: opens nothing, writes a `refused` audit line when it can,
//!   exit 2;
//! - a pen session that will not open, or a listener that cannot bind: exit 1, for the
//!   supervisor to restart;
//! - a send that fails, or a TCP write that stalls past 10 s: counted (`send_errors`); the
//!   data waits in the Outbox and ages out rather than going late; a TCP destination is
//!   re-dialled after 1 s;
//! - no consumer connected to a `tcp-server://` destination: the frame is counted
//!   (`no_client`) and not kept, except status frames kept for replay;
//! - an audit line that cannot be written: counted (`audit_errors`); the send stands.

use std::path::Path;
use std::process::ExitCode;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::Context;
use clap::Parser;
use octo_arm_common::{Outbox, Pending, Policy, SessionConfig, Stats};
use octo_egress::audit::AuditLog;
use octo_egress::dest::{Destination, DestinationAllowlist, Peers, Scheme};
use octo_egress::gate::{fabric_key, pen_key, DownlinkGate};
use octo_egress::serve::{Frame, ServeConfig, ServeCounters, Server, MAX_CLIENTS, MAX_REPLAY};
use serde_json::json;
use tokio::io::AsyncWriteExt;
use tokio::net::{TcpStream, UdpSocket};
use tracing::{error, info, warn};
use zenoh::key_expr::OwnedKeyExpr;

/// Exit code for a declaration the arm refuses to run with.
const REFUSED: u8 = 2;
/// Idle wake-up, so the drain loop notices state changes on a quiet pen.
const IDLE: Duration = Duration::from_millis(500);
/// Delay before re-dialling a TCP destination that refused or dropped us.
const REDIAL: Duration = Duration::from_secs(1);
/// Bound on one write to a TCP destination. A consumer that stops draining parks
/// `write_all` behind a zero window for as long as `tcp_retries2` allows (around 15 minutes
/// on Linux), and this arm has one drain loop: unbounded, every queued sample ages out while
/// `send_errors` stays flat and the failure goes unseen. The same bound the sapient arm
/// holds on its writer.
const WRITE_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Parser, Debug)]
#[command(
    name = "egress-arm",
    about = "Deliver one destination's released downlink keys the way its consumer connects"
)]
struct Args {
    /// This destination's id; names the audit file and appears in every record.
    #[arg(long, env = "EGRESS_ID")]
    id: String,
    /// Fabric keys this destination was declared with, comma-separated.
    #[arg(long, env = "EGRESS_KEYS")]
    keys: String,
    /// The destination: `udp://IP:PORT`, `tcp://IP:PORT` or `tcp-server://IP:PORT`.
    #[arg(long, env = "EGRESS_DEST")]
    dest: String,
    /// The declared allow list: literal `IP:PORT` entries. Empty allows nothing.
    #[arg(long, env = "EGRESS_ALLOW_DEST", default_value = "")]
    allow_dest: String,
    /// `tcp-server://` only: consumers allowed to connect, literal IPs, comma-separated.
    #[arg(long, env = "EGRESS_PEERS", default_value = "")]
    peers: String,
    /// `tcp-server://` only: most consumers at once.
    #[arg(long, env = "EGRESS_MAX_CLIENTS", default_value_t = 1)]
    max_clients: usize,
    /// `tcp-server://` only: newest status frames replayed to a consumer on connect.
    #[arg(long, env = "EGRESS_REPLAY", default_value_t = 0)]
    replay: usize,
    /// Pen router endpoint.
    #[arg(long, env = "EGRESS_ENDPOINT", default_value = "tls/127.0.0.1:7447")]
    endpoint: String,
    /// This arm's pod-CA certificate (PEM).
    #[arg(long, env = "EGRESS_CERT")]
    cert: Option<String>,
    /// Its private key (PEM).
    #[arg(long, env = "EGRESS_KEY")]
    key: Option<String>,
    /// The pod CA (PEM).
    #[arg(long, env = "EGRESS_ROOT_CA")]
    root_ca: Option<String>,
    /// Where the audit record is kept.
    #[arg(long, env = "EGRESS_AUDIT_DIR", default_value = "/var/lib/octo-egress")]
    audit_dir: String,
    /// Seconds between stats lines and audit summaries.
    #[arg(long, env = "EGRESS_STATS_SECS", default_value_t = 60)]
    stats_secs: u64,
}

/// Counters the shared `Stats` has no name for.
#[derive(Default)]
struct EgressCounters {
    dropped_key: AtomicU64,
    dest_mismatch: AtomicU64,
    audit_errors: AtomicU64,
}

/// Everything the arm checked before it was allowed to open anything.
struct Plan {
    dest: Destination,
    gate: DownlinkGate,
    pen_keys: Vec<String>,
    serve: Option<ServeConfig>,
}

/// Validate the declaration. Any refusal means the arm never opens a socket.
fn plan(args: &Args) -> Result<Plan, String> {
    let dest = Destination::parse(&args.dest).map_err(|e| e.to_string())?;
    let allow = DestinationAllowlist::from_csv(&args.allow_dest).map_err(|e| e.to_string())?;
    allow.admit(&dest).map_err(|e| e.to_string())?;
    let keys: Vec<String> = args.keys.split(',').map(|k| k.trim().to_string()).collect();
    let (gate, refused) = DownlinkGate::new(&keys);
    if !refused.is_empty() {
        let why: Vec<String> = refused.iter().map(ToString::to_string).collect();
        return Err(format!("declared keys refused: {}", why.join("; ")));
    }
    if gate.is_empty() {
        return Err("no keys declared".into());
    }
    let serve = if dest.scheme == Scheme::TcpServer {
        let peers = Peers::from_csv(&args.peers).map_err(|e| e.to_string())?;
        if peers.is_empty() {
            return Err(format!(
                "{dest} needs --peers: a listener with no allowed consumer answers no one"
            ));
        }
        if !(1..=MAX_CLIENTS).contains(&args.max_clients) {
            return Err(format!("max_clients must be 1-{MAX_CLIENTS}"));
        }
        if args.replay > MAX_REPLAY {
            return Err(format!("replay must be 0-{MAX_REPLAY}"));
        }
        Some(ServeConfig {
            bind: dest.addr,
            peers,
            max_clients: args.max_clients,
            replay: args.replay,
        })
    } else {
        None
    };
    let pen_keys = gate.entries().iter().map(|k| pen_key(k.as_str())).collect();
    Ok(Plan {
        dest,
        gate,
        pen_keys,
        serve,
    })
}

/// The sending half for `udp://` and `tcp://`. Owns the socket; the subscriber callbacks
/// never touch it.
struct Sender {
    dest: Destination,
    udp: Option<UdpSocket>,
    tcp: Option<TcpStream>,
    write_timeout: Duration,
}

impl Sender {
    async fn new(dest: Destination) -> anyhow::Result<Self> {
        let udp = if dest.scheme == Scheme::Udp {
            let bind = if dest.addr.is_ipv4() {
                "0.0.0.0:0"
            } else {
                "[::]:0"
            };
            let sock = UdpSocket::bind(bind).await.context("binding udp")?;
            // Connected: the kernel itself refuses to send this socket anywhere else.
            sock.connect(dest.addr).await.context("connecting udp")?;
            Some(sock)
        } else {
            None
        };
        Ok(Self {
            dest,
            udp,
            tcp: None,
            write_timeout: WRITE_TIMEOUT,
        })
    }

    /// Send one payload. `Ok(false)` means it was withheld (wrong peer), not failed.
    async fn send(&mut self, payload: &[u8], counters: &EgressCounters) -> anyhow::Result<bool> {
        if let Some(sock) = &self.udp {
            sock.send(payload).await.context("udp send")?;
            return Ok(true);
        }
        if self.tcp.is_none() {
            let stream = tokio::time::timeout(REDIAL, TcpStream::connect(self.dest.addr))
                .await
                .context("tcp connect timed out")?
                .context("tcp connect")?;
            let peer = stream.peer_addr().context("peer address")?;
            if peer != self.dest.addr {
                counters.dest_mismatch.fetch_add(1, Ordering::Relaxed);
                warn!(declared = %self.dest.addr, peer = %peer, "connected peer is not the declared destination; closing");
                return Ok(false);
            }
            info!(dest = %self.dest, "tcp destination connected");
            self.tcp = Some(stream);
        }
        if let Some(stream) = &mut self.tcp {
            // Expiry drops the connection along with any partial write, so the next send
            // re-dials rather than continuing a stream left mid-payload.
            match tokio::time::timeout(self.write_timeout, stream.write_all(payload)).await {
                Ok(Ok(())) => {}
                Ok(Err(e)) => {
                    self.tcp = None;
                    return Err(e).context("tcp write");
                }
                Err(_) => {
                    self.tcp = None;
                    anyhow::bail!(
                        "tcp write timed out after {:?}; the destination is not draining",
                        self.write_timeout
                    );
                }
            }
        }
        Ok(true)
    }
}

/// How this arm delivers: it sends, or it serves consumers that dial in.
enum Delivery {
    Send(Sender),
    Serve(Server),
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
            error!(id = %args.id, dest = %args.dest, reason = %why, "REFUSING TO START");
            if let Ok(audit) = AuditLog::open(Path::new(&args.audit_dir), &args.id, &args.dest) {
                let _ = audit.event("refused", json!({ "reason": why }));
            }
            return REFUSED;
        }
    };
    match run(args, plan, shutdown).await {
        Ok(()) => 0,
        Err(e) => {
            error!(error = %format!("{e:#}"), "egress-arm stopped");
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
                "peers": plan.serve.as_ref().map(|s| s.peers.describe()) }),
    )?;
    info!(id = %args.id, dest = %plan.dest, pen_keys = ?plan.pen_keys, audit = %audit.path().display(), "egress-arm starting");

    let session = SessionConfig {
        endpoint: args.endpoint.clone(),
        cert: args.cert.clone(),
        key: args.key.clone(),
        root_ca: args.root_ca.clone(),
    }
    .open()
    .await?;
    let stats = Arc::new(Stats::default());
    let outbox = Arc::new(Outbox::new(Policy::default(), stats.clone()));
    let counters = Arc::new(EgressCounters::default());
    let serve_counters = Arc::new(ServeCounters::default());

    // Subscribe to exactly the granted pen keys. A sample that is somehow outside the
    // declared set (a broader grant, a router bug) is dropped and counted here too.
    let mut subscribers = Vec::new();
    for pen in &plan.pen_keys {
        let (gate, outbox, stats, counters) = (
            plan.gate.clone(),
            outbox.clone(),
            stats.clone(),
            counters.clone(),
        );
        let sub = session
            .declare_subscriber(pen.clone())
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
            .map_err(|e| anyhow::anyhow!("declare_subscriber {pen}: {e}"))?;
        subscribers.push(sub);
    }

    let mut delivery = match plan.serve.clone() {
        Some(cfg) => Delivery::Serve(
            Server::start(cfg, audit.clone(), serve_counters.clone())
                .await
                .with_context(|| format!("listening on {}", plan.dest))?,
        ),
        None => Delivery::Send(Sender::new(plan.dest).await?),
    };
    let stats_task = spawn_stats(
        &args,
        &plan,
        stats.clone(),
        counters.clone(),
        serve_counters.clone(),
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
                match &mut delivery {
                    Delivery::Serve(server) => {
                        let frame: Frame = Arc::from(item.payload.as_slice());
                        let consumers = server.offer(&item.key, &frame);
                        if consumers > 0 {
                            seq += 1;
                            Stats::bump(&stats.forwarded);
                            if audit
                                .served(seq, &fabric, &item.key, &item.payload, consumers)
                                .is_err()
                            {
                                counters.audit_errors.fetch_add(1, Ordering::Relaxed);
                            }
                        }
                    }
                    Delivery::Send(sender) => match sender.send(&item.payload, &counters).await {
                        Ok(true) => {
                            seq += 1;
                            Stats::bump(&stats.forwarded);
                            if audit.sent(seq, &fabric, &item.key, &item.payload).is_err() {
                                counters.audit_errors.fetch_add(1, Ordering::Relaxed);
                            }
                        }
                        Ok(false) => tokio::time::sleep(REDIAL).await,
                        Err(e) => {
                            Stats::bump(&stats.forward_errors);
                            warn!(dest = %plan.dest, error = %format!("{e:#}"), "send failed; held data ages out rather than going late");
                            tokio::time::sleep(REDIAL).await;
                        }
                    },
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
    Ok(())
}

fn spawn_stats(
    args: &Args,
    plan: &Plan,
    stats: Arc<Stats>,
    counters: Arc<EgressCounters>,
    serve: Arc<ServeCounters>,
    audit: Arc<AuditLog>,
    outbox: Arc<Outbox>,
) -> tokio::task::JoinHandle<()> {
    let period = Duration::from_secs(args.stats_secs.max(1));
    let dest = plan.dest.to_string();
    let serving = plan.serve.is_some();
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(period).await;
            let mut line = json!({
                "received": stats.received.load(Ordering::Relaxed),
                "sent": stats.forwarded.load(Ordering::Relaxed),
                "send_errors": stats.forward_errors.load(Ordering::Relaxed),
                "dropped_key": counters.dropped_key.load(Ordering::Relaxed),
                "dest_mismatch": counters.dest_mismatch.load(Ordering::Relaxed),
                "dropped_stale": stats.dropped_stale.load(Ordering::Relaxed),
                "dropped_overflow": stats.dropped_overflow.load(Ordering::Relaxed),
                "audit_errors": counters.audit_errors.load(Ordering::Relaxed),
                "pending": outbox.len(),
            });
            if serving {
                if let Some(map) = line.as_object_mut() {
                    for (name, c) in [
                        ("consumers_connected", &serve.connected),
                        ("consumers_refused", &serve.refused),
                        ("consumers_replaced", &serve.evicted),
                        ("no_client", &serve.no_client),
                        ("dropped_slow", &serve.dropped_slow),
                        ("ignored_bytes", &serve.ignored_bytes),
                    ] {
                        map.insert(name.into(), json!(c.load(Ordering::Relaxed)));
                    }
                }
            }
            info!(dest = %dest, stats = %line, "egress stats");
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

    fn args(dest: &str, allow_dest: &str, keys: &str, extra: &[&str]) -> Args {
        let mut argv = vec![
            "egress-arm",
            "--id",
            "acts",
            "--keys",
            keys,
            "--dest",
            dest,
            "--allow-dest",
            allow_dest,
        ];
        argv.extend_from_slice(extra);
        Args::try_parse_from(argv).unwrap()
    }

    #[test]
    fn a_listed_destination_with_a_publishers_key_is_planned_onto_its_pen_key() {
        let p = plan(&args("udp://10.1.2.3:4001", "10.1.2.3:4001", KEY, &[])).unwrap();
        assert_eq!(p.dest.to_string(), "udp://10.1.2.3:4001");
        assert_eq!(p.pen_keys, vec![format!("downlink/{KEY}")]);
        assert!(p.serve.is_none());
    }

    #[test]
    fn a_release_lane_key_still_plans() {
        let lane = "release/REL-ALL/c43671680793b76e9f9688e3911d8f23/echidna/**";
        let p = plan(&args("udp://10.1.2.3:4001", "10.1.2.3:4001", lane, &[])).unwrap();
        assert_eq!(p.pen_keys, vec![format!("downlink/{lane}")]);
    }

    /// A TCP destination that accepts and never reads must fail the send within the write
    /// bound, counted by the caller and re-dialled, not park the drain loop for minutes.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_tcp_destination_that_stops_draining_fails_the_send_in_bounded_time() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let consumer = tokio::spawn(async move {
            let (s, _) = listener.accept().await.unwrap();
            tokio::time::sleep(Duration::from_secs(30)).await;
            drop(s);
        });
        let mut sender = Sender::new(Destination::parse(&format!("tcp://{addr}")).unwrap())
            .await
            .unwrap();
        sender.write_timeout = Duration::from_millis(200);
        let counters = EgressCounters::default();
        // Far more than both socket buffers hold, so the write cannot complete.
        let payload = vec![0u8; 64 * 1024 * 1024];
        let out = tokio::time::timeout(Duration::from_secs(5), sender.send(&payload, &counters))
            .await
            .expect("the write bound must end the send");
        consumer.abort();
        let err = format!("{:#}", out.expect_err("a stalled write is a failed send"));
        assert!(err.contains("timed out"), "{err}");
        assert!(
            sender.tcp.is_none(),
            "the stalled connection is dropped for a re-dial"
        );
    }

    use octo_arm_common::testing::{free_tcp_port, TestPen, ARRIVES};
    use serde_json::Value;
    use tokio::io::AsyncReadExt;
    use tokio::sync::oneshot;

    const PAYLOAD: &[u8] = b"\x30\x00\x0bASTERIX-62";

    fn scratch(name: &str) -> String {
        let dir = std::env::temp_dir().join(format!("egress-arm-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir.to_string_lossy().into_owned()
    }

    fn audit_lines(dir: &str) -> Vec<Value> {
        std::fs::read_to_string(Path::new(dir).join("acts-egress.ndjson"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).expect("every audit line is JSON"))
            .collect()
    }

    fn kinds(lines: &[Value]) -> Vec<String> {
        lines
            .iter()
            .map(|l| l["kind"].as_str().unwrap_or_default().to_string())
            .collect()
    }

    fn start(args: Args) -> (oneshot::Sender<()>, tokio::task::JoinHandle<u8>) {
        let (stop, stopped) = oneshot::channel::<()>();
        let task = tokio::spawn(arm(args, async move {
            let _ = stopped.await;
            Ok(())
        }));
        (stop, task)
    }

    async fn publish(pen: &TestPen) {
        pen.session
            .put(format!("downlink/{KEY}"), PAYLOAD.to_vec())
            .await
            .expect("pen put");
    }

    /// Until `deadline`, or fail the test.
    fn before(deadline: tokio::time::Instant) {
        assert!(tokio::time::Instant::now() < deadline, "never delivered");
    }

    const TRY: Duration = Duration::from_millis(250);

    #[tokio::test(flavor = "multi_thread")]
    async fn a_released_sample_is_sent_unchanged_over_udp_and_audited() {
        let pen = TestPen::start().await;
        let consumer = UdpSocket::bind("127.0.0.1:0").await.expect("consumer");
        let addr = consumer.local_addr().expect("addr");
        let dir = scratch("udp");
        let (stop, task) = start(args(
            &format!("udp://{addr}"),
            &addr.to_string(),
            KEY,
            &[
                "--endpoint",
                &pen.endpoint,
                "--audit-dir",
                &dir,
                "--stats-secs",
                "1",
            ],
        ));

        let mut got = vec![0u8; 2048];
        let deadline = tokio::time::Instant::now() + ARRIVES;
        let n = loop {
            publish(&pen).await;
            if let Ok(Ok(n)) = tokio::time::timeout(TRY, consumer.recv(&mut got)).await {
                break n;
            }
            before(deadline);
        };
        assert_eq!(&got[..n], PAYLOAD, "one datagram per sample, unchanged");
        tokio::time::sleep(Duration::from_millis(1_300)).await;
        stop.send(()).expect("stop");
        assert_eq!(task.await.expect("join"), 0);

        let lines = audit_lines(&dir);
        assert_eq!(lines[0]["kind"], "start");
        assert_eq!(lines[0]["pen_keys"][0], format!("downlink/{KEY}"));
        let sent = lines
            .iter()
            .find(|l| l["kind"] == "sent")
            .expect("a sent line");
        assert_eq!(sent["seq"], 1);
        assert_eq!(sent["fabric_key"], KEY);
        assert_eq!(sent["pen_key"], format!("downlink/{KEY}"));
        assert_eq!(sent["len"], PAYLOAD.len());
        assert_eq!(
            sent["payload_hash"],
            octo_egress::audit::payload_hash(PAYLOAD)
        );
        assert_eq!(sent["destination"], format!("udp://{addr}"));
        let summary = lines
            .iter()
            .find(|l| l["kind"] == "summary")
            .expect("a summary");
        assert!(summary["sent"].as_u64().expect("sent") >= 1);
        assert_eq!(summary["dest_mismatch"], 0);
        assert!(
            summary.get("consumers_connected").is_none(),
            "not a listener"
        );
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn an_ipv6_udp_destination_is_sent_from_an_ipv6_socket() {
        let pen = TestPen::start().await;
        let Ok(consumer) = UdpSocket::bind("[::1]:0").await else {
            return; // no IPv6 loopback on this runner
        };
        let addr = consumer.local_addr().expect("addr");
        let dir = scratch("udp6");
        let (stop, task) = start(args(
            &format!("udp://{addr}"),
            &addr.to_string(),
            KEY,
            &["--endpoint", &pen.endpoint, "--audit-dir", &dir],
        ));
        let mut got = vec![0u8; 2048];
        let deadline = tokio::time::Instant::now() + ARRIVES;
        let (n, from) = loop {
            publish(&pen).await;
            if let Ok(Ok(v)) = tokio::time::timeout(TRY, consumer.recv_from(&mut got)).await {
                break v;
            }
            before(deadline);
        };
        assert_eq!(&got[..n], PAYLOAD);
        assert!(from.is_ipv6());
        stop.send(()).expect("stop");
        assert_eq!(task.await.expect("join"), 0);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_tcp_destination_is_dialled_and_streams_each_sample_in_order() {
        let pen = TestPen::start().await;
        let consumer = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("consumer");
        let addr = consumer.local_addr().expect("addr");
        let dir = scratch("tcp");
        let (stop, task) = start(args(
            &format!("tcp://{addr}"),
            &addr.to_string(),
            KEY,
            &["--endpoint", &pen.endpoint, "--audit-dir", &dir],
        ));

        // The arm dials when it first has something to send.
        let deadline = tokio::time::Instant::now() + ARRIVES;
        let mut conn = loop {
            publish(&pen).await;
            if let Ok(Ok((s, _))) = tokio::time::timeout(TRY, consumer.accept()).await {
                break s;
            }
            before(deadline);
        };
        publish(&pen).await;
        // Every sample is the payload, back to back, nothing between or around them.
        let mut stream = vec![0u8; PAYLOAD.len() * 2];
        tokio::time::timeout(ARRIVES, conn.read_exact(&mut stream))
            .await
            .expect("streamed")
            .expect("read");
        assert_eq!(&stream[..PAYLOAD.len()], PAYLOAD);
        assert_eq!(&stream[PAYLOAD.len()..], PAYLOAD);

        stop.send(()).expect("stop");
        assert_eq!(task.await.expect("join"), 0);
        assert!(
            kinds(&audit_lines(&dir))
                .iter()
                .filter(|k| *k == "sent")
                .count()
                >= 2
        );
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_listening_destination_serves_a_declared_peer_that_dials_in() {
        let pen = TestPen::start().await;
        let port = free_tcp_port();
        let dir = scratch("serve");
        let (stop, task) = start(args(
            &format!("tcp-server://127.0.0.1:{port}"),
            &format!("127.0.0.1:{port}"),
            KEY,
            &[
                "--peers",
                "127.0.0.1",
                "--endpoint",
                &pen.endpoint,
                "--audit-dir",
                &dir,
                "--stats-secs",
                "1",
            ],
        ));

        // Published while no consumer is connected: counted, not kept. With no replay
        // configured, the consumer that connects next receives none of it.
        tokio::time::sleep(Duration::from_secs(1)).await;
        for _ in 0..3 {
            publish(&pen).await;
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
        let deadline = tokio::time::Instant::now() + ARRIVES;
        let mut consumer = loop {
            if let Ok(s) = TcpStream::connect(("127.0.0.1", port)).await {
                break s;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "listener never came up"
            );
            tokio::time::sleep(Duration::from_millis(50)).await;
        };
        let mut probe = [0u8; 1];
        assert!(
            tokio::time::timeout(Duration::from_millis(500), consumer.read(&mut probe))
                .await
                .is_err(),
            "a consumer must not be handed samples from before it connected"
        );
        let mut got = vec![0u8; PAYLOAD.len()];
        let deadline = tokio::time::Instant::now() + ARRIVES;
        loop {
            publish(&pen).await;
            if let Ok(Ok(_)) = tokio::time::timeout(TRY, consumer.read_exact(&mut got)).await {
                break;
            }
            before(deadline);
        }
        assert_eq!(got, PAYLOAD);
        tokio::time::sleep(Duration::from_millis(1_300)).await;

        stop.send(()).expect("stop");
        assert_eq!(task.await.expect("join"), 0);
        let lines = audit_lines(&dir);
        let sent = lines
            .iter()
            .find(|l| l["kind"] == "sent")
            .expect("a sent line");
        assert_eq!(sent["consumers"], 1);
        // The last summary, after the consumer connected; the first predates it.
        let summary = lines
            .iter()
            .rev()
            .find(|l| l["kind"] == "summary")
            .expect("a summary");
        assert_eq!(summary["consumers_connected"], 1);
        assert!(summary["no_client"].as_u64().expect("no_client") >= 1);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_refused_declaration_exits_2_and_records_why_without_opening_anything() {
        // The default endpoint is never dialled: nothing listens there, and the arm must
        // not get far enough to try.
        let dir = scratch("refused");
        let code = arm(
            args(
                "udp://10.1.2.66:4001",
                "10.1.2.3:4001",
                KEY,
                &["--audit-dir", &dir],
            ),
            async { Ok(()) },
        )
        .await;
        assert_eq!(code, REFUSED);
        let lines = audit_lines(&dir);
        assert_eq!(kinds(&lines), vec!["refused".to_string()]);
        assert!(lines[0]["reason"]
            .as_str()
            .expect("reason")
            .contains("not in the declared allow list"));
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_pen_that_will_not_open_exits_1_for_the_supervisor() {
        let dir = scratch("nopen");
        let nowhere = format!("tcp/127.0.0.1:{}", free_tcp_port());
        let code = arm(
            args(
                "udp://10.1.2.3:4001",
                "10.1.2.3:4001",
                KEY,
                &["--endpoint", &nowhere, "--audit-dir", &dir],
            ),
            async { Ok(()) },
        )
        .await;
        assert_eq!(code, 1);
        assert_eq!(kinds(&audit_lines(&dir)), vec!["start".to_string()]);
    }

    #[test]
    fn a_listening_destination_plans_with_its_peers() {
        let p = plan(&args(
            "tcp-server://192.0.2.18:9000",
            "192.0.2.18:9000",
            KEY,
            &["--peers", "192.0.2.17", "--replay", "2"],
        ))
        .unwrap();
        let serve = p.serve.unwrap();
        assert!(serve.peers.permits("192.0.2.17".parse().unwrap()));
        assert_eq!((serve.max_clients, serve.replay), (1, 2));
    }

    #[test]
    fn a_listening_destination_without_peers_is_refused() {
        let why = plan(&args(
            "tcp-server://192.0.2.18:9000",
            "192.0.2.18:9000",
            KEY,
            &[],
        ))
        .err()
        .unwrap();
        assert!(why.contains("needs --peers"), "{why}");
    }

    #[test]
    fn out_of_range_serving_limits_are_refused() {
        for extra in [
            &["--peers", "192.0.2.17", "--max-clients", "0"][..],
            &["--peers", "192.0.2.17", "--max-clients", "65"],
            &["--peers", "192.0.2.17", "--replay", "1025"],
            &["--peers", "192.0.2.0/24"],
        ] {
            assert!(
                plan(&args(
                    "tcp-server://192.0.2.18:9000",
                    "192.0.2.18:9000",
                    KEY,
                    extra
                ))
                .is_err(),
                "{extra:?} must be refused"
            );
        }
    }

    #[test]
    fn a_destination_missing_from_the_allow_list_is_refused() {
        let why = plan(&args("udp://10.1.2.66:4001", "10.1.2.3:4001", KEY, &[]))
            .err()
            .unwrap();
        assert!(why.contains("not in the declared allow list"), "{why}");
    }

    #[test]
    fn an_empty_allow_list_refuses_every_destination() {
        assert!(plan(&args("udp://10.1.2.3:4001", "", KEY, &[])).is_err());
    }

    #[test]
    fn a_host_name_destination_is_refused() {
        assert!(plan(&args(
            "tcp://display.example.test:4100",
            "10.1.2.3:4100",
            KEY,
            &[]
        ))
        .is_err());
    }

    #[test]
    fn a_key_reaching_the_whole_fabric_refuses_the_whole_declaration() {
        let keys = format!("{KEY},**");
        let why = plan(&args("udp://10.1.2.3:4001", "10.1.2.3:4001", &keys, &[]))
            .err()
            .unwrap();
        assert!(why.contains("declared keys refused"), "{why}");
    }

    #[test]
    fn a_declaration_with_no_keys_is_refused() {
        let why = plan(&args("udp://10.1.2.3:4001", "10.1.2.3:4001", " , ", &[]))
            .err()
            .unwrap();
        assert!(why.contains("no keys declared"), "{why}");
    }
}
