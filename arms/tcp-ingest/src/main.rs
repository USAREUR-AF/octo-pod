// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! tcp-ingest — octo-pod arm #2: labeled TCP listeners with pluggable
//! framing → the shared ingest path (octo-arm-common).
//!
//! The legacy-TCP-feed class: SAPIENT sensor traffic (protobuf over TCP),
//! CoT-over-TCP, line protocols, or raw stream capture. Each accepted
//! connection is read continuously; the feed's framing mode turns the
//! stream into ingestible units; everything after that (gate, provenance
//! key `<prefix>/<label>/<src-ip>`, mTLS publish, counters) is
//! `octo_arm_common::Ingestor` — identical across arms by contract.
//!
//! Feeds: `label=port[:framing]` (framing: stream|newline|len32be|len32le;
//! default from --default-framing, itself defaulting to `stream`). When a
//! feed's real framing is unknown, start with `stream` — nothing is lost,
//! only unit boundaries — and fix the mode after seeing real bytes.
//!
//! License: Apache-2.0

use std::sync::Arc;

use anyhow::{Context, Result};
use clap::Parser;
use octo_arm_common::{FrameBuf, Framing, Ingestor, SessionConfig, SourceAllowlist};
use tokio::io::AsyncReadExt;
use tokio::net::{TcpListener, TcpStream};
use tracing::{error, info, warn};

#[derive(Parser, Debug, Clone)]
#[command(
    name = "tcp-ingest",
    about = "octo-pod arm: labeled TCP listeners (pluggable framing) to native mTLS Zenoh publish"
)]
struct Args {
    /// Feeds to serve: comma-separated `label=port[:framing]`.
    /// Example: `sapient=48000:len32le,cot=8087:newline,mystery=9000`.
    #[arg(long, env = "TCP_INGEST_FEEDS")]
    feeds: String,

    /// Framing for feeds that don't specify one.
    #[arg(long, env = "TCP_INGEST_DEFAULT_FRAMING", default_value = "stream")]
    default_framing: String,

    /// Host address the TCP listeners bind.
    #[arg(long, env = "TCP_INGEST_BIND_HOST", default_value = "0.0.0.0")]
    bind_host: String,

    /// Key prefix every publish lands under: `<prefix>/<label>/<src-ip>`.
    #[arg(long, env = "TCP_INGEST_KEY_PREFIX", default_value = "raw/octo")]
    key_prefix: String,

    /// Zenoh router endpoint (`tls/...` = mTLS; `tcp/...` local testing).
    #[arg(
        long,
        env = "TCP_INGEST_ENDPOINT",
        default_value = "tls/127.0.0.1:7447"
    )]
    endpoint: String,

    /// Client leaf certificate (PEM). Required for a `tls/` endpoint.
    #[arg(long, env = "TCP_INGEST_CERT")]
    cert: Option<String>,

    /// Client private key (PEM). Required for a `tls/` endpoint.
    #[arg(long, env = "TCP_INGEST_KEY")]
    key: Option<String>,

    /// Trust root for the router's server cert (PEM). Required for `tls/`.
    #[arg(long, env = "TCP_INGEST_ROOT_CA")]
    root_ca: Option<String>,

    /// Comma-separated source-IP prefixes to accept. Empty = any.
    #[arg(long, env = "TCP_INGEST_ALLOW_SOURCES", default_value = "")]
    allow_sources: String,

    /// Seconds between stats lines.
    #[arg(long, env = "TCP_INGEST_STATS_SECS", default_value_t = 60)]
    stats_secs: u64,
}

#[derive(Debug, Clone)]
struct Feed {
    label: String,
    port: u16,
    framing: Framing,
}

