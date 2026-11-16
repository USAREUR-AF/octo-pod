// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The running gateway: subscribe, queue, convert, publish, count.
//!
//! ```text
//!  pen subscriber callback          bounded queue           one converter task
//!  count, check key and size,  -->  (drop and count    -->  drop what waited too long;
//!  never convert here               when full)              per target: convert, put
//!                                                           with content type and
//!                                                           attachment, audit line
//! ```
//!
//! The callback runs on Zenoh's receive path, so it only counts, checks and enqueues. The
//! queue is bounded in items, each item is bounded in bytes, and a sample that waited
//! longer than `max_age` is dropped rather than converted late, as in the arms
//! (`octo_arm_common::outbox`): a converted track from six seconds ago is misleading.
//! Everything dropped is counted.
//!
//! Failure behavior:
//! - a declaration it refuses (formats, keys, context, a pack bundle that does not match
//!   its manifest): opens nothing, writes a `refused` audit line when it can, exit 2;
//! - the pen not reachable at start: exit 1, for the supervisor to restart;
//! - a sample that does not convert: counted (`dropped_invalid` or `skipped`), recorded
//!   in the audit file with the engine's error, logged the first time each error occurs
//!   and every hundredth time after, and dropped. Its bytes are never published;
//! - a sample the engine panics on: counted and recorded as `engine-panic`, like any
//!   other sample that does not convert; the gateway keeps running;
//! - a `put` that fails: counted (`forward_errors`); the sample is not retried.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::Context;
use goat_transformers::Value;
use octo_arm_common::lifecycle::worth_reporting;
use octo_arm_common::{SessionConfig, Stats};
use serde_json::json;
use tokio::sync::mpsc;
use tracing::{error, info, warn};

use crate::audit::{payload_hash, AuditLog, Source};
use crate::bundle::{distribution, Bundle};
use crate::convert::{Failure, Outcome, Translator};
use crate::route::{encoding, Route};

/// Exit code for a declaration the gateway refuses to run with.
pub const REFUSED: u8 = 2;

/// Queue length when none is declared. The arms' outbox default.
pub const DEFAULT_QUEUE: usize = 512;
/// Largest sample accepted when none is declared: 1 MiB. With [`DEFAULT_QUEUE`] this
/// bounds the queue at 512 MiB in the worst case; a typical track message is under 1 KiB.
pub const DEFAULT_MAX_BYTES: usize = 1024 * 1024;
/// How long a sample may wait before it is dropped instead of converted. The arms'
/// outbox default.
pub const DEFAULT_MAX_AGE: Duration = Duration::from_millis(1500);

/// Everything one gateway was declared with.
#[derive(Debug, Clone)]
pub struct Declaration {
    /// This gateway's id; names the audit file and appears in every record.
    pub id: String,
    /// Pen key expressions to read captured samples from.
    pub sources: Vec<String>,
    /// The format of every sample on those keys.
    pub from: String,
    /// The formats to convert each sample to.
    pub to: Vec<String>,
    /// The literal key every result is published beneath.
    pub namespace: String,
    /// The publish key template (see [`crate::route`]).
    pub publish_key: String,
    /// Model keys the source format does not carry, as `key=value` text.
    pub context: Vec<(String, String)>,
    /// Convert a sample only when each decoded model key has this value, as
    /// `key=value` text (see [`crate::convert`]).
    pub only_when: Vec<(String, String)>,
    /// `target key = source key`: a result takes `target` from the sample's `source`
    /// when the sample carries no `target`.
    pub carry: Vec<(String, String)>,
    /// When set, each result is given `stale_ms` = receive time + this, if the source
    /// carries no stale time and the declared context sets none.
    pub stale_after: Option<Duration>,
    /// When true, each result is given `time_ms` = receive time, if the source carries
    /// no time.
    pub stamp_time: bool,
    /// The pack bundle directory.
    pub packs_dir: String,
    /// `public` or `controlled`: the most restricted pack the engine may load.
    pub max_distribution: String,
    /// How this gateway reaches the pen.
    pub pen: SessionConfig,
    /// Where the audit record is kept.
    pub audit_dir: String,
    /// Seconds between stats lines and audit summaries.
    pub stats_secs: u64,
    /// Queue length, in samples.
    pub queue: usize,
    /// Largest sample accepted, in bytes.
    pub max_bytes: usize,
    /// How long a sample may wait in the queue.
    pub max_age: Duration,
}

