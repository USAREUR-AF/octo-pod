#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
bin/octo-pod-peer on a scratch identity: the refusals and edits test_templates.py's peer
walk-through does not reach.

WHY THIS EXISTS
    peer widens what a partner may do: dial the pen over the LAN, read fabric keys. Each
    widening is one explicit edit, so every malformed request must be refused with the
    declaration unchanged, and each edit must land exactly as written, comments and
    neighbours intact. test_templates.py walks the main path end to end; this covers
    the edges.

RUN
    python3 platform/tests/test_peer.py
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cli import Scratch  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
K1 = "1d18de66e8a09a5e68170b3b3de6563b/sensors/sensor2/**"
K2 = "1d18de66e8a09a5e68170b3b3de6563b/sensors/tpq50/**"

PEERS = """peers:
  # the partner's C2, onboarded after the site visit
  - id: c2
    kind: zenoh-native
    cert_cn: octo-peer-c2
    namespace: "raw/c2/**"
    enabled: true
    connects: client
    release:
      to_fabric: false
  - id: bare
    kind: zenoh-native
    namespace: "raw/bare/**"
  - id: noname
    kind: zenoh-native
    cert_cn: octo-peer-noname
"""


class Peer(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = Scratch(REPO, tmp.name)
        self.s.write("20-peers.yml", PEERS)

    def peer(self, ident):
        return {p["id"]: p for p in yaml.safe_load(self.s.read("20-peers.yml"))["peers"]}[ident]

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.s.pod.iterdir()}

    def run_peer(self, *argv):
        return self.s.run("octo-pod-peer", *argv)

    def refused(self, needle, *argv):
        before = self.snapshot()
        rc, out = self.run_peer(*argv)
        self.assertNotEqual(rc, 0, out)
        self.assertIn(needle, out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(self.snapshot(), before, f"{argv} changed a file")
        return out

    def test_usage_refusals(self):
        self.refused("usage: octo-pod peer allow-read <peer-id>", "allow-read")
        self.refused("usage: octo-pod peer allow-from <id> --from", "allow-from", "c2")
        self.refused("give --from or --clear, not both", "allow-from", "c2",
                     "--from", "192.0.2.30", "--clear")
        self.refused("is not a declared peer. Known: bare, c2, noname", "allow-from", "ghost",
                     "--from", "192.0.2.30")

    def test_needs_root(self):
        r = subprocess.run([sys.executable, str(REPO / "bin" / "octo-pod-peer"), "check"],
                           capture_output=True, text=True, env=self.s.env, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("sudo octo-pod peer check", r.stderr)

    def test_allow_from_refuses_what_is_not_one_literal_ipv4_address(self):
        for bad, needle in (("c2.example.invalid", "is not a literal IPv4 address"),
                            ("2001:db8::1", "is not a literal IPv4 address"),
                            ("0.0.0.0", "would open the pen to anyone"),
                            (" , ", "needs at least one IPv4 address")):
            with self.subTest(bad=bad):
                self.refused(needle, "allow-from", "c2", "--from", bad)

    def test_allow_from_keeps_comments_and_neighbours_and_notes_a_missing_lan_ip(self):
        rc, out = self.run_peer("allow-from", "c2", "--from", "192.0.2.30,192.0.2.31")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.peer("c2")["allow_sources"], "192.0.2.30, 192.0.2.31")
        self.assertIn("note: this pod has no lan_ip", out)
        text = self.s.read("20-peers.yml")
        self.assertIn("# the partner's C2, onboarded after the site visit", text)
        self.assertEqual(self.peer("bare"), {"id": "bare", "kind": "zenoh-native",
                                             "namespace": "raw/bare/**"})
        self.s.write("00-site.yml", 'lan_ip: "192.0.2.5"\n')
        rc, out = self.run_peer("allow-from", "c2", "--from", "192.0.2.32")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.peer("c2")["allow_sources"], "192.0.2.32")
        self.assertNotIn("note: this pod has no lan_ip", out)

    def test_an_entry_with_no_namespace_line_is_left_for_a_person(self):
        self.refused("has no namespace line; fix 20-peers.yml by hand",
                     "allow-from", "noname", "--from", "192.0.2.30")

    def test_an_entry_it_cannot_find_by_line_is_refused(self):
        self.s.write("20-peers.yml", 'peers: [{id: flow, namespace: "raw/flow/**"}]\n')
        self.refused("flow has no entry in 20-peers.yml", "allow-from", "flow",
                     "--from", "192.0.2.30")

    def test_allow_read_needs_keys_and_a_slot_for_a_fingerprint(self):
        self.refused("give at least one --key", "allow-read", "c2")
        self.refused("pass it with --slot", "allow-read", "c2", "--key", K1,
                     "--ca-fingerprint", "SHA256:" + "a" * 64)

    def test_allow_read_without_a_slot_says_nothing_will_come_down(self):
        rc, out = self.run_peer("allow-read", "c2", "--key", K1)
        self.assertEqual(rc, 0, out)
        self.assertIn("this pod holds no slot", out)
        self.assertIs(self.peer("c2")["release"]["from_fabric"], True)
        self.assertIs(self.peer("c2")["release"]["to_fabric"], False)

    def test_allow_read_on_a_peer_with_no_release_block_adds_one(self):
        rc, out = self.run_peer("allow-read", "bare", "--key", K1)
        self.assertEqual(rc, 0, out)
        bare = self.peer("bare")
        self.assertEqual((bare["read_keys"], bare["release"]), ([K1], {"from_fabric": True}))
        self.assertIn(f"downlink/{K1}", out)

    def test_revoke_read_on_a_peer_that_reads_nothing(self):
        rc, out = self.run_peer("revoke-read", "c2")
        self.assertEqual(rc, 0, out)
        self.assertIn("c2 reads nothing; nothing to revoke", out)

    def test_revoke_one_of_two_keys_keeps_the_other_open(self):
        self.assertEqual(self.run_peer("allow-read", "c2", "--key", K1, "--key", K2)[0], 0)
        rc, out = self.run_peer("revoke-read", "c2", "--key", K1)
        self.assertEqual(rc, 0, out)
        c2 = self.peer("c2")
        self.assertEqual(c2["read_keys"], [K2])
        self.assertIs(c2["release"]["from_fabric"], True)
        pod = yaml.safe_load(self.s.read("10-pod.yml"))
        self.assertEqual(pod["downlink_allow"], K2)
        self.assertIn("downlink_allow now holds 1 key(s)", out)

    def test_bundle_reports_what_the_peer_may_do(self):
        self.assertEqual(self.run_peer("allow-read", "c2", "--key", K1)[0], 0)
        src = self.s.pen / "bundles" / "c2"
        (src / "tls").mkdir(parents=True)
        for name in ("README.md", "tls/octo-peer-c2-cert.pem", "tls/octo-peer-c2-key.pem",
                     "tls/ca-roots.pem"):
            (src / name).write_text(f"synthetic {name}\n")
        out_path = self.s.root / "c2.tar.gz"
        rc, out = self.run_peer("bundle", "c2", "--out", str(out_path))
        self.assertEqual(rc, 0, out)
        self.assertIn("c2 may write   raw/c2/**", out)
        self.assertIn(f"c2 may read    downlink/{K1}", out)
        self.assertIn("c2 dials in    over the mesh", out)
        self.assertEqual(out_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
