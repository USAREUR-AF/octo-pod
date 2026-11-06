// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! udp-ingest — octo-pod arm #1: labeled UDP listeners → the shared
//! ingest path (octo-arm-common).
//!
//! Site-local systems that will never adopt X.509 / mesh / Zenoh emit raw
//! UDP datagrams to a statically-programmed IP+port. This arm listens
//! where the device expects; everything after the socket (allowlist gate,
//! `<prefix>/<label>/<src-ip>` provenance keying, mTLS publish, counters)
//! is `octo_arm_common::Ingestor` — identical across arms by contract.
//!
//! One process serves N feeds (`label=port` pairs); one UDP listener per
//! feed. Capture-first: raw bytes, no schema prerequisite; typed decode is
//! a per-feed upgrade: parse in the arm and publish with `ingest_with_encoding`.
//!
//! License: Apache-2.0

use std::sync::Arc;

use anyhow::{Context, Result};
use clap::Parser;
use octo_arm_common::{Ingestor, SessionConfig, SourceAllowlist};
use tokio::net::UdpSocket;
use tracing::{error, info};

#[derive(Parser, Debug, Clone)]
#[command(
    name = "udp-ingest",
    about = "octo-pod arm: labeled UDP listeners to native mTLS Zenoh publish"
)]
struct Args {
    /// Feeds to serve: comma-separated `label=port` pairs. One UDP listener
    /// per feed. Example: `telemetry=5001,tracks=5002`.
    #[arg(long, env = "UDP_INGEST_FEEDS")]
    feeds: String,

    /// Host address the UDP listeners bind. On the pod this is the LAN IP
    /// the source systems are programmed to send to.
    #[arg(long, env = "UDP_INGEST_BIND_HOST", default_value = "0.0.0.0")]
    bind_host: String,

    /// Key prefix every publish lands under: `<prefix>/<label>/<src-ip>`.
    /// Must sit inside the namespace the arm's cert is ACL-scoped to.
    #[arg(long, env = "UDP_INGEST_KEY_PREFIX", default_value = "raw/octo")]
    key_prefix: String,

    /// Zenoh router endpoint. `tls/...` selects native mTLS; `tcp/...` is
    /// plaintext for local testing only.
    #[arg(
        long,
        env = "UDP_INGEST_ENDPOINT",
        default_value = "tls/127.0.0.1:7447"
    )]
    endpoint: String,

    /// Client leaf certificate (PEM). Required for a `tls/` endpoint.
    #[arg(long, env = "UDP_INGEST_CERT")]
    cert: Option<String>,

    /// Client private key (PEM). Required for a `tls/` endpoint.
    #[arg(long, env = "UDP_INGEST_KEY")]
    key: Option<String>,

    /// Trust root the router's server cert must chain to (PEM). Required
    /// for a `tls/` endpoint.
    #[arg(long, env = "UDP_INGEST_ROOT_CA")]
    root_ca: Option<String>,

    /// Comma-separated source-IP prefixes to accept (string-prefix match,
    /// e.g. `192.0.2.`). Empty = accept any source. Non-matching
    /// datagrams are dropped and counted.
    #[arg(long, env = "UDP_INGEST_ALLOW_SOURCES", default_value = "")]
    allow_sources: String,

    /// Seconds between stats lines.
    #[arg(long, env = "UDP_INGEST_STATS_SECS", default_value_t = 60)]
    stats_secs: u64,
}

#[derive(Debug, Clone)]
struct Feed {
    label: String,
    port: u16,
}

fn parse_feeds(spec: &str) -> Result<Vec<Feed>> {
    let mut feeds = Vec::new();
    for part in spec.split(',').map(str::trim).filter(|s| !s.is_empty()) {
        let (label, port) = part
            .split_once('=')
            .with_context(|| format!("feed `{part}` is not `label=port`"))?;
        let label = label.trim();
        anyhow::ensure!(
            !label.is_empty()
                && label
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'),
            "feed label `{label}` must be non-empty [A-Za-z0-9_-] (it becomes a key chunk)"
        );
        let port: u16 = port
            .trim()
            .parse()
            .with_context(|| format!("feed `{part}`: bad port"))?;
        feeds.push(Feed {
            label: label.to_string(),
            port,
        });
    }
    anyhow::ensure!(!feeds.is_empty(), "no feeds configured (UDP_INGEST_FEEDS)");
    Ok(feeds)
}

