//! tcp-connect — octo-pod arm #4: dial-out TCP with reconnect → the shared
//! ingest path (octo-arm-common).
//!
//! The inverse of `tcp-ingest`, and the reason it exists: `tcp-ingest`
//! LISTENS for sources that dial in, but a large class of sensor is itself
//! the server. A radar of the radar-one class accepts a client on its data
//! port and then writes at it; so does the simulator that replays captured
//! radar-one wire bytes. Two servers never connect, so that whole class had no
//! arm — field integrations were bridging it with hand-written scripts.
//!
//! Everything downstream of "bytes arrived" is identical to every other arm
//! by contract: allowlist gate, provenance key `<prefix>/<label>/<src-ip>`,
//! mTLS publish, counters. Only the dial direction differs.
//!
//! Feeds: `label=host:port[:framing]` (framing: stream|newline|len32be|
//! len32le; default from --default-framing, itself defaulting to `stream`).
//!
//! Reconnect is the other thing dial-out needs that listening does not: the
//! sensor may be powered down, rebooting, or across a link that drops. Each
//! feed retries forever with capped exponential backoff, so a pod left
//! running comes back on its own when the sensor does.
//!
//! License: Apache-2.0

use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use clap::Parser;
use octo_arm_common::{FrameBuf, Framing, Ingestor, SessionConfig, SourceAllowlist};
use tokio::io::AsyncReadExt;
use tokio::net::TcpStream;
use tracing::{error, info, warn};

/// First retry delay after a failed connect or a dropped stream.
const BACKOFF_START: Duration = Duration::from_secs(1);
/// Ceiling for the retry delay. A sensor that is off for an hour should not
/// be hammered, but must be picked up promptly once it returns.
const BACKOFF_MAX: Duration = Duration::from_secs(30);

#[derive(Parser, Debug, Clone)]
#[command(
    name = "tcp-connect",
    about = "octo-pod arm: dial-out TCP to sensors that serve their own stream, to native mTLS Zenoh publish"
)]
struct Args {
    /// Feeds to dial: comma-separated `label=host:port[:framing]`.
    /// Example: `radar-one=192.0.2.104:9000,radar2=192.0.2.105:9001:len32le`.
    #[arg(long, env = "TCP_CONNECT_FEEDS")]
    feeds: String,

    /// Framing for feeds that don't specify one.
    #[arg(long, env = "TCP_CONNECT_DEFAULT_FRAMING", default_value = "stream")]
    default_framing: String,

    /// Key prefix every publish lands under: `<prefix>/<label>/<src-ip>`.
    #[arg(long, env = "TCP_CONNECT_KEY_PREFIX", default_value = "raw/octo")]
    key_prefix: String,

    /// Zenoh router endpoint (`tls/...` = mTLS; `tcp/...` local testing).
    #[arg(
        long,
        env = "TCP_CONNECT_ENDPOINT",
        default_value = "tls/127.0.0.1:7447"
    )]
    endpoint: String,

    /// Client leaf certificate (PEM). Required for a `tls/` endpoint.
    #[arg(long, env = "TCP_CONNECT_CERT")]
    cert: Option<String>,

    /// Client private key (PEM). Required for a `tls/` endpoint.
    #[arg(long, env = "TCP_CONNECT_KEY")]
    key: Option<String>,

    /// Trust root for the router's server cert (PEM). Required for `tls/`.
    #[arg(long, env = "TCP_CONNECT_ROOT_CA")]
    root_ca: Option<String>,

    /// Comma-separated source-IP prefixes to accept. Empty = any.
    ///
    /// Weaker here than on a listening arm — we chose who to dial — but it
    /// still catches a feed whose name resolves somewhere unexpected.
    #[arg(long, env = "TCP_CONNECT_ALLOW_SOURCES", default_value = "")]
    allow_sources: String,

    /// Seconds between stats lines.
    #[arg(long, env = "TCP_CONNECT_STATS_SECS", default_value_t = 60)]
    stats_secs: u64,
}

#[derive(Debug, Clone)]
struct Feed {
    label: String,
    /// `host:port` as written; resolved fresh on every dial so a sensor that
    /// moves behind a name is picked up without restarting the arm.
    target: String,
    framing: Framing,
}

