//! Source-address handling: the ip-only provenance chunk and the ingress
//! allowlist gate.

use std::net::SocketAddr;

/// Source IP → key chunk. Dots/colons become dashes so the chunk stays a
/// single key element (`192.0.2.50` → `192-0-2-50`).
pub fn source_chunk(addr: &SocketAddr) -> String {
    addr.ip()
        .to_string()
        .chars()
        .map(|c| if c == '.' || c == ':' { '-' } else { c })
        .collect()
}

/// Ingress gate: comma-separated IP string-prefixes (e.g. `192.0.2.`).
/// Empty = accept any source. This is defense-in-depth, not identity —
/// the trust class of everything an arm forwards stays *ip-only*.
#[derive(Debug, Clone, Default)]
pub struct SourceAllowlist {
    prefixes: Vec<String>,
}

impl SourceAllowlist {
    pub fn from_csv(spec: &str) -> Self {
        Self {
            prefixes: spec
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.prefixes.is_empty()
    }

    pub fn allows(&self, addr: &SocketAddr) -> bool {
        if self.prefixes.is_empty() {
            return true;
        }
        let ip = addr.ip().to_string();
        self.prefixes.iter().any(|p| ip.starts_with(p))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_chunk_sanitizes() {
        let v4: SocketAddr = "192.0.2.50:9999".parse().unwrap();
        assert_eq!(source_chunk(&v4), "192-0-2-50");
        let v6: SocketAddr = "[fe80::1]:9999".parse().unwrap();
        assert_eq!(source_chunk(&v6), "fe80--1");
    }

    #[test]
    fn allowlist_gates() {
        let ok: SocketAddr = "192.0.2.50:1".parse().unwrap();
        let bad: SocketAddr = "10.0.0.9:1".parse().unwrap();
        let open = SourceAllowlist::from_csv("");
        assert!(open.allows(&ok) && open.allows(&bad));
        let gated = SourceAllowlist::from_csv("192.0.2., 198.51.100.");
        assert!(gated.allows(&ok));
        assert!(!gated.allows(&bad));
    }
}
