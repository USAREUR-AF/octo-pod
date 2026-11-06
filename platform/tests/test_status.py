#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
bin/octo-pod-status end to end: declared against actual, on a scratch identity.

WHY THIS EXISTS
    status is the view an operator reads to answer "is this pod doing what it was told".
    Its job is the disagreements: a publisher declared but not running, a container
    running that nothing declares, a release decision that does nothing without a slot.
    Each is checked here against a stub podman, curl and openssl, through both the table
    and --json. status must also never fail: no identity, no podman and unreadable
    declarations are all a picture, not an exit code.

RUN
    python3 platform/tests/test_status.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATUS = REPO / "bin" / "octo-pod-status"

PODMAN = r'''#!/bin/sh
case "$1" in
  ps) cat "$STUB/ps" 2>/dev/null ;;
  inspect) [ -f "$STUB/ip-$2" ] && cat "$STUB/ip-$2" || exit 1 ;;
esac
'''
CURL = '#!/bin/sh\ncat "$STUB/metrics" 2>/dev/null\n'
OPENSSL = r'''#!/bin/sh
for last; do :; done
case "$last" in
  *odd*) echo "notAfter=sometime next year" ;;
  *) echo "notAfter=Mar  5 10:02:07 2029 GMT" ;;
esac
'''

ARMS = """arms:
  - id: radar-1
    kind: udp-ingest
    transport: udp
    port: 48021
    allow_sources: "192.0.2.3"
    namespace: "raw/radar-1/**"
    cert_cn: octo-arm-radar-1
    release: {to_fabric: true}
  - id: dialer
    kind: tcp-connect
    transport: tcp
    target: "192.0.2.104:9000"
    namespace: "raw/dialer/**"
    release: {to_fabric: false}
  - id: quiet
    transport: tcp
    port: 48022
    namespace: "raw/quiet/**"
  - id: parked-arm
    enabled: false
    namespace: "raw/parked-arm/**"
"""
PEERS = """peers:
  - id: c2
    allow_sources: "192.0.2.30"
    read_keys: ["slot/sensors/a/**", "slot/sensors/b/**"]
    namespace: "raw/c2/**"
    release: {to_fabric: false, from_fabric: true}
  - id: meshy
    namespace: "raw/meshy/**"
"""
EGRESS = """egress:
  - id: relay
    dest: udp://192.0.2.20:8600
    keys: ["slot/sensors/a/**"]
    release: {from_fabric: true}
  - id: idle
    dest: tcp-server://192.0.2.10:9100
    keys: ["slot/sensors/b/**"]
  - id: parked
    enabled: false
    dest: udp://192.0.2.21:8601
"""


