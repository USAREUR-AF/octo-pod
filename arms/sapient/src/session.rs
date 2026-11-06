// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The connection driver: reach the peer, settle the role, register, hold the session.
//!
//! Concurrency shape, and why:
//!
//! ```text
//!   ┌──────────────┐  try_send   ┌─────────────┐  write_all + timeout
//!   │ driver loop  │ ──────────► │ writer task │ ───────────────────► socket
//!   │ (this file)  │             └─────────────┘
//!   │              │  try_send   ┌─────────────┐
//!   │              │ ──────────► │ publisher   │ ───────────────────► pen
//!   └──────────────┘   (Emit)    │ (main.rs)   │
//! ```
//!
//! The driver loop **never awaits a socket write and never awaits a zenoh put**. Both are
//! bounded channels written with `try_send`. A peer that stops draining its receive
//! buffer parks `write_all` behind a zero window for as long as `tcp_retries2` allows
//! (~15 minutes on Linux), and a write awaited inside this loop would take the read path,
//! the heartbeat timer and shutdown down with it. Likewise a stalled pen must never delay
//! a heartbeat, or this node is deregistered by its peer.
//!
//! The writer task is the sole owner of the write half, so frames cannot interleave —
//! which matters on a length-prefixed stream, where a torn write desynchronises it
//! permanently. For the same reason a write is never cancelled mid-frame: it is bounded
//! by a timeout, and expiry ends the session instead of leaving half a frame on the wire.

use std::collections::hash_map::RandomState;
use std::fmt;
use std::hash::BuildHasher;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::mpsc;
use tokio::time::{interval, timeout, MissedTickBehavior};
use tracing::{error, info, warn};

use octo_arm_common::lifecycle::worth_reporting;
use octo_arm_common::outbox::DEFAULT_MAX_AGE;
use octo_arm_common::stats::Stats;

use crate::framing::{ByteOrder, FrameBuf};
use crate::wire;

// THE THREE TIMERS ARE SCALED DOWN UNDER `cfg(test)`, and only these three. A peer that
// goes silent, stalls a registration or never answers is exactly what the session driver
// exists to survive, and each of those takes tens of seconds to reach at field values —
// so at field values they would not be tested at all. The two builds can only diverge in
// how long a wait lasts, never in what happens at the end of one.
/// How long the ASM waits for `RegistrationAck` before giving up on this attempt.
#[cfg(not(test))]
const REGISTRATION_TIMEOUT: Duration = Duration::from_secs(20);
#[cfg(test)]
const REGISTRATION_TIMEOUT: Duration = Duration::from_millis(300);
/// Watchdog cadence — checks idle, writer health and the registration deadline.
#[cfg(not(test))]
const WATCHDOG: Duration = Duration::from_secs(1);
#[cfg(test)]
const WATCHDOG: Duration = Duration::from_millis(25);
/// A session must last this long before its reconnect backoff resets, so a peer that
/// accepts-then-immediately-drops still gets backed off.
const STABLE_SESSION: Duration = Duration::from_secs(60);
/// Silence is judged against this many heartbeat intervals. Three, because the standard's
/// own contract is one status report per interval: two missed reports is a peer with a
/// problem, and one is a scheduler.
const IDLE_INTERVALS: u32 = 3;
/// Floor under the idle window, for a peer that declared an absurdly short interval.
#[cfg(not(test))]
const IDLE_FLOOR: Duration = Duration::from_secs(10);
#[cfg(test)]
const IDLE_FLOOR: Duration = Duration::from_millis(400);
/// Bounded egress queue. Full means the peer is not draining.
const EGRESS_QUEUE: usize = 64;
/// Reconnect backoff bounds. Fixed rather than configurable: unlike a TAK server, a
/// SAPIENT peer is a sensor or controller on a LAN, and these are right for both.
const BACKOFF_MIN: Duration = Duration::from_secs(2);
const BACKOFF_MAX: Duration = Duration::from_secs(60);
/// Source stand-in for lifecycle events that happen before a socket exists. The pen key
/// needs an address chunk and a failed dial has none — `0.0.0.0` is not a peer, so
/// `<label>-session/0-0-0-0` reads unambiguously as "this arm, no peer yet".
const NO_PEER: SocketAddr = SocketAddr::new(IpAddr::V4(Ipv4Addr::UNSPECIFIED), 0);

/// Which side of the conversation this arm is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    /// The sensor side: connect, register, heartbeat, stream detections.
    Asm,
    /// The controller side: accept registrations and acknowledge them.
    Dmm,
    /// A client of a SAPIENT message handler: a service that sits between nodes and
    /// decision makers and forwards what it receives to the clients connected to it. The arm
    /// dials, sends nothing of its own — no registration, no heartbeat, no acknowledgement
    /// of anything forwarded to it — and captures everything. It may relay decision-side
    /// messages, each already addressed to the node it is for.
    Client,
    /// Settle it from who registers first.
    Auto,
}

impl Role {
    pub fn parse(s: &str) -> Result<Self> {
        Ok(match s.trim().to_ascii_lowercase().as_str() {
            "asm" => Self::Asm,
            "dmm" => Self::Dmm,
            "client" => Self::Client,
            "auto" => Self::Auto,
            other => bail!("unknown role `{other}` (asm|dmm|client|auto)"),
        })
    }
}

impl fmt::Display for Role {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Asm => "asm",
            Self::Dmm => "dmm",
            Self::Client => "client",
            Self::Auto => "auto",
        })
    }
}

/// Which pen stream a byte string belongs on. Each maps to its own label, so the key
/// grammar stays `<prefix>/<label>/<src-ip>` — unchanged from every other arm.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stream {
    /// Frames received from the peer, raw and undecoded.
    Data,
    /// Everything the arm wrote to the wire.
    Egress,
    /// Session lifecycle, as JSON.
    Session,
}

/// One thing to publish. The driver produces these; `main.rs` owns the gates.
#[derive(Debug)]
pub struct Emit {
    pub label: String,
    pub stream: Stream,
    pub peer: SocketAddr,
    pub bytes: Vec<u8>,
}

/// The peer this arm holds a session with, and the identity it presents.
#[derive(Debug, Clone)]
pub struct PeerConfig {
    pub label: String,
    /// `host:port` to dial. Exactly one of this and `listen` is set.
    pub target: Option<String>,
    /// Port to accept on.
    pub listen: Option<u16>,
    pub role: Role,
    /// `None` detects the length-prefix order from the first frame.
    pub framing: Option<ByteOrder>,

    pub node_id: String,
    pub icd_version: String,
    pub node_type: i32,
    pub capabilities: Vec<crate::wire::Capability>,
    pub status_interval: Duration,

    pub connect_timeout: Duration,
    pub write_timeout: Duration,
    pub role_probe: Duration,
    /// Silence a `client` session tolerates before it reconnects. Zero never reconnects on
    /// silence. A client has no heartbeat contract to judge by: a handler forwards only
    /// what its nodes send, so this is the only way a half-open connection to a quiet one
    /// is ever noticed.
    pub client_idle: Duration,
    /// Mirror everything written to the wire onto the pen as `<label>-egress`. Off for a
    /// `sapient://` egress destination: what it sends came down from the fabric, and the
    /// pen is a way back up.
    pub mirror_egress: bool,
    /// The local record of relayed sends, for a `sapient://` egress destination.
    pub audit: Option<crate::egress::AuditHook>,
}

impl PeerConfig {
    pub fn endpoint(&self) -> String {
        match (&self.target, self.listen) {
            (Some(t), _) => format!("dial {t}"),
            (None, Some(p)) => format!("listen :{p}"),
            _ => "unconfigured".to_string(),
        }
    }

    /// How long silence may last. The peer's own heartbeat contract decides it, so a
    /// slower interval buys a proportionally longer window rather than a flapping link.
    fn idle_window(&self) -> Duration {
        (self.status_interval * IDLE_INTERVALS).max(IDLE_FLOOR)
    }
}

