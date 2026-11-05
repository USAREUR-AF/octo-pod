#!/usr/bin/env python3
"""Compare a requested management origin with NetBird's structured status."""
import ipaddress
import json
import sys
from urllib.parse import urlsplit


def origin(value):
    """Keep IPv6 addresses whole and compare the scheme and effective port too."""
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise ValueError("invalid management URL")
    url = urlsplit(value)
    if (url.scheme not in ("http", "https") or not url.hostname
            or url.username is not None or url.password is not None
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("management URL must be an HTTP(S) origin")
    host = url.hostname.lower()
    try:
        host = ipaddress.ip_address(host).compressed
    except ValueError:
        # A colon belongs to a bracketed IP literal, never a DNS name.
        if ":" in host or not all(c.isalnum() or c in ".-" for c in host):
            raise ValueError("invalid management hostname") from None
        host = host.rstrip(".").encode("idna").decode("ascii")
        if not host or any(not label or len(label) > 63 for label in host.split(".")):
            raise ValueError("invalid management hostname")
    port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("invalid management port")
    return url.scheme, host, port


def connected(expected, status):
    if not isinstance(status, dict):
        return False
    management = status.get("management")
    return (isinstance(management, dict) and management.get("connected") is True
            and origin(management.get("url")) == origin(expected))


def main():
    try:
        if len(sys.argv) != 3 or sys.argv[1] not in ("validate", "connected"):
            return 2
        origin(sys.argv[2])
        if sys.argv[1] == "validate":
            return 0
        return 0 if connected(sys.argv[2], json.load(sys.stdin)) else 1
    except (ValueError, TypeError, UnicodeError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
