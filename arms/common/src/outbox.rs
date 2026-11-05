//! The arm's hand-off queue between the socket and the session.
//!
//! # What this is for
//!
//! Not durability. This is a streaming fabric: a track from six seconds ago is not
//! valuable, it is misleading, and replaying a backlog into a live picture after a
//! reconnect is worse than having dropped it. If the pen goes away, the right answer
//! is to drop what cannot be delivered fresh and carry on.
//!
//! It exists for two things that still matter on a box nobody can reach:
//!
//! 1. **Keeping the socket loop off the network.** `ingest` gates, keys and enqueues;
//!    one drain task owns the session and does the publishing. A slow or dead pen can
//!    never stall a read loop, so it can never push loss down into the kernel's
//!    receive buffer where nothing counts it.
//! 2. **Making an outage legible.** `Session::put` returns `Ok` into a dead session,
//!    so before this an arm could not tell a working pen from a missing one, and the
//!    stats line said `forwarded` either way. Now the drain task watches the session
//!    and the counters say what actually left the box.
//!
//! # The three bounds, and why each exists
//!
//! - `max_age` — a unit older than this is dropped at drain time rather than
//!   published. THIS is the streaming property: freshness beats completeness.
//! - `capacity` — items, so a burst cannot grow the queue without limit.
//! - `max_bytes` — because `capacity` alone is not a memory bound. A `stream`-framed
//!   TCP arm can hand over frames up to 16 MB, so a few hundred of those is gigabytes
//!   on an edge device with no one to reboot it. Losing data is acceptable here;
//!   the process being OOM-killed is not.
//!
//! Everything dropped is counted. Bounded, reported and attributable — not silent.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tokio::sync::Notify;
use zenoh::bytes::Encoding;

use crate::stats::Stats;

/// Drop anything that has waited longer than this rather than publishing it late.
/// Roughly one radar scan: long enough to ride out a momentary hiccup, short enough
/// that nothing stale ever reaches a consumer.
pub const DEFAULT_MAX_AGE: Duration = Duration::from_millis(1500);
/// Items. Generous headroom for a 60/s feed inside `DEFAULT_MAX_AGE`.
pub const DEFAULT_CAPACITY: usize = 512;
/// Hard memory ceiling for the queue, whatever the item count says.
pub const DEFAULT_MAX_BYTES: usize = 32 * 1024 * 1024;

/// What the queue is allowed to hold. Bounds, not targets.
#[derive(Debug, Clone, Copy)]
pub struct Policy {
    pub capacity: usize,
    pub max_bytes: usize,
    pub max_age: Duration,
}

impl Default for Policy {
    fn default() -> Self {
        Self {
            capacity: DEFAULT_CAPACITY,
            max_bytes: DEFAULT_MAX_BYTES,
            max_age: DEFAULT_MAX_AGE,
        }
    }
}

/// One unit of wire data, waiting for a session to put it on.
#[derive(Debug, Clone)]
pub struct Pending {
    pub key: String,
    pub payload: Vec<u8>,
    pub encoding: Encoding,
    /// When it came off the wire. The drain task compares this against `max_age`;
    /// monotonic, so a clock step on the box cannot make data look fresh or ancient.
    pub queued_at: Instant,
}

impl Pending {
    pub fn new(key: String, payload: Vec<u8>, encoding: Encoding) -> Self {
        Self {
            key,
            payload,
            encoding,
            queued_at: Instant::now(),
        }
    }

    pub fn is_stale(&self, max_age: Duration) -> bool {
        self.queued_at.elapsed() > max_age
    }

    fn weight(&self) -> usize {
        self.payload.len() + self.key.len()
    }
}

/// Bounded FIFO with drop-oldest eviction on both item count and bytes.
pub struct Outbox {
    q: Mutex<VecDeque<Pending>>,
    bytes: Mutex<usize>,
    policy: Policy,
    /// Wakes the drain task the moment something arrives, so a healthy pen adds
    /// queueing latency measured in microseconds rather than a poll interval.
    notify: Notify,
    stats: Arc<Stats>,
}

impl Outbox {
    pub fn new(policy: Policy, stats: Arc<Stats>) -> Self {
        Self {
            q: Mutex::new(VecDeque::with_capacity(policy.capacity.min(1024))),
            bytes: Mutex::new(0),
            policy: Policy {
                capacity: policy.capacity.max(1),
                ..policy
            },
            notify: Notify::new(),
            stats,
        }
    }

    /// Accept a unit. Never blocks and never fails: if a bound is reached the OLDEST
    /// units are evicted and counted. The caller is a socket read loop, and blocking
    /// it would push the loss somewhere nothing counts it.
    pub fn push(&self, item: Pending) {
        {
            let mut q = self.q.lock().expect("outbox mutex poisoned");
            let mut bytes = self.bytes.lock().expect("outbox bytes mutex poisoned");
            let w = item.weight();
            while q.len() >= self.policy.capacity
                || (*bytes + w > self.policy.max_bytes && !q.is_empty())
            {
                if let Some(old) = q.pop_front() {
                    *bytes = bytes.saturating_sub(old.weight());
                    Stats::bump(&self.stats.dropped_overflow);
                }
            }
            *bytes += w;
            q.push_back(item);
        }
        Stats::bump(&self.stats.queued);
        self.notify.notify_one();
    }

