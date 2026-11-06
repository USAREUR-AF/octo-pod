#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
bin/octo-pod-remove-arm on a scratch identity: the paths test_cli.py does not walk.

WHY THIS EXISTS
    Removal is a revocation. It must stop the running thing first, delete every copy of
    the private key, and say so plainly when it could not: "REVOCATION INCOMPLETE" with a
    non-zero exit is the difference between a retired publisher and one that only looks
    retired. It must also change nothing when the operator says no, keep the key when
    asked to, and take down an orphan that no declaration names any more.

    Runs the script in its own interpreter under test_cli's root shim, with podman and
    systemctl replaced by recording stubs. Nothing touches a real pod.

RUN
    python3 platform/tests/test_remove.py
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cli import ARMS_YML, ARM_A, ARM_B, PEER, PEERS_YML, SHIM, Scratch  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


class Remove(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = Scratch(REPO, tmp.name)
        self.s.write("30-arms.yml", ARMS_YML)
        self.s.write("20-peers.yml", PEERS_YML)
        self.tls = self.s.pen / "zenoh" / "tls"
        self.tls.mkdir(parents=True)
        self.key = self.tls / f"{ARM_A}-arm-key.pem"
        self.cert = self.tls / f"{ARM_A}-arm-cert.pem"
        self.key.write_text("synthetic key\n")
        self.cert.write_text("synthetic cert\n")
        self.csr = self.s.pen / "ca" / f"{ARM_A}-arm.csr"
        self.csr.parent.mkdir(parents=True)
        self.csr.write_text("synthetic csr\n")
        self.containers({f"octo-udp-ingest-{ARM_A}": "Up 2 hours",
                         f"octo-pen-audit-sink-{ARM_A}": "Up 2 hours",
                         f"octo-udp-ingest-{ARM_B}": "Up 2 hours"})

    def containers(self, names):
        (self.s.state / "containers").write_text("".join(f"{n}\t{v}\n" for n, v in names.items()))

    def running(self):
        text = (self.s.state / "containers").read_text()
        return {line.split("\t")[0] for line in text.splitlines() if line}

    def calls(self):
        path = self.s.state / "calls"
        return path.read_text() if path.exists() else ""

    def remove(self, *argv, answer=None):
        r = subprocess.run([sys.executable, "-c", SHIM, str(REPO / "bin" / "octo-pod-remove-arm"),
                            *argv], capture_output=True, text=True, env=self.s.env, timeout=120,
                           input=answer)
        return r.returncode, r.stdout + r.stderr

    def test_no_at_the_prompt_changes_nothing(self):
        before = (self.s.read("30-arms.yml"), self.running())
        for answer in ("n\n", "\n", ""):
            with self.subTest(answer=answer):
                rc, out = self.remove("arm", ARM_A, answer=answer)
                self.assertEqual(rc, 0, out)
                self.assertIn("nothing was changed.", out)
                self.assertEqual((self.s.read("30-arms.yml"), self.running()), before)
                self.assertTrue(self.key.exists())
        self.assertNotIn("systemctl stop", self.calls())

    def test_yes_at_the_prompt_stops_first_then_revokes_then_edits(self):
        rc, out = self.remove("arm", ARM_A, answer="y\n")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.running(), {f"octo-udp-ingest-{ARM_B}"})
        self.assertIn(f"stop + delete container  octo-pen-audit-sink-{ARM_A}", out)
        self.assertIn("its audit trail stops here", out)
        self.assertFalse(self.key.exists())
        self.assertFalse(self.csr.exists())
        self.assertTrue(self.cert.exists())
        self.assertIn("keep certificate", out)
        self.assertNotIn(f"id: {ARM_A}", self.s.read("30-arms.yml"))
        self.assertIn("The key is gone, so the\ngrant cannot be used", out)
        # Stopped before the key went: the container order in the output proves it.
        self.assertLess(out.index("removed container"), out.index("deleted private key"))

    def test_keep_key_moves_an_arm_without_revoking_it(self):
        rc, out = self.remove("arm", ARM_A, "--yes", "--keep-key")
        self.assertEqual(rc, 0, out)
        self.assertIn("keep private key", out)
        self.assertIn("--keep-key", out)
        self.assertTrue(self.key.exists())
        self.assertTrue(self.csr.exists())
        self.assertNotIn(f"octo-udp-ingest-{ARM_A}", self.running())

    def test_a_key_that_is_not_where_it_must_be_is_an_incomplete_revocation(self):
        self.key.unlink()
        rc, out = self.remove("arm", ARM_A, "--yes")
        self.assertEqual(rc, 1, out)
        self.assertIn("!! NOT FOUND", out)
        self.assertIn("REVOCATION INCOMPLETE", out)
        self.assertIn(str(self.key), out)
        self.assertNotIn("The key is gone", out)

    def test_an_orphan_is_removed_by_its_container_alone(self):
        self.containers({"octo-tcp-ingest-ghost": "Up 9 days"})
        (self.tls / "ghost-arm-key.pem").write_text("synthetic key\n")
        rc, out = self.remove("ghost", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIn("removing orphan ghost", out)
        self.assertIn("NOT DECLARED", out)
        self.assertEqual(self.running(), set())
        self.assertFalse((self.tls / "ghost-arm-key.pem").exists())

    def test_stray_key_material_alone_is_something_to_remove(self):
        self.containers({})
        (self.tls / "leftover-arm-key.pem").write_text("synthetic key\n")
        rc, out = self.remove("leftover", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertFalse((self.tls / "leftover-arm-key.pem").exists())

    def test_a_container_podman_will_not_remove_is_a_warning_not_a_claim(self):
        stub = Path(self.s.env["PATH"].split(":")[0]) / "podman"
        stub.write_text('#!/bin/sh\necho "podman $*" >> "$OCTO_TEST_STATE/calls"\n'
                        'case "$1" in\n  ps) cat "$OCTO_TEST_STATE/containers" ;;\n'
                        '  rm) echo "container is busy" >&2; exit 125 ;;\nesac\n')
        rc, out = self.remove("arm", ARM_A, "--yes")
        self.assertIn(f"WARN could not remove octo-udp-ingest-{ARM_A}: container is busy", out)
        self.assertNotIn(f"  removed container octo-udp-ingest-{ARM_A}", out)

    def test_an_open_release_gate_is_reported_not_edited(self):
        pod = self.s.read("10-pod.yml").replace('bridge_allow: ""', f'bridge_allow: "raw/{ARM_A}/**"')
        self.s.write("10-pod.yml", pod)
        rc, out = self.remove("arm", ARM_A, "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"still mentions {ARM_A}", out)
        self.assertIn(f"raw/{ARM_A}/**", self.s.read("10-pod.yml"))

    def test_an_entry_it_cannot_find_by_line_is_left_for_the_operator(self):
        # Flow style parses to the same arm, but is not a "- id:" line to cut.
        self.s.write("30-arms.yml", f"arms: [{{id: {ARM_A}, kind: udp-ingest}}]\n")
        rc, out = self.remove("arm", ARM_A, "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"could not find the '{ARM_A}' block", out)
        self.assertIn(ARM_A, self.s.read("30-arms.yml"))

    def test_removing_a_middle_entry_keeps_its_neighbours_and_comments(self):
        text = ARMS_YML.replace(f"  - id: {ARM_B}", f"  # the second radar\n  - id: {ARM_B}")
        text += "  - id: third\n    kind: udp-ingest\n    port: 48023\n"
        self.s.write("30-arms.yml", text)
        rc, out = self.remove("arm", ARM_B, "--yes")
        self.assertIn(f"removed declaration from", out)
        after = self.s.read("30-arms.yml")
        self.assertNotIn(f"id: {ARM_B}", after)
        self.assertIn(f"id: {ARM_A}", after)
        self.assertIn("id: third", after)
        self.assertIn("# shut until you have seen their data", after)

    def test_a_peer_declared_as_one_refuses_the_arm_verb(self):
        rc, out = self.remove("arm", PEER, "--yes")
        self.assertNotEqual(rc, 0)
        self.assertIn(f"remove-peer {PEER}", out)

    def test_needs_root_and_one_pod(self):
        r = subprocess.run([sys.executable, str(REPO / "bin" / "octo-pod-remove-arm"), ARM_A],
                           capture_output=True, text=True, env=self.s.env, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("run with sudo", r.stderr)
        (self.s.pod.parent / "pod-y").mkdir()
        rc, out = self.remove(ARM_A, "--yes")
        self.assertNotEqual(rc, 0)
        self.assertIn("more than one pod under", out)
        self.s.env["OCTO_POD_ETC"] = str(self.s.root / "nowhere")
        rc, out = self.remove(ARM_A, "--yes")
        self.assertNotEqual(rc, 0)
        self.assertIn("this box is not a pod yet", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
