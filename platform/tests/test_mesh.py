#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
bin/octo_pod_mesh.py: is this pod connected to the management server it was told to join?

WHY THIS EXISTS
    octo-pod join must not report success against the wrong mesh. It compares the
    requested management URL with NetBird's own status by origin: scheme, host and
    effective port, with IPv6 kept whole and names compared the way DNS compares them.
    A URL that is not a bare origin is refused rather than half-compared. test_join.py
    runs it through join; this holds each rule directly.

RUN
    python3 platform/tests/test_mesh.py
"""
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("octo_pod_mesh_under_test", REPO / "bin" / "octo_pod_mesh.py")
mesh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mesh)


class Origin(unittest.TestCase):
    def test_equivalent_spellings_are_one_origin(self):
        self.assertEqual(mesh.origin("https://NetBird.Example.NET."), ("https", "netbird.example.net", 443))
        self.assertEqual(mesh.origin("https://netbird.example.net:443/"),
                         ("https", "netbird.example.net", 443))
        self.assertEqual(mesh.origin("http://192.0.2.1"), ("http", "192.0.2.1", 80))
        self.assertEqual(mesh.origin("https://[2001:db8:0:0::1]:33073"),
                         ("https", "2001:db8::1", 33073))
        self.assertEqual(mesh.origin("https://bücher.example"), ("https", "xn--bcher-kva.example", 443))

    def test_anything_but_a_bare_http_origin_is_refused(self):
        for url in ("ftp://netbird.example.net", "https://", "https://u:p@netbird.example.net",
                    "https://netbird.example.net/api", "https://netbird.example.net?x=1",
                    "https://netbird.example.net#x", "https://net bird.example.net",
                    "https://netbird_example.net", "https://a..b.example", "https://" + "a" * 64 + ".net",
                    "https://netbird.example.net:0", "https://netbird.example.net:70000", None, 443):
            with self.subTest(url=url):
                with self.assertRaises((ValueError, TypeError)):
                    mesh.origin(url)


class Connected(unittest.TestCase):
    def test_connected_only_to_the_requested_management_server(self):
        status = {"management": {"connected": True, "url": "https://netbird.example.net:443"}}
        self.assertTrue(mesh.connected("https://netbird.example.net", status))
        self.assertFalse(mesh.connected("https://other.example.net", status))
        self.assertFalse(mesh.connected("https://netbird.example.net",
                                        {"management": {"connected": False,
                                                        "url": "https://netbird.example.net"}}))
        self.assertFalse(mesh.connected("https://netbird.example.net",
                                        {"management": {"connected": "true",
                                                        "url": "https://netbird.example.net"}}))
        self.assertFalse(mesh.connected("https://netbird.example.net", {"management": "up"}))
        self.assertFalse(mesh.connected("https://netbird.example.net", ["not", "a", "map"]))

    def run_main(self, *argv, stdin=""):
        with patch.object(sys, "argv", ["octo_pod_mesh.py", *argv]), \
             patch.object(sys, "stdin", io.StringIO(stdin)):
            return mesh.main()

    def test_the_command_line(self):
        status = json.dumps({"management": {"connected": True, "url": "https://netbird.example.net"}})
        self.assertEqual(self.run_main("validate", "https://netbird.example.net"), 0)
        self.assertEqual(self.run_main("validate", "https://netbird.example.net/x"), 1)
        self.assertEqual(self.run_main("connected", "https://netbird.example.net", stdin=status), 0)
        self.assertEqual(self.run_main("connected", "https://other.example.net", stdin=status), 1)
        self.assertEqual(self.run_main("connected", "https://netbird.example.net", stdin="not json"), 1)
        self.assertEqual(self.run_main("probe", "https://netbird.example.net"), 2)
        self.assertEqual(self.run_main("validate"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
