// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Arm counters + the periodic stats line.
//!
//! Every arm emits the same counters on the same schedule, so an operator can read any
//! arm's log the same way (and the sync leg's `forwarded=` grammar carries over from
//! uplink-bridge).
//!
//! The counters are split the way they are because the arm sits on both sides of a
//! failure it cannot see: `Session::put` returns `Ok` whether or not a router is
//! listening, so "we handed it to zenoh" and "it left the box" are different facts and
//! are counted separately. `queued` minus `forwarded` minus `pending` is what the pen
//! swallowed.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tracing::info;

#[derive(Default)]
pub struct Stats {
    /// Units off the wire, before any gate.
    pub received: AtomicU64,
    /// Rejected by allow_sources — the arm's only access control.
    pub dropped_source: AtomicU64,
    /// Accepted into the outbox.
    pub queued: AtomicU64,
    /// Evicted because the queue hit a bound (items or bytes) before it could drain.
    pub dropped_overflow: AtomicU64,
    /// Discarded at drain time for being older than max_age. On a streaming fabric
    /// this is correct behaviour, not a fault — but it is the measure of how much a
    /// pen outage cost, so it is counted rather than assumed.
    pub dropped_stale: AtomicU64,
    /// A unit for a peer that never reached the writer: its queue was full, or the unit
    /// waited past `max_age` for a connection. Kept apart from `dropped_overflow` and
    /// `dropped_stale`, which are the queue toward the pen — this one is data a partner's
    /// network did not receive, and the two must not be confused when reading a stats line.
    /// An arm that ends the session on a full writer queue rather than dropping never
    /// moves it.
    pub dropped_egress: AtomicU64,
    /// A relayed unit refused by the protocol's validity check before it could be written:
    /// something this protocol cannot carry as one unit. Refused rather than written,
    /// because a malformed unit on someone else's network is worse than a missing one.
    pub dropped_invalid: AtomicU64,
    /// A unit that was well formed and deliberately not carried: a translator's recipe has
    /// no form for it on the other side (a SAPIENT status report on its way to TAK, a
    /// friendly position on its way to a SAPIENT controller). Not a fault. Counted apart
    /// from `dropped_invalid` so that a quiet far end can be told apart from a broken
    /// source.
    pub skipped: AtomicU64,
    /// Handed to zenoh successfully. NOT proof of delivery: a router that denies the
    /// write by ACL accepts it here and discards it, logging nothing at either end.
    pub forwarded: AtomicU64,
    /// `put` returned an error — transport faults, not ACL denials.
    pub forward_errors: AtomicU64,
    /// Units written to a peer's socket. Zero for an arm that only captures. For an arm
    /// that writes, this and `forwarded` are different facts about the same unit: one
    /// says it left for the pen, the other that it left for the peer's network. Reusing
    /// `forwarded` for both would make the word mean two things depending on the arm.
    pub sent: AtomicU64,
    /// A socket write returned an error. Rare on UDP, which reports almost nothing about
    /// a datagram nobody is listening for, and the usual first sign on TCP that the far
    /// end has gone.
    pub send_errors: AtomicU64,
    /// Times the pen went from reachable to unreachable. A steadily climbing value is
    /// a flapping router, which no single log line would show.
    pub pen_outages: AtomicU64,
}

impl Stats {
    pub fn bump(counter: &AtomicU64) {
        counter.fetch_add(1, Ordering::Relaxed);
    }

    /// The pen went from reachable to unreachable. Counted rather than only logged,
    /// so a router that is flapping shows up as a number climbing between two stats
    /// lines instead of as a pair of messages someone has to notice.
    pub fn note_pen_down(&self) {
        Stats::bump(&self.pen_outages);
    }
}

