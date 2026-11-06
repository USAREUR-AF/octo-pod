#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
bin/octo-pod-declare on a scratch identity: what each verb writes, and every refusal.

WHY THIS EXISTS
    Each verb edits a declaration an apply later turns into firewall rules, listeners and
    certificates. A value it lets through reaches the pod; a value it refuses must leave
    every file exactly as it was. So each refusal here also checks that nothing changed,
    and each write checks the file still parses with the entry in it.

    The script runs in its own interpreter with the root check answered by test_cli's
    shim and OCTO_POD_ETC pointed at a temporary directory. Nothing touches a real pod.

RUN
    python3 platform/tests/test_declare.py
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cli import ARMS_YML, ARM_A, Scratch  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


class Declare(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = Scratch(REPO, tmp.name)
        self.s.write("30-arms.yml", ARMS_YML)
        self.s.write("10-pod.yml", 'pen_id: "pod-x"\nfabric_slot_anchor: ""\nbridge_allow: ""\n')

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.s.pod.iterdir()}

    def run_declare(self, *argv):
        return self.s.run("octo-pod-declare", *argv)

    def refused(self, needle, *argv):
        before = self.snapshot()
        rc, out = self.run_declare(*argv)
        self.assertNotEqual(rc, 0, out)
        self.assertIn(needle, out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(self.snapshot(), before, f"{argv} changed a file")
        return out

    # The identity this edits.

    def test_refuses_without_root_before_parsing_anything(self):
        r = subprocess.run([sys.executable, str(REPO / "bin" / "octo-pod-declare"), "add-arm"],
                           capture_output=True, text=True, env=self.s.env, timeout=60)
        if os.geteuid() == 0:
            self.skipTest("running as root")
        self.assertEqual(r.returncode, 2)
        self.assertIn("run with sudo", r.stderr)
        self.assertIn("sudo octo-pod add-arm", r.stderr)

    def test_refuses_with_no_pod_identity_or_with_two(self):
        empty = Path(self.s.root) / "empty"
        self.s.env["OCTO_POD_ETC"] = str(empty)
        rc, out = self.run_declare("set-firewall", "on")
        self.assertNotEqual(rc, 0)
        self.assertIn("no pod identity under", out)
        self.s.env["OCTO_POD_ETC"] = str(self.s.root / "etc")
        (self.s.pod.parent / "pod-y").mkdir()
        rc, out = self.run_declare("set-firewall", "on")
        self.assertNotEqual(rc, 0)
        self.assertIn("more than one pod under", out)

    # set-firewall

    def test_set_firewall_on_then_off(self):
        rc, out = self.run_declare("set-firewall", "on")
        self.assertEqual(rc, 0, out)
        self.assertIn("pod_firewall_enabled: true", self.s.read("10-pod.yml"))
        self.assertIn("SSH stays open to the mesh ONLY", out)
        rc, out = self.run_declare("set-firewall", "OFF")
        self.assertEqual(rc, 0, out)
        pod = self.s.read("10-pod.yml")
        self.assertEqual(pod.count("pod_firewall_enabled:"), 1)
        self.assertIn("pod_firewall_enabled: false", pod)
        self.assertNotIn("SSH stays open", out)

    def test_set_firewall_on_names_the_lan_ssh_sources(self):
        self.s.write("00-site.yml", 'mgmt_ssh_sources: ["192.0.2.9/32"]\n')
        rc, out = self.run_declare("set-firewall", "on")
        self.assertEqual(rc, 0, out)
        self.assertIn("SSH stays open to the mesh and to 192.0.2.9/32", out)

    def test_set_firewall_needs_on_or_off(self):
        self.refused("usage: octo-pod set-firewall on|off", "set-firewall", "maybe")
        self.refused("usage: octo-pod set-firewall on|off", "set-firewall")

    # set-lan

    def test_set_lan_writes_and_then_replaces(self):
        rc, out = self.run_declare("set-lan", "--iface", "eth1", "--addr", "192.0.2.10/24",
                                   "--ssh-from", "192.0.2.9, 198.51.100.0/24")
        self.assertEqual(rc, 0, out)
        site = yaml.safe_load(self.s.read("00-site.yml"))
        self.assertEqual(site, {"lan_iface": "eth1", "lan_ip": "192.0.2.10", "lan_prefix": 24,
                                "mgmt_ssh_sources": ["192.0.2.9/32", "198.51.100.0/24"]})
        rc, out = self.run_declare("set-lan", "--iface", "eth2", "--addr", "192.0.2.11/25",
                                   "--ssh-from", "")
        self.assertEqual(rc, 0, out)
        site = yaml.safe_load(self.s.read("00-site.yml"))
        self.assertEqual(site, {"lan_iface": "eth2", "lan_ip": "192.0.2.11", "lan_prefix": 25,
                                "mgmt_ssh_sources": []})
        self.assertIn("(none: SSH only from the mesh)", out)

    def test_set_lan_refusals(self):
        self.refused("usage: octo-pod set-lan", "set-lan", "--iface", "eth1")
        self.refused("needs a prefix", "set-lan", "--iface", "eth1", "--addr", "192.0.2.10")
        for addr in ("192.0.2.300/24", "192.0.2.10/0", "192.0.2.10/33", "2001:db8::1/64",
                     "192.0.2.10/x"):
            with self.subTest(addr=addr):
                self.refused("is not a valid IPv4 address with prefix",
                             "set-lan", "--iface", "eth1", "--addr", addr)
        self.refused("is not an address or CIDR", "set-lan", "--iface", "eth1", "--addr",
                     "192.0.2.10/24", "--ssh-from", "jump.example.invalid")
        for wide in ("10.0.0.0/7", "2001:db8::/64"):
            with self.subTest(wide=wide):
                self.refused("must be IPv4 and no wider than /8", "set-lan", "--iface", "eth1",
                             "--addr", "192.0.2.10/24", "--ssh-from", wide)

    # set-mesh

    def test_set_mesh_records_which_mesh_and_prints_the_join(self):
        rc, out = self.run_declare("set-mesh", "--mgmt", "https://netbird.example.net",
                                   "--repo", "https://releases.example.net",
                                   "--portal", "https://portal.example.net",
                                   "--mirror", "localhost:5000")
        self.assertEqual(rc, 0, out)
        fabric = yaml.safe_load(self.s.read("05-fabric.yml"))
        self.assertEqual(fabric["netbird_mgmt_host"], "https://netbird.example.net")
        self.assertEqual(fabric["octo_repo_url"], "https://releases.example.net")
        self.assertEqual(fabric["fabric_portal_url"], "https://portal.example.net")
        self.assertEqual(fabric["image_mirror"], "localhost:5000")
        self.assertIn("sudo octo-pod join --setup-key <KEY> --mgmt https://netbird.example.net "
                      "--pod pod-x", out)
        rc, out = self.run_declare("set-mesh", "--mirror", "registry.example.net:5000")
        self.assertEqual(rc, 0, out)
        text = self.s.read("05-fabric.yml")
        self.assertEqual(text.count("image_mirror:"), 1)
        self.assertIn('image_mirror: "registry.example.net:5000"', text)
        self.assertIn("octo-pod check", out)

    def test_set_mesh_refusals(self):
        self.refused("usage: octo-pod set-mesh", "set-mesh")
        self.refused("must be a URL", "set-mesh", "--mgmt", "netbird.example.net")
        self.refused("must be a URL", "set-mesh", "--repo", "releases.example.net")
        self.refused("must be a URL", "set-mesh", "--portal", "ftp://portal.example.net")
        self.refused("is a registry host:port, not a URL", "set-mesh",
                     "--mirror", "https://localhost:5000")

    # release: the refusals before any bundle is read. The bundle paths are in
    # test_templates.py (arm release).

    def test_release_needs_an_id_and_a_slot(self):
        self.refused("usage: octo-pod release <publisher-id> --slot <dir>", "release")
        self.refused("--slot <dir> is required", "release", ARM_A)

    # add-arm

    def test_add_arm_listening_with_framing_and_bind(self):
        rc, out = self.run_declare("add-arm", "radar-2", "--tcp", "48030", "--from", "192.0.2.7",
                                   "--framing", "len32be", "--bind", "192.0.2.10",
                                   "--feed", "cat62-v1", "--description", "a radar")
        self.assertEqual(rc, 0, out)
        arm = next(a for a in yaml.safe_load(self.s.read("30-arms.yml"))["arms"]
                   if a["id"] == "radar-2")
        self.assertEqual({k: arm[k] for k in ("kind", "transport", "port", "framing",
                                              "bind_host", "allow_sources", "feed",
                                              "description", "cert_cn", "namespace")},
                         {"kind": "tcp-ingest", "transport": "tcp", "port": 48030,
                          "framing": "len32be", "bind_host": "192.0.2.10",
                          "allow_sources": "192.0.2.7", "feed": "cat62-v1",
                          "description": "a radar", "cert_cn": "octo-arm-radar-2",
                          "namespace": "raw/radar-2/**"})
        self.assertIs(arm["release"]["to_fabric"], False)
        self.assertIn("octo-pod check", out)

    def test_add_arm_dial_takes_its_source_from_the_target(self):
        rc, out = self.run_declare("add-arm", "radar-3", "--dial", "192.0.2.104:9000")
        self.assertEqual(rc, 0, out)
        self.assertIn("note: allow_sources taken from --dial (192.0.2.104)", out)
        arm = yaml.safe_load(self.s.read("30-arms.yml"))["arms"][-1]
        self.assertEqual((arm["kind"], arm["target"], arm["allow_sources"]),
                         ("tcp-connect", "192.0.2.104:9000", "192.0.2.104"))
        self.assertNotIn("port", arm)

    def test_add_arm_into_an_empty_or_missing_list(self):
        self.s.write("30-arms.yml", "arms: []\n")
        rc, out = self.run_declare("add-arm", "first", "--udp", "48040", "--from", "192.0.2.8")
        self.assertEqual(rc, 0, out)
        self.assertEqual([a["id"] for a in yaml.safe_load(self.s.read("30-arms.yml"))["arms"]],
                         ["first"])
        (self.s.pod / "30-arms.yml").unlink()
        rc, out = self.run_declare("add-arm", "second", "--udp", "48041", "--from", "192.0.2.8")
        self.assertEqual(rc, 0, out)
        self.assertEqual([a["id"] for a in yaml.safe_load(self.s.read("30-arms.yml"))["arms"]],
                         ["second"])

    def test_add_arm_refusals(self):
        ok = ["--from", "192.0.2.7"]
        cases = [
            ("must be lowercase alphanumeric/dashes", ["add-arm", "Radar", "--udp", "1", *ok]),
            ("must be lowercase alphanumeric/dashes", ["add-arm"]),
            ("is already declared in", ["add-arm", ARM_A, "--udp", "48050", *ok]),
            ("give exactly one of --udp, --tcp or --dial", ["add-arm", "r", *ok]),
            ("(got --udp, --tcp)", ["add-arm", "r", "--udp", "48050", "--tcp", "48051", *ok]),
            ("must be HOST:PORT", ["add-arm", "r", "--dial", "192.0.2.1"]),
            ("must be HOST:PORT", ["add-arm", "r", "--dial", "192.0.2.1:70000"]),
            ("must be HOST:PORT", ["add-arm", "r", "--dial", ":9000"]),
            ("--from is required when --dial names a hostname",
             ["add-arm", "r", "--dial", "sensor.example.invalid:9000"]),
            ("must be a port number, 1-65535", ["add-arm", "r", "--udp", "0", *ok]),
            ("must be a port number, 1-65535", ["add-arm", "r", "--tcp", "65536", *ok]),
            ("is already used by another arm", ["add-arm", "r", "--udp", "48021", *ok]),
            ("is reserved (pen listener / ssh)", ["add-arm", "r", "--tcp", "7447", *ok]),
            ("is reserved (pen listener / ssh)", ["add-arm", "r", "--tcp", "22", *ok]),
            ("is not an IP address. It is the address on THIS pod",
             ["add-arm", "r", "--udp", "48050", "--bind", "lan0", *ok]),
            ("must match [A-Za-z0-9_-]", ["add-arm", "r", "--udp", "48050", "--feed", "a/b", *ok]),
            ("--from is required: an arm's publisher presents no credential",
             ["add-arm", "r", "--udp", "48050"]),
            ("is not an IP address", ["add-arm", "r", "--udp", "48050", "--from", "192.0.2.7,x"]),
            ("--framing applies to --tcp or --dial only",
             ["add-arm", "r", "--udp", "48050", "--framing", "newline", *ok]),
        ]
        for needle, argv in cases:
            with self.subTest(argv=argv):
                self.refused(needle, *argv)

    def test_an_id_yaml_would_read_as_something_else_is_refused_before_writing(self):
        # Written unquoted, off is false, null is nothing and 0x1f is 31. Each used to be
        # written, then fail its own read-back, leaving an entry no command could find.
        for arm_id in ("off", "yes", "no", "on", "true", "null", "123", "0x1f", "017", "0b11"):
            with self.subTest(arm_id=arm_id):
                self.refused("in YAML, not as a name", "add-arm", arm_id, "--udp", "48090",
                             "--from", "192.0.2.7")
                self.refused("in YAML, not as a name", "add-peer", arm_id)
                before = self.snapshot()
                rc, out = self.s.run("octo-pod-egress", "add", arm_id, "--dest",
                                     "udp://192.0.2.20:8600", "--key", "slot/sensors/a/**")
                self.assertNotEqual(rc, 0, out)
                self.assertIn("in YAML, not as a name", out)
                self.assertEqual(self.snapshot(), before)
        # A name YAML keeps as a string is still accepted.
        rc, out = self.run_declare("add-arm", "1e3", "--udp", "48091", "--from", "192.0.2.7")
        self.assertEqual(rc, 0, out)

    def test_an_egress_id_is_taken_for_arms_and_peers(self):
        self.s.write("40-egress.yml", "egress:\n- id: relay\n  dest: udp://192.0.2.20:8600\n")
        self.refused("is already declared in", "add-arm", "relay", "--udp", "48060",
                     "--from", "192.0.2.7")
        self.refused("is already declared in", "add-peer", "relay")

    # add-peer

    def test_add_peer_mesh_only_and_lan(self):
        rc, out = self.run_declare("add-peer", "acme", "--description", "a partner")
        self.assertEqual(rc, 0, out)
        peer = yaml.safe_load(self.s.read("20-peers.yml"))["peers"][0]
        self.assertEqual((peer["id"], peer["kind"], peer["connects"], peer["cert_cn"]),
                         ("acme", "zenoh-native", "client", "octo-peer-acme"))
        self.assertNotIn("allow_sources", peer)
        self.assertIn("sudo octo-pod peer bundle acme", out)
        rc, out = self.run_declare("add-peer", "beta", "--from", "192.0.2.30, 192.0.2.31")
        self.assertEqual(rc, 0, out)
        peer = yaml.safe_load(self.s.read("20-peers.yml"))["peers"][1]
        self.assertEqual(peer["allow_sources"], "192.0.2.30, 192.0.2.31")

    def test_add_peer_refusals(self):
        self.refused("--framing applies to an arm, not a peer", "add-peer", "p",
                     "--framing", "stream")
        self.refused("--udp/--tcp/--dial apply to add-arm", "add-peer", "p", "--udp", "48070")
        self.refused("--udp/--tcp/--dial apply to add-arm", "add-peer", "p",
                     "--dial", "192.0.2.1:9000")
        self.refused("is not a literal IPv4 address", "add-peer", "p", "--from", "2001:db8::1")
        self.refused("--from 0.0.0.0 would open the pen to anyone", "add-peer", "p",
                     "--from", "0.0.0.0")

    def test_a_declaration_that_does_not_parse_is_named_not_a_traceback(self):
        # A hand-edit left an unclosed quote. Every declaring CLI reads the file before it
        # writes, so each must name it and change nothing.
        self.s.write("20-peers.yml", 'peers:\n  - id: "broken\n')
        for script, argv in (("octo-pod-declare", ["add-peer", "gamma"]),
                             ("octo-pod-declare", ["add-arm", "r", "--udp", "48080",
                                                   "--from", "192.0.2.7"]),
                             ("octo-pod-peer", ["allow-from", "gamma", "--from", "192.0.2.30"]),
                             ("octo-pod-egress", ["add", "relay", "--dest",
                                                  "udp://192.0.2.20:8600", "--key", "a/b"])):
            with self.subTest(script=script, argv=argv):
                before = self.snapshot()
                rc, out = self.s.run(script, *argv)
                self.assertNotEqual(rc, 0, out)
                self.assertIn("20-peers.yml is not valid YAML, so nothing was changed", out)
                self.assertNotIn("Traceback", out)
                self.assertEqual(self.snapshot(), before)

    def test_set_firewall_on_a_pod_with_no_site_file(self):
        # set-lan treats 00-site.yml as optional; set-firewall must too.
        self.assertFalse((self.s.pod / "00-site.yml").exists())
        rc, out = self.run_declare("set-firewall", "on")
        self.assertEqual(rc, 0, out)
        self.assertIn("SSH stays open to the mesh ONLY", out)

if __name__ == "__main__":
    unittest.main(verbosity=2)
