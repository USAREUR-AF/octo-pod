// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Portable SAPIENT registration test receiver using the pod's protobuf definitions.
//! Strict mode rejects registrations that fail the pinned reference-harness checks;
//! lenient mode logs those failures and accepts. Neither mode emulates a specific
//! controller or certifies complete BSI conformance. See README.md for limitations.

use sapient_receiver::session;

use anyhow::{Context, Result};
use clap::Parser;
use tokio::net::TcpListener;
use tracing::{info, warn};

#[derive(Parser, Debug)]
#[command(
    name = "sapient-receiver",
    version,
    about = "SAPIENT BSI Flex 335 v2.0 fusion-node receiver"
)]
struct Args {
    /// TCP port to listen on. 5020 matches the lab stand-in receiver.
    #[arg(short, long, default_value_t = 5020)]
    port: u16,

    /// Address to bind. Default is all interfaces.
    #[arg(long, default_value = "0.0.0.0")]
    bind: String,

    /// Reject registrations that fail the implemented checks and return the reasons.
    /// Default (lenient) logs the violations but accepts.
    #[arg(long)]
    strict: bool,

    /// Controller status cadence after acceptance; keep below the peer's idle timeout.
    #[arg(long, default_value_t = 1000, value_parser = clap::value_parser!(u64).range(50..=60000))]
    status_interval_ms: u64,

    /// A friendly name for log lines.
    #[arg(long, default_value = "sapient-receiver")]
    name: String,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let args = Args::parse();
    let listener = TcpListener::bind((args.bind.as_str(), args.port))
        .await
        .with_context(|| format!("binding {}:{}", args.bind, args.port))?;
    serve(listener, &args).await
}

