//! sapient — octo-pod session arm: BSI Flex 335 v2.0 over TCP.
//!
//! A SAPIENT connection has two roles. The **ASM** is the sensor side: it connects,
//! declares what it is and what it can report, waits to be accepted, then heartbeats and
//! streams detections. The **DMM** is the controller side: it accepts registrations,
//! acknowledges them, and may task the sensor. The peer is one of the two, so this arm is
//! the other — and which one is a fact about the peer, not a preference, so `role=auto`
//! discovers it from who registers first.
//!
//! A SAPIENT message handler sits between nodes and decision makers and forwards what it
//! receives to the clients connected to it. Its sensor-facing interface takes this arm as
//! an ASM like any controller. Its client-facing interface takes the **client** role,
//! which is declared, never detected: a handler that forwards a node's registration first
//! would otherwise read as a sensor registering with us.
//!
//! What it does NOT do, on purpose: it does not interpret a `DetectionReport`. Inbound
//! frames are published raw (capture-first), and the only parsing is what the session
//! itself needs — the envelope's discriminant, `RegistrationAck`, `Task`. Turning a
//! detection into another format is a translator's job, not an arm's.
//!
//! Write authority, all of it, by role:
//!
//!   * ASM — `Registration` on connect, `StatusReport` every `status_interval`,
//!     `TaskAck` for every `Task`; relayed `DetectionReport` and `Alert`.
//!   * DMM — `RegistrationAck` for every `Registration`; relayed `Task` and `AlertAck`,
//!     addressed to the sensor that registered.
//!   * client — nothing of its own; relayed `Task` and `AlertAck`, each already addressed
//!     to the node it is for.
//!
//! That list is the complete set of bytes this arm can put on a SAPIENT network. A relayed
//! message arrives on `SAPIENT_RELAY_KEY`, which a bridge into this arm feeds and the router
//! lets nothing else write. It goes out only once the session is registered (for a client,
//! once connected), under this arm's node id, and a sample outside the set for the arm's
//! role is refused and counted.
//!
//! Keys (`Ingestor` unmodified — the label carries the stream):
//! ```text
//! <prefix>/<label>/<peer-ip>           inbound frames, raw
//! <prefix>/<label>-egress/<peer-ip>    everything written to the wire
//! <prefix>/<label>-session/<peer-ip>   lifecycle, JSON
//! <prefix>/<label>-session/0-0-0-0     lifecycle before a socket exists
//! ```
//!
//! Only the first is ingress, and only it goes through the source-allowlisted gate. The
//! rest are authored here rather than received from anyone, so they go through a second,
//! ungated gate over the same session, queue and counters — gating a record on an address
//! this arm invented is how an unreachable peer ends up publishing nothing at all.
//!
//! As an egress destination (`sapient://` in `octo-pod egress`, see `egress.rs`), the arm
//! is declared with fabric keys instead of a relay key. It then dials one allowed address
//! as the sensor side, relays only what the downlink bridge released for those keys, writes
//! nothing it sends back onto the pen, and records each send in a local audit file.
//!
//! License: Apache-2.0

mod egress;
mod framing;
mod identity;
mod session;
mod wire;

// The generated types live in the shared crate; `crate::proto::v2` names them here.
use sapient_proto as proto;

use std::sync::Arc;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use clap::Parser;
use octo_arm_common::lifecycle::worth_reporting;
use octo_arm_common::{Ingestor, SessionConfig, SourceAllowlist, Stats};
use tokio::sync::mpsc;
use tracing::{error, info, warn};
use zenoh::bytes::Encoding;

use session::{Emit, PeerConfig, Relayed, Role, Stream};

/// Bounded uplink queue. Full means the pen is slower than the peer's feed; samples are
/// dropped loudly rather than stalling the session, because a late heartbeat gets this
/// node deregistered.
const UPLINK_QUEUE: usize = 1024;

/// Bounded relay queue. Full means the peer's session is slower than the bridge feeding it,
/// or down; samples are dropped and counted rather than held for later.
const RELAY_QUEUE: usize = 256;

#[derive(Parser, Debug, Clone)]
#[command(
    name = "sapient",
    about = "octo-pod arm: SAPIENT session (BSI Flex 335 v2.0) to native mTLS Zenoh publish"
)]
struct Args {
    /// The peer to dial, `host:port`. Mutually exclusive with --listen.
    #[arg(long, env = "SAPIENT_TARGET")]
    target: Option<String>,