class Status(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.etc = self.root / "etc"
        self.pod = self.etc / "host_vars" / "pod-a"
        self.pod.mkdir(parents=True)
        self.pen = self.root / "pen"
        tls = self.pen / "zenoh" / "tls"
        tls.mkdir(parents=True)
        for name in ("octo-arm-radar-1-cert.pem", "dialer-arm-cert.pem", "relay-egress-cert.pem",
                     "octo-arm-quiet-cert.pem"):
            (tls / name).write_text("fixture\n")
        self.stub = self.root / "stub"
        bindir = self.root / "bin"
        for d in (self.stub, bindir):
            d.mkdir()
        for name, body in (("podman", PODMAN), ("curl", CURL), ("openssl", OPENSSL)):
            (bindir / name).write_text(body)
            (bindir / name).chmod(0o755)
        self.env = dict(os.environ, OCTO_POD_ETC=str(self.etc), OCTO_PEN_STATE=str(self.pen),
                        STUB=str(self.stub), PATH=f"{bindir}:{os.environ['PATH']}")
        self.write("30-arms.yml", ARMS)
        self.write("20-peers.yml", PEERS)
        self.write("40-egress.yml", EGRESS)
        self.write("10-pod.yml", 'fabric_slot_anchor: "slot"\nbridge_allow: "raw/radar-1/**"\n')
        self.write("05-fabric.yml", 'fabric_endpoint: "tls/fabric.example.invalid:7447"\n')
        self.running({
            "octo-pen-zenoh-router": "Up 2 hours",
            "octo-udp-ingest-radar-1": "Up 2 hours",
            "octo-pen-audit-sink-radar-1": "Up 2 hours",
            "octo-tcp-connect-dialer": "Up 5 minutes",
            "octo-pen-audit-sink-dialer": "Up 5 minutes",
            "octo-egress-relay": "Up 1 hour",
            "octo-uplink-bridge": "Up 2 hours",
            "octo-registry": "Up 3 days",
            "octo-health-pub": "Up 2 hours",
            "octo-udp-ingest-ghost": "Up 9 days",
            "unrelated-container": "Up 1 day",
        })
        (self.stub / "ip-octo-pen-audit-sink-radar-1").write_text("10.88.0.5\n")
        (self.stub / "metrics").write_text("# HELP x\ngoat_audit_subscriber_session_state 1\n")

    def write(self, name, text):
        (self.pod / name).write_text(text)

    def running(self, names):
        (self.stub / "ps").write_text("".join(f"{n}\t{s}\n" for n, s in names.items()))

    def status(self, *args, env=None):
        r = subprocess.run([sys.executable, str(STATUS), *args], capture_output=True, text=True,
                           env=env or self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout, r.stderr

    def rows(self):
        out, _ = self.status("--json")
        doc = json.loads(out)
        return doc["pod"], {(r["type"], r["id"]): r for r in doc["publishers"]}

    def test_declared_against_actual(self):
        info, rows = self.rows()
        self.assertEqual(info["pod"], "pod-a")
        self.assertEqual(info["router"], "Up 2 hours")
        self.assertEqual(info["bridge_container"], "octo-uplink-bridge (Up 2 hours)")
        radar = rows[("arm", "radar-1")]
        self.assertEqual((radar["state"], radar["sink"], radar["released"], radar["cert"]),
                         ("up 2 hours", "live", True, "2029-03-05"))
        self.assertEqual(radar["transport"], "udp :48021 from 192.0.2.3")
        dialer = rows[("arm", "dialer")]
        self.assertEqual((dialer["transport"], dialer["sink"], dialer["cert"]),
                         ("tcp -> 192.0.2.104:9000", "\u2014", "2029-03-05"))
        quiet = rows[("arm", "quiet")]
        self.assertEqual((quiet["state"], quiet["transport"]), ("NOT RUNNING", "tcp :48022 from any"))
        self.assertEqual(rows[("arm", "parked-arm")]["state"], "disabled")

    def test_peers_are_declared_not_running(self):
        _, rows = self.rows()
        c2 = rows[("peer", "c2")]
        self.assertEqual((c2["state"], c2["transport"]),
                         ("declared", "mTLS, LAN from 192.0.2.30, reads 2 key(s)"))
        self.assertEqual(rows[("peer", "meshy")]["transport"], "mTLS, mesh")

    def test_egress_destinations(self):
        _, rows = self.rows()
        relay = rows[("egress", "relay")]
        self.assertEqual((relay["state"], relay["released"], relay["cert"], relay["topic"]),
                         ("up 1 hour", True, "2029-03-05", "slot/sensors/a/**"))
        self.assertEqual(rows[("egress", "idle")]["state"], "NOT RUNNING")
        self.assertEqual(rows[("egress", "parked")]["state"], "disabled")
        self.assertEqual(rows[("egress", "parked")]["topic"], "?")

    def test_an_undeclared_publisher_is_an_orphan_and_infrastructure_is_not(self):
        info, _ = self.rows()
        orphans = [n for n, _ in info["orphans"]]
        self.assertEqual(orphans, ["octo-udp-ingest-ghost"])
        out, _ = self.status()
        self.assertIn("ORPHANED", out)
        self.assertIn("octo-udp-ingest-ghost  (Up 9 days)", out)

    def test_a_sink_that_lost_its_subscription_reads_down(self):
        (self.stub / "metrics").write_text("goat_audit_subscriber_session_state 0\n")
        _, rows = self.rows()
        self.assertEqual(rows[("arm", "radar-1")]["sink"], "DOWN")
        (self.stub / "metrics").write_text("nothing about sessions\n")
        _, rows = self.rows()
        self.assertEqual(rows[("arm", "radar-1")]["sink"], "\u2014")

    def test_the_table_states_the_release_gate(self):
        out, _ = self.status()
        self.assertIn("pod pod-a", out)
        self.assertIn("upstream octo-uplink-bridge (Up 2 hours) -> tls/fabric.example.invalid:7447", out)
        self.assertIn("gate allows raw/radar-1/** · 1 of 6 publisher(s) released", out)
        header = next(line for line in out.splitlines() if "TRANSPORT" in line)
        for column in ("TYPE", "ID", "STATE", "TOPIC", "SINK", "RELEASED", "CERT"):
            self.assertIn(column, header)

    def test_without_a_slot_a_release_decision_is_called_inert(self):
        self.write("10-pod.yml", 'fabric_slot_anchor: ""\nbridge_allow: ""\n')
        out, _ = self.status()
        self.assertIn("no slot \u2014 this pod captures locally and releases nothing", out)
        self.assertIn("note: radar-1 declare release.to_fabric: true, which has no effect", out)

    def test_a_slot_with_no_endpoint_cannot_start_the_bridge(self):
        self.write("05-fabric.yml", "fabric_endpoint: ''\n")
        out, _ = self.status()
        self.assertIn("slot held, but no fabric_endpoint", out)

    def test_a_stopped_uplink_is_not_covered_by_a_downlink(self):
        self.running({"octo-pen-zenoh-router": "Up 1 hour", "octo-downlink-bridge": "Up 1 hour"})
        info, _ = self.rows()
        self.assertIsNone(info["bridge_container"])
        out, _ = self.status()
        self.assertIn("upstream NOT RUNNING -> tls/fabric.example.invalid:7447", out)

    def test_an_odd_certificate_date_is_shown_as_openssl_printed_it(self):
        (self.pen / "zenoh" / "tls" / "octo-arm-radar-1-cert.pem").unlink()
        (self.pen / "zenoh" / "tls" / "odd-cert.pem").write_text("fixture\n")
        self.write("30-arms.yml", "arms:\n  - id: radar-1\n    cert_cn: odd\n")
        _, rows = self.rows()
        self.assertEqual(rows[("arm", "radar-1")]["cert"], "sometime next year")

    def test_a_hand_written_id_yaml_reads_as_a_boolean_still_draws(self):
        # `- id: off` is false to YAML. The CLI refuses to write one; a hand-edit can.
        self.write("30-arms.yml", "arms:\n  - id: off\n    transport: udp\n    port: 48023\n")
        _, rows = self.rows()
        self.assertEqual(rows[("arm", "False")]["state"], "NOT RUNNING")

    def test_the_inventory_names_the_pod(self):
        (self.etc / "host_vars" / "pod-b").mkdir()
        (self.etc / "inventory.ini").write_text(
            "[pods]\n# the one this box is\npod-a ansible_connection=local\n")
        info, _ = self.rows()
        self.assertEqual(info["pod"], "pod-a")

    def test_no_publishers_and_no_podman(self):
        for name in ("30-arms.yml", "20-peers.yml", "40-egress.yml"):
            (self.pod / name).unlink()
        env = dict(self.env, PATH="/nonexistent")
        r = subprocess.run([sys.executable, str(STATUS)], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no publishers declared yet", r.stdout)
        self.assertIn("router   NOT RUNNING", r.stdout)

    def test_an_unreadable_declaration_is_an_empty_one(self):
        self.write("30-arms.yml", 'arms:\n  - id: "unclosed\n')
        _, rows = self.rows()
        self.assertFalse(any(t == "arm" for t, _ in rows))
        self.assertIn(("peer", "c2"), rows)

    def test_a_box_that_is_not_a_pod_yet(self):
        out, err = self.status(env=dict(self.env, OCTO_POD_ETC=str(self.root / "absent")))
        self.assertEqual(out, "")
        self.assertIn("this box is not a pod yet", err)
        empty = self.root / "empty"
        (empty / "host_vars").mkdir(parents=True)
        out, err = self.status(env=dict(self.env, OCTO_POD_ETC=str(empty)))
        self.assertIn("has no pod identity", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
