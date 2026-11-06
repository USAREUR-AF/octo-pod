#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
tools/push-images.py end to end: the operator side over a stub ssh, and the pod side's
refusals before anything is staged or loaded.

WHY THIS EXISTS
    Image delivery runs as root on a pod, from a command an operator types with a host
    name in it. The host name must never become an ssh option, the pod must refuse a
    bundle built for other pins, a stage directory anyone else can write, or a registry
    that serves different bytes, and the temporary upload must be removed even when the
    pod refuses it. test_push_images.py covers bundle validation and extraction; this
    covers the paths around them.

    No network and no pod: ssh and scp are stubs that record their arguments and answer
    the way the pod would.

RUN
    python3 platform/tests/test_image_delivery.py
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
PINFILE = "platform/group_vars/all/10-infrastructure.yml"


def load(path=REPO / "tools" / "push-images.py"):
    spec = importlib.util.spec_from_file_location("push_images_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


delivery = load()

SSH = r'''#!/bin/sh
# One record per call: arguments separated by NUL, calls by RS. The remote command carries
# push-images.py's whole source, newlines and all, so match only on how it ENDS.
printf '%s\0' "$@" >> "$STUB_LOG.ssh"; printf '\036' >> "$STUB_LOG.ssh"
for last; do :; done
case "$last" in
  *" --remote-preflight") cat "$STUB_PREFLIGHT" ;;
  *" --remote-receive "*" stage"|*" --remote-receive "*" load") exit "${STUB_RECEIVE_RC:-0}" ;;
  "mktemp -d "*) echo "${STUB_TMPDIR:-/tmp/octo-images.Ab12Cd34}" ;;
esac
exit 0
'''
SCP = r'''#!/bin/sh
printf '%s\0' "$@" >> "$STUB_LOG.scp"; printf '\036' >> "$STUB_LOG.scp"
exit "${STUB_SCP_RC:-0}"
'''


def bundle(root):
    """A complete two-architecture OCI layout for every pinned tag, and its pins."""
    blobs = root / "images" / "oci" / "blobs" / "sha256"
    blobs.mkdir(parents=True)

    def blob(data, media):
        digest = hashlib.sha256(data).hexdigest()
        (blobs / digest).write_bytes(data)
        return {"digest": "sha256:" + digest, "size": len(data), "mediaType": media}

    layer = blob(b"layer", "application/vnd.oci.image.layer.v1.tar")
    config = blob(b"{}", "application/vnd.oci.image.config.v1+json")
    manifest = blob(json.dumps({"schemaVersion": 2, "config": config, "layers": [layer]}).encode(),
                    "application/vnd.oci.image.manifest.v1+json")
    index = blob(json.dumps({"schemaVersion": 2, "manifests": [
        dict(manifest, platform={"os": "linux", "architecture": a}) for a in ("amd64", "arm64")
    ]}).encode(), "application/vnd.oci.image.index.v1+json")
    (root / "images" / "oci" / "index.json").write_text(
        json.dumps({"schemaVersion": 2, "manifests": [index]}))
    (root / "images" / "MAP").write_text("".join(
        f"{tag} example/{tag} {index['digest']}\n" for tag in delivery.PINS))
    for arch in ("amd64", "arm64"):
        for tool in ("registry", "skopeo"):
            (root / "images" / f"{tool}-{arch}.tar").write_bytes(b"tool")
    (root / "images" / "REGISTRY_IMAGE").write_text("registry:2\n")
    (root / "images" / "SKOPEO_IMAGE").write_text("quay.io/skopeo/stable:latest\n")
    return {tag: f"ghcr.io/example/{tag}@{index['digest']}" for tag in delivery.PINS}


def pinfile(pins):
    return "".join(f'{var}_image_upstream: "{pins[tag]}"   # fixture\n'
                   for tag, var in delivery.PINS.items())


class OperatorSide(unittest.TestCase):
    """main(): what runs on the operator's machine."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        # A checkout whose pins match the fixture bundle.
        self.checkout = self.tmp / "checkout"
        (self.checkout / "tools").mkdir(parents=True)
        shutil.copy2(REPO / "tools" / "push-images.py", self.checkout / "tools")
        self.bundle = self.tmp / "bundle"
        self.pins = bundle(self.bundle)
        (self.checkout / PINFILE).parent.mkdir(parents=True)
        (self.checkout / PINFILE).write_text(pinfile(self.pins))
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        for name, body in (("ssh", SSH), ("scp", SCP)):
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        self.log = self.tmp / "log"
        self.preflight = self.tmp / "preflight.json"
        self.preflight.write_text(json.dumps({"pins": self.pins, "architecture": "arm64",
                                              "free": 10 * 1024**3}))
        self.env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}",
                        STUB_LOG=str(self.log), STUB_PREFLIGHT=str(self.preflight))

    def run_push(self, *args, **env):
        r = subprocess.run([sys.executable, str(self.checkout / "tools" / "push-images.py"), *args],
                           capture_output=True, text=True, timeout=120,
                           env=dict(self.env, **env))
        return r.returncode, r.stdout + r.stderr

    def calls(self, tool):
        path = Path(f"{self.log}.{tool}")
        if not path.exists():
            return []
        return [call.split("\0")[:-1] for call in path.read_text().split("\x1e") if call]

    def test_delivers_a_verified_bundle_and_cleans_up(self):
        rc, out = self.run_push("pod-a", "--bundle", str(self.bundle))
        self.assertEqual(rc, 0, out)
        self.assertIn("Image delivery complete", out)
        ssh = self.calls("ssh")
        for call in ssh:
            # Host key checking, no agent, no prompts, and the target after `--`.
            self.assertEqual(call[call.index("--") + 1], "pod-a", call)
            for option in ("BatchMode=yes", "ForwardAgent=no", "StrictHostKeyChecking=yes"):
                self.assertIn(option, call)
        receive = [c[-1] for c in ssh if c[-1].endswith((" stage", " load"))]
        self.assertEqual(len(receive), 1, [c[-1][-120:] for c in ssh])
        self.assertIn("/tmp/octo-images.Ab12Cd34/bundle.tar.gz", receive[0])
        self.assertTrue(receive[0].endswith(" stage"), receive[0])
        scp = self.calls("scp")
        self.assertEqual(len(scp), 1)
        self.assertEqual(scp[0][-1], "pod-a:/tmp/octo-images.Ab12Cd34/bundle.tar.gz")
        self.assertIn("rm -f /tmp/octo-images.Ab12Cd34/bundle.tar.gz && rmdir /tmp/octo-images.Ab12Cd34",
                      ssh[-1][-1])

    def test_without_a_bundle_the_checkout_builds_one(self):
        # The checkout's own build script, stood in: it writes the fixture into --out.
        script = self.checkout / "tools" / "build-airgap-bundle.sh"
        script.write_text(f'#!/bin/sh\n[ "$1" = --out ] || exit 9\ncp -R "{self.bundle}" "$2"\n')
        rc, out = self.run_push("pod-a")
        self.assertEqual(rc, 0, out)
        self.assertIn("Image delivery complete", out)
        script.write_text("#!/bin/sh\nexit 4\n")
        rc, out = self.run_push("pod-a")
        self.assertNotEqual(rc, 0)
        self.assertIn("Image delivery failed: command exited 4", out)
        self.assertEqual(len(self.calls("scp")), 1)

    def test_load_is_asked_for_explicitly(self):
        rc, out = self.run_push("pod-a", "--bundle", str(self.bundle), "--load")
        self.assertEqual(rc, 0, out)
        receive = [c[-1] for c in self.calls("ssh") if c[-1].endswith((" stage", " load"))]
        self.assertTrue(receive and receive[0].endswith(" load"), receive)

    def test_a_target_that_could_be_an_ssh_option_is_refused_before_ssh(self):
        for target in ("-oProxyCommand=touch /tmp/x", "pod a", "pod;id", ""):
            with self.subTest(target=target):
                rc, out = self.run_push(target, "--bundle", str(self.bundle))
                self.assertNotEqual(rc, 0)
                self.assertIn("use a simple SSH alias or user@host", out)
        self.assertEqual(self.calls("ssh"), [])

    def test_a_pod_running_other_pins_is_refused_before_upload(self):
        other = dict(self.pins, sapient="ghcr.io/example/sapient@sha256:" + "a" * 64)
        self.preflight.write_text(json.dumps({"pins": other, "architecture": "arm64",
                                              "free": 10 * 1024**3}))
        rc, out = self.run_push("pod-a", "--bundle", str(self.bundle))
        self.assertNotEqual(rc, 0)
        self.assertIn("checkout pins differ from installed pod", out)
        self.assertEqual(self.calls("scp"), [])

    def test_a_pod_without_room_is_refused_before_upload(self):
        self.preflight.write_text(json.dumps({"pins": self.pins, "architecture": "arm64",
                                              "free": 10}))
        rc, out = self.run_push("pod-a", "--bundle", str(self.bundle))
        self.assertNotEqual(rc, 0)
        self.assertIn("bundle too large for destination", out)
        self.assertEqual(self.calls("scp"), [])

    def test_a_corrupt_bundle_is_refused_before_upload(self):
        layer = next(p for p in (self.bundle / "images/oci/blobs/sha256").iterdir()
                     if p.read_bytes() == b"layer")
        layer.write_bytes(b"other")
        rc, out = self.run_push("pod-a", "--bundle", str(self.bundle))
        self.assertNotEqual(rc, 0)
        self.assertIn("Image delivery refused", out)
        self.assertEqual(self.calls("scp"), [])

    def test_the_upload_is_removed_when_the_pod_refuses_it(self):
        rc, out = self.run_push("pod-a", "--bundle", str(self.bundle), STUB_RECEIVE_RC="3")
        self.assertNotEqual(rc, 0)
        self.assertIn("remote operation failed (3)", out)
        self.assertIn("rm -f /tmp/octo-images.Ab12Cd34/bundle.tar.gz", self.calls("ssh")[-1][-1])

    def test_an_unexpected_remote_directory_is_refused(self):
        rc, out = self.run_push("pod-a", "--bundle", str(self.bundle),
                                STUB_TMPDIR="/var/tmp/../../etc")
        self.assertNotEqual(rc, 0)
        self.assertIn("unexpected remote temporary directory", out)
        self.assertEqual(self.calls("scp"), [])

    def test_a_failed_copy_still_cleans_up(self):
        rc, out = self.run_push("pod-a", "--bundle", str(self.bundle), STUB_SCP_RC="1")
        self.assertNotEqual(rc, 0)
        self.assertIn("Image delivery failed: command exited 1", out)
        self.assertIn("rmdir /tmp/octo-images.Ab12Cd34", self.calls("ssh")[-1][-1])
        self.assertFalse(any(c[-1].endswith((" stage", " load")) for c in self.calls("ssh")))


class PodSide(unittest.TestCase):
    """preflight() and receive(): what runs as root on the pod."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Resolved: receive() refuses a stage under any symlink, and macOS's /var is one.
        self.tmp = Path(tmp.name).resolve()

    def test_pins_reads_every_tag_from_the_repository(self):
        pins = delivery.pins(REPO)
        self.assertEqual(set(pins), set(delivery.PINS))
        for ref in pins.values():
            self.assertRegex(ref, r"@sha256:[0-9a-f]{64}$")

    def test_pins_refuses_a_tag_a_duplicate_or_a_missing_pin(self):
        good = bundle(self.tmp / "b")
        (self.tmp / PINFILE).parent.mkdir(parents=True)
        cases = {
            "tag": pinfile(good).replace(good["egress"], "ghcr.io/example/egress:latest"),
            "duplicate": pinfile(good) + f'egress_image_upstream: "{good["egress"]}"\n',
            "missing": "".join(line for line in pinfile(good).splitlines(True)
                               if not line.startswith("sapient_")),
        }
        for name, text in cases.items():
            with self.subTest(name):
                (self.tmp / PINFILE).write_text(text)
                with self.assertRaisesRegex(ValueError, "missing, duplicate or unpinned image"):
                    delivery.pins(self.tmp)

    def opt(self):
        root = self.tmp / "opt"
        (root / "bin").mkdir(parents=True, exist_ok=True)
        (root / "bin" / "octo-pod").write_text("#!/bin/sh\n")
        (root / PINFILE).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / PINFILE, root / PINFILE)
        real = Path
        return lambda p, *a: real(root) if str(p) == "/opt/octo-pod" else real(p, *a)

    def test_preflight_reports_pins_architecture_and_space(self):
        with patch.object(delivery.os, "geteuid", return_value=0), \
             patch.object(delivery, "Path", self.opt()), \
             patch.object(delivery.subprocess, "check_output", return_value="arm64\n"), \
             patch.object(delivery.shutil, "which", return_value="/usr/bin/x"):
            info = delivery.preflight()
        self.assertEqual(info["architecture"], "arm64")
        self.assertEqual(info["pins"], delivery.pins(REPO))
        self.assertGreater(info["free"], 0)

    def test_preflight_refusals(self):
        with patch.object(delivery.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(ValueError, "require root"):
                delivery.preflight()
        with patch.object(delivery.os, "geteuid", return_value=0), \
             patch.object(delivery, "Path", lambda p, *a: self.tmp / "nothing-installed"):
            with self.assertRaisesRegex(ValueError, "install octo-pod"):
                delivery.preflight()
        with patch.object(delivery.os, "geteuid", return_value=0), \
             patch.object(delivery, "Path", self.opt()), \
             patch.object(delivery.subprocess, "check_output", return_value="riscv64\n"):
            with self.assertRaisesRegex(ValueError, "unsupported destination architecture"):
                delivery.preflight()
        with patch.object(delivery.os, "geteuid", return_value=0), \
             patch.object(delivery, "Path", self.opt()), \
             patch.object(delivery.subprocess, "check_output", return_value="amd64\n"), \
             patch.object(delivery.shutil, "which", side_effect=lambda t: None if t == "skopeo" else t):
            with self.assertRaisesRegex(ValueError, "missing destination prerequisite: skopeo"):
                delivery.preflight()

    def test_receive_refuses_without_root(self):
        with patch.object(delivery.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(ValueError, "require root"):
                delivery.receive("/tmp/octo-images.x/bundle.tar.gz", "a" * 64, False)

    def test_receive_refuses_a_stage_directory_another_user_owns(self):
        # Root on a pod; here the stage is owned by the test user, which is the point.
        with patch.object(delivery.os, "geteuid", return_value=0), \
             patch.object(delivery, "STAGES", self.tmp / "stages"):
            with self.assertRaisesRegex(ValueError, "unsafe staging directory ownership"):
                delivery.receive("/tmp/octo-images.x/bundle.tar.gz", "a" * 64, False)

    def test_receive_takes_the_lock_before_staging(self):
        stages = self.tmp / "stages"
        stages.mkdir(mode=0o700)
        owned = os.stat_result
        real_stat = Path.stat

        def as_root(path, *a, **k):
            s = real_stat(path, *a, **k)
            return owned((s.st_mode & ~0o022, *list(s)[1:4], 0, *list(s)[5:]))

        seen = []
        with patch.object(delivery.os, "geteuid", return_value=0), \
             patch.object(delivery, "STAGES", stages), \
             patch.object(Path, "stat", as_root), \
             patch.object(delivery, "receive_unlocked",
                          side_effect=lambda *a: seen.append(a) or "staged"):
            self.assertEqual(delivery.receive("/tmp/octo-images.x/bundle.tar.gz", "a" * 64, True),
                             "staged")
        self.assertEqual(seen, [("/tmp/octo-images.x/bundle.tar.gz", "a" * 64, True)])
        self.assertTrue((stages / ".lock").exists())

    def test_receive_refuses_a_malformed_digest_or_path(self):
        with patch.object(delivery, "preflight", return_value={"pins": {}}):
            with self.assertRaisesRegex(ValueError, "invalid transfer digest"):
                delivery.receive_unlocked("/tmp/octo-images.x/bundle.tar.gz", "A" * 64, False)
            for path in ("/tmp/octo-images.x/../../etc/shadow", "/var/tmp/bundle.tar.gz",
                         "/tmp/octo-images.x/other.tar.gz"):
                with self.subTest(path=path):
                    with self.assertRaisesRegex(ValueError, "invalid transfer path"):
                        delivery.receive_unlocked(path, "a" * 64, False)

    def test_receive_refuses_a_symlinked_or_shared_stage_root(self):
        target = self.tmp / "elsewhere"
        target.mkdir()
        link = self.tmp / "stages"
        link.symlink_to(target)
        with patch.object(delivery, "preflight", return_value={"pins": {}}), \
             patch.object(delivery, "STAGES", link):
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                delivery.receive_unlocked("/tmp/octo-images.x/bundle.tar.gz", "a" * 64, False)
        shared = self.tmp / "shared"
        shared.mkdir(mode=0o777)
        shared.chmod(0o777)
        with patch.object(delivery, "preflight", return_value={"pins": {}}), \
             patch.object(delivery, "STAGES", shared):
            with self.assertRaisesRegex(ValueError, "must be root-only"):
                delivery.receive_unlocked("/tmp/octo-images.x/bundle.tar.gz", "a" * 64, False)

    def test_load_verifies_every_digest_the_registry_serves(self):
        """--load seeds the registry, then reads each index back and hashes it."""
        src = self.tmp / "src"
        pins = bundle(src)
        index = json.loads((src / "images/oci/index.json").read_text())["manifests"][0]
        raw = (src / "images/oci/blobs/sha256" / index["digest"].split(":")[1]).read_bytes()
        stages = self.tmp / "stages"
        stages.mkdir(mode=0o700)
        real_stat = Path.stat

        def as_root(path, *a, **k):
            s = real_stat(path, *a, **k)
            if Path(path) == stages:
                v = list(s)
                v[4] = 0
                return os.stat_result(v)
            return s

        def deliver(served):
            receipt = {"schema": 1, "pins": pins, "files": delivery.files(src)}
            transfer = tempfile.mkdtemp(prefix="octo-images.", dir="/tmp")
            self.addCleanup(shutil.rmtree, transfer, True)
            archive = Path(transfer) / "bundle.tar.gz"
            import io
            import tarfile
            with tarfile.open(archive, "w:gz") as tf:
                for name in receipt["files"]:
                    tf.add(src / name, arcname=name)
                data = json.dumps(receipt).encode()
                info = tarfile.TarInfo("DELIVERY.json")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            for old in stages.iterdir():
                shutil.rmtree(old)
            with patch.object(delivery, "STAGES", stages), \
                 patch.object(delivery, "preflight", return_value={"pins": pins}), \
                 patch.object(Path, "stat", as_root), \
                 patch.object(delivery.subprocess, "run") as run, \
                 patch.object(delivery.subprocess, "check_output", return_value=served) as inspect:
                final = delivery.receive_unlocked(str(archive), delivery.sha(archive), True)
            return final, run, inspect

        final, run, inspect = deliver(raw)
        self.assertEqual(run.call_args[0][0][:4], ["/opt/octo-pod/bin/octo-pod", "bootstrap",
                                                   "--tags", "images"])
        self.assertIn(f"stage_dir={final}", run.call_args[0][0])
        self.assertEqual(inspect.call_count, len(delivery.PINS))
        with self.assertRaisesRegex(ValueError, "registry digest verification failed"):
            deliver(raw + b" ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