/// One sample from the relay key, and when it arrived. The arrival time is what lets a
/// sample that waited out a reconnect be dropped rather than sent as a stale report.
#[derive(Debug)]
pub struct Relayed {
    pub bytes: Vec<u8>,
    pub at: Instant,
    /// The pen key it arrived on, for the audit record.
    pub key: String,
}

/// The relay's next sample, or never when there is no relay. A `select!` branch has to have
/// something to await even when its guard keeps it quiet.
async fn recv_relay(relay: &mut Option<&mut mpsc::Receiver<Relayed>>) -> Option<Relayed> {
    match relay {
        Some(rx) => rx.recv().await,
        None => std::future::pending().await,
    }
}

/// Reach the peer, run a session, reconnect forever. Never returns.
///
/// The relay receiver lives here rather than inside a session, so it survives a reconnect:
/// a translator publishing while the peer is away finds the arm ready when it returns, and
/// what it published meanwhile has aged out rather than piled up.
pub async fn run_peer(
    cfg: PeerConfig,
    tx: mpsc::Sender<Emit>,
    stats: Arc<Stats>,
    mut relay: Option<mpsc::Receiver<Relayed>>,
) {
    let mut backoff = BACKOFF_MIN;
    // Jitter entropy without a rand dependency. `RandomState` takes its SipHash key from
    // the OS once per process, so the spread differs per pod as well as per peer — the
    // property that matters when a whole fleet loses the same peer in the same second.
    let entropy = RandomState::new();
    let mut attempt: u64 = 0;
    loop {
        let started = Instant::now();
        match serve_once(&cfg, &tx, &stats, relay.as_mut()).await {
            Ok(()) => info!(peer = %cfg.label, "session closed by peer"),
            Err(e) => warn!(peer = %cfg.label, error = %format!("{e:#}"), "session ended"),
        }
        if started.elapsed() >= STABLE_SESSION {
            backoff = BACKOFF_MIN;
        }
        attempt += 1;
        let wait = reconnect_wait(backoff, &entropy, attempt);
        info!(peer = %cfg.label, wait_ms = wait.as_millis() as u64, "reconnecting");
        // The gap between sessions is itself a state an operator reads off the pen —
        // without it a peer in permanent backoff looks the same as one nobody configured.
        session_event(
            &tx,
            &cfg,
            None,
            "reconnecting",
            &format!("in {}ms", wait.as_millis()),
        );
        tokio::time::sleep(wait).await;
        backoff = (backoff * 2).min(BACKOFF_MAX);
    }
}

/// Backoff plus up to half of itself again, drawn from a per-process key. The spread has
/// to scale with the backoff — a flat cap is nothing next to a 60s tail, and the tail is
/// exactly where a fleet reconnect storm lands.
fn reconnect_wait(backoff: Duration, entropy: &RandomState, attempt: u64) -> Duration {
    let span_ms = (backoff.as_millis() as u64) / 2;
    backoff + Duration::from_millis(entropy.hash_one(attempt) % (span_ms + 1))
}

/// Publish a failure that happens before `drive` is reached, then propagate it. Without
/// this a wrong address leaves the pen unable to tell a refused peer from an arm that was
/// never started.
fn step<T>(
    tx: &mpsc::Sender<Emit>,
    cfg: &PeerConfig,
    peer: Option<SocketAddr>,
    result: Result<T>,
) -> Result<T> {
    if let Err(e) = &result {
        session_event(tx, cfg, peer, "dial-failed", &format!("{e:#}"));
    }
    result
}

async fn serve_once(
    cfg: &PeerConfig,
    tx: &mpsc::Sender<Emit>,
    stats: &Arc<Stats>,
    relay: Option<&mut mpsc::Receiver<Relayed>>,
) -> Result<()> {
    match (&cfg.target, cfg.listen) {
        (Some(target), _) => {
            session_event(tx, cfg, None, "dialing", target);
            let connected = match timeout(cfg.connect_timeout, TcpStream::connect(target)).await {
                Err(_) => Err(anyhow!(
                    "connect to {target} timed out after {:?}",
                    cfg.connect_timeout
                )),
                Ok(r) => r.with_context(|| format!("connecting to {target}")),
            };
            let stream = step(tx, cfg, None, connected)?;
            let peer = step(tx, cfg, None, stream.peer_addr().context("peer address"))?;
            let _ = stream.set_nodelay(true);
            info!(peer = %cfg.label, addr = %peer, "connected");
            drive(stream, cfg, peer, tx, stats, relay).await
        }
        (None, Some(port)) => {
            let bind = format!("0.0.0.0:{port}");
            session_event(tx, cfg, None, "listening", &bind);
            let listener = step(
                tx,
                cfg,
                None,
                TcpListener::bind(&bind)
                    .await
                    .with_context(|| format!("binding {bind}")),
            )?;
            let (stream, peer) = step(
                tx,
                cfg,
                None,
                listener.accept().await.context("accepting a peer"),
            )?;
            let _ = stream.set_nodelay(true);
            info!(peer = %cfg.label, addr = %peer, "peer connected");
            drive(stream, cfg, peer, tx, stats, relay).await
        }
        _ => bail!("peer has neither a target to dial nor a port to listen on"),
    }
}

/// What the session is doing, which is not the same question as which role it plays.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    /// `role=auto`, waiting to see whether the peer registers with us.
    Probing,
    /// We sent `Registration` and are waiting for the acknowledgement.
    Registering,
    /// Accepted. The ASM heartbeats; the DMM answers.
    Registered,
}

