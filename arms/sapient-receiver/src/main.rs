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
    // This receiver's own node identity, stable for the life of the process. The ack's
    // envelope node_id is ours; its destination_id is the registering node's.
    let our_id = uuid::Uuid::new_v4().to_string();

    let listener = TcpListener::bind((args.bind.as_str(), args.port))
        .await
        .with_context(|| format!("binding {}:{}", args.bind, args.port))?;
    info!(
        name = %args.name,
        node_id = %our_id,
        mode = if args.strict { "strict" } else { "lenient" },
        "listening on {}:{}",
        args.bind,
        args.port
    );

    loop {
        let (sock, peer) = listener.accept().await.context("accept")?;
        let our_id = our_id.clone();
        let name = args.name.clone();
        let strict = args.strict;
        tokio::spawn(async move {
            if let Err(e) = session(
                sock,
                &our_id,
                &name,
                strict,
                std::time::Duration::from_millis(args.status_interval_ms),
            )
            .await
            {
                warn!(%peer, "session ended with error: {e:#}");
            }
        });
    }
}