/// Counters this gateway has no shared name for.
#[derive(Default)]
struct Counters {
    /// A sample on a key outside the declared sources.
    dropped_key: AtomicU64,
    /// A sample larger than `max_bytes`.
    dropped_oversize: AtomicU64,
    /// An audit line that could not be written.
    audit_errors: AtomicU64,
    /// Samples not converted, by error name.
    errors: Mutex<BTreeMap<String, u64>>,
}

impl Counters {
    /// Count one occurrence of an error name; returns the new count for that name.
    fn note(&self, name: &str) -> u64 {
        let Ok(mut errors) = self.errors.lock() else {
            return 1;
        };
        let count = errors.entry(name.to_string()).or_insert(0);
        *count += 1;
        *count
    }

    fn errors(&self) -> BTreeMap<String, u64> {
        self.errors.lock().map(|e| e.clone()).unwrap_or_default()
    }
}

/// One captured sample, waiting to be converted.
struct Job {
    key: String,
    payload: Vec<u8>,
    timestamp: Option<String>,
    queued_at: Instant,
    received_ms: i64,
}

/// Everything the gateway checked before it opened anything.
struct Plan {
    route: Route,
    translator: Translator,
}

/// Validate the declaration. Any refusal means the gateway never opens a session.
fn plan(d: &Declaration) -> Result<Plan, String> {
    if d.id.is_empty()
        || !d
            .id
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
    {
        return Err(format!("id {:?} must match [a-z0-9-]", d.id));
    }
    if d.queue == 0 || d.max_bytes == 0 {
        return Err("queue and max_bytes must be at least 1".into());
    }
    let route = Route::new(&d.sources, &d.namespace, &d.publish_key).map_err(|e| e.to_string())?;
    let limit = distribution(&d.max_distribution).map_err(|e| e.to_string())?;
    let bundle = Bundle::load(Path::new(&d.packs_dir), limit)
        .map_err(|e| format!("pack bundle {}: {e}", d.packs_dir))?;
    let translator = Translator::new(bundle, &d.from, &d.to, &d.context)
        .and_then(|t| t.with_rules(&d.only_when, &d.carry))
        .map_err(|e| e.to_string())?;
    // The two values the gateway can add per sample are model keys like any other: check
    // now that the loaded modules declare them, not on the first sample.
    if d.stale_after.is_some() {
        translator
            .accepts_context("stale_ms", "0")
            .map_err(|e| e.to_string())?;
    }
    if d.stamp_time {
        translator
            .accepts_context("time_ms", "0")
            .map_err(|e| e.to_string())?;
    }
    Ok(Plan { route, translator })
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|d| i64::try_from(d.as_millis()).ok())
        .unwrap_or(0)
}

/// Plan, then run until `shutdown` resolves. Returns the exit code: 0 stopped, 1 failed
/// (for the supervisor to restart), [`REFUSED`] for a declaration it will not serve.
pub async fn gateway(
    d: Declaration,
    shutdown: impl std::future::Future<Output = anyhow::Result<()>>,
) -> u8 {
    let plan = match plan(&d) {
        Ok(p) => p,
        Err(why) => {
            // Fail closed: nothing is opened, nothing is published.
            error!(id = %d.id, reason = %why, "REFUSING TO START");
            if let Ok(audit) = AuditLog::open(Path::new(&d.audit_dir), &d.id) {
                let _ = audit.event("refused", json!({ "reason": why }));
            }
            return REFUSED;
        }
    };
    match run(d, plan, shutdown).await {
        Ok(()) => 0,
        Err(e) => {
            error!(error = %format!("{e:#}"), "octo-translate stopped");
            1
        }
    }
}