/// Spawn the periodic stats logger. Returns the task handle so the arm can abort it at
/// shutdown. `pending` is read live from the outbox, so the line shows the backlog as
/// it drains rather than a number frozen at the last publish.
pub fn spawn_stats_logger_with_depth(
    stats: Arc<Stats>,
    period_secs: u64,
    depth: Option<Arc<crate::outbox::Outbox>>,
) -> tokio::task::JoinHandle<()> {
    let period = Duration::from_secs(period_secs.max(1));
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(period).await;
            let (pending, capacity) = match &depth {
                Some(o) => crate::outbox::depth_of(o),
                None => (0, 0),
            };
            info!(
                received = stats.received.load(Ordering::Relaxed),
                forwarded = stats.forwarded.load(Ordering::Relaxed),
                forward_errors = stats.forward_errors.load(Ordering::Relaxed),
                sent = stats.sent.load(Ordering::Relaxed),
                send_errors = stats.send_errors.load(Ordering::Relaxed),
                dropped_source = stats.dropped_source.load(Ordering::Relaxed),
                dropped_overflow = stats.dropped_overflow.load(Ordering::Relaxed),
                dropped_stale = stats.dropped_stale.load(Ordering::Relaxed),
                dropped_egress = stats.dropped_egress.load(Ordering::Relaxed),
                dropped_invalid = stats.dropped_invalid.load(Ordering::Relaxed),
                skipped = stats.skipped.load(Ordering::Relaxed),
                pen_outages = stats.pen_outages.load(Ordering::Relaxed),
                pending = pending,
                outbox_capacity = capacity,
                "stats"
            );
        }
    })
}

/// Back-compatible entry point for an arm that has no outbox to report.
pub fn spawn_stats_logger(stats: Arc<Stats>, period_secs: u64) -> tokio::task::JoinHandle<()> {
    spawn_stats_logger_with_depth(stats, period_secs, None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::outbox::{Outbox, Pending, Policy};
    use std::io::Write;
    use std::sync::Mutex;
    use zenoh::bytes::Encoding;

    /// Collects formatted log lines so a test can read what an operator would.
    #[derive(Clone, Default)]
    struct Captured(Arc<Mutex<Vec<u8>>>);

    impl Write for Captured {
        fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
            self.0.lock().expect("log buffer").extend_from_slice(buf);
            Ok(buf.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    impl Captured {
        fn text(&self) -> String {
            String::from_utf8_lossy(&self.0.lock().expect("log buffer")).into_owned()
        }
    }

    fn capture() -> (Captured, tracing::subscriber::DefaultGuard) {
        let out = Captured::default();
        let writer = out.clone();
        let subscriber = tracing_subscriber::fmt()
            .with_ansi(false)
            .with_writer(move || writer.clone())
            .finish();
        (out, tracing::subscriber::set_default(subscriber))
    }

    #[test]
    fn a_pen_outage_is_counted() {
        let s = Stats::default();
        s.note_pen_down();
        s.note_pen_down();
        assert_eq!(s.pen_outages.load(Ordering::Relaxed), 2);
    }

    #[tokio::test(flavor = "current_thread", start_paused = true)]
    async fn the_stats_line_reports_every_counter_and_the_live_backlog() {
        let (log, _guard) = capture();
        let stats = Arc::new(Stats::default());
        Stats::bump(&stats.received);
        Stats::bump(&stats.received);
        Stats::bump(&stats.dropped_source);
        stats.note_pen_down();
        let outbox = Arc::new(Outbox::new(Policy::default(), stats.clone()));
        outbox.push(Pending::new(
            "raw/t/1".into(),
            vec![1],
            Encoding::APPLICATION_OCTET_STREAM,
        ));
        let task = spawn_stats_logger_with_depth(stats.clone(), 30, Some(outbox.clone()));

        // Nothing before the first period ends.
        tokio::time::sleep(Duration::from_secs(29)).await;
        outbox.push(Pending::new(
            "raw/t/2".into(),
            vec![2],
            Encoding::APPLICATION_OCTET_STREAM,
        ));
        assert!(!log.text().contains("stats"), "{}", log.text());
        tokio::time::sleep(Duration::from_secs(2)).await;
        let line = log.text();
        task.abort();
        // `pending` is read from the outbox when the line is written, not when the
        // logger started.
        for field in [
            "received=2",
            "dropped_source=1",
            "pen_outages=1",
            "pending=2",
            &format!("outbox_capacity={}", Policy::default().capacity),
        ] {
            assert!(line.contains(field), "missing {field} in {line}");
        }
    }

    #[tokio::test(flavor = "current_thread", start_paused = true)]
    async fn an_arm_without_an_outbox_reports_no_backlog_and_a_zero_period_is_one_second() {
        let (log, _guard) = capture();
        let task = spawn_stats_logger(Arc::new(Stats::default()), 0);
        tokio::time::sleep(Duration::from_millis(1_100)).await;
        let line = log.text();
        task.abort();
        assert!(line.contains("pending=0"), "{line}");
        assert!(line.contains("outbox_capacity=0"), "{line}");
    }
}