    /// Port to listen on, for a peer that dials us. Implies the DMM role.
    #[arg(long, env = "SAPIENT_LISTEN")]
    listen: Option<u16>,

    /// `asm`, `dmm`, `client` for the client-facing interface of a SAPIENT message
    /// handler, or `auto` to settle asm or dmm from who registers first.
    #[arg(long, env = "SAPIENT_ROLE", default_value = "auto")]
    role: String,

    /// Feed label — the key chunk this peer's frames land under.
    #[arg(long, env = "SAPIENT_FEED", default_value = "sapient")]
    feed: String,

    /// Length-prefix byte order: `len32le`, `len32be`, or empty to detect it.
    #[arg(long, env = "SAPIENT_FRAMING", default_value = "")]
    framing: String,

    /// The ICD version this arm claims. It must be exactly what the peer runs.
    #[arg(long, env = "SAPIENT_ICD_VERSION", default_value = "BSI Flex 335 v2.0")]
    icd_version: String,

    /// Node type from the standard's enum, without the `NODE_TYPE_` prefix.
    #[arg(long, env = "SAPIENT_NODE_TYPE", default_value = "OTHER")]
    node_type: String,

    /// Heartbeat period, seconds. Declared in the registration and then honoured.
    #[arg(long, env = "SAPIENT_STATUS_INTERVAL_S", default_value_t = 5)]
    status_interval_s: u64,

    /// Capabilities to declare, `category:type[:value[:units]]`, comma-separated.
    #[arg(long, env = "SAPIENT_CAPABILITIES", default_value = "")]
    capabilities: String,

    /// Where the node id is kept, so a restart registers as the same node.
    #[arg(long, env = "SAPIENT_STATE_DIR")]
    state_dir: Option<String>,

    /// This arm's declared id, used for the default state directory.
    #[arg(long, env = "SAPIENT_ARM_ID", default_value = "sapient")]
    arm_id: String,

    /// Key prefix every publish lands under: `<prefix>/<label>/<peer-ip>`.
    #[arg(long, env = "SAPIENT_KEY_PREFIX", default_value = "raw/octo")]
    key_prefix: String,

    /// Zenoh router endpoint (`tls/...` = mTLS; `tcp/...` local testing).
    #[arg(long, env = "SAPIENT_ENDPOINT", default_value = "tls/127.0.0.1:7447")]
    endpoint: String,

    /// Arm's zenoh leaf certificate (PEM). Required for a `tls/` endpoint.
    #[arg(long, env = "SAPIENT_CERT")]
    cert: Option<String>,

    /// Arm's zenoh private key (PEM).
    #[arg(long, env = "SAPIENT_KEY")]
    key: Option<String>,

    /// Trust root for the pen router's server cert (PEM).
    #[arg(long, env = "SAPIENT_ROOT_CA")]
    root_ca: Option<String>,

    /// Comma-separated source-IP prefixes to accept. Empty = any.
    #[arg(long, env = "SAPIENT_ALLOW_SOURCES", default_value = "")]
    allow_sources: String,

    /// Key expression on the pen whose samples are relayed onto the peer's network. Empty
    /// disables the relay, which is the default: the arm then writes only its own session
    /// messages. `apply` sets it for an arm a bridge targets.
    #[arg(long, env = "SAPIENT_RELAY_KEY", default_value = "")]
    relay_key: String,

    /// Egress mode: the fabric keys this `sapient://` destination was released for,
    /// comma-separated. The arm relays their `downlink/...` pen keys, nothing else.
    #[arg(long, env = "SAPIENT_EGRESS_KEYS", default_value = "")]
    egress_keys: String,

    /// Egress mode: the declared allow list, literal `IP:PORT` entries. The target must be
    /// in it, or the arm opens nothing.
    #[arg(long, env = "SAPIENT_ALLOW_DEST", default_value = "")]
    allow_dest: String,

