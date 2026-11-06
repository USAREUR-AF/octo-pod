// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The shared ingest path: gate → provenance key → queue → publish → count.
//!
//! Arms hand every unit of wire data (datagram, frame, line) to [`Ingestor::ingest`]
//! and get identical trust behavior: allowlist gating, `<prefix>/<label>/<src-ip>`
//! keying, bounded buffering across a pen outage, counters. An arm that decodes before
//! publishing (typed upgrade) transforms the payload first and passes an explicit
//! encoding via [`Ingestor::ingest_with_encoding`].
//!
//! THE SOCKET PATH DOES NOT TOUCH THE NETWORK. `ingest` gates, keys and enqueues; a
//! single drain task owns the session and does the publishing. Two reasons, both
//! learned rather than designed: a `put` that blocks stalls the read loop and pushes
//! loss into the kernel's receive buffer where nothing counts it, and a `put` into a
//! dead session returns `Ok`, so the read loop could never have noticed an outage
//! anyway. One task watching one thing can.

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use tokio::task::JoinHandle;
use tracing::{error, info, warn};
use zenoh::bytes::Encoding;

use crate::outbox::{pen_reachable, Outbox, Pending, Policy};
use crate::source::{source_chunk, SourceAllowlist};
use crate::stats::Stats;

/// How often the drain task re-checks the pen while it is down, and the longest an
/// idle arm waits before noticing a state change. Short enough that a router restart
/// is a blip; long enough that an idle pod is not polling in a tight loop.
const LIVENESS_INTERVAL: Duration = Duration::from_millis(500);

pub struct Ingestor {
    session: Arc<zenoh::Session>,
    key_prefix: String,
    allowlist: SourceAllowlist,
    pub stats: Arc<Stats>,
    pub outbox: Arc<Outbox>,
}

impl Ingestor {
    pub fn new(
        session: Arc<zenoh::Session>,
        key_prefix: impl Into<String>,
        allowlist: SourceAllowlist,
    ) -> Self {
        Self::with_policy(session, key_prefix, allowlist, Policy::default())
    }

    /// Override the queue bounds. A feed whose units are large or whose staleness
    /// tolerance differs from a radar scan wants its own numbers.
    pub fn with_policy(
        session: Arc<zenoh::Session>,
        key_prefix: impl Into<String>,
        allowlist: SourceAllowlist,
        policy: Policy,
    ) -> Self {
        let stats = Arc::new(Stats::default());
        let outbox = Arc::new(Outbox::new(policy, stats.clone()));
        Self {
            session,
            key_prefix: key_prefix.into(),
            allowlist,
            stats,
            outbox,
        }
    }

    /// A second gate over one publish path: same session, same key prefix, same
    /// counters, same queue — only the allowlist differs.
    ///
    /// A session arm needs two gates, because it publishes two different kinds of
    /// thing. Frames from the peer are ingress and must be vouched for by source
    /// address; the arm's own egress mirror and lifecycle records are authored on the
    /// box and have no source to vouch for — a dial that never reached a server has no
    /// peer address at all, so gating those would silence exactly the failure the
    /// records exist to report.
    ///
    /// They must not be separate `Ingestor`s: a second one built with `new` carries its
    /// own `Stats` and its own `Outbox`, which means a second drain task, a stats line
    /// that covers half the arm, and queue drops counted where nothing reads them.
    pub fn sharing(other: &Self, allowlist: SourceAllowlist) -> Self {
        Self {
            session: Arc::clone(&other.session),
            key_prefix: other.key_prefix.clone(),
            allowlist,
            stats: Arc::clone(&other.stats),
            outbox: Arc::clone(&other.outbox),
        }
    }

    /// Capture-first ingest: raw bytes, octet-stream encoding.
    pub async fn ingest(&self, label: &str, src: &SocketAddr, payload: &[u8]) {
        self.ingest_with_encoding(label, src, payload, Encoding::APPLICATION_OCTET_STREAM)
            .await;
    }

    /// Ingest with an explicit encoding (for arms that decode/transcode before
    /// publishing). Gates, keys, and enqueues. Does not block on the network.
    pub async fn ingest_with_encoding(
        &self,
        label: &str,
        src: &SocketAddr,
        payload: &[u8],
        encoding: Encoding,
    ) {
        Stats::bump(&self.stats.received);

        if !self.allowlist.allows(src) {
            Stats::bump(&self.stats.dropped_source);
            warn!(feed = label, source = %src.ip(), "dropped: source not allowed");
            return;
        }

        self.outbox.push(Pending::new(
            format!("{}/{}/{}", self.key_prefix, label, source_chunk(src)),
            payload.to_vec(),
            encoding,
        ));
    }

