// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Test support: a real Zenoh router in the test process, so an arm's whole path
//! (socket in, gate, key, publish) runs against the same library it runs against on a
//! pod, over loopback, with nothing mocked.
//!
//! Compiled only for this crate's tests and for crates that enable the `test-support`
//! feature from their `[dev-dependencies]`. No arm binary carries it.

use std::time::Duration;

use crate::SessionConfig;

/// A router-mode Zenoh session listening on a loopback port: the test's pen.
pub struct TestPen {
    /// The router's own session. A subscriber declared here sees every publication
    /// routed to the pen.
    pub session: zenoh::Session,
    /// `tcp/127.0.0.1:<port>`: what an arm under test dials.
    pub endpoint: String,
}

impl TestPen {
    /// Start a router on a free loopback port, with scouting off so a test never finds
    /// (or is found by) anything else on the machine.
    ///
    /// # Panics
    ///
    /// Panics if no port is free or the router cannot open: a test cannot proceed.
    pub async fn start() -> Self {
        Self::start_on(free_tcp_port()).await
    }

    /// Start a router on a given loopback port: how a test brings the pen back after
    /// stopping it, at the address its clients already dial.
    ///
    /// # Panics
    ///
    /// Panics if the router cannot open.
    pub async fn start_on(port: u16) -> Self {
        let endpoint = format!("tcp/127.0.0.1:{port}");
        let mut config = zenoh::Config::default();
        for (key, value) in [
            ("mode", "\"router\"".to_string()),
            ("listen/endpoints", format!("[\"{endpoint}\"]")),
            ("scouting/multicast/enabled", "false".to_string()),
            ("scouting/gossip/enabled", "false".to_string()),
        ] {
            config
                .insert_json5(key, &value)
                .unwrap_or_else(|e| panic!("test pen config {key}: {e}"));
        }
        let session = zenoh::open(config)
            .await
            .unwrap_or_else(|e| panic!("test pen did not open: {e}"));
        Self { session, endpoint }
    }

    /// The loopback port this pen listens on.
    ///
    /// # Panics
    ///
    /// Never: the endpoint is built by [`start_on`](Self::start_on) from a port.
    pub fn port(&self) -> u16 {
        self.endpoint
            .rsplit(':')
            .next()
            .and_then(|p| p.parse().ok())
            .unwrap_or_else(|| panic!("endpoint {} has no port", self.endpoint))
    }

    /// The plaintext client configuration an arm under test uses to reach this pen.
    pub fn client(&self) -> SessionConfig {
        SessionConfig {
            endpoint: self.endpoint.clone(),
            cert: None,
            key: None,
            root_ca: None,
        }
    }

    /// Subscribe on the pen. Declared before the arm publishes, so nothing is missed.
    ///
    /// # Panics
    ///
    /// Panics if the router refuses the subscriber.
    pub async fn subscribe(
        &self,
        key: &str,
    ) -> zenoh::pubsub::Subscriber<zenoh::handlers::FifoChannelHandler<zenoh::sample::Sample>> {
        self.session
            .declare_subscriber(key.to_string())
            .await
            .unwrap_or_else(|e| panic!("subscribe {key}: {e}"))
    }
}

/// A free TCP port on loopback. The listener is dropped before the port is used, which
/// is a race in principle and has not been one in practice on a test runner.
///
/// # Panics
///
/// Panics if the OS has no free port.
pub fn free_tcp_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or_else(|e| panic!("no free tcp port: {e}"))
}

/// A free UDP port on loopback, with the same caveat as [`free_tcp_port`].
///
/// # Panics
///
/// Panics if the OS has no free port.
pub fn free_udp_port() -> u16 {
    std::net::UdpSocket::bind("127.0.0.1:0")
        .and_then(|s| s.local_addr())
        .map(|a| a.port())
        .unwrap_or_else(|e| panic!("no free udp port: {e}"))
}

/// The next sample on `sub`, or `None` after `within`.
pub async fn next_sample(
    sub: &zenoh::pubsub::Subscriber<zenoh::handlers::FifoChannelHandler<zenoh::sample::Sample>>,
    within: Duration,
) -> Option<zenoh::sample::Sample> {
    tokio::time::timeout(within, sub.recv_async())
        .await
        .ok()
        .and_then(Result::ok)
}

/// How long a test waits for something that should arrive. Generous, because a loaded
/// CI runner is slow; a test that expects nothing waits [`QUIET`] instead.
pub const ARRIVES: Duration = Duration::from_secs(10);

/// How long a test waits to conclude that something did NOT arrive.
pub const QUIET: Duration = Duration::from_millis(750);