    /// Egress mode: where the local audit record of relayed sends is kept.
    #[arg(
        long,
        env = "SAPIENT_AUDIT_DIR",
        default_value = "/var/lib/octo-egress"
    )]
    audit_dir: String,

    /// Seconds between stats lines.
    #[arg(long, env = "SAPIENT_STATS_SECS", default_value_t = 60)]
    stats_secs: u64,

    /// Bound on the TCP connect and, separately, on nothing else: a peer that never
    /// answers must reach the reconnect loop rather than park for the life of the
    /// process.
    #[arg(long, env = "SAPIENT_CONNECT_TIMEOUT_S", default_value_t = 10)]
    connect_timeout_s: u64,

    /// Bound on a single socket write. Expiry ends the session rather than leaving a
    /// partial frame on a length-prefixed stream.
    #[arg(long, env = "SAPIENT_WRITE_TIMEOUT_S", default_value_t = 10)]
    write_timeout_s: u64,

    /// How long `role=auto` waits for the peer to speak before deciding it is the DMM
    /// and registering with it.
    #[arg(long, env = "SAPIENT_ROLE_PROBE_S", default_value_t = 3)]
    role_probe_s: u64,

    /// In the `client` role, seconds of silence from the handler before reconnecting; 0
    /// never reconnects on silence. A handler forwards only what its nodes send, so the
    /// default is long enough for a quiet one and short enough that a half-open connection
    /// is noticed.
    #[arg(long, env = "SAPIENT_CLIENT_IDLE_S", default_value_t = 300)]
    client_idle_s: u64,
}