    /// Own the session: publish what the socket path queues, and hold it when the pen
    /// is unreachable instead of putting it into the void.
    ///
    /// This is the task that makes a pen restart survivable. `apply` restarts the
    /// router on every ACL change — i.e. whenever any publisher is added anywhere on
    /// the pod — and the arms are deliberately NOT restarted with it. Measured
    /// reconnect is a few seconds; everything that arrives in that window waits here.
    pub fn spawn_drain(self: &Arc<Self>) -> JoinHandle<()> {
        let me = Arc::clone(self);
        tokio::spawn(async move {
            // Assume up: the arm only reaches this point after a session opened, and
            // starting "down" would log a spurious outage on every clean start.
            let mut up = true;
            loop {
                if me.outbox.is_empty() {
                    me.outbox.wait(LIVENESS_INTERVAL).await;
                }

                let now_up = pen_reachable(&me.session).await;
                if now_up != up {
                    if now_up {
                        info!(
                            pending = me.outbox.len(),
                            "pen session restored — resuming; anything older than max_age \
                             is discarded rather than published late"
                        );
                    } else {
                        me.stats.note_pen_down();
                        warn!(
                            pending = me.outbox.len(),
                            capacity = me.outbox.policy().capacity,
                            "PEN UNREACHABLE — dropping this feed until it returns. Data is \
                             held only briefly (max_age) and is counted, never replayed late."
                        );
                    }
                    up = now_up;
                }

                if !up {
                    tokio::time::sleep(LIVENESS_INTERVAL).await;
                    continue;
                }

                // Drain what is there, skipping anything that has aged out. On the
                // far side of an outage this is usually the whole backlog, and that is
                // the intended behaviour: a stale track is worse than a missing one.
                while let Some(item) = me.outbox.pop_fresh() {
                    match me
                        .session
                        .put(&item.key, item.payload.clone())
                        .encoding(item.encoding.clone())
                        .await
                    {
                        Ok(()) => Stats::bump(&me.stats.forwarded),
                        Err(e) => {
                            Stats::bump(&me.stats.forward_errors);
                            // Dropped, not retried. This is a transport fault, not an
                            // ACL denial — a denied write is accepted here and
                            // discarded by the router, logging nothing at either end.
                            // Retrying would only publish it later, and later is what
                            // makes a track wrong. Break to re-check the session:
                            // whatever is behind this is about to age out anyway.
                            error!(key = item.key, error = %format!("{e}"), "zenoh put failed");
                            break;
                        }
                    }
                }
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::{next_sample, TestPen, ARRIVES, QUIET};
    use std::sync::atomic::Ordering;

    async fn ingestor(pen: &TestPen, allow: &str) -> Arc<Ingestor> {
        let session = pen.client().open().await.expect("open");
        Arc::new(Ingestor::new(
            Arc::new(session),
            "raw/octo",
            SourceAllowlist::from_csv(allow),
        ))
    }

    fn addr(s: &str) -> SocketAddr {
        s.parse().expect("socket address")
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn an_allowed_unit_is_published_under_label_and_source_ip_unchanged() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("raw/octo/**").await;
        let ing = ingestor(&pen, "192.0.2.").await;
        let _drain = ing.spawn_drain();

        ing.ingest("tracks", &addr("192.0.2.50:4000"), b"\x01\x02payload")
            .await;

        let sample = next_sample(&sub, ARRIVES).await.expect("published");
        // Provenance is the source IP only; the source port never reaches the key.
        assert_eq!(sample.key_expr().as_str(), "raw/octo/tracks/192-0-2-50");
        assert_eq!(sample.payload().to_bytes().as_ref(), b"\x01\x02payload");
        assert_eq!(*sample.encoding(), Encoding::APPLICATION_OCTET_STREAM);
        assert_eq!(ing.stats.received.load(Ordering::Relaxed), 1);
        assert_eq!(ing.stats.queued.load(Ordering::Relaxed), 1);
        assert_eq!(ing.stats.dropped_source.load(Ordering::Relaxed), 0);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_source_outside_the_allowlist_is_counted_and_never_published() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("raw/octo/**").await;
        let ing = ingestor(&pen, "192.0.2., 2001:db8:1:").await;
        let _drain = ing.spawn_drain();

        // 2001:db8:10::1 starts with "2001:db8:1" but not with the declared "2001:db8:1:".
        // Both are documentation addresses (RFC 3849), unlike an IPv4 near-miss of
        // "192.0.2.", which would have to leave 192.0.2.0/24.
        ing.ingest("tracks", &addr("[2001:db8:10::1]:4000"), b"spoof")
            .await;
        ing.ingest("tracks", &addr("198.51.100.7:4000"), b"other")
            .await;

        assert!(next_sample(&sub, QUIET).await.is_none());
        assert_eq!(ing.stats.received.load(Ordering::Relaxed), 2);
        assert_eq!(ing.stats.dropped_source.load(Ordering::Relaxed), 2);
        assert_eq!(ing.stats.queued.load(Ordering::Relaxed), 0);
        assert!(ing.outbox.is_empty());
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn an_explicit_encoding_reaches_the_pen() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("raw/octo/**").await;
        let ing = ingestor(&pen, "").await;
        let _drain = ing.spawn_drain();

        ing.ingest_with_encoding(
            "cot",
            &addr("[2001:db8::7]:9"),
            b"<event/>",
            Encoding::TEXT_XML,
        )
        .await;

        let sample = next_sample(&sub, ARRIVES).await.expect("published");
        assert_eq!(sample.key_expr().as_str(), "raw/octo/cot/2001-db8--7");
        assert_eq!(*sample.encoding(), Encoding::TEXT_XML);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_shared_gate_uses_its_own_allowlist_but_the_same_queue_and_counters() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("raw/octo/**").await;
        let peer_gate = ingestor(&pen, "192.0.2.").await;
        let own_gate = Arc::new(Ingestor::sharing(&peer_gate, SourceAllowlist::default()));
        assert!(Arc::ptr_eq(&peer_gate.stats, &own_gate.stats));
        assert!(Arc::ptr_eq(&peer_gate.outbox, &own_gate.outbox));
        let _drain = peer_gate.spawn_drain();

        // The box's own record has no vouched-for source; the open gate passes it.
        own_gate
            .ingest("lifecycle", &addr("203.0.113.9:1"), b"up")
            .await;
        // The peer gate still refuses the same address.
        peer_gate
            .ingest("tracks", &addr("203.0.113.9:1"), b"no")
            .await;

        let sample = next_sample(&sub, ARRIVES).await.expect("own record");
        assert_eq!(sample.key_expr().as_str(), "raw/octo/lifecycle/203-0-113-9");
        assert!(next_sample(&sub, QUIET).await.is_none());
        assert_eq!(own_gate.stats.received.load(Ordering::Relaxed), 2);
        assert_eq!(own_gate.stats.dropped_source.load(Ordering::Relaxed), 1);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn the_drain_counts_a_pen_outage_and_resumes_when_the_pen_returns() {
        let pen = TestPen::start().await;
        let port = pen.port();
        let ing = ingestor(&pen, "").await;
        let _drain = ing.spawn_drain();

        pen.session.close().await.expect("stop the pen");
        drop(pen);
        let deadline = tokio::time::Instant::now() + ARRIVES;
        while ing.stats.pen_outages.load(Ordering::Relaxed) == 0 {
            assert!(
                tokio::time::Instant::now() < deadline,
                "outage never noticed"
            );
            tokio::time::sleep(Duration::from_millis(50)).await;
        }

        let pen = TestPen::start_on(port).await;
        let sub = pen.subscribe("raw/octo/**").await;
        // Keep offering until the client has reconnected and the drain resumed.
        let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
        let sample = loop {
            ing.ingest("tracks", &addr("192.0.2.1:1"), b"after").await;
            if let Some(s) = next_sample(&sub, Duration::from_millis(500)).await {
                break s;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "drain never resumed"
            );
        };
        assert_eq!(sample.payload().to_bytes().as_ref(), b"after");
        assert_eq!(ing.stats.pen_outages.load(Ordering::Relaxed), 1);
        assert!(ing.stats.forwarded.load(Ordering::Relaxed) >= 1);
    }
}
