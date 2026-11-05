//! Where an egress arm may deliver: the one destination it was declared with.
//!
//! Three shapes, the mirror of the inbound arms, because a consumer connects the way it
//! would connect to the sensor itself:
//!
//! - `udp://IP:PORT`: the arm sends each sample as a datagram (the consumer listens);
//! - `tcp://IP:PORT`: the arm dials the consumer and streams (the consumer listens);
//! - `tcp-server://IP:PORT`: the consumer dials the arm, as it would dial the radar, and
//!   the arm streams to it. IP is the pod's own address to listen on.
//!
//! It fails closed: the address must be a literal socket address (a name could resolve
//! somewhere new tomorrow) and appear in the declared allow list, matched exactly on
//! address AND port. A `tcp-server://` destination also needs [`Peers`], the consumers
//! allowed to connect. The inbound `SourceAllowlist` matches IP string prefixes, so
//! `10.1.2.3` also admits `10.1.2.30`; these lists do not, and tests below hold them to it.

use std::fmt;
use std::net::{IpAddr, SocketAddr};

/// How bytes reach the destination.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scheme {
    /// One sample per datagram, from a socket connected to the destination.
    Udp,
    /// A byte stream on a connection this arm dials, re-dialled after a drop.
    Tcp,
    /// A byte stream to each consumer that dials this arm's listening address.
    TcpServer,
}

/// The destination an egress arm was declared with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Destination {
    /// Datagram or stream.
    pub scheme: Scheme,
    /// The literal socket address.
    pub addr: SocketAddr,
}

/// A destination or allow list the arm refuses to run with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DestError(pub String);

impl fmt::Display for DestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DestError {}

fn literal(spec: &str) -> Result<SocketAddr, DestError> {
    let addr: SocketAddr = spec.trim().parse().map_err(|_| {
        DestError(format!(
            "{spec:?} is not a literal IP:port; names are refused because they can resolve elsewhere"
        ))
    })?;
    if addr.port() == 0 || addr.ip().is_unspecified() {
        return Err(DestError(format!("{spec:?} names no concrete destination")));
    }
    Ok(addr)
}

impl Destination {
    /// Parse `udp://IP:PORT`, `tcp://IP:PORT` or `tcp-server://IP:PORT`.
    ///
    /// # Errors
    ///
    /// Fails on any other scheme, a host name, port 0 or an unspecified address (a
    /// listener on `0.0.0.0` would answer on every leg of the pod, the mesh included).
    pub fn parse(spec: &str) -> Result<Self, DestError> {
        let (scheme, rest) = if let Some(r) = spec.strip_prefix("udp://") {
            (Scheme::Udp, r)
        } else if let Some(r) = spec.strip_prefix("tcp-server://") {
            (Scheme::TcpServer, r)
        } else if let Some(r) = spec.strip_prefix("tcp://") {
            (Scheme::Tcp, r)
        } else {
            return Err(DestError(format!(
                "{spec:?} must start with udp://, tcp:// or tcp-server://"
            )));
        };
        Ok(Self {
            scheme,
            addr: literal(rest)?,
        })
    }
}

impl fmt::Display for Destination {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let scheme = match self.scheme {
            Scheme::Udp => "udp",
            Scheme::Tcp => "tcp",
            Scheme::TcpServer => "tcp-server",
        };
        write!(f, "{scheme}://{}", self.addr)
    }
}

/// The declared allow list: exact socket addresses. Empty allows nothing.
#[derive(Debug, Clone, Default)]
pub struct DestinationAllowlist {
    allowed: Vec<SocketAddr>,
}

impl DestinationAllowlist {
    /// Parse a comma-separated list of literal `IP:port` entries.
    ///
    /// # Errors
    ///
    /// Fails on the first entry that is not a literal socket address.
    pub fn from_csv(spec: &str) -> Result<Self, DestError> {
        let allowed = spec
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(literal)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self { allowed })
    }

    /// Exact match on address and port. Never a prefix, never a subnet.
    #[must_use]
    pub fn allows(&self, addr: &SocketAddr) -> bool {
        self.allowed.contains(addr)
    }

    /// Check the declared destination before anything is opened.
    ///
    /// # Errors
    ///
    /// Fails, so the arm exits without sending, when the destination is not listed.
    pub fn admit(&self, dest: &Destination) -> Result<(), DestError> {
        if self.allows(&dest.addr) {
            Ok(())
        } else {
            Err(DestError(format!(
                "destination {dest} is not in the declared allow list {:?}",
                self.allowed
            )))
        }
    }
}