#[tokio::main]
async fn main() -> Result<()> {
    octo_arm_common::init_tracing();
    run(Args::parse(), async {
        tokio::signal::ctrl_c().await.context("waiting for ctrl-c")
    })
    .await
}

/// The whole arm: bind every feed, open the pen session, publish until `shutdown`
/// resolves. `main` passes ctrl-c; a test passes its own trigger.
async fn run(args: Args, shutdown: impl std::future::Future<Output = Result<()>>) -> Result<()> {
    let feeds = parse_feeds(&args.feeds)?;

    info!(
        feeds = ?feeds,
        bind_host = %args.bind_host,
        key_prefix = %args.key_prefix,
        endpoint = %args.endpoint,
        allow_sources = %args.allow_sources,
        "udp-ingest starting"
    );

    let session = SessionConfig {
        endpoint: args.endpoint.clone(),
        cert: args.cert.clone(),
        key: args.key.clone(),
        root_ca: args.root_ca.clone(),
    }
    .open()
    .await?;
    info!("zenoh session opened");

    let ingestor = Arc::new(Ingestor::new(
        Arc::new(session),
        args.key_prefix.clone(),
        SourceAllowlist::from_csv(&args.allow_sources),
    ));

    for feed in &feeds {
        let bind = format!("{}:{}", args.bind_host, feed.port);
        let socket = UdpSocket::bind(&bind)
            .await
            .with_context(|| format!("binding udp {bind} for feed `{}`", feed.label))?;
        info!(feed = %feed.label, bind = %bind, "udp listener bound");
        tokio::spawn(serve_feed(socket, feed.clone(), ingestor.clone()));
    }

    // Owns the session and publishes what the socket path queues. Without it an arm
    // holds data and never sends any; with it, a pen restart is latency, not loss.
    let drain_task = ingestor.spawn_drain();
    let stats_task = octo_arm_common::stats::spawn_stats_logger_with_depth(
        ingestor.stats.clone(),
        args.stats_secs,
        Some(ingestor.outbox.clone()),
    );

    shutdown.await?;
    info!("shutdown signal received");
    stats_task.abort();
    drain_task.abort();
    Ok(())
}