async fn drive<S>(
    stream: S,
    cfg: &PeerConfig,
    peer: SocketAddr,
    tx: &mpsc::Sender<Emit>,
    stats: &Arc<Stats>,
    mut relay: Option<&mut mpsc::Receiver<Relayed>>,
) -> Result<()>
where
    S: AsyncRead + AsyncWrite + Send + Unpin + 'static,
{
    let (mut rd, wr) = tokio::io::split(stream);
    let (wtx, wrx) = mpsc::channel::<Vec<u8>>(EGRESS_QUEUE);
    let writer = tokio::spawn(writer_task(
        wr,
        wrx,
        cfg.write_timeout,
        cfg.label.clone(),
        Arc::clone(stats),
    ));

    session_event(tx, cfg, Some(peer), "connected", &cfg.endpoint());

    let mut fb = FrameBuf::new(cfg.framing);
    let mut buf = vec![0u8; 64 * 1024];
    let mut last_rx = Instant::now();
    // What this arm writes with. The ASM writes first, before any frame has arrived to
    // learn the order from, so it starts with the octo-pod default and — once, if no
    // acknowledgement comes — tries the other way round.
    let mut write_order = cfg.framing.unwrap_or(ByteOrder::Le);
    let mut order_retried = cfg.framing.is_some();
    let mut announced_order = false;
    let mut streamed_unregistered = false;
    // The sensor that registered with us, when we are the controller side: a relayed
    // direction is addressed to it.
    let mut registered_peer: Option<String> = None;
    let mut relay_dropped: u64 = 0;
    let mut audit_errors: u64 = 0;

    // `auto` starts by listening; a declared role starts by acting on it. A client has
    // nothing to settle and nothing to say, so it is ready as soon as it is connected.
    let (mut role, mut state, since) = match cfg.role {
        Role::Auto => (Role::Auto, State::Probing, Instant::now()),
        Role::Dmm => (Role::Dmm, State::Probing, Instant::now()),
        Role::Client => {
            session_event(
                tx,
                cfg,
                Some(peer),
                "client-ready",
                "receiving what the handler forwards; sending nothing of its own",
            );
            (Role::Client, State::Registered, Instant::now())
        }
        Role::Asm => {
            register(cfg, tx, &wtx, peer, write_order)?;
            (Role::Asm, State::Registering, Instant::now())
        }
    };
    let mut since = since;

    let mut heartbeat = interval(cfg.status_interval);
    heartbeat.set_missed_tick_behavior(MissedTickBehavior::Delay);
    let mut watchdog = interval(WATCHDOG);
    watchdog.set_missed_tick_behavior(MissedTickBehavior::Delay);

    let outcome = loop {
        tokio::select! {
            read = rd.read(&mut buf) => {
                let n = match read {
                    Ok(0) => break Ok(()),
                    Ok(n) => n,
                    Err(e) => break Err(e).context("read from peer"),
                };
                last_rx = Instant::now();
                fb.extend(&buf[..n]);
                loop {
                    let frame = match fb.next_frame(&wire::decodes) {
                        Ok(Some(f)) => f,
                        Ok(None) => break,
                        Err(e) => return finish(Err(e), writer, wtx, tx, cfg, peer),
                    };
                    // The order is settled by the first frame we can read. Say so on the
                    // pen: it is a fact about the peer that nothing else reports, and the
                    // operator never had to supply it.
                    if !announced_order {
                        if let Some(o) = fb.order() {
                            announced_order = true;
                            if !fb.is_pinned() {
                                session_event(tx, cfg, Some(peer), "framing-detected", o.as_str());
                                // Reply the way the peer speaks, not the way we guessed.
                                write_order = o;
                            }
                        }
                    }

                    let msg = wire::decode(&frame);
                    emit(tx, &cfg.label, Stream::Data, peer, frame);

                    let Some(msg) = msg else { continue };
                    if wire::is_registration(&msg) {
                        registered_peer = wire::node_id_of(&msg);
                    }
                    match on_message(
                        &msg, cfg, tx, &wtx, peer, &mut role, &mut state, write_order,
                        &mut streamed_unregistered,
                    ) {
                        Ok(true) => since = Instant::now(),
                        Ok(false) => {}
                        Err(e) => return finish(Err(e), writer, wtx, tx, cfg, peer),
                    }
                }
            }

            // THE RELAY, and the only path by which anything in the pen reaches this
            // network. Offered only once registered — before that there is no session to
            // speak in — so a sample that arrives earlier waits, and one that waited past
            // the outbox's age is dropped rather than delivered as a stale report.
            relayed = recv_relay(&mut relay), if relay.is_some() && state == State::Registered => {
                let Some(item) = relayed else {
                    // The bridge is gone. The session is still good for capture.
                    relay = None;
                    continue;
                };
                if item.at.elapsed() > DEFAULT_MAX_AGE {
                    Stats::bump(&stats.dropped_egress);
                    continue;
                }
                match wire::relay_for_role(&item.bytes, role, &cfg.node_id, registered_peer.as_deref()) {
                    Ok(msg) => {
                        if let Err(e) = send(&wtx, tx, cfg, peer, wire::encode(&msg, write_order)) {
                            break Err(e);
                        }
                        if let Some(audit) = &cfg.audit {
                            if let Err(e) = audit.sent(&item.key, &item.bytes) {
                                audit_errors += 1;
                                if worth_reporting(audit_errors) {
                                    warn!(error = %e, audit_errors, "audit line not written; the send stands");
                                }
                            }
                        }
                    }
                    Err(reason) => {
                        relay_dropped += 1;
                        Stats::bump(&stats.dropped_invalid);
                        // Said on the first, then sparingly: a translator emitting the wrong
                        // thing at rate must not turn the pen into its log.
                        if worth_reporting(relay_dropped) {
                            session_event(
                                tx, cfg, Some(peer), "relay-dropped",
                                &format!("{reason} ({relay_dropped} refused so far)"),
                            );
                        }
                    }
                }
            }

            // Only an accepted ASM heartbeats: before acceptance there is nothing to
            // report about, and a DMM answers rather than announces.
            _ = heartbeat.tick(), if role == Role::Asm && state == State::Registered => {
                let msg = wire::status_report(&cfg.node_id);
                if let Err(e) = send(&wtx, tx, cfg, peer, wire::encode(&msg, write_order)) {
                    break Err(e);
                }
            }

            _ = watchdog.tick() => {
                if writer.is_finished() {
                    break Err(anyhow!("writer task stopped — peer stalled or write failed"));
                }
                match state {
                    State::Probing if role == Role::Auto && since.elapsed() > cfg.role_probe => {
                        // Nobody registered with us, so we are the sensor side.
                        role = Role::Asm;
                        session_event(tx, cfg, Some(peer), "role-detected", "asm");
                        info!(peer = %cfg.label, "peer did not register with us; registering as an ASM");
                        if let Err(e) = register(cfg, tx, &wtx, peer, write_order) { break Err(e); }
                        state = State::Registering;
                        since = Instant::now();
                    }
                    State::Registering if since.elapsed() > REGISTRATION_TIMEOUT => {
                        if !order_retried {
                            // The most likely reason a well-formed registration is
                            // ignored: the peer could not read its length prefix. Flip
                            // once and say so, rather than back off forever against a
                            // peer that is listening perfectly well.
                            order_retried = true;
                            write_order = match write_order {
                                ByteOrder::Le => ByteOrder::Be,
                                ByteOrder::Be => ByteOrder::Le,
                            };
                            session_event(tx, cfg, Some(peer), "framing-retry", write_order.as_str());
                            warn!(
                                peer = %cfg.label, order = write_order.as_str(),
                                "no registration acknowledgement; retrying with the other byte order"
                            );
                            if let Err(e) = register(cfg, tx, &wtx, peer, write_order) { break Err(e); }
                            since = Instant::now();
                        } else {
                            break Err(anyhow!(
                                "no RegistrationAck within {REGISTRATION_TIMEOUT:?}, in either byte order"
                            ));
                        }
                    }
                    _ => {}
                }
                if role == Role::Client {
                    if !cfg.client_idle.is_zero() && last_rx.elapsed() > cfg.client_idle {
                        break Err(anyhow!(
                            "nothing forwarded by the handler for {:?}", cfg.client_idle
                        ));
                    }
                } else {
                    let window = cfg.idle_window();
                    if state == State::Registered && last_rx.elapsed() > window {
                        break Err(anyhow!(
                            "no data from peer for {window:?} ({} heartbeat intervals)", IDLE_INTERVALS
                        ));
                    }
                    // A declared DMM waits for the peer to register, and nothing else
                    // bounds that wait: the role probe is auto's and the registration
                    // deadline is the ASM's. Listening, the arm serves one connection at a
                    // time, so a peer that connects and stays silent would hold the only
                    // slot until TCP gave up on it. Data before registration does not extend
                    // this deadline.
                    if role == Role::Dmm && state == State::Probing && since.elapsed() > window {
                        break Err(anyhow!(
                            "peer connected but never registered within {window:?}"
                        ));
                    }
                }
            }
        }
    };

    finish(outcome, writer, wtx, tx, cfg, peer)
}

/// Close the session down in one place, so every exit publishes the same record.
fn finish(
    outcome: Result<()>,
    writer: tokio::task::JoinHandle<()>,
    wtx: mpsc::Sender<Vec<u8>>,
    tx: &mpsc::Sender<Emit>,
    cfg: &PeerConfig,
    peer: SocketAddr,
) -> Result<()> {
    drop(wtx);
    writer.abort();
    let detail = match &outcome {
        Ok(()) => "eof".to_string(),
        Err(e) => format!("{e:#}"),
    };
    session_event(tx, cfg, Some(peer), "closed", &detail);
    outcome
}

