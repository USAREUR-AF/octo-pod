#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
bin/octo-pod-egress: the rules a destination must meet, and each verb's refusals and edits
on a scratch identity.

WHY THIS EXISTS
    An egress destination is where fabric data leaves the pod. The rules that decide
    whether one is safe (a literal address, keys that start at a publisher, a listener
    with named consumers, a SAPIENT registration the controller will accept) are checked
    here function by function, then each verb is run end to end: a refusal must leave
    every file unchanged, and remove must stop the arm before it forgets the destination.
    test_cli.py and test_templates.py cover the main release and revoke walk-through.

RUN
    python3 platform/tests/test_egress.py
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cli import SHIM, Scratch, load_egress  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EG = load_egress(REPO)
KEY = "1d18de66e8a09a5e68170b3b3de6563b/sensors/radar1/asterix/data"


def dest(**fields):
    base = {"id": "d", "dest": "udp://192.0.2.20:8600", "allow_dest": "192.0.2.20:8600",
            "keys": [KEY], "release": {"from_fabric": False}}
    base.update(fields)
    return base


class Rules(unittest.TestCase):
    def test_key_problem(self):
        self.assertIsNone(EG.key_problem(KEY))
        self.assertIsNone(EG.key_problem("release/lane-a/**"))
        for key, why in (("", "not a well-formed"), (" a/b", "not a well-formed"),
                         ("a//b", "not a well-formed"), ("ops/audit/x", "reserved namespace"),
                         ("@/x", "reserved namespace"), ("downlink/x", "reserved namespace"),
                         ("*/sensors", "not a wildcard"), ("$*/x", "not a wildcard"),
                         ("release", "must name its release lane"),
                         ("release/*/x", "must name its release lane")):
            with self.subTest(key=key):
                self.assertIn(why, EG.key_problem(key))

    def test_literal_addresses_only(self):
        for good in ("192.0.2.1:8600", "[2001:db8::1]:8600", "192.0.2.1:65535"):
            self.assertTrue(EG.literal(good), good)
        for bad in ("relay.example.invalid:8600", "0.0.0.0:8600", "192.0.2.1:0",
                    "192.0.2.1:65536", "192.0.2.1:²", "192.0.2.1:", "192.0.2.1:123456",
                    None, 8600):
            self.assertFalse(EG.literal(bad), bad)

    def test_ipv4_lists_and_whole_numbers(self):
        self.assertEqual(EG.ipv4_list(" 192.0.2.1, ,192.0.2.2 "), ["192.0.2.1", "192.0.2.2"])
        self.assertEqual(EG.ipv4_list(None), [])
        self.assertIsNone(EG.ipv4_list("192.0.2.1,host"))
        self.assertIsNone(EG.ipv4_list("0.0.0.0"))
        self.assertTrue(EG.whole(3))
        self.assertFalse(EG.whole(True))
        self.assertFalse(EG.whole("3"))

    def test_a_well_formed_destination_of_each_kind_has_no_problems(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for f in ("cert.pem", "key.pem", "ca.pem"):
            Path(tmp.name, f).write_text("synthetic\n")
        for d in (dest(),
                  dest(dest="tcp-server://192.0.2.10:9100", allow_dest="192.0.2.10:9100",
                       allow_sources="192.0.2.30", max_clients=4, replay=16),
                  dest(dest="sapient://192.0.2.40:5020", allow_dest="192.0.2.40:5020",
                       sapient={"node_type": "radar", "status_interval_s": 10,
                                "capabilities": "detection:track, x:y:1:m"}),
                  dest(dest="zenoh://192.0.2.50:7447", allow_dest="192.0.2.50:7447",
                       prefix="raw/goat", allow_prefix="raw/goat", partner_bundle_dir=tmp.name)):
            with self.subTest(dest=d["dest"]):
                self.assertEqual(EG.problems_with(d), [])

    def test_problems_with_names_every_fault(self):
        cases = [
            ("not a map", ["udp://192.0.2.20:8600"], "destination must be a map"),
            ("release", dest(release="open"), "release must be a map"),
            ("keys type", dest(keys="a/b"), "keys must be a list"),
            ("no keys", dest(keys=[]), "it declares no keys"),
            ("bad key", dest(keys=["ops/x"]), "reserved namespace"),
            ("scheme", dest(dest="http://192.0.2.20:80", allow_dest="192.0.2.20:80"),
             "must be udp://, tcp://"),
            ("allow_dest", dest(allow_dest="192.0.2.21:8600"), "is not in its own allow_dest"),
            ("no sources", dest(dest="tcp-server://192.0.2.10:9100", allow_dest="192.0.2.10:9100"),
             "needs allow_sources"),
            ("clients", dest(dest="tcp-server://192.0.2.10:9100", allow_dest="192.0.2.10:9100",
                             allow_sources="192.0.2.30", max_clients=True), "max_clients must be"),
            ("replay", dest(dest="tcp-server://192.0.2.10:9100", allow_dest="192.0.2.10:9100",
                            allow_sources="192.0.2.30", replay=5000), "replay must be"),
            ("sapient map", dest(dest="sapient://192.0.2.40:5020", allow_dest="192.0.2.40:5020",
                                 sapient="radar"), "sapient must be a map"),
            ("zenoh prefix", dest(dest="zenoh://192.0.2.50:7447", allow_dest="192.0.2.50:7447",
                                  prefix="raw/*"), "needs a literal prefix"),
            ("zenoh allow", dest(dest="zenoh://192.0.2.50:7447", allow_dest="192.0.2.50:7447",
                                 prefix="raw/goat", allow_prefix="raw"), "allow_prefix must equal"),
            ("zenoh bundle", dest(dest="zenoh://192.0.2.50:7447", allow_dest="192.0.2.50:7447",
                                  prefix="raw/goat", allow_prefix="raw/goat",
                                  partner_bundle_dir="/nonexistent"), "lacks cert.pem, key.pem"),
        ]
        for name, d, needle in cases:
            with self.subTest(name):
                self.assertTrue(any(needle in p for p in EG.problems_with(d)),
                                EG.problems_with(d))

    def test_sapient_problems(self):
        self.assertEqual(EG.sapient_problems({}), [])
        self.assertEqual(EG.sapient_problems({"node_type": "NODE_TYPE_RADAR"}), [])
        for sap, needle in (({"node_type": "toaster"}, "is not one of the standard's"),
                            ({"status_interval_s": True}, "status_interval_s must be"),
                            ({"status_interval_s": 0}, "status_interval_s must be"),
                            ({"status_interval_s": 301}, "status_interval_s must be"),
                            ({"capabilities": "detection"}, "is not category:type"),
                            ({"capabilities": "a:b:c:d:e"}, "is not category:type"),
                            ({"capabilities": ":b"}, "is not category:type")):
            with self.subTest(sap=sap):
                self.assertTrue(any(needle in p for p in EG.sapient_problems(sap)))

    def test_released_and_session(self):
        self.assertTrue(EG.released({"release": {"from_fabric": True}}))
        self.assertFalse(EG.released({"release": "open"}))
        self.assertFalse(EG.released({}))
        self.assertTrue(EG.holds_session({"dest": "sapient://192.0.2.40:5020"}))
        self.assertFalse(EG.holds_session({"dest": "udp://192.0.2.20:8600"}))


class Verbs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = Scratch(REPO, tmp.name)
        self.partner = self.s.root / "partner-download"
        self.partner.mkdir()
        for f in ("cert.pem", "key.pem", "ca.pem"):
            (self.partner / f).write_text(f"synthetic {f}\n")
        self.s.env["OCTO_PARTNER_BUNDLES"] = str(self.s.root / "partner-bundles")

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.s.pod.iterdir()}

    def egress(self, *argv, answer=None):
        r = subprocess.run([sys.executable, "-c", SHIM, str(REPO / "bin" / "octo-pod-egress"), *argv],
                           capture_output=True, text=True, env=self.s.env, timeout=120,
                           input=answer)
        return r.returncode, r.stdout + r.stderr

    def refused(self, needle, *argv):
        before = self.snapshot()
        rc, out = self.egress(*argv)
        self.assertNotEqual(rc, 0, out)
        self.assertIn(needle, out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(self.snapshot(), before, f"{argv} changed a file")

    def declared(self, ident):
        return {e["id"]: e for e in yaml.safe_load(self.s.read("40-egress.yml"))["egress"]}[ident]

    def test_needs_root_and_an_id(self):
        r = subprocess.run([sys.executable, str(REPO / "bin" / "octo-pod-egress"), "check"],
                           capture_output=True, text=True, env=self.s.env, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("sudo octo-pod egress check", r.stderr)
        self.refused("usage: octo-pod egress release <destination-id>", "release")
        self.refused("usage: octo-pod egress add <id> --dest", "add", "relay")

    def test_add_refusals(self):
        k = ["--key", KEY]
        cases = [
            ("give at least one --key", ["add", "r", "--dest", "udp://192.0.2.20:8600"]),
            ("refusing these keys", ["add", "r", "--dest", "udp://192.0.2.20:8600", "--key", "**"]),
            ("a name could resolve somewhere else",
             ["add", "r", "--dest", "udp://relay.example.invalid:8600", *k]),
            ("apply to sapient:// only",
             ["add", "r", "--dest", "udp://192.0.2.20:8600", "--node-type", "RADAR", *k]),
            ("apply to zenoh:// only",
             ["add", "r", "--dest", "udp://192.0.2.20:8600", "--prefix", "raw/goat", *k]),
            ("--from applies to tcp-server:// only",
             ["add", "r", "--dest", "udp://192.0.2.20:8600", "--from", "192.0.2.30", *k]),
            ("needs --from", ["add", "r", "--dest", "tcp-server://192.0.2.10:9100", *k]),
            ("--max-clients must be 1-64", ["add", "r", "--dest", "tcp-server://192.0.2.10:9100",
                                            "--from", "192.0.2.30", "--max-clients", "0", *k]),
            ("--replay must be 0-1024", ["add", "r", "--dest", "tcp-server://192.0.2.10:9100",
                                         "--from", "192.0.2.30", "--replay", "2000", *k]),
            ("for zenoh:// the pod dials the receiver",
             ["add", "r", "--dest", "zenoh://192.0.2.50:7447", "--from", "192.0.2.30", *k]),
            ("needs --prefix", ["add", "r", "--dest", "zenoh://192.0.2.50:7447", *k]),
            ("needs --prefix", ["add", "r", "--dest", "zenoh://192.0.2.50:7447",
                                "--prefix", "raw/*", *k]),
            ("needs --partner-bundle", ["add", "r", "--dest", "zenoh://192.0.2.50:7447",
                                        "--prefix", "raw/goat", *k]),
            ("(missing: ca.pem)", ["add", "r", "--dest", "zenoh://192.0.2.50:7447",
                                   "--prefix", "raw/goat", "--partner-bundle",
                                   str(self.s.root / "half"), *k]),
            ("refusing this SAPIENT declaration", ["add", "r", "--dest", "sapient://192.0.2.40:5020",
                                                   "--node-type", "toaster", *k]),
        ]
        (self.s.root / "half").mkdir()
        for f in ("cert.pem", "key.pem"):
            (self.s.root / "half" / f).write_text("x\n")
        for needle, argv in cases:
            with self.subTest(argv=argv):
                self.refused(needle, *argv)

    def test_add_a_listener_and_note_an_address_off_the_lan(self):
        self.s.write("00-site.yml", 'lan_ip: "192.0.2.5"\n')
        rc, out = self.egress("add", "feed", "--dest", "tcp-server://192.0.2.10:9100",
                              "--from", "192.0.2.30,192.0.2.31", "--max-clients", "2",
                              "--replay", "8", "--key", KEY)
        self.assertEqual(rc, 0, out)
        self.assertIn("is not this pod's lan_ip (192.0.2.5)", out)
        feed = self.declared("feed")
        self.assertEqual((feed["allow_sources"], feed["max_clients"], feed["replay"]),
                         ("192.0.2.30, 192.0.2.31", 2, 8))
        self.assertEqual(EG.problems_with(feed), [])

    def test_add_a_zenoh_receiver_keeps_its_bundle_under_etc(self):
        rc, out = self.egress("add", "partner", "--dest", "zenoh://192.0.2.50:7447",
                              "--prefix", "/raw/goat/", "--partner-bundle", str(self.partner),
                              "--no-verify-name", "--key", KEY)
        self.assertEqual(rc, 0, out)
        kept = Path(self.s.env["OCTO_PARTNER_BUNDLES"]) / "partner"
        self.assertEqual(kept.stat().st_mode & 0o777, 0o700)
        self.assertEqual((kept / "key.pem").stat().st_mode & 0o777, 0o600)
        self.assertEqual((kept / "cert.pem").read_text(), "synthetic cert.pem\n")
        partner = self.declared("partner")
        self.assertEqual((partner["kind"], partner["prefix"], partner["partner_bundle_dir"],
                          partner["verify_name_partner"]),
                         ("zenoh-egress", "raw/goat", str(kept), False))
        self.assertEqual(EG.problems_with(partner), [])

    def test_add_a_sapient_session_and_release_says_it_starts_the_arm(self):
        rc, out = self.egress("add", "c2", "--dest", "sapient://192.0.2.40:5020",
                              "--node-type", "node_type_radar", "--status-interval", "10",
                              "--capabilities", "detection:track", "--key", KEY)
        self.assertEqual(rc, 0, out)
        self.assertIn("opens no session with the destination, until it is released", out)
        self.assertEqual(self.declared("c2")["sapient"],
                         {"node_type": "RADAR", "status_interval_s": 10,
                          "capabilities": "detection:track"})
        rc, out = self.egress("release", "c2")
        self.assertEqual(rc, 0, out)
        self.assertIn("this pod holds no slot", out)
        self.assertIn("opens its session with the destination only now", out)
        rc, out = self.egress("release", "c2")
        self.assertIn("already open: release.from_fabric for c2", out)
        rc, out = self.egress("revoke", "c2")
        self.assertEqual(rc, 0, out)
        self.assertIn("stops the arm, which ends its session", out)

    def test_release_and_revoke_refusals(self):
        self.refused("is not declared in 40-egress.yml. Known: none", "release", "ghost")
        self.refused("is not declared in 40-egress.yml", "revoke", "ghost")
        self.refused("is not declared in 40-egress.yml", "remove", "ghost", "--yes")
        self.s.write("40-egress.yml", "egress:\n  - id: bad\n    dest: \"udp://relay.example.invalid:1\"\n"
                                      "    keys: []\n    release:\n      from_fabric: false\n")
        self.refused("refusing to release bad", "release", "bad")
        self.assertEqual(self.egress("add", "ok", "--dest", "udp://192.0.2.20:8600",
                                     "--key", KEY)[0], 0)
        self.refused("pass it with --slot", "release", "ok", "--ca-fingerprint",
                     "SHA256:" + "a" * 64)

    def test_remove_asks_for_the_id_then_stops_deletes_and_forgets(self):
        rc, _ = self.egress("add", "relay", "--dest", "udp://192.0.2.20:8600", "--key", KEY)
        self.assertEqual(rc, 0)
        self.assertEqual(self.egress("release", "relay")[0], 0)
        tls = self.s.pen / "zenoh" / "tls"
        tls.mkdir(parents=True)
        key = tls / "relay-egress-key.pem"
        cert = tls / "relay-egress-cert.pem"
        csr = self.s.pen / "ca" / "relay-egress.csr"
        csr.parent.mkdir(parents=True)
        for f in (key, cert, csr):
            f.write_text("synthetic\n")
        own = Path(self.s.env["OCTO_EGRESS_ETC"]) / "octo-egress-relay" / "tls"
        own.mkdir(parents=True)
        (own / "key.pem").write_text("synthetic\n")
        unit = Path(self.s.env["OCTO_QUADLET_DIR"]) / "octo-egress-relay.container"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Container]\n")

        before = self.snapshot()
        rc, out = self.egress("remove", "relay", answer="rel\n")
        self.assertNotEqual(rc, 0)
        self.assertIn("not confirmed; nothing changed", out)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(key.exists())

        rc, out = self.egress("remove", "relay", answer="relay\n")
        self.assertEqual(rc, 0, out)
        self.assertFalse(key.exists() or csr.exists() or unit.exists()
                         or own.parent.exists())
        self.assertTrue(cert.exists())
        self.assertEqual(yaml.safe_load(self.s.read("40-egress.yml")), {"egress": []})
        self.assertNotIn(KEY, self.s.read("10-pod.yml"))
        calls = (self.s.state / "calls").read_text()
        self.assertLess(calls.index("systemctl stop octo-egress-relay.service"),
                        calls.index("systemctl daemon-reload"))

    def test_remove_one_of_two_leaves_the_other_and_a_zenoh_bundle_goes(self):
        self.assertEqual(self.egress("add", "keep", "--dest", "udp://192.0.2.20:8600",
                                     "--key", KEY)[0], 0)
        self.assertEqual(self.egress("add", "partner", "--dest", "zenoh://192.0.2.50:7447",
                                     "--prefix", "raw/goat", "--partner-bundle", str(self.partner),
                                     "--key", KEY)[0], 0)
        kept = Path(self.s.env["OCTO_PARTNER_BUNDLES"]) / "partner"
        self.assertTrue(kept.is_dir())
        rc, out = self.egress("remove", "partner", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"{kept}/", out)
        self.assertFalse(kept.exists())
        self.assertEqual([e["id"] for e in yaml.safe_load(self.s.read("40-egress.yml"))["egress"]],
                         ["keep"])

    def test_a_pod_from_before_egress_gains_the_downlink_line(self):
        self.s.write("10-pod.yml", 'pen_id: "pod-x"\nbridge_allow: ""\n')
        (self.s.pod / "40-egress.yml").unlink()
        rc, out = self.egress("add", "relay", "--dest", "udp://192.0.2.20:8600", "--key", KEY)
        self.assertEqual(rc, 0, out)
        self.assertIn('downlink_allow: ""', self.s.read("10-pod.yml"))
        self.assertEqual([e["id"] for e in yaml.safe_load(self.s.read("40-egress.yml"))["egress"]],
                         ["relay"])

    def test_set_fabric_fills_only_what_is_empty(self):
        fabric = self.s.pod / "05-fabric.yml"
        EG.set_fabric(self.s.pod, "tls/fabric.example.invalid:7447", "192.0.2.100")
        text = yaml.safe_load(fabric.read_text())
        self.assertEqual(text["fabric_endpoint"], "tls/fabric.example.invalid:7447")
        self.assertEqual(text["fabric_host_pins"],
                         [{"ip": "192.0.2.100", "names": "fabric.example.invalid"}])
        EG.set_fabric(self.s.pod, "tls/other.example.invalid:7447", "192.0.2.101")
        self.assertEqual(yaml.safe_load(fabric.read_text())["fabric_endpoint"],
                         "tls/fabric.example.invalid:7447")
        fabric.write_text("fabric_endpoint: ''\nfabric_host_pins: []\n")
        with self.assertRaises(SystemExit):
            EG.set_fabric(self.s.pod, "tls/fabric.example.invalid:7447", "not-an-ip")
        fabric.unlink()
        with self.assertRaises(SystemExit):
            EG.set_fabric(self.s.pod, "tls/fabric.example.invalid:7447", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