    /// Take the oldest unit, if any.
    pub fn pop(&self) -> Option<Pending> {
        let mut q = self.q.lock().expect("outbox mutex poisoned");
        let item = q.pop_front();
        if let Some(ref i) = item {
            let mut bytes = self.bytes.lock().expect("outbox bytes mutex poisoned");
            *bytes = bytes.saturating_sub(i.weight());
        }
        item
    }

    /// Take the next unit that is still fresh, discarding anything that has aged out.
    /// Called by the drain task, so staleness is judged at the moment of publishing
    /// rather than when it was queued — which is the only moment that matters.
    pub fn pop_fresh(&self) -> Option<Pending> {
        while let Some(item) = self.pop() {
            if item.is_stale(self.policy.max_age) {
                Stats::bump(&self.stats.dropped_stale);
                continue;
            }
            return Some(item);
        }
        None
    }

    pub fn len(&self) -> usize {
        self.q.lock().expect("outbox mutex poisoned").len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn policy(&self) -> Policy {
        self.policy
    }

    /// Wait until something is queued, or the timeout elapses. The timeout is what
    /// makes session-state transitions visible while the pod is idle.
    pub async fn wait(&self, timeout: Duration) {
        let _ = tokio::time::timeout(timeout, self.notify.notified()).await;
    }
}

/// Is the pen reachable? `Session::put` cannot answer this — it returns `Ok` whether
/// or not a router is on the other end, which is the whole reason an outage used to be
/// invisible. The count of connected routers can.
pub async fn pen_reachable(session: &zenoh::Session) -> bool {
    session.info().routers_zid().await.count() > 0
}

/// Snapshot for the stats line: queued now, and the item bound.
pub fn depth_of(outbox: &Outbox) -> (usize, usize) {
    (outbox.len(), outbox.policy().capacity)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::Ordering;

    fn policy(capacity: usize, max_age_ms: u64) -> Policy {
        Policy {
            capacity,
            max_bytes: DEFAULT_MAX_BYTES,
            max_age: Duration::from_millis(max_age_ms),
        }
    }

    fn item(n: u8) -> Pending {
        Pending::new(
            format!("raw/t/{n}"),
            vec![n],
            Encoding::APPLICATION_OCTET_STREAM,
        )
    }

    #[test]
    fn fifo_order_is_preserved() {
        let o = Outbox::new(policy(8, 5_000), Arc::new(Stats::default()));
        for n in 0..4 {
            o.push(item(n));
        }
        let got: Vec<u8> = std::iter::from_fn(|| o.pop())
            .map(|p| p.payload[0])
            .collect();
        assert_eq!(got, vec![0, 1, 2, 3]);
    }

    #[test]
    fn overflow_evicts_the_oldest_and_counts_it() {
        let s = Arc::new(Stats::default());
        let o = Outbox::new(policy(3, 5_000), s.clone());
        for n in 0..5 {
            o.push(item(n));
        }
        assert_eq!(o.len(), 3);
        // The newest three survive: for a track picture, recent beats complete.
        let got: Vec<u8> = std::iter::from_fn(|| o.pop())
            .map(|p| p.payload[0])
            .collect();
        assert_eq!(got, vec![2, 3, 4]);
        assert_eq!(s.dropped_overflow.load(Ordering::Relaxed), 2);
        assert_eq!(s.queued.load(Ordering::Relaxed), 5);
    }

    #[test]
    fn stale_units_are_dropped_at_drain_not_published_late() {
        let s = Arc::new(Stats::default());
        let o = Outbox::new(policy(8, 10), s.clone());
        o.push(item(1));
        o.push(item(2));
        std::thread::sleep(Duration::from_millis(40));
        assert!(
            o.pop_fresh().is_none(),
            "aged-out data must not be published"
        );
        assert_eq!(s.dropped_stale.load(Ordering::Relaxed), 2);
        assert!(o.is_empty());
    }

    #[test]
    fn fresh_units_still_get_through() {
        let s = Arc::new(Stats::default());
        let o = Outbox::new(policy(8, 5_000), s.clone());
        o.push(item(7));
        assert_eq!(o.pop_fresh().unwrap().payload[0], 7);
        assert_eq!(s.dropped_stale.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn bytes_are_bounded_even_when_the_item_count_is_not() {
        let s = Arc::new(Stats::default());
        let p = Policy {
            capacity: 1000,
            max_bytes: 4096,
            max_age: Duration::from_secs(60),
        };
        let o = Outbox::new(p, s.clone());
        for _ in 0..10 {
            o.push(Pending::new(
                "k".into(),
                vec![0u8; 1024],
                Encoding::APPLICATION_OCTET_STREAM,
            ));
        }
        // Item count would have allowed all ten; the byte ceiling did not.
        assert!(
            o.len() <= 4,
            "held {} items, byte bound not enforced",
            o.len()
        );
        assert!(s.dropped_overflow.load(Ordering::Relaxed) >= 6);
    }

    #[test]
    fn capacity_is_never_zero() {
        let o = Outbox::new(policy(0, 5_000), Arc::new(Stats::default()));
        o.push(item(1));
        assert_eq!(o.len(), 1);
    }

    #[test]
    fn depth_reports_len_and_capacity() {
        let o = Outbox::new(policy(4, 5_000), Arc::new(Stats::default()));
        o.push(item(1));
        assert_eq!(depth_of(&o), (1, 4));
    }
}