/// The consumers a `tcp-server://` destination accepts: exact IP addresses. Empty allows
/// nothing, and the arm refuses to listen without at least one.
#[derive(Debug, Clone, Default)]
pub struct Peers {
    allowed: Vec<IpAddr>,
}

impl Peers {
    /// Parse a comma-separated list of literal IP addresses.
    ///
    /// # Errors
    ///
    /// Fails on the first entry that is not a literal IP address, or an unspecified one.
    pub fn from_csv(spec: &str) -> Result<Self, DestError> {
        let allowed = spec
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|s| {
                s.parse::<IpAddr>()
                    .ok()
                    .filter(|ip| !ip.is_unspecified())
                    .ok_or_else(|| DestError(format!("{s:?} is not a literal IP address")))
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self { allowed })
    }

    /// Exact match on the address. Never a prefix, never a subnet.
    #[must_use]
    pub fn permits(&self, ip: IpAddr) -> bool {
        self.allowed.contains(&ip)
    }

    /// True when no consumer may connect.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.allowed.is_empty()
    }

    /// The listed addresses, for logs and the audit record.
    #[must_use]
    pub fn describe(&self) -> String {
        let list: Vec<String> = self.allowed.iter().map(ToString::to_string).collect();
        list.join(",")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sa(s: &str) -> SocketAddr {
        s.parse().unwrap()
    }

    #[test]
    fn parses_a_listening_destination() {
        let d = Destination::parse("tcp-server://192.0.2.18:9000").unwrap();
        assert_eq!(d.scheme, Scheme::TcpServer);
        assert_eq!(d.addr, sa("192.0.2.18:9000"));
        assert_eq!(d.to_string(), "tcp-server://192.0.2.18:9000");
        assert!(
            Destination::parse("tcp-server://0.0.0.0:9000").is_err(),
            "a listener on every leg of the pod is refused"
        );
    }

    #[test]
    fn peers_are_exact_and_fail_closed() {
        let peers = Peers::from_csv("10.1.2.3, 10.1.2.4").unwrap();
        assert!(peers.permits("10.1.2.3".parse().unwrap()));
        assert!(
            !peers.permits("10.1.2.30".parse().unwrap()),
            "no prefix match"
        );
        assert!(Peers::from_csv("").unwrap().is_empty());
        assert!(Peers::from_csv("receiver.example.test").is_err());
        assert!(Peers::from_csv("0.0.0.0").is_err());
        assert!(Peers::from_csv("10.1.2.0/24").is_err(), "no subnets");
    }

    #[test]
    fn parses_literal_destinations() {
        let d = Destination::parse("udp://192.0.2.20:4001").unwrap();
        assert_eq!(d.scheme, Scheme::Udp);
        assert_eq!(d.addr, sa("192.0.2.20:4001"));
        assert_eq!(d.to_string(), "udp://192.0.2.20:4001");
        assert_eq!(
            Destination::parse("tcp://[fe80::1]:4100").unwrap().scheme,
            Scheme::Tcp
        );
    }

    #[test]
    fn refuses_names_and_vague_destinations() {
        for bad in [
            "udp://ext-standin:4001",
            "192.0.2.20:4001",
            "http://192.0.2.20:4001",
            "udp://192.0.2.20:0",
            "udp://0.0.0.0:4001",
            "udp://192.0.2.20",
        ] {
            assert!(Destination::parse(bad).is_err(), "{bad} must be refused");
        }
    }

    #[test]
    fn allow_list_is_exact_on_address_and_port() {
        let allow = DestinationAllowlist::from_csv("10.1.2.3:4001, 10.1.2.4:4100").unwrap();
        assert!(allow.allows(&sa("10.1.2.3:4001")));
        assert!(allow.allows(&sa("10.1.2.4:4100")));
        // The prefix case octo-pod's inbound SourceAllowlist admits:
        assert!(!allow.allows(&sa("10.1.2.30:4001")));
        assert!(
            !allow.allows(&sa("10.1.2.3:4002")),
            "another port is another destination"
        );
    }

    #[test]
    fn empty_allow_list_fails_closed() {
        let allow = DestinationAllowlist::from_csv("").unwrap();
        let dest = Destination::parse("udp://10.1.2.3:4001").unwrap();
        assert!(allow.admit(&dest).is_err());
    }

    #[test]
    fn admit_checks_the_declared_destination() {
        let allow = DestinationAllowlist::from_csv("10.1.2.3:4001").unwrap();
        assert!(allow
            .admit(&Destination::parse("udp://10.1.2.3:4001").unwrap())
            .is_ok());
        assert!(allow
            .admit(&Destination::parse("udp://10.1.2.66:4001").unwrap())
            .is_err());
        assert!(DestinationAllowlist::from_csv("ext-standin:4001").is_err());
    }
}