/// What one sample's conversion to one target ended as.
fn record_failure(
    why: &Failure,
    source: &Source<'_>,
    target: &str,
    stats: &Stats,
    counters: &Counters,
    audit: &AuditLog,
) {
    let kind = match why.outcome {
        Outcome::Invalid => {
            Stats::bump(&stats.dropped_invalid);
            "failed"
        }
        Outcome::Skipped => {
            Stats::bump(&stats.skipped);
            "skipped"
        }
    };
    let count = counters.note(&why.name);
    if audit
        .not_translated(kind, source, target, &why.name, &why.detail)
        .is_err()
    {
        counters.audit_errors.fetch_add(1, Ordering::Relaxed);
    }
    if worth_reporting(count) {
        warn!(
            source_key = source.key, from = source.format, to = target, error = %why.name,
            detail = %why.detail, count,
            "sample not converted ({kind}); nothing was published for it"
        );
    }
}

#[allow(clippy::too_many_lines)] // one linear setup; splitting it hides the order
async fn run(
    d: Declaration,
    plan: Plan,
    shutdown: impl std::future::Future<Output = anyhow::Result<()>>,
) -> anyhow::Result<()> {
    let Plan { route, translator } = plan;
    let audit = Arc::new(
        AuditLog::open(Path::new(&d.audit_dir), &d.id).context("opening the audit record")?,
    );
    let sources: Vec<&str> = route.sources().iter().map(|k| k.as_str()).collect();
    audit.event(
        "start",
        json!({ "sources": sources, "from": translator.from(), "to": translator.targets(),
                "namespace": route.namespace(), "publish_key": d.publish_key,
                "packs_commit": translator.packs_commit(),
                "max_distribution": d.max_distribution }),
    )?;
    info!(
        id = %d.id, sources = ?sources, from = translator.from(), to = ?translator.targets(),
        namespace = route.namespace(), packs_commit = translator.packs_commit(),
        audit = %audit.path().display(), "octo-translate starting"
    );

    let pen = d.pen.open().await?;
    let stats = Arc::new(Stats::default());
    let counters = Arc::new(Counters::default());
    let (tx, mut rx) = mpsc::channel::<Job>(d.queue);
    let route = Arc::new(route);

    // Subscribe to exactly the declared sources. A sample outside them (a broader grant,
    // a router fault) is dropped and counted here too.
    let mut subscribers = Vec::new();
    for source in route.sources() {
        let (route, stats, counters, tx) =
            (route.clone(), stats.clone(), counters.clone(), tx.clone());
        let max_bytes = d.max_bytes;
        let sub = pen
            .declare_subscriber(source.clone())
            .callback(move |sample| {
                Stats::bump(&stats.received);
                let key = sample.key_expr().as_str();
                if !route.admits(key) {
                    counters.dropped_key.fetch_add(1, Ordering::Relaxed);
                    return;
                }
                if sample.payload().len() > max_bytes {
                    let n = counters.dropped_oversize.fetch_add(1, Ordering::Relaxed) + 1;
                    if worth_reporting(n) {
                        warn!(
                            key,
                            len = sample.payload().len(),
                            max_bytes,
                            count = n,
                            "sample larger than max_bytes; dropped, not converted"
                        );
                    }
                    return;
                }
                let job = Job {
                    key: key.to_string(),
                    payload: sample.payload().to_bytes().to_vec(),
                    timestamp: sample.timestamp().map(|t| {
                        humantime::format_rfc3339_millis(t.get_time().to_system_time()).to_string()
                    }),
                    queued_at: Instant::now(),
                    received_ms: now_ms(),
                };
                if tx.try_send(job).is_ok() {
                    Stats::bump(&stats.queued);
                } else {
                    // Full: the converter is slower than the feed. The newest sample is
                    // dropped and counted; the receive path is never blocked.
                    let n = stats.dropped_overflow.fetch_add(1, Ordering::Relaxed) + 1;
                    if worth_reporting(n) {
                        warn!(key, count = n, "queue full; sample dropped, not converted");
                    }
                }
            })
            .await
            .map_err(|e| anyhow::anyhow!("declare_subscriber {source}: {e}"))?;
        subscribers.push(sub);
    }
    drop(tx);

    let stats_line = octo_arm_common::stats::spawn_stats_logger(stats.clone(), d.stats_secs);
    let summary = spawn_summary(d.stats_secs, stats.clone(), counters.clone(), audit.clone());

    let mut seq: u64 = 0;
    let convert = async {
        while let Some(job) = rx.recv().await {
            if job.queued_at.elapsed() > d.max_age {
                Stats::bump(&stats.dropped_stale);
                continue;
            }
            let hash = payload_hash(&job.payload);
            let source = Source {
                key: &job.key,
                timestamp: job.timestamp.as_deref(),
                hash: &hash,
                format: translator.from(),
            };
            let mut extra: Vec<(String, Value)> = Vec::new();
            if d.stamp_time {
                extra.push(("time_ms".into(), Value::I64(job.received_ms)));
            }
            if let Some(after) = d.stale_after {
                let after = i64::try_from(after.as_millis()).unwrap_or(i64::MAX);
                extra.push((
                    "stale_ms".into(),
                    Value::I64(job.received_ms.saturating_add(after)),
                ));
            }
            for target in translator.targets() {
                let attempt = translator.translate(target, &job.payload, &extra);
                let result = attempt.and_then(|bytes| {
                    route
                        .publish_key(&job.key, translator.from(), target)
                        .map(|key| (key, bytes))
                        .ok_or_else(|| Failure {
                            outcome: Outcome::Invalid,
                            name: "bad-publish-key".into(),
                            detail: "the source key does not yield a key in the namespace".into(),
                        })
                });
                let (key, bytes) = match result {
                    Ok(r) => r,
                    Err(why) => {
                        record_failure(&why, &source, target, &stats, &counters, &audit);
                        continue;
                    }
                };
                let Some(wire) = translator.target_wire(target) else {
                    continue;
                };
                // Provenance travels with the sample: what it was converted from.
                let attachment = json!({
                    "source_key": job.key, "source_ts": job.timestamp, "source_hash": hash,
                    "from": translator.from(), "to": target, "gateway": d.id,
                    "packs_commit": translator.packs_commit(),
                })
                .to_string();
                match pen
                    .put(&key, bytes.clone())
                    .encoding(encoding(target, wire))
                    .attachment(attachment)
                    .await
                {
                    Ok(()) => {
                        seq += 1;
                        Stats::bump(&stats.forwarded);
                        if audit
                            .translated(seq, &source, target, &key, &bytes)
                            .is_err()
                        {
                            counters.audit_errors.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                    Err(e) => {
                        // A transport fault, not an ACL denial: a denied write is
                        // accepted here and discarded by the router. Not retried.
                        Stats::bump(&stats.forward_errors);
                        warn!(key, error = %e, "pen put failed; the result is dropped, not sent late");
                    }
                }
            }
        }
    };
    tokio::select! {
        () = convert => {}
        r = shutdown => { r?; info!("shutdown signal received"); }
    }
    stats_line.abort();
    summary.abort();
    drop(subscribers);
    let _ = audit.event("stop", summary_line(&stats, &counters));
    let _ = pen.close().await;
    Ok(())
}

fn summary_line(stats: &Stats, counters: &Counters) -> serde_json::Value {
    json!({
        "received": stats.received.load(Ordering::Relaxed),
        "translated": stats.forwarded.load(Ordering::Relaxed),
        "failed": stats.dropped_invalid.load(Ordering::Relaxed),
        "skipped": stats.skipped.load(Ordering::Relaxed),
        "forward_errors": stats.forward_errors.load(Ordering::Relaxed),
        "dropped_key": counters.dropped_key.load(Ordering::Relaxed),
        "dropped_oversize": counters.dropped_oversize.load(Ordering::Relaxed),
        "dropped_overflow": stats.dropped_overflow.load(Ordering::Relaxed),
        "dropped_stale": stats.dropped_stale.load(Ordering::Relaxed),
        "audit_errors": counters.audit_errors.load(Ordering::Relaxed),
        "errors": counters.errors(),
    })
}

/// The gateway's own counters, beside the arms' shared `stats` line: logged and written
/// to the audit record on the same period.
fn spawn_summary(
    stats_secs: u64,
    stats: Arc<Stats>,
    counters: Arc<Counters>,
    audit: Arc<AuditLog>,
) -> tokio::task::JoinHandle<()> {
    let period = Duration::from_secs(stats_secs.max(1));
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(period).await;
            let line = summary_line(&stats, &counters);
            info!(stats = %line, "translate stats");
            if audit.event("summary", line).is_err() {
                counters.audit_errors.fetch_add(1, Ordering::Relaxed);
            }
        }
    })
}