#[tokio::main]
async fn main() -> Result<()> {
    octo_arm_common::init_tracing();
    let args = Args::parse();

    let role = Role::parse(&args.role)?;
    let framing = match args.framing.trim() {
        "" => None,
        s => Some(framing::ByteOrder::parse(s)?),
    };

    // One peer per arm, and the declaration decides which side of the socket we are on.
    // A listener that also dials would be two peers in one process with one identity.
    let listen = match (&args.target, args.listen) {
        (Some(_), Some(_)) => bail!("give --target or --listen, not both"),
        (None, None) => bail!(
            "give --target <host>:<port> to dial the peer, or --listen <port> for a peer \
             that dials us"
        ),
        (None, Some(port)) => {
            if matches!(role, Role::Asm | Role::Client) {
                bail!(
                    "--listen implies the DMM role (the peer registers with us), but \
                     --role {role} was given: an ASM and a client connect out"
                );
            }
            Some(port)
        }
        (Some(_), None) => None,
    };

    let egress_plan = match egress::plan(&egress::Declared {
        egress_keys: &args.egress_keys,
        relay_key: &args.relay_key,
        role,
        target: args.target.as_deref(),
        listen,
        allow_dest: &args.allow_dest,
    }) {
        Ok(p) => p,
        Err(why) => {
            error!(reason = %why, "sapient:// destination refused; opening nothing");
            if let Ok(hook) = egress::AuditHook::open(
                std::path::Path::new(&args.audit_dir),
                &args.arm_id,
                &format!("sapient://{}", args.target.as_deref().unwrap_or("")),
            ) {
                let _ = hook.event("refused", serde_json::json!({ "reason": why }));
            }
            std::process::exit(2);
        }
    };
    let audit = match &egress_plan {
        None => None,
        Some(p) => {
            let hook = egress::AuditHook::open(
                std::path::Path::new(&args.audit_dir),
                &args.arm_id,
                &format!("sapient://{}", p.dest),
            )
            .context("opening the egress audit record")?;
            let _ = hook.event(
                "start",
                serde_json::json!({ "keys": p.pen_keys, "dest": p.dest.to_string() }),
            );
            Some(hook)
        }
    };

    let state_dir = args
        .state_dir
        .clone()
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| identity::state_dir(&args.arm_id));
    let node_id = identity::node_id(&state_dir)?;

    let cfg = PeerConfig {
        label: args.feed.clone(),
        target: args.target.clone(),
        listen,
        role,
        framing,
        node_id: node_id.clone(),
        icd_version: args.icd_version.clone(),
        node_type: wire::node_type(&args.node_type)?,
        capabilities: wire::capabilities(&args.capabilities)?,
        status_interval: std::time::Duration::from_secs(args.status_interval_s.max(1)),
        connect_timeout: std::time::Duration::from_secs(args.connect_timeout_s.max(1)),
        write_timeout: std::time::Duration::from_secs(args.write_timeout_s.max(1)),
        role_probe: std::time::Duration::from_secs(args.role_probe_s),
        client_idle: std::time::Duration::from_secs(args.client_idle_s),
        mirror_egress: egress_plan.is_none(),
        audit: audit.clone(),
    };

    info!(
        node_id = %node_id,
        role = %cfg.role,
        peer = %cfg.endpoint(),
        icd_version = %cfg.icd_version,
        status_interval_s = args.status_interval_s,
        framing = %framing.map(|f| f.as_str()).unwrap_or("detect"),
        "sapient starting"
    );
    if matches!(cfg.role, Role::Asm | Role::Auto) {
        info!(
            node_id = %node_id,
            "this arm will register as a node with that peer, and will keep heartbeating \
             to stay registered"
        );
    }

    if !args.allow_sources.is_empty() {
        warn!(
            allow_sources = %args.allow_sources,
            "source allowlist is set — it gates INBOUND frames only, so the peer's address \
             must match a prefix here or its frames are dropped at the gate (this arm's own \
             egress and session records are never gated)"
        );
    }

    let session = SessionConfig {
        endpoint: args.endpoint.clone(),
        cert: args.cert.clone(),
        key: args.key.clone(),
        root_ca: args.root_ca.clone(),
    }
    .open()
    .await?;
    info!("zenoh session opened");
    let session = Arc::new(session);
    // The relay subscribes on the same session the gates publish through: one identity, so
    // what this arm may read is decided by the router against the certificate it writes
    // with.
    let relay_session = Arc::clone(&session);

    // Two gates over one publish path, differing in exactly one thing: the allowlist.
    // `SourceAllowlist` is an *ingress* control — it answers "did these bytes really come
    // from where we expected" — and only inbound frames are subject to that question.
    let ingress = Arc::new(Ingestor::new(
        session,
        args.key_prefix.clone(),
        SourceAllowlist::from_csv(&args.allow_sources),
    ));
    // The egress mirror and the lifecycle records are authored here. Everything that
    // happens before a socket exists keys on the 0.0.0.0 sentinel, which matches no
    // prefix an operator would set, so gating them would leave an unreachable peer
    // looking exactly like an arm nobody started.
    let arm_ingestor = Arc::new(Ingestor::sharing(&ingress, SourceAllowlist::default()));

    let (tx, rx) = mpsc::channel::<Emit>(UPLINK_QUEUE);
    let publisher = tokio::spawn(publish_task(rx, ingress.clone(), arm_ingestor, audit));
    let relay_key = args.relay_key.trim().to_string();
    let relay = if let Some(plan) = &egress_plan {
        let (rtx, rrx) = mpsc::channel::<Relayed>(RELAY_QUEUE);
        info!(
            keys = ?plan.pen_keys,
            dest = %plan.dest,
            "sapient:// destination: relaying released downlink keys to the controller, \
             with nothing written back onto the pen"
        );
        for key in &plan.pen_keys {
            tokio::spawn(relay_task(
                Arc::clone(&relay_session),
                key.clone(),
                rtx.clone(),
                Arc::clone(&ingress.stats),
                Some(Arc::clone(&plan.gate)),
            ));
        }
        Some(rrx)
    } else if relay_key.is_empty() {
        None
    } else {
        let (rtx, rrx) = mpsc::channel::<Relayed>(RELAY_QUEUE);
        warn!(
            key = %relay_key,
            "relay enabled — samples on this key that this arm's role may send will be \
             written to the SAPIENT network under its node id"
        );
        tokio::spawn(relay_task(
            relay_session,
            relay_key,
            rtx,
            Arc::clone(&ingress.stats),
            None,
        ));
        Some(rrx)
    };
    let peer = tokio::spawn(session::run_peer(
        cfg,
        tx,
        Arc::clone(&ingress.stats),
        relay,
    ));

    // Owns the session and publishes what both gates queue. Without it the arm captures
    // into the outbox and never sends any of it.
    let drain = ingress.spawn_drain();
    let stats = octo_arm_common::stats::spawn_stats_logger_with_depth(
        ingress.stats.clone(),
        args.stats_secs,
        Some(ingress.outbox.clone()),
    );

    tokio::signal::ctrl_c()
        .await
        .context("waiting for ctrl-c")?;
    info!("shutdown signal received");
    stats.abort();
    drain.abort();
    peer.abort();
    publisher.abort();
    Ok(())
}