fn parse_feeds(spec: &str, default_framing: Framing) -> Result<Vec<Feed>> {
    let mut feeds = Vec::new();
    for part in spec.split(',').map(str::trim).filter(|s| !s.is_empty()) {
        let (label, rest) = part
            .split_once('=')
            .with_context(|| format!("feed `{part}` is not `label=host:port[:framing]`"))?;
        let label = label.trim();
        anyhow::ensure!(
            !label.is_empty()
                && label
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'),
            "feed label `{label}` must be non-empty [A-Za-z0-9_-] (it becomes a key chunk)"
        );

        // `host:port` or `host:port:framing`. Split from the right so IPv6
        // literals in brackets and hostnames both behave.
        let rest = rest.trim();
        let (target, framing) = match rest.rsplit_once(':') {
            Some((head, tail)) if Framing::parse(tail.trim()).is_ok() => {
                (head.trim(), Framing::parse(tail.trim())?)
            }
            _ => (rest, default_framing),
        };

        anyhow::ensure!(
            target
                .rsplit_once(':')
                .is_some_and(|(h, p)| { !h.is_empty() && p.parse::<u16>().is_ok() }),
            "feed `{part}`: target `{target}` is not `host:port`"
        );

        feeds.push(Feed {
            label: label.to_string(),
            target: target.to_string(),
            framing,
        });
    }
    anyhow::ensure!(!feeds.is_empty(), "no feeds configured (TCP_CONNECT_FEEDS)");
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
        key_prefix = %args.key_prefix,
        endpoint = %args.endpoint,
        allow_sources = %args.allow_sources,
        "tcp-connect starting"
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
        tokio::spawn(dial_feed(feed.clone(), ingestor.clone()));
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

/// Dial one feed forever: connect, stream until it ends, back off, repeat.
async fn dial_feed(feed: Feed, ingestor: Arc<Ingestor>) {
    let mut backoff = BACKOFF_START;
    loop {
        match TcpStream::connect(&feed.target).await {
            Ok(stream) => {
                backoff = BACKOFF_START; // a successful dial resets the ramp
                info!(feed = %feed.label, target = %feed.target, framing = ?feed.framing, "connected");
                stream_conn(stream, &feed, &ingestor).await;
                warn!(feed = %feed.label, target = %feed.target, "stream ended; reconnecting");
            }
            Err(e) => {
                warn!(
                    feed = %feed.label,
                    target = %feed.target,
                    error = %e,
                    retry_in_s = backoff.as_secs(),
                    "connect failed"
                );
            }
        }
        tokio::time::sleep(backoff).await;
        backoff = (backoff * 2).min(BACKOFF_MAX);
    }
}

/// Read one connection to exhaustion, framing and ingesting as it goes.
async fn stream_conn(mut stream: TcpStream, feed: &Feed, ingestor: &Arc<Ingestor>) {
    // Provenance is the address we actually reached, not the name we dialed.
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
                info!(feed = %feed.label, peer = %peer, "sensor closed the connection");
                return;
            }
            Ok(n) => n,
            Err(e) => {
                warn!(feed = %feed.label, peer = %peer, error = %e, "read failed");
                return;
            }
        };
        fb.extend(&buf[..n]);
        loop {
            match fb.next_frame() {
                Ok(Some(frame)) => ingestor.ingest(&feed.label, &peer, &frame).await,
                Ok(None) => break,
                Err(e) => {
                    // Oversize frame / wrong framing mode: unrecoverable for
                    // this connection. Close loudly and let backoff retry.
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
    fn feeds_parse_target_and_framing() {
        let feeds = parse_feeds(
            "radar-one=192.0.2.104:9000, sapient=sensor-a:48000:len32le, cot=host:8087:newline",
            Framing::Stream,
        )
        .unwrap();
        assert_eq!(feeds[0].target, "192.0.2.104:9000");
        assert_eq!(feeds[0].framing, Framing::Stream);
        assert_eq!(feeds[1].target, "sensor-a:48000");
        assert_eq!(feeds[1].framing, Framing::Len32Le);
        assert_eq!(feeds[2].target, "host:8087");
        assert_eq!(feeds[2].framing, Framing::Newline);
    }

    #[test]
    fn default_framing_applies_when_unspecified() {
        let feeds = parse_feeds("x=h:1234", Framing::Newline).unwrap();
        assert_eq!(feeds[0].framing, Framing::Newline);
    }

    #[test]
    fn feeds_reject_bad() {
        // no port
        assert!(parse_feeds("a=hostonly", Framing::Stream).is_err());
        // bad port
        assert!(parse_feeds("a=host:notaport", Framing::Stream).is_err());
        // empty spec
        assert!(parse_feeds("", Framing::Stream).is_err());
        // label that would not survive as a key chunk
        assert!(parse_feeds("bad/label=host:1", Framing::Stream).is_err());
    }

    #[test]
    fn backoff_ramps_and_caps() {
        let mut b = BACKOFF_START;
        for _ in 0..10 {
            b = (b * 2).min(BACKOFF_MAX);
        }
        assert_eq!(b, BACKOFF_MAX);
    }
}