fn parse_feeds(spec: &str, default_framing: Framing) -> Result<Vec<Feed>> {
    let mut feeds = Vec::new();
    for part in spec.split(',').map(str::trim).filter(|s| !s.is_empty()) {
        let (label, rest) = part
            .split_once('=')
            .with_context(|| format!("feed `{part}` is not `label=port[:framing]`"))?;
        let label = label.trim();
        anyhow::ensure!(
            !label.is_empty()
                && label
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'),
            "feed label `{label}` must be non-empty [A-Za-z0-9_-] (it becomes a key chunk)"
        );
        let (port_s, framing) = match rest.split_once(':') {
            Some((p, f)) => (p, Framing::parse(f.trim())?),
            None => (rest, default_framing),
        };
        let port: u16 = port_s
            .trim()
            .parse()
            .with_context(|| format!("feed `{part}`: bad port"))?;
        feeds.push(Feed {
            label: label.to_string(),
            port,
            framing,
        });
    }
    anyhow::ensure!(!feeds.is_empty(), "no feeds configured (TCP_INGEST_FEEDS)");
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
    let default_framing = Framing::parse(&args.default_framing)?;
    let feeds = parse_feeds(&args.feeds, default_framing)?;

    info!(
        feeds = ?feeds,
        bind_host = %args.bind_host,
        key_prefix = %args.key_prefix,
        endpoint = %args.endpoint,
        allow_sources = %args.allow_sources,
        "tcp-ingest starting"
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
        let listener = TcpListener::bind(&bind)
            .await
            .with_context(|| format!("binding tcp {bind} for feed `{}`", feed.label))?;
        info!(feed = %feed.label, bind = %bind, framing = ?feed.framing, "tcp listener bound");
        tokio::spawn(serve_feed(listener, feed.clone(), ingestor.clone()));
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

async fn serve_feed(listener: TcpListener, feed: Feed, ingestor: Arc<Ingestor>) {
    loop {
        let (stream, peer) = match listener.accept().await {
            Ok(v) => v,
            Err(e) => {
                error!(feed = %feed.label, error = %e, "tcp accept failed");
                tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                continue;
            }
        };
        info!(feed = %feed.label, peer = %peer, "connection accepted");
        tokio::spawn(serve_conn(stream, feed.clone(), ingestor.clone()));
    }
}

async fn serve_conn(mut stream: TcpStream, feed: Feed, ingestor: Arc<Ingestor>) {
    let peer = match stream.peer_addr() {
        Ok(p) => p,
        Err(e) => {
            warn!(feed = %feed.label, error = %e, "no peer addr; dropping connection");
            return;
        }
    };
    let mut fb = FrameBuf::new(feed.framing);
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let n = match stream.read(&mut buf).await {
            Ok(0) => {
                info!(feed = %feed.label, peer = %peer, "connection closed");
                return;
            }
            Ok(n) => n,
            Err(e) => {
                warn!(feed = %feed.label, peer = %peer, error = %e, "read failed; closing");
                return;
            }
        };
        fb.extend(&buf[..n]);
        loop {
            match fb.next_frame() {
                Ok(Some(frame)) => ingestor.ingest(&feed.label, &peer, &frame).await,
                Ok(None) => break,
                Err(e) => {
                    // Unrecoverable for this connection (oversize frame /
                    // wrong framing mode) — close loudly.
                    error!(feed = %feed.label, peer = %peer, error = %e, "framing error; closing connection");
                    return;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_label_may_use_dash_and_underscore_but_may_not_be_empty() {
        let feeds = parse_feeds("cot-1=8087:newline,sapient_2=48000", Framing::Stream).unwrap();
        assert_eq!(feeds[0].label, "cot-1");
        assert_eq!(feeds[1].label, "sapient_2");
        assert!(parse_feeds("=8087", Framing::Stream).is_err());
    }

    #[test]
    fn feeds_parse_with_framing() {
        let feeds = parse_feeds(
            "sapient=48000:len32le, cot=8087:newline, x=9000",
            Framing::Stream,
        )
        .unwrap();
        assert_eq!(feeds[0].framing, Framing::Len32Le);
        assert_eq!(feeds[1].framing, Framing::Newline);
        assert_eq!(feeds[2].framing, Framing::Stream);
    }

    #[test]
    fn feeds_reject_bad() {
        assert!(parse_feeds("a=1:bogus", Framing::Stream).is_err());
        assert!(parse_feeds("", Framing::Stream).is_err());
        assert!(parse_feeds("a.b=1", Framing::Stream).is_err());
        assert!(parse_feeds("a=70000", Framing::Stream).is_err());
        assert!(parse_feeds("nolabel", Framing::Stream).is_err());
    }

    use octo_arm_common::testing::{free_tcp_port, next_sample, TestPen, ARRIVES, QUIET};
    use std::time::Duration;
    use tokio::io::AsyncWriteExt;
    use tokio::sync::oneshot;

    type Sub =
        zenoh::pubsub::Subscriber<zenoh::handlers::FifoChannelHandler<zenoh::sample::Sample>>;

    fn args(pen: &TestPen, feeds: &str, allow: &str) -> Args {
        Args::try_parse_from([
            "tcp-ingest",
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

    fn start(args: Args) -> (oneshot::Sender<()>, tokio::task::JoinHandle<Result<()>>) {
        let (stop, stopped) = oneshot::channel::<()>();
        let task = tokio::spawn(run(args, async move {
            let _ = stopped.await;
            Ok(())
        }));
        (stop, task)
    }

    /// Connect once the arm's listener is up.
    async fn connect(port: u16) -> TcpStream {
        let deadline = tokio::time::Instant::now() + ARRIVES;
        loop {
            if let Ok(s) = TcpStream::connect(("127.0.0.1", port)).await {
                return s;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "listener never came up"
            );
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    }

    async fn payloads(sub: &Sub, n: usize) -> Vec<Vec<u8>> {
        let mut out = Vec::new();
        for _ in 0..n {
            let s = next_sample(sub, ARRIVES).await.expect("frame published");
            assert_eq!(s.key_expr().as_str().rsplit('/').next(), Some("127-0-0-1"));
            out.push(s.payload().to_bytes().to_vec());
        }
        out
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn newline_frames_split_across_reads_publish_one_sample_per_line() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("raw/octo/cot/**").await;
        let port = free_tcp_port();
        let (stop, task) = start(args(&pen, &format!("cot={port}:newline"), "127.0.0."));

        let mut conn = connect(port).await;
        conn.write_all(b"first\r\nsec").await.expect("write");
        conn.flush().await.expect("flush");
        tokio::time::sleep(Duration::from_millis(100)).await;
        conn.write_all(b"ond\npartial").await.expect("write");

        assert_eq!(
            payloads(&sub, 2).await,
            vec![b"first".to_vec(), b"second".to_vec()]
        );
        // A line with no newline yet is held, not published as a fragment.
        assert!(next_sample(&sub, QUIET).await.is_none());

        stop.send(()).expect("stop");
        task.await.expect("join").expect("clean shutdown");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn length_prefixed_frames_publish_their_payloads_without_the_prefix() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("raw/octo/sapient/**").await;
        let port = free_tcp_port();
        let (stop, task) = start(args(&pen, &format!("sapient={port}:len32le"), ""));

        let mut wire = Framing::Len32Le.encode(b"\x08\x01").expect("frame");
        wire.extend(Framing::Len32Le.encode(b"").expect("empty frame"));
        wire.extend(Framing::Len32Le.encode(b"tail").expect("frame"));
        let mut conn = connect(port).await;
        // One byte at a time: every split point inside a header and a payload.
        for b in &wire {
            conn.write_all(std::slice::from_ref(b))
                .await
                .expect("write");
        }

        assert_eq!(
            payloads(&sub, 3).await,
            vec![b"\x08\x01".to_vec(), Vec::new(), b"tail".to_vec()]
        );
        stop.send(()).expect("stop");
        task.await.expect("join").expect("clean shutdown");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn an_oversize_length_closes_that_connection_and_the_listener_keeps_serving() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("raw/octo/**").await;
        let port = free_tcp_port();
        let (stop, task) = start(args(&pen, &format!("feed={port}:len32be"), ""));

        let mut bad = connect(port).await;
        let huge = u32::try_from(octo_arm_common::framing::MAX_FRAME + 1).expect("fits");
        bad.write_all(&huge.to_be_bytes()).await.expect("write");
        let mut rest = Vec::new();
        let closed = tokio::time::timeout(ARRIVES, bad.read_to_end(&mut rest)).await;
        assert!(
            closed.is_ok(),
            "the arm must close a connection it cannot frame"
        );
        assert!(next_sample(&sub, QUIET).await.is_none());

        let mut good = connect(port).await;
        good.write_all(&Framing::Len32Be.encode(b"ok").expect("frame"))
            .await
            .expect("write");
        assert_eq!(payloads(&sub, 1).await, vec![b"ok".to_vec()]);
        stop.send(()).expect("stop");
        task.await.expect("join").expect("clean shutdown");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_connection_from_outside_the_allowlist_publishes_nothing() {
        let pen = TestPen::start().await;
        let sub = pen.subscribe("raw/octo/**").await;
        let port = free_tcp_port();
        let (stop, task) = start(args(&pen, &format!("cot={port}:newline"), "192.0.2."));

        let mut conn = connect(port).await;
        conn.write_all(b"one\ntwo\n").await.expect("write");
        assert!(next_sample(&sub, QUIET).await.is_none());
        stop.send(()).expect("stop");
        task.await.expect("join").expect("clean shutdown");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn startup_fails_on_an_unknown_default_framing_or_a_taken_port() {
        let pen = TestPen::start().await;
        let mut bad_default = args(&pen, "x=1", "");
        bad_default.default_framing = "chunks".into();
        let err = run(bad_default, async { Ok(()) })
            .await
            .expect_err("framing");
        assert!(format!("{err:#}").contains("unknown framing"), "{err:#}");

        let taken = std::net::TcpListener::bind("127.0.0.1:0").expect("occupy");
        let port = taken.local_addr().expect("addr").port();
        let err = run(args(&pen, &format!("cot={port}"), ""), async { Ok(()) })
            .await
            .expect_err("bind");
        assert!(format!("{err:#}").contains("feed `cot`"), "{err:#}");
    }
}
