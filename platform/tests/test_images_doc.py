#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
docs/images.md is generated from the image pins, and CI fails when the two disagree.

WHY THIS EXISTS
    The page tells an operator which image digests a pod runs. A page that drifts from
    the pins describes images the pod does not run, and an image with no description, or
    pinned by a mutable tag, must never reach the page at all. Each is a refusal here.

RUN
    python3 platform/tests/test_images_doc.py
"""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PINS = "platform/group_vars/all/10-infrastructure.yml"
DIGEST = "sha256:" + "c" * 64


class ImagesDoc(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "tools").mkdir()
        shutil.copy2(REPO / "tools" / "gen-images-doc.py", self.root / "tools")
        (self.root / PINS).parent.mkdir(parents=True)
        shutil.copy2(REPO / PINS, self.root / PINS)
        (self.root / "docs").mkdir()
        self.doc = self.root / "docs" / "images.md"

    def gen(self, *args):
        r = subprocess.run([sys.executable, str(self.root / "tools" / "gen-images-doc.py"), *args],
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def set_pin(self, key, line):
        pins = self.root / PINS
        text, n = re.subn(rf"^{key}:.*$", line, pins.read_text(), flags=re.M)
        self.assertEqual(n, 1, key)
        pins.write_text(text)

    def test_writes_the_page_then_check_agrees(self):
        rc, out = self.gen()
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"wrote docs/images\.md \(\d+ images\)")
        rc, out = self.gen("--check")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"docs/images\.md matches \d+ pinned images")

    def test_lists_every_pin_with_its_digest_and_version(self):
        self.set_pin("egress_image_upstream",
                     f'egress_image_upstream: "ghcr.io/example/octo-egress@{DIGEST}"   # main@1234567, signed')
        self.set_pin("sapient_image_upstream",
                     f'sapient_image_upstream: "ghcr.io/example/octo-sapient@{DIGEST}"')
        self.assertEqual(self.gen()[0], 0)
        page = self.doc.read_text()
        pins = re.findall(r"^[a-z_]+_image_upstream:", (self.root / PINS).read_text(), re.M)
        rows = [line for line in page.splitlines() if line.startswith("| `")]
        self.assertEqual(len(rows), len(pins))
        self.assertIn(f"| `ghcr.io/example/octo-egress` |", page)
        self.assertIn(f"| `main@1234567` | `{DIGEST}` |", page)
        # No note: the version column says so instead of inventing one.
        sapient = next(r for r in rows if "octo-sapient" in r)
        self.assertTrue(sapient.endswith(f"| `-` | `{DIGEST}` |"), sapient)

    def test_check_fails_when_the_page_is_stale_or_missing(self):
        rc, out = self.gen("--check")
        self.assertNotEqual(rc, 0)
        self.assertIn("does not match the image pins", out)
        self.assertEqual(self.gen()[0], 0)
        self.set_pin("egress_image_upstream",
                     f'egress_image_upstream: "ghcr.io/example/octo-egress@{DIGEST}"')
        rc, out = self.gen("--check")
        self.assertNotEqual(rc, 0)
        self.assertIn("run tools/gen-images-doc.py", out)

    def test_refuses_an_image_pinned_by_tag(self):
        self.set_pin("egress_image_upstream",
                     'egress_image_upstream: "ghcr.io/example/octo-egress:latest"')
        rc, out = self.gen()
        self.assertNotEqual(rc, 0)
        self.assertIn("egress_image_upstream is not pinned by digest", out)
        self.assertFalse(self.doc.exists())

    def test_refuses_an_image_nobody_described(self):
        pins = self.root / PINS
        pins.write_text(pins.read_text() + f'\nnew_arm_image_upstream: "ghcr.io/example/x@{DIGEST}"\n')
        rc, out = self.gen()
        self.assertNotEqual(rc, 0)
        self.assertIn("new_arm_image_upstream has no ROLES entry", out)

    def test_refuses_a_pins_file_with_no_pins(self):
        (self.root / PINS).write_text("# nothing pinned\n")
        rc, out = self.gen()
        self.assertNotEqual(rc, 0)
        self.assertIn("no *_image_upstream pins found", out)

    def test_the_repository_page_matches_its_pins(self):
        r = subprocess.run([sys.executable, str(REPO / "tools" / "gen-images-doc.py"), "--check"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
