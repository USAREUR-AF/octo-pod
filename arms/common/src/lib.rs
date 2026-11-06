// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! octo-arm-common — the shared body every octo-pod arm plugs into.
//!
//! An arm is a thin binary that owns exactly one thing: its wire protocol
//! (UDP datagrams, CoT-over-TCP, syslog, ...). Everything else — the mTLS
//! Zenoh session, provenance keying, source gating, counters — is this
//! library, so every arm behaves identically where identical behavior IS
//! the contract (trust model, key grammar, loud-failure discipline).
//!
//! ```text
//! arm binary               octo-arm-common                    local pen
//! ┌────────────┐  bytes +  ┌──────────────────────────┐  put  ┌────────┐
//! │ wire proto │ ────────► │ Ingestor: gate → key →   │ ────► │ zenoh  │
//! │ listen/dial│  src addr │ publish (mTLS session) + │ mTLS  │ router │
//! └────────────┘           │ stats                    │       └────────┘
//! ```
//!
//! Copy the shape of `udp-ingest/` for a new wire protocol: implement only the
//! listener and hand each unit to `Ingestor::ingest`. Everything that defines trust
//! behaviour lives here so it cannot drift between arms.
//!
//! License: Apache-2.0

pub mod framing;
pub mod ingest;
pub mod lifecycle;
pub mod outbox;
pub mod session;
pub mod source;
pub mod stats;
#[cfg(any(test, feature = "test-support"))]
pub mod testing;

pub use framing::{FrameBuf, Framing};
pub use ingest::Ingestor;
pub use outbox::{Outbox, Pending, Policy};
pub use session::SessionConfig;
pub use source::{source_chunk, SourceAllowlist};
pub use stats::Stats;

/// Standard tracing init for arms: env-filter with an `info` floor.
pub fn init_tracing() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive(tracing::Level::INFO.into()),
        )
        .init();
}