/// Act on one decoded message. Returns whether it advanced the session's state, which is
/// what resets the deadline the watchdog measures.
#[allow(clippy::too_many_arguments)]
fn on_message(
    msg: &crate::proto::v2::SapientMessage,
    cfg: &PeerConfig,
    tx: &mpsc::Sender<Emit>,
    wtx: &mpsc::Sender<Vec<u8>>,
    peer: SocketAddr,
    role: &mut Role,
    state: &mut State,
    write_order: ByteOrder,
    streamed_unregistered: &mut bool,
) -> Result<bool> {
    // A client answers nothing. What a handler forwards — other nodes' registrations,
    // their acknowledgements, tasks addressed to them — is data for the pen, and a reply
    // from this arm would be a message the handler never asked it for.
    if *role == Role::Client {
        return Ok(false);
    }
    // A peer that registers with us is the sensor side, whatever we assumed.
    if wire::is_registration(msg) {
        if *role == Role::Auto || *role == Role::Dmm {
            if *role == Role::Auto {
                *role = Role::Dmm;
                session_event(tx, cfg, Some(peer), "role-detected", "dmm");
            }
            if let Some(interval) = wire::declared_status_interval(msg) {
                session_event(
                    tx,
                    cfg,
                    Some(peer),
                    "peer-status-interval",
                    &format!("{:?}", interval),
                );
            }
            let ack = wire::registration_ack(&cfg.node_id, wire::node_id_of(msg), true);
            send(wtx, tx, cfg, peer, wire::encode(&ack, write_order))?;
            *state = State::Registered;
            session_event(tx, cfg, Some(peer), "registered", "acknowledged the peer");
            return Ok(true);
        }
        // An ASM receiving a registration is a peer confused about its own role. Publish
        // the frame (already done) and carry on rather than tearing down a working link.
        warn!(peer = %cfg.label, "peer sent a Registration to an ASM; ignoring");
        return Ok(false);
    }

    if let Some((accepted, reasons)) = wire::registration_ack_of(msg) {
        if accepted {
            *state = State::Registered;
            session_event(tx, cfg, Some(peer), "registered", "accepted");
            info!(peer = %cfg.label, node_id = %cfg.node_id, "registered with the peer");
        } else {
            // The peer's own words, verbatim: `icd_version` mismatches and missing
            // mandatory fields both land here, and paraphrasing them would cost the
            // operator the one message that says what to change.
            let detail = if reasons.is_empty() {
                "no reason given".to_string()
            } else {
                reasons.join("; ")
            };
            session_event(tx, cfg, Some(peer), "registration-rejected", &detail);
            bail!("registration rejected: {detail}");
        }
        return Ok(true);
    }

    if let Some(task_id) = wire::task_id_of(msg) {
        session_event(tx, cfg, Some(peer), "task-received", &task_id);
        // This arm carries no sensor of its own to task, so the honest answer is a
        // rejection with a reason. Silence would leave the peer waiting for an
        // acknowledgement the standard says it will get.
        let ack = wire::task_ack(
            &cfg.node_id,
            &task_id,
            "this node relays a feed and accepts no tasking",
        );
        send(wtx, tx, cfg, peer, wire::encode(&ack, write_order))?;
        return Ok(true);
    }

    // Anything else is data. An ASM that is streaming before we registered is worth
    // saying once: it means the peer does not require registration at all, and this
    // endpoint could have been a plain capture arm.
    if *state != State::Registered && !*streamed_unregistered {
        *streamed_unregistered = true;
        session_event(
            tx,
            cfg,
            Some(peer),
            "peer-streams-unregistered",
            wire::content_name(msg),
        );
    }
    Ok(false)
}

/// Send this arm's registration.
fn register(
    cfg: &PeerConfig,
    tx: &mpsc::Sender<Emit>,
    wtx: &mpsc::Sender<Vec<u8>>,
    peer: SocketAddr,
    order: ByteOrder,
) -> Result<()> {
    let msg = wire::registration(cfg);
    send(wtx, tx, cfg, peer, wire::encode(&msg, order))?;
    session_event(tx, cfg, Some(peer), "registering", &cfg.icd_version);
    Ok(())
}

/// Enqueue bytes for the wire, mirroring them to the pen first unless the arm is an egress
/// destination (`mirror_egress` off).
///
/// The mirror is published *before* the write is enqueued: the record of what the arm put
/// on the wire must not be the thing that gets lost.
fn send(
    wtx: &mpsc::Sender<Vec<u8>>,
    tx: &mpsc::Sender<Emit>,
    cfg: &PeerConfig,
    peer: SocketAddr,
    bytes: Vec<u8>,
) -> Result<()> {
    if cfg.mirror_egress {
        emit(tx, &cfg.label, Stream::Egress, peer, bytes.clone());
    }
    wtx.try_send(bytes).map_err(|e| match e {
        mpsc::error::TrySendError::Full(_) => {
            anyhow!("egress queue full ({EGRESS_QUEUE}) — peer is not draining")
        }
        mpsc::error::TrySendError::Closed(_) => anyhow!("writer task is gone"),
    })
}

async fn writer_task<W>(
    mut w: W,
    mut rx: mpsc::Receiver<Vec<u8>>,
    write_timeout: Duration,
    label: String,
    stats: Arc<Stats>,
) where
    W: AsyncWrite + Unpin,
{
    while let Some(bytes) = rx.recv().await {
        match timeout(write_timeout, w.write_all(&bytes)).await {
            Err(_) => {
                Stats::bump(&stats.send_errors);
                error!(peer = %label, "write timed out — peer stalled; ending session");
                return;
            }
            Ok(Err(e)) => {
                Stats::bump(&stats.send_errors);
                error!(peer = %label, error = %e, "write failed; ending session");
                return;
            }
            // Counted here rather than where the frame was queued: this is the point the
            // bytes actually left for the peer's network, and the queue is bounded, so
            // the two numbers differ exactly by what was dropped.
            Ok(Ok(())) => Stats::bump(&stats.sent),
        }
        if timeout(write_timeout, w.flush()).await.is_err() {
            error!(peer = %label, "flush timed out — peer stalled; ending session");
            return;
        }
    }
}

/// Non-blocking publish. A full uplink queue drops the sample loudly rather than
/// back-pressuring into the session; a closed one means the publisher is gone, which is
/// reported at `error` rather than passed over.
fn emit(tx: &mpsc::Sender<Emit>, label: &str, stream: Stream, peer: SocketAddr, bytes: Vec<u8>) {
    let record = Emit {
        label: label.to_string(),
        stream,
        peer,
        bytes,
    };
    match tx.try_send(record) {
        Ok(()) => {}
        Err(mpsc::error::TrySendError::Full(dropped)) => {
            warn!(
                peer = %dropped.label,
                stream = ?stream,
                bytes = dropped.bytes.len(),
                "uplink queue full; dropping sample (session liveness takes priority)"
            );
        }
        Err(mpsc::error::TrySendError::Closed(dropped)) => {
            error!(
                peer = %dropped.label,
                stream = ?stream,
                "uplink queue closed — the publisher task is gone; captured frames are no \
                 longer reaching the pen"
            );
        }
    }
}

/// Publish a lifecycle transition. `peer` is `None` for everything that happens before
/// the socket exists — a dial that fails has no peer address, and publishing nothing at
/// all is precisely the silence this arm is built to avoid.
fn session_event(
    tx: &mpsc::Sender<Emit>,
    cfg: &PeerConfig,
    peer: Option<SocketAddr>,
    event: &str,
    detail: &str,
) {
    let peer_text = match peer {
        Some(p) => p.to_string(),
        None => "-".to_string(),
    };
    let json = format!(
        r#"{{"event":"{}","label":"{}","peer":"{}","endpoint":"{}","node_id":"{}","detail":"{}"}}"#,
        json_esc(event),
        json_esc(&cfg.label),
        peer_text,
        json_esc(&cfg.endpoint()),
        json_esc(&cfg.node_id),
        json_esc(detail),
    );
    emit(
        tx,
        &cfg.label,
        Stream::Session,
        peer.unwrap_or(NO_PEER),
        json.into_bytes(),
    );
}

