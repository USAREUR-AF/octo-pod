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
    let args = Args::parse();
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

    tokio::signal::ctrl_c()
        .await
        .context("waiting for ctrl-c")?;
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
    }
}