/// Subscribe to the relay key and hand each sample to the session, stamped with when and
/// where it arrived. With a gate (egress mode), a sample on a key outside the declared set
/// is dropped and counted.
///
/// A failed subscribe is fatal to the relay and to nothing else: the arm keeps its session
/// and keeps capturing. A key this arm holds no read grant on does not fail here — the
/// router accepts the subscription and never feeds it — so the relay key and the grant are
/// generated together, from the bridge that targets this arm.
async fn relay_task(
    session: Arc<zenoh::Session>,
    key: String,
    sink: mpsc::Sender<Relayed>,
    stats: Arc<Stats>,
    gate: Option<Arc<octo_egress::gate::DownlinkGate>>,
) {
    let sub = match session.declare_subscriber(&key).await {
        Ok(s) => s,
        Err(e) => {
            error!(
                key = %key,
                error = %format!("{e}"),
                "relay subscribe failed — nothing will be relayed. The key expression is \
                 invalid or the session is gone"
            );
            return;
        }
    };
    info!(key = %key, "relay subscribed");
    let mut dropped: u64 = 0;
    while let Ok(sample) = sub.recv_async().await {
        let arrived_on = sample.key_expr().as_str().to_string();
        // Egress mode checks every sample against the declared keys again, whether or not
        // the pen ACL is right: the ACL is the outer gate, this the inner.
        if let Some(gate) = &gate {
            if !egress::permits_pen_key(gate, &arrived_on) {
                Stats::bump(&stats.dropped_invalid);
                continue;
            }
        }
        let item = Relayed {
            bytes: sample.payload().to_bytes().to_vec(),
            at: Instant::now(),
            key: arrived_on,
        };
        if sink.try_send(item).is_err() {
            dropped += 1;
            Stats::bump(&stats.dropped_egress);
            if worth_reporting(dropped) {
                warn!(
                    key = %key,
                    dropped,
                    "relay queue full or the session is down; dropping samples rather than \
                     holding them"
                );
            }
        }
    }
    warn!(key = %key, "relay subscription ended; nothing further will be relayed");
}

/// Sole owner of both gates. Keeping the publish here — off the connection task — is what
/// stops a slow pen from delaying a heartbeat.
///
/// An egress destination (`egress_audit` set) writes nothing to the pen: the controller's
/// frames are the return path, read by the session and not captured, and lifecycle records
/// go to the local audit file. Its pen grant is then read-only, like `egress-arm`'s.
async fn publish_task(
    mut rx: mpsc::Receiver<Emit>,
    ingress: Arc<Ingestor>,
    arm_ingestor: Arc<Ingestor>,
    egress_audit: Option<egress::AuditHook>,
) {
    while let Some(e) = rx.recv().await {
        if let Some(audit) = &egress_audit {
            match e.stream {
                Stream::Session => {
                    // The audit line merges an object's fields and drops anything else, so a
                    // record that is not a JSON object is kept as one field rather than lost.
                    let detail = match serde_json::from_slice(&e.bytes) {
                        Ok(v @ serde_json::Value::Object(_)) => v,
                        _ => serde_json::json!({ "record": String::from_utf8_lossy(&e.bytes) }),
                    };
                    if let Err(err) = audit.event("session", detail) {
                        warn!(error = %err, "session record not written to the audit file");
                    }
                }
                Stream::Data | Stream::Egress => Stats::bump(&ingress.stats.skipped),
            }
            continue;
        }
        match e.stream {
            Stream::Data => ingress.ingest(&e.label, &e.peer, &e.bytes).await,
            Stream::Egress => {
                arm_ingestor
                    .ingest(&format!("{}-egress", e.label), &e.peer, &e.bytes)
                    .await
            }
            Stream::Session => {
                arm_ingestor
                    .ingest_with_encoding(
                        &format!("{}-session", e.label),
                        &e.peer,
                        &e.bytes,
                        Encoding::APPLICATION_JSON,
                    )
                    .await
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;

    fn help_of(id: &str) -> String {
        Args::command()
            .get_arguments()
            .find(|a| a.get_id() == id)
            .and_then(|a| a.get_help())
            .map(|h| h.to_string())
            .unwrap_or_default()
    }

    /// `--help` is what an operator reads at the box. A stray line once opened the relay
    /// flag's help with the stats interval and left `--stats-secs` with no help at all.
    #[test]
    fn each_flags_help_describes_that_flag() {
        let relay = help_of("relay_key");
        assert!(relay.starts_with("Key expression on the pen"), "{relay}");
        assert!(!relay.contains("stats"), "{relay}");
        let stats = help_of("stats_secs");
        assert!(stats.contains("Seconds between stats lines"), "{stats:?}");
    }
}