async fn serve_feed(socket: UdpSocket, feed: Feed, ingestor: Arc<Ingestor>) {
    // Max UDP payload; datagrams arrive whole or not at all.
    let mut buf = vec![0u8; 65535];
    loop {
        let (len, src) = match socket.recv_from(&mut buf).await {
            Ok(v) => v,
            Err(e) => {
                error!(feed = %feed.label, error = %e, "udp recv failed");
                tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                continue;
            }
        };
        ingestor.ingest(&feed.label, &src, &buf[..len]).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_label_may_use_dash_and_underscore_but_may_not_be_empty() {
        let feeds = parse_feeds("radar-1=5001,beacon_2=5002").unwrap();
        assert_eq!(feeds[0].label, "radar-1");
        assert_eq!(feeds[1].label, "beacon_2");
        assert!(parse_feeds("=5001").is_err());
        assert!(parse_feeds(" =5001").is_err());
    }

    #[test]
    fn feeds_parse() {
        let feeds = parse_feeds(" telemetry=5001, tracks=5002 ").unwrap();
        assert_eq!(feeds.len(), 2);
        assert_eq!(feeds[0].label, "telemetry");
        assert_eq!(feeds[0].port, 5001);
        assert_eq!(feeds[1].label, "tracks");
    }

    #[test]
    fn feeds_reject_bad_label_and_port() {
        assert!(parse_feeds("bad label=1").is_err());
        assert!(parse_feeds("x=notaport").is_err());
        assert!(parse_feeds("").is_err());
        assert!(parse_feeds("noport").is_err());
        assert!(
            parse_feeds("a/b=1").is_err(),
            "a slash would add a key level"
        );
    }

    use octo_arm_common::testing::{free_udp_port, next_sample, TestPen, ARRIVES, QUIET};
    use tokio::sync::oneshot;

    fn args(pen: &TestPen, feeds: &str, allow: &str) -> Args {
        Args::try_parse_from([
            "udp-ingest",
            "--feeds",
            feeds,
            "--bind-host",
            "127.0.0.1",
            "--endpoint",
            &pen.endpoint,
            "--allow-sources",
            allow,
        ])
        .expect("args")
    }

    /// Start the arm; the returned sender stops it.
    fn start(args: Args) -> (oneshot::Sender<()>, tokio::task::JoinHandle<Result<()>>) {
        let (stop, stopped) = oneshot::channel::<()>();
        let task = tokio::spawn(run(args, async move {
            let _ = stopped.await;
            Ok(())
        }));
        (stop, task)
    }

    /// Send until the arm's listener is bound and the datagram comes out on the pen.
    async fn send_until_published(
        port: u16,
        payload: &[u8],
        sub: &zenoh::pubsub::Subscriber<zenoh::handlers::FifoChannelHandler<zenoh::sample::Sample>>,
    ) -> zenoh::sample::Sample {
        let tx = std::net::UdpSocket::bind("127.0.0.1:0").expect("sender");
        let deadline = tokio::time::Instant::now() + ARRIVES;
        loop {
            tx.send_to(payload, ("127.0.0.1", port)).expect("send");
            if let Some(s) = next_sample(sub, std::time::Duration::from_millis(250)).await {
                return s;
            }
            assert!(tokio::time::Instant::now() < deadline, "never published");
        }
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn each_feed_publishes_its_datagrams_whole_under_its_own_label() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("raw/octo/**").await;
        let (a, b) = (free_udp_port(), free_udp_port());
        let (stop, task) = start(args(&pen, &format!("radar={a},beacon={b}"), "127.0.0."));

        let first = send_until_published(a, b"\x00scan\xff", &sub).await;
        assert_eq!(first.key_expr().as_str(), "raw/octo/radar/127-0-0-1");
        assert_eq!(first.payload().to_bytes().as_ref(), b"\x00scan\xff");
        let second = send_until_published(b, b"ping", &sub).await;
        assert_eq!(second.key_expr().as_str(), "raw/octo/beacon/127-0-0-1");

        stop.send(()).expect("stop");
        task.await.expect("join").expect("clean shutdown");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_datagram_from_outside_the_allowlist_never_reaches_the_pen() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("raw/octo/**").await;
        let port = free_udp_port();
        let (stop, task) = start(args(&pen, &format!("radar={port}"), "192.0.2."));

        let tx = std::net::UdpSocket::bind("127.0.0.1:0").expect("sender");
        for _ in 0..5 {
            tx.send_to(b"spoof", ("127.0.0.1", port)).expect("send");
            assert!(next_sample(&sub, QUIET).await.is_none());
        }
        stop.send(()).expect("stop");
        task.await.expect("join").expect("clean shutdown");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_port_already_in_use_fails_startup_naming_the_feed() {
        let pen = TestPen::start().await;
        let taken = std::net::UdpSocket::bind("127.0.0.1:0").expect("occupy");
        let port = taken.local_addr().expect("addr").port();
        let (_stop, task) = start(args(&pen, &format!("radar={port}"), ""));
        let err = task.await.expect("join").expect_err("bind must fail");
        assert!(format!("{err:#}").contains("feed `radar`"), "{err:#}");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_feed_port_out_of_range_fails_startup() {
        let pen = TestPen::start().await;
        let (_stop, task) = start(args(&pen, "radar=99999", ""));
        let err = task.await.expect("join").expect_err("bad port");
        assert!(format!("{err:#}").contains("bad port"), "{err:#}");
    }
}