/// Accept connections forever, one SAPIENT session each.
async fn serve(listener: TcpListener, args: &Args) -> Result<()> {
    // This receiver's own node identity, stable for the life of the process. The ack's
    // envelope node_id is ours; its destination_id is the registering node's.
    let our_id = uuid::Uuid::new_v4().to_string();
    info!(
        name = %args.name,
        node_id = %our_id,
        mode = if args.strict { "strict" } else { "lenient" },
        "listening on {}",
        listener.local_addr().context("local_addr")?
    );

    loop {
        let (sock, peer) = listener.accept().await.context("accept")?;
        let our_id = our_id.clone();
        let name = args.name.clone();
        let strict = args.strict;
        let status_interval = std::time::Duration::from_millis(args.status_interval_ms);
        tokio::spawn(async move {
            if let Err(e) = session(sock, &our_id, &name, strict, status_interval).await {
                warn!(%peer, "session ended with error: {e:#}");
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use prost::Message;
    use sapient_proto::v2::{sapient_message::Content, SapientMessage};
    use std::time::Duration;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpStream;
    use tokio::time::timeout;

    const NODE: &str = "00000000-0000-4000-8000-00000000bbbb";

    fn args(extra: &[&str]) -> Args {
        let mut argv = vec!["sapient-receiver", "--status-interval-ms", "50"];
        argv.extend_from_slice(extra);
        Args::try_parse_from(argv).expect("args")
    }

    /// Serve on a loopback port; returns the address to dial.
    async fn start(args: Args) -> std::net::SocketAddr {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        tokio::spawn(async move { serve(listener, &args).await });
        addr
    }

    fn registration(valid: bool) -> Vec<u8> {
        let mut m = sapient_receiver::json::decode(include_str!(
            "../tests/vectors/true/Registration.FollowObject.json"
        ))
        .expect("vector");
        m.node_id = Some(NODE.into());
        if !valid {
            m.timestamp = None;
        }
        m.encode_to_vec()
    }

    async fn send(sock: &mut TcpStream, body: &[u8]) {
        let len = u32::try_from(body.len()).expect("fits");
        sock.write_all(&len.to_le_bytes()).await.expect("prefix");
        sock.write_all(body).await.expect("body");
    }

    /// The next frame, or None when the receiver closed the connection.
    async fn recv(sock: &mut TcpStream) -> Option<SapientMessage> {
        let mut hdr = [0u8; 4];
        let got = timeout(Duration::from_secs(5), sock.read_exact(&mut hdr))
            .await
            .expect("receiver answers or closes");
        if got.is_err() {
            return None;
        }
        let mut body = vec![0u8; u32::from_le_bytes(hdr) as usize];
        sock.read_exact(&mut body).await.expect("body");
        Some(SapientMessage::decode(body.as_slice()).expect("decodes"))
    }

    fn acceptance(m: &SapientMessage) -> (Option<bool>, usize) {
        match &m.content {
            Some(Content::RegistrationAck(a)) => (a.acceptance, a.ack_response_reason.len()),
            other => panic!("expected an ack, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn strict_mode_rejects_a_bad_registration_with_reasons_and_hangs_up() {
        let addr = start(args(&["--strict"])).await;
        let mut node = TcpStream::connect(addr).await.expect("connect");
        // An undecodable frame is logged and skipped, not fatal.
        send(&mut node, b"\xff\xff\xff").await;
        send(&mut node, &registration(false)).await;
        let ack = recv(&mut node).await.expect("ack");
        let (accepted, reasons) = acceptance(&ack);
        assert_eq!(accepted, Some(false));
        assert!(reasons > 0, "a rejection must carry its reasons");
        assert_eq!(ack.destination_id.as_deref(), Some(NODE));
        assert!(
            recv(&mut node).await.is_none(),
            "strict rejection ends the session"
        );
    }

    #[tokio::test]
    async fn lenient_mode_accepts_the_same_registration_and_starts_heartbeats() {
        let addr = start(args(&[])).await;
        let mut node = TcpStream::connect(addr).await.expect("connect");
        send(&mut node, &registration(false)).await;
        let ack = recv(&mut node).await.expect("ack");
        assert_eq!(acceptance(&ack), (Some(true), 0));
        let status = recv(&mut node).await.expect("heartbeat");
        assert!(matches!(status.content, Some(Content::StatusReport(_))));
        assert_eq!(status.node_id, ack.node_id, "one identity for the process");
    }

    #[tokio::test]
    async fn a_node_that_desyncs_loses_only_its_own_session() {
        let addr = start(args(&[])).await;
        let mut bad = TcpStream::connect(addr).await.expect("bad");
        bad.write_all(&0u32.to_le_bytes())
            .await
            .expect("zero prefix");
        assert!(
            recv(&mut bad).await.is_none(),
            "the receiver hangs up on a desync"
        );
        // The listener is unaffected.
        let mut good = TcpStream::connect(addr).await.expect("good");
        send(&mut good, &registration(true)).await;
        assert_eq!(
            acceptance(&recv(&mut good).await.expect("ack")),
            (Some(true), 0)
        );
    }

    #[tokio::test]
    async fn concurrent_nodes_each_get_their_own_session() {
        let addr = start(args(&["--strict"])).await;
        let mut a = TcpStream::connect(addr).await.expect("a");
        let mut b = TcpStream::connect(addr).await.expect("b");
        send(&mut b, &registration(true)).await;
        send(&mut a, &registration(true)).await;
        assert_eq!(
            acceptance(&recv(&mut a).await.expect("a ack")),
            (Some(true), 0)
        );
        assert_eq!(
            acceptance(&recv(&mut b).await.expect("b ack")),
            (Some(true), 0)
        );
    }

    #[test]
    fn defaults_are_lenient_on_the_lab_port_and_the_cadence_is_bounded() {
        let a = Args::try_parse_from(["sapient-receiver"]).expect("defaults");
        assert_eq!(a.port, 5020);
        assert!(!a.strict);
        assert_eq!(a.status_interval_ms, 1000);
        for bad in ["49", "60001", "0"] {
            assert!(
                Args::try_parse_from(["sapient-receiver", "--status-interval-ms", bad]).is_err(),
                "{bad}"
            );
        }
    }
}