fn json_esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push(' '),
            c => out.push(c),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `sent` counts what reached the peer's network, and it is counted in the writer
    /// rather than where the frame was queued — the queue is bounded, so the two numbers
    /// differ by exactly what was dropped, and only the writer knows which is which.
    #[tokio::test(flavor = "multi_thread", worker_threads = 1)]
    async fn a_frame_that_reaches_the_wire_is_counted_as_sent() {
        let stats = Arc::new(Stats::default());
        let (wtx, wrx) = mpsc::channel::<Vec<u8>>(4);
        let handle = tokio::spawn(writer_task(
            Vec::<u8>::new(),
            wrx,
            Duration::from_secs(5),
            "sapient".to_string(),
            Arc::clone(&stats),
        ));

        wtx.send(vec![0, 0, 0, 1, 7]).await.unwrap();
        wtx.send(vec![0, 0, 0, 1, 9]).await.unwrap();
        drop(wtx);
        handle.await.unwrap();

        assert_eq!(stats.sent.load(std::sync::atomic::Ordering::Relaxed), 2);
        assert_eq!(
            stats.send_errors.load(std::sync::atomic::Ordering::Relaxed),
            0
        );
    }

    fn cfg() -> PeerConfig {
        PeerConfig {
            label: "sapient".into(),
            target: Some("10.0.0.1:48000".into()),
            listen: None,
            role: Role::Auto,
            framing: None,
            node_id: "1c4a1f0e-0000-4000-8000-000000000000".into(),
            icd_version: "BSI Flex 335 v2.0".into(),
            node_type: 1,
            capabilities: vec![],
            status_interval: Duration::from_secs(5),
            connect_timeout: Duration::from_secs(10),
            write_timeout: Duration::from_secs(10),
            role_probe: Duration::from_secs(3),
            client_idle: Duration::ZERO,
            mirror_egress: true,
            audit: None,
        }
    }

    #[test]
    fn roles_parse_and_print() {
        assert_eq!(Role::parse("asm").unwrap(), Role::Asm);
        assert_eq!(Role::parse(" DMM ").unwrap(), Role::Dmm);
        assert_eq!(Role::parse("client").unwrap(), Role::Client);
        assert_eq!(Role::parse("auto").unwrap(), Role::Auto);
        assert_eq!(Role::Client.to_string(), "client");
        assert!(Role::parse("both").is_err());
        assert_eq!(Role::Asm.to_string(), "asm");
    }

    #[test]
    fn the_idle_window_follows_the_declared_heartbeat() {
        let mut c = cfg();
        c.status_interval = Duration::from_secs(30);
        assert_eq!(c.idle_window(), Duration::from_secs(90));
    }

    #[test]
    fn a_tiny_heartbeat_still_gets_a_usable_window() {
        // A peer that declares a very short interval must not have its link torn down by
        // a momentary hiccup; the floor is what keeps a fast heartbeat from becoming a
        // flapping one. Stated against the floor rather than a literal, because these
        // timers are scaled down under cfg(test) and a literal would be testing the
        // scaling instead of the rule.
        let mut c = cfg();
        c.status_interval = IDLE_FLOOR / (IDLE_INTERVALS * 2);
        assert_eq!(c.idle_window(), IDLE_FLOOR);
    }

    #[test]
    fn the_endpoint_says_which_side_of_the_socket_we_are() {
        assert!(cfg().endpoint().starts_with("dial "));
        let mut c = cfg();
        c.target = None;
        c.listen = Some(48000);
        assert_eq!(c.endpoint(), "listen :48000");
    }

    #[test]
    fn reconnect_wait_jitters_within_the_backoff_floor_and_half_again() {
        let entropy = RandomState::new();
        let base = Duration::from_secs(4);
        for attempt in 0..500 {
            let w = reconnect_wait(base, &entropy, attempt);
            assert!(w >= base, "{w:?} is below the floor");
            assert!(w <= base + base / 2, "{w:?} exceeds floor + half");
        }
    }

    #[test]
    fn reconnect_wait_survives_a_sub_millisecond_backoff() {
        // span_ms is 0 there, and a modulus of zero panics.
        let entropy = RandomState::new();
        let w = reconnect_wait(Duration::from_micros(500), &entropy, 1);
        assert!(w >= Duration::from_micros(500));
    }

    #[test]
    fn json_escaping_survives_quotes_and_newlines() {
        let out = json_esc("a\"b\\c\nd\te");
        assert!(!out.contains('\n'));
        assert!(out.contains("\\\""));
        assert!(out.contains("\\\\"));
    }

    // ---------------------------------------------------------------------------
    // The driver against a real socket.
    //
    // Everything above tests a rule in isolation. These test the thing the rules exist
    // for: a peer that accepts, refuses, goes quiet, frames the other way round, speaks
    // first, or was never there at all. The fake peer is a plain TCP listener on
    // loopback, and the arm is driven through `serve_once` exactly as `run_peer` drives
    // it — one session, then back to the caller.
    //
    // No zenoh here: the driver hands every sample to a channel, so what would reach the
    // pen is what these read back.
    // ---------------------------------------------------------------------------

    use crate::proto::v2::SapientMessage;
    use tokio::net::TcpStream;

    /// A socket nobody is listening on yet, and the address to dial it.
    async fn fake_peer() -> (TcpListener, String) {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = l.local_addr().unwrap().to_string();
        (l, addr)
    }

    /// Read one length-prefixed message the way a peer would.
    async fn read_msg(s: &mut TcpStream, order: ByteOrder) -> Option<SapientMessage> {
        let mut hdr = [0u8; 4];
        s.read_exact(&mut hdr).await.ok()?;
        let len = match order {
            ByteOrder::Le => u32::from_le_bytes(hdr),
            ByteOrder::Be => u32::from_be_bytes(hdr),
        } as usize;
        let mut body = vec![0u8; len];
        s.read_exact(&mut body).await.ok()?;
        wire::decode(&body)
    }

    async fn write_msg(s: &mut TcpStream, msg: &SapientMessage, order: ByteOrder) {
        s.write_all(&wire::encode(msg, order)).await.unwrap();
    }

    /// Run one session against a peer, bounded so a hang fails the test instead of it.
    async fn run_session(cfg: &PeerConfig) -> (Result<()>, Vec<Emit>) {
        let (tx, mut rx) = mpsc::channel::<Emit>(256);
        let stats = Arc::new(Stats::default());
        let out = tokio::time::timeout(Duration::from_secs(5), serve_once(cfg, &tx, &stats, None))
            .await
            .expect("the session should end on its own");
        drop(tx);
        let mut emits = Vec::new();
        while let Ok(e) = rx.try_recv() {
            emits.push(e);
        }
        (out, emits)
    }

    /// Run one session with relay samples already waiting, and return what it published
    /// and counted.
    async fn run_session_relaying(
        cfg: &PeerConfig,
        samples: Vec<Relayed>,
    ) -> (Vec<Emit>, Arc<Stats>) {
        let (tx, mut rx) = mpsc::channel::<Emit>(256);
        let stats = Arc::new(Stats::default());
        let (rtx, mut rrx) = mpsc::channel::<Relayed>(16);
        for sample in samples {
            rtx.try_send(sample).unwrap();
        }
        let _ = tokio::time::timeout(
            Duration::from_secs(5),
            serve_once(cfg, &tx, &stats, Some(&mut rrx)),
        )
        .await
        .expect("the session should end on its own");
        drop(tx);
        let mut emits = Vec::new();
        while let Ok(e) = rx.try_recv() {
            emits.push(e);
        }
        (emits, stats)
    }

    fn relay_sample(content: crate::proto::v2::sapient_message::Content, age: Duration) -> Relayed {
        use prost::Message;
        let msg = SapientMessage {
            timestamp: None,
            node_id: Some("translator".into()),
            destination_id: None,
            content: Some(content),
            additional_information: None,
        };
        Relayed {
            bytes: msg.encode_to_vec(),
            at: Instant::now() - age,
            key: "downlink/aa11/sapient/radar1".into(),
        }
    }

    /// The relay end to end as the sensor side: a report goes out under the arm's own node
    /// id once registered, a registration from the translator is refused, and a report
    /// that waited too long is dropped rather than sent late.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_registered_sensor_side_relays_a_report_under_its_own_node_id() {
        use crate::proto::v2::sapient_message::Content as C;
        use crate::proto::v2::{DetectionReport, Registration};
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            let reg = read_msg(&mut s, ByteOrder::Le)
                .await
                .expect("a registration");
            let ack = wire::registration_ack("dmm", reg.node_id.clone(), true);
            write_msg(&mut s, &ack, ByteOrder::Le).await;
            loop {
                let m = read_msg(&mut s, ByteOrder::Le)
                    .await
                    .expect("the relayed report");
                if matches!(m.content, Some(C::DetectionReport(_))) {
                    return (reg.node_id, m);
                }
            }
        });
        let report = |id: &str| {
            C::DetectionReport(DetectionReport {
                id: Some(id.into()),
                ..Default::default()
            })
        };
        let samples = vec![
            relay_sample(report("stale"), Duration::from_secs(5)),
            relay_sample(C::Registration(Registration::default()), Duration::ZERO),
            relay_sample(report("fresh"), Duration::ZERO),
        ];

        let (emits, stats) = run_session_relaying(&dialing(addr, Role::Asm), samples).await;
        let (registered_as, relayed) = peer.await.unwrap();

        assert_eq!(relayed.node_id, registered_as, "it goes out as this node");
        let Some(C::DetectionReport(d)) = &relayed.content else {
            unreachable!()
        };
        assert_eq!(
            d.id.as_deref(),
            Some("fresh"),
            "the stale report must not be sent"
        );
        assert_eq!(
            stats
                .dropped_egress
                .load(std::sync::atomic::Ordering::Relaxed),
            1
        );
        assert_eq!(
            stats
                .dropped_invalid
                .load(std::sync::atomic::Ordering::Relaxed),
            1
        );
        assert!(saw(&emits, "relay-dropped"), "{:?}", events(&emits));
        assert!(
            written(&emits)
                .iter()
                .any(|m| matches!(m.content, Some(C::DetectionReport(_)))),
            "the relayed report is mirrored like everything else the arm writes"
        );
    }

    /// As the controller side: a task goes out addressed to the sensor that registered, and
    /// a detection report — which a controller does not send — is refused.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_registered_controller_side_relays_a_task_to_its_sensor() {
        use crate::proto::v2::sapient_message::Content as C;
        use crate::proto::v2::{DetectionReport, Task};
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            let mut sensor = cfg();
            sensor.node_id = "5f0b0d3a-0000-4000-8000-000000000002".into();
            sensor.status_interval = Duration::from_secs(30);
            write_msg(&mut s, &wire::registration(&sensor), ByteOrder::Le).await;
            loop {
                let m = read_msg(&mut s, ByteOrder::Le)
                    .await
                    .expect("the relayed task");
                if matches!(m.content, Some(C::Task(_))) {
                    return m;
                }
            }
        });
        let samples = vec![
            relay_sample(
                C::DetectionReport(DetectionReport::default()),
                Duration::ZERO,
            ),
            relay_sample(
                C::Task(Task {
                    task_id: Some("01J0000000000000000000TASK".into()),
                    ..Default::default()
                }),
                Duration::ZERO,
            ),
        ];

        let cfg_under_test = dialing(addr, Role::Auto);
        let arm_node = cfg_under_test.node_id.clone();
        let (emits, stats) = run_session_relaying(&cfg_under_test, samples).await;
        let task = peer.await.unwrap();

        assert_eq!(
            task.destination_id.as_deref(),
            Some("5f0b0d3a-0000-4000-8000-000000000002"),
            "a task is addressed to the sensor that registered"
        );
        assert_eq!(task.node_id.as_deref(), Some(arm_node.as_str()));
        assert_eq!(
            stats
                .dropped_invalid
                .load(std::sync::atomic::Ordering::Relaxed),
            1
        );
        assert!(saw(&emits, "relay-dropped"), "{:?}", events(&emits));
    }

    /// A client of a message handler: what the handler forwards — a node's registration, a
    /// task addressed to that node, a detection — is captured, and nothing is written back.
    /// A relayed task that names its node goes out as it was addressed; one that names no
    /// node is refused, because a handler serves many.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_client_captures_what_is_forwarded_answers_nothing_and_relays_addressed_tasks() {
        use crate::proto::v2::sapient_message::Content as C;
        use crate::proto::v2::{DetectionReport, Task};
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            let mut node = cfg();
            node.node_id = "5f0b0d3a-0000-4000-8000-000000000003".into();
            write_msg(&mut s, &wire::registration(&node), ByteOrder::Le).await;
            let mut task = wire::registration_ack("dmm", Some(node.node_id.clone()), true);
            task.content = Some(C::Task(Task {
                task_id: Some("01J0000000000000000000FWDT".into()),
                ..Default::default()
            }));
            write_msg(&mut s, &task, ByteOrder::Le).await;
            let mut det = wire::registration_ack("x", None, true);
            det.node_id = Some(node.node_id.clone());
            det.content = Some(C::DetectionReport(DetectionReport::default()));
            write_msg(&mut s, &det, ByteOrder::Le).await;
            // Whatever the arm writes. The only thing it may send is the addressed task.
            tokio::time::timeout(Duration::from_millis(800), read_msg(&mut s, ByteOrder::Le))
                .await
                .ok()
                .flatten()
        });
        let addressed = SapientMessage {
            timestamp: None,
            node_id: Some("translator".into()),
            destination_id: Some("5f0b0d3a-0000-4000-8000-000000000003".into()),
            content: Some(C::Task(Task {
                task_id: Some("01J0000000000000000000RELT".into()),
                ..Default::default()
            })),
            additional_information: None,
        };
        let samples = vec![
            relay_sample(
                C::Task(Task {
                    task_id: Some("01J0000000000000000000NODE".into()),
                    ..Default::default()
                }),
                Duration::ZERO,
            ),
            Relayed {
                bytes: prost::Message::encode_to_vec(&addressed),
                at: Instant::now(),
                key: "downlink/aa11/sapient/radar1".into(),
            },
        ];

        let (emits, stats) = run_session_relaying(&dialing(addr, Role::Client), samples).await;
        let sent = peer.await.unwrap();

        let Some(C::Task(t)) = sent.as_ref().and_then(|m| m.content.clone()) else {
            panic!("the addressed task should be the arm's only write, got {sent:?}");
        };
        assert_eq!(t.task_id.as_deref(), Some("01J0000000000000000000RELT"));
        assert_eq!(
            sent.unwrap().destination_id.as_deref(),
            Some("5f0b0d3a-0000-4000-8000-000000000003"),
            "a client keeps the destination it was given"
        );
        let wrote = written(&emits);
        assert_eq!(
            wrote.len(),
            1,
            "no registration, ack or heartbeat: {wrote:?}"
        );
        assert_eq!(
            stats
                .dropped_invalid
                .load(std::sync::atomic::Ordering::Relaxed),
            1,
            "a task that names no node is refused"
        );
        let captured = emits.iter().filter(|e| e.stream == Stream::Data).count();
        assert_eq!(captured, 3, "everything forwarded is captured");
        assert!(saw(&emits, "client-ready"), "{:?}", events(&emits));
        assert!(!saw(&emits, "registered") && !saw(&emits, "task-received"));
    }

    /// A client with an idle bound reconnects when the handler forwards nothing.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_client_ends_a_silent_session_after_its_idle_bound() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (s, _) = l.accept().await.unwrap();
            tokio::time::sleep(Duration::from_secs(3)).await;
            drop(s);
        });
        let mut c = dialing(addr, Role::Client);
        c.client_idle = Duration::from_millis(200);
        let (out, emits) = run_session(&c).await;
        peer.abort();
        let err = format!(
            "{:#}",
            out.expect_err("silence past the bound ends the session")
        );
        assert!(err.contains("nothing forwarded"), "{err}");
        assert!(saw(&emits, "closed"));
    }

    /// The lifecycle records, as the JSON that would land on the pen.
    fn events(emits: &[Emit]) -> Vec<String> {
        emits
            .iter()
            .filter(|e| e.stream == Stream::Session)
            .map(|e| String::from_utf8_lossy(&e.bytes).to_string())
            .collect()
    }

    fn saw(emits: &[Emit], event: &str) -> bool {
        events(emits)
            .iter()
            .any(|e| e.contains(&format!("\"event\":\"{event}\"")))
    }

    /// What the arm actually wrote to the wire, decoded.
    fn written(emits: &[Emit]) -> Vec<SapientMessage> {
        emits
            .iter()
            .filter(|e| e.stream == Stream::Egress)
            .filter_map(|e| wire::decode(&e.bytes[4..]))
            .collect()
    }

    fn dialing(addr: String, role: Role) -> PeerConfig {
        let mut c = cfg();
        c.target = Some(addr);
        c.role = role;
        c.status_interval = Duration::from_millis(100);
        c.role_probe = Duration::from_millis(150);
        c
    }

    // Uses the production driver and receiver over real loopback TCP, with no Zenoh or
    // live lab dependency. The arm's registration now carries every mandatory field, so
    // both receiver policies accept it against the pinned reference rules. The driver's handling
    // of a refusal is covered separately by `a_refusal_carries_the_peers_own_reasons`.
    async fn against_receiver(strict: bool) {
        let (listener, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (socket, _) = listener.accept().await.unwrap();
            timeout(
                Duration::from_secs(1),
                sapient_receiver::session(
                    socket,
                    "00000000-0000-4000-8000-00000000aaaa",
                    "test",
                    strict,
                    Duration::from_millis(50),
                ),
            )
            .await
        });
        let (out, emits) = run_session(&dialing(addr, Role::Asm)).await;
        let receiver_end = peer.await.unwrap();
        let registration = written(&emits)
            .into_iter()
            .find(wire::is_registration)
            .expect("the real arm sends registration");
        assert!(registration.node_id.is_some());
        let heartbeat = written(&emits).iter().any(|m| {
            matches!(
                m.content,
                Some(crate::proto::v2::sapient_message::Content::StatusReport(_))
            )
        });
        // The reference-valid registration is accepted under either receiver policy.
        // Acceptance keeps the session open until the deadline and enables heartbeats.
        assert!(
            receiver_end.is_err(),
            "the receiver must remain connected until the test deadline: {receiver_end:?}; arm: {out:?}"
        );
        assert!(saw(&emits, "registered"));
        assert!(heartbeat, "acceptance enables the real arm's heartbeat");
        let received = emits
            .iter()
            .filter(|e| e.stream == Stream::Data)
            .filter_map(|e| wire::decode(&e.bytes))
            .filter(|m| {
                matches!(
                    m.content,
                    Some(crate::proto::v2::sapient_message::Content::StatusReport(_))
                )
            })
            .count();
        assert!(
            received >= 5,
            "session survives multiple idle windows; received {received}"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn receiver_lenient_accepts_current_arm_and_it_heartbeats() {
        against_receiver(false).await;
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn receiver_strict_accepts_the_conformant_arm() {
        against_receiver(true).await;
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn an_asm_registers_and_the_peers_acceptance_is_published() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            let reg = read_msg(&mut s, ByteOrder::Le)
                .await
                .expect("a registration");
            assert!(wire::is_registration(&reg), "the ASM registers first");
            let ack = wire::registration_ack("dmm", reg.node_id.clone(), true);
            write_msg(&mut s, &ack, ByteOrder::Le).await;
            // Close, so the session ends on EOF rather than waiting out the idle window.
        });

        let (out, emits) = run_session(&dialing(addr, Role::Asm)).await;
        peer.await.unwrap();
        assert!(out.is_ok(), "{out:?}");
        for event in [
            "dialing",
            "connected",
            "registering",
            "registered",
            "closed",
        ] {
            assert!(saw(&emits, event), "missing {event}: {:?}", events(&emits));
        }
        // What it sent is mirrored to the pen, and it is the registration.
        assert!(
            written(&emits).iter().any(wire::is_registration),
            "the registration must be mirrored"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_refusal_carries_the_peers_own_reasons() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            let reg = read_msg(&mut s, ByteOrder::Le)
                .await
                .expect("a registration");
            let mut ack = wire::registration_ack("dmm", reg.node_id.clone(), false);
            if let Some(crate::proto::v2::sapient_message::Content::RegistrationAck(inner)) =
                &mut ack.content
            {
                inner.ack_response_reason = vec![
                    "icd_version unsupported".into(),
                    "capabilities empty".into(),
                ];
            }
            write_msg(&mut s, &ack, ByteOrder::Le).await;
            tokio::time::sleep(Duration::from_millis(200)).await;
        });

        let (out, emits) = run_session(&dialing(addr, Role::Asm)).await;
        peer.abort();
        assert!(out.is_err(), "a refused registration ends the session");
        let rejected = events(&emits)
            .into_iter()
            .find(|e| e.contains("registration-rejected"))
            .expect("the refusal must reach the pen");
        // Verbatim: the peer's words are the only thing that says what to change.
        assert!(rejected.contains("icd_version unsupported"), "{rejected}");
        assert!(rejected.contains("capabilities empty"), "{rejected}");
        // And nothing was sent after the refusal.
        assert!(
            !written(&emits).iter().any(|m| matches!(
                m.content,
                Some(crate::proto::v2::sapient_message::Content::StatusReport(_))
            )),
            "a refused node must not heartbeat"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_refusal_without_reasons_still_says_it_was_refused() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            let reg = read_msg(&mut s, ByteOrder::Le)
                .await
                .expect("a registration");
            let ack = wire::registration_ack("dmm", reg.node_id.clone(), false);
            write_msg(&mut s, &ack, ByteOrder::Le).await;
            tokio::time::sleep(Duration::from_millis(200)).await;
        });
        let (out, emits) = run_session(&dialing(addr, Role::Asm)).await;
        peer.abort();
        let err = format!("{:#}", out.expect_err("a refusal ends the session"));
        assert!(err.contains("no reason given"), "{err}");
        assert!(saw(&emits, "registration-rejected"), "{:?}", events(&emits));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn an_asm_ignores_a_peer_that_also_tries_to_register_and_keeps_the_link() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            let reg = read_msg(&mut s, ByteOrder::Le)
                .await
                .expect("a registration");
            // A confused peer registers back instead of acknowledging.
            let mut confused = cfg();
            confused.node_id = "5f0b0d3a-0000-4000-8000-000000000002".into();
            write_msg(&mut s, &wire::registration(&confused), ByteOrder::Le).await;
            tokio::time::sleep(Duration::from_millis(100)).await;
            // Then behaves: the ASM must still be there to accept the acknowledgement.
            let ack = wire::registration_ack("dmm", reg.node_id.clone(), true);
            write_msg(&mut s, &ack, ByteOrder::Le).await;
        });
        let (out, emits) = run_session(&dialing(addr, Role::Asm)).await;
        peer.await.unwrap();
        assert!(out.is_ok(), "{out:?}");
        assert!(saw(&emits, "registered"), "{:?}", events(&emits));
        // It never acknowledged the stray registration: an ASM does not act as a DMM.
        assert!(
            !written(&emits)
                .iter()
                .any(|m| wire::registration_ack_of(m).is_some()),
            "an ASM sent a RegistrationAck"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn an_auto_arm_whose_peer_stays_silent_registers_as_the_sensor() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            // Say nothing; after the probe the arm must register with us.
            let reg = read_msg(&mut s, ByteOrder::Le)
                .await
                .expect("a registration");
            assert!(wire::is_registration(&reg));
            let ack = wire::registration_ack("dmm", reg.node_id.clone(), true);
            write_msg(&mut s, &ack, ByteOrder::Le).await;
        });
        let (_out, emits) = run_session(&dialing(addr, Role::Auto)).await;
        peer.await.unwrap();
        let role = events(&emits)
            .into_iter()
            .find(|e| e.contains("role-detected"))
            .expect("the role must be published");
        assert!(role.contains("asm"), "{role}");
        assert!(saw(&emits, "registered"));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn the_byte_order_is_detected_from_the_peers_first_frame() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            // The arm writes little-endian first; this peer reads it and answers the
            // other way round, which is what it must then be read as.
            let reg = read_msg(&mut s, ByteOrder::Le)
                .await
                .expect("a registration");
            let ack = wire::registration_ack("dmm", reg.node_id.clone(), true);
            write_msg(&mut s, &ack, ByteOrder::Be).await;
        });

        let (out, emits) = run_session(&dialing(addr, Role::Asm)).await;
        peer.await.unwrap();
        assert!(out.is_ok(), "{out:?}");
        let detected = events(&emits)
            .into_iter()
            .find(|e| e.contains("framing-detected"))
            .expect("the order must be published, not merely used");
        assert!(detected.contains("len32be"), "{detected}");
        assert!(saw(&emits, "registered"));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn an_unanswered_registration_is_retried_in_the_other_byte_order() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            // Read the first registration and say nothing: the shape of a peer that
            // cannot read our length prefix.
            let _ = read_msg(&mut s, ByteOrder::Le).await;
            let second = read_msg(&mut s, ByteOrder::Be).await;
            assert!(
                second.map(|m| wire::is_registration(&m)).unwrap_or(false),
                "the retry must be a registration, big-endian this time"
            );
        });

        let (_out, emits) = run_session(&dialing(addr, Role::Asm)).await;
        peer.await.unwrap();
        let retry = events(&emits)
            .into_iter()
            .find(|e| e.contains("framing-retry"))
            .expect("the retry must be visible on the pen");
        assert!(retry.contains("len32be"), "{retry}");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_peer_that_registers_with_us_makes_this_arm_the_dmm() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            // Speak first, as a sensor does to its controller.
            let mut sensor = cfg();
            sensor.node_id = "5f0b0d3a-0000-4000-8000-000000000001".into();
            sensor.status_interval = Duration::from_secs(30);
            write_msg(&mut s, &wire::registration(&sensor), ByteOrder::Le).await;
            let ack = read_msg(&mut s, ByteOrder::Le)
                .await
                .expect("an acknowledgement");
            assert_eq!(
                wire::registration_ack_of(&ack).map(|(ok, _)| ok),
                Some(true),
                "a registering peer must be acknowledged"
            );
        });

        let (_out, emits) = run_session(&dialing(addr, Role::Auto)).await;
        peer.await.unwrap();
        let role = events(&emits)
            .into_iter()
            .find(|e| e.contains("role-detected"))
            .expect("the role must be published");
        assert!(role.contains("dmm"), "{role}");
        // The peer's own heartbeat contract is read off its registration and reported.
        assert!(saw(&emits, "peer-status-interval"), "{:?}", events(&emits));
        assert!(saw(&emits, "registered"));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_registered_session_that_goes_quiet_is_closed() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            let reg = read_msg(&mut s, ByteOrder::Le)
                .await
                .expect("a registration");
            let ack = wire::registration_ack("dmm", reg.node_id.clone(), true);
            write_msg(&mut s, &ack, ByteOrder::Le).await;
            // Hold the socket open and say nothing more. A peer that is up but silent
            // past its own heartbeat contract is indistinguishable from one that is gone,
            // and must be treated as gone.
            tokio::time::sleep(Duration::from_secs(4)).await;
        });

        let (out, emits) = run_session(&dialing(addr, Role::Asm)).await;
        peer.abort();
        let err = format!("{:#}", out.unwrap_err());
        assert!(err.contains("no data from peer"), "{err}");
        assert!(saw(&emits, "registered"));
        assert!(saw(&emits, "closed"));
        // It kept its side of the contract while it waited.
        assert!(
            written(&emits).iter().any(|m| matches!(
                m.content,
                Some(crate::proto::v2::sapient_message::Content::StatusReport(_))
            )),
            "a registered ASM heartbeats"
        );
    }

    /// A declared DMM facing a peer that connects and never speaks frees the connection,
    /// so a listening arm's one slot goes back to accepting.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_dmm_ends_a_session_whose_peer_never_registers() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (s, _) = l.accept().await.unwrap();
            tokio::time::sleep(Duration::from_secs(4)).await;
            drop(s);
        });
        let (out, emits) = run_session(&dialing(addr, Role::Dmm)).await;
        peer.abort();
        let err = format!(
            "{:#}",
            out.expect_err("a silent unregistered peer ends the session")
        );
        assert!(err.contains("never registered"), "{err}");
        assert!(saw(&emits, "closed"));
        assert!(!saw(&emits, "registered"));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn chatter_does_not_extend_a_dmms_registration_deadline() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            for _ in 0..40 {
                let bytes = wire::encode(&wire::status_report("unregistered"), ByteOrder::Le);
                if s.write_all(&bytes).await.is_err() {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        });
        let (out, emits) = run_session(&dialing(addr, Role::Dmm)).await;
        peer.abort();
        let err = format!(
            "{:#}",
            out.expect_err("unregistered chatter must not retain the slot")
        );
        assert!(err.contains("never registered"), "{err}");
        assert!(saw(&emits, "closed"));
        assert!(!saw(&emits, "registered"));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_task_is_answered_even_though_this_arm_accepts_none() {
        let (l, addr) = fake_peer().await;
        let peer = tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            let reg = read_msg(&mut s, ByteOrder::Le)
                .await
                .expect("a registration");
            let ack = wire::registration_ack("dmm", reg.node_id.clone(), true);
            write_msg(&mut s, &ack, ByteOrder::Le).await;

            let mut task = crate::proto::v2::SapientMessage {
                timestamp: None,
                node_id: Some("dmm".into()),
                destination_id: None,
                content: None,
                additional_information: None,
            };
            task.content = Some(crate::proto::v2::sapient_message::Content::Task(
                crate::proto::v2::Task {
                    task_id: Some("01J0000000000000000000000A".into()),
                    ..Default::default()
                },
            ));
            write_msg(&mut s, &task, ByteOrder::Le).await;
            tokio::time::sleep(Duration::from_millis(300)).await;
        });

        let (_out, emits) = run_session(&dialing(addr, Role::Asm)).await;
        peer.abort();
        assert!(saw(&emits, "task-received"), "{:?}", events(&emits));
        let acked = written(&emits).into_iter().any(|m| {
            matches!(
                m.content,
                Some(crate::proto::v2::sapient_message::Content::TaskAck(_))
            )
        });
        assert!(acked, "the standard promises the peer an acknowledgement");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_dial_that_never_reached_a_peer_is_published_not_only_logged() {
        // Take a port and give it straight back, so nothing is listening on it.
        let (l, addr) = fake_peer().await;
        drop(l);

        let (out, emits) = run_session(&dialing(addr, Role::Asm)).await;
        assert!(out.is_err(), "the dial should fail");
        assert!(saw(&emits, "dial-failed"), "{:?}", events(&emits));
        // Keyed on the sentinel, because there is no peer address to key on. This is the
        // one case where the pen is the only place the failure is visible at all.
        let sentinel = emits
            .iter()
            .filter(|e| e.stream == Stream::Session)
            .all(|e| e.peer == NO_PEER);
        assert!(sentinel, "a peer-less failure keys on 0.0.0.0");
    }
}
