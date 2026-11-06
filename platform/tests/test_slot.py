#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
bin/octo_pod_slot.py, the module that decides whether a GOAT slot bundle may be trusted.

WHY THIS EXISTS
    A slot bundle carries a private key and the CA roots every apply trusts afterwards.
    This module finds its files, reads the signed profile with a small CBOR reader, and
    decides whether a fingerprint, a pin or a person vouches for it. Every malformed
    input here must be a SlotError naming the problem, never a traceback and never a
    silent yes. test_templates.py runs real signed bundles through release; this holds
    the parser and each refusal to account directly.

RUN
    python3 platform/tests/test_slot.py
"""
import base64
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin"))
spec = importlib.util.spec_from_file_location("octo_pod_slot_under_test", REPO / "bin" / "octo_pod_slot.py")
slot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(slot)

ANCHOR = "1d18de66e8a09a5e68170b3b3de6563b"
POINT = b"\x04" + bytes(range(64))
FINGERPRINT = hashlib.sha256(POINT).hexdigest()


def cbor(value):
    """Just enough CBOR to build test profiles: maps, text, bytes, ints, lists."""
    def head(mt, n):
        if n < 24:
            return bytes([mt << 5 | n])
        for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([mt << 5 | ai]) + n.to_bytes(size, "big")
    if isinstance(value, bool):
        return bytes([0xF5 if value else 0xF4])
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        return head(3, len(value.encode())) + value.encode()
    if isinstance(value, list):
        return head(4, len(value)) + b"".join(cbor(v) for v in value)
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(value)


def pem(der):
    body = base64.b64encode(der).decode()
    return ("-----BEGIN PUBLIC KEY-----\n" + "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
            + "\n-----END PUBLIC KEY-----\n")


class Cbor(unittest.TestCase):
    def test_decodes_what_a_profile_holds(self):
        value = {"a": 1, "b": -3, "c": b"\x00\x01", "d": "text", "e": [1, [2]], 7: {"x": True},
                 "big": 70000, "huge": 1 << 40}
        decoded, end = slot._decode(cbor(value), 0)
        self.assertEqual(decoded, value)
        self.assertEqual(end, len(cbor(value)))

    def test_reads_tags_and_simple_values(self):
        self.assertEqual(slot._decode(bytes([0xC1, 0x1A]) + (1700000000).to_bytes(4, "big"), 0)[0],
                         1700000000)
        self.assertEqual([slot._decode(bytes([b]), 0)[0] for b in (0xF4, 0xF5, 0xF6, 0xF7)],
                         [False, True, None, 23])

    def test_skip_steps_over_every_kind(self):
        for value in (b"\x01\x02", "t", [1, "x", [b""]], {"k": [1, 2]}, 5, -5):
            data = cbor(value) + b"\xff"
            self.assertEqual(slot._skip(data, 0), len(data) - 1, value)
        tagged = bytes([0xC1]) + cbor(5)
        self.assertEqual(slot._skip(tagged, 0), len(tagged))

    def test_refuses_what_it_cannot_read_safely(self):
        for data, why in ((bytes([0x5F]), "indefinite"), (bytes([0x1B, 0, 0]), "truncated head"),
                          (bytes([0x43, 1]), "truncated string"),
                          (cbor({"a": 1})[:1] + cbor(b"k") + cbor(1), "map key")):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, why):
                    slot._decode(data, 0)
        with self.assertRaisesRegex(ValueError, "truncated string"):
            slot._skip(bytes([0x43, 1]), 0)
        with self.assertRaisesRegex(ValueError, "appears twice"):
            slot._decode(bytes([0xA2]) + cbor("k") + cbor(1) + cbor("k") + cbor(2), 0)

    def test_map_heads_of_every_size(self):
        for n in (0, 23, 24, 255, 256, 65535, 65536, 1 << 32):
            mt, value, _ = slot._head(slot._map_head(n) + bytes(8), 0)
            self.assertEqual((mt, value), (5, n))
        with self.assertRaisesRegex(ValueError, "map too large"):
            slot._map_head(1 << 64)


class Profile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, data):
        path = self.dir / f"{ANCHOR}-goat-cli.cbor"
        path.write_bytes(data)
        return path

    def test_cuts_the_signature_out_of_the_issuers_own_bytes(self):
        body = {"goat_cli_extras": {"router_endpoint": "tls/fabric.example.invalid:7447"},
                "expires_at": 1}
        path = self.write(cbor({**body, "signature": b"sig"}))
        signed, signature, fields = slot.read_profile(path)
        self.assertEqual(signature, b"sig")
        self.assertEqual(signed, cbor(body))
        self.assertEqual(fields, body)

    def test_refusals(self):
        cases = [
            (b"\x00" * (slot.PROFILE_MAX + 1), "too large to be a signed profile"),
            (cbor([1, 2]), "not a CBOR map"),
            (cbor({"a": 1, "signature": b"s"}) + b"\x00", "bytes after the map"),
            (bytes([0xA3]) + cbor("a") + cbor(1) + cbor("a") + cbor(2) + cbor("signature")
             + cbor(b"s"), "'a' appears twice"),
            (bytes([0xA2]) + cbor("signature") + cbor(b"s") + cbor("signature") + cbor(b"t"),
             "'signature' appears twice"),
            (bytes([0xA1]) + cbor(b"k") + cbor(1), "map key that is not a text or integer"),
            (cbor({"a": 1}), "carries no signature"),
            (cbor({"a": 1, "signature": b""}), "carries no signature"),
            (cbor({"a": "x" * 4})[:-2], "is not a valid signed profile"),
        ]
        for data, why in cases:
            with self.subTest(why=why):
                with self.assertRaisesRegex(slot.SlotError, why):
                    slot.read_profile(self.write(data))


class Keys(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def key(self, text):
        path = self.dir / "signer.pem"
        path.write_text(text)
        return path

    def test_fingerprint_is_over_the_point_and_ignores_a_header(self):
        text = pem(slot.P256_SPKI + POINT).replace("-----\n", "-----\nCA-ID: goatnet-a\n", 1)
        fp, clean = slot.signer_key(self.key(text))
        self.assertEqual(fp, FINGERPRINT)
        self.assertNotIn("CA-ID", clean)

    def test_refuses_anything_but_one_uncompressed_p256_key(self):
        for text, why in (("no key here", "exactly one public key"),
                          (pem(slot.P256_SPKI + POINT) * 2, "exactly one public key"),
                          ("-----BEGIN PUBLIC KEY-----\n!!!!\n-----END PUBLIC KEY-----\n",
                           "not valid base64"),
                          (pem(slot.P256_SPKI + b"\x02" + bytes(64)), "not an uncompressed P-256"),
                          (pem(b"\x30" * 91), "not an uncompressed P-256")):
            with self.subTest(why=why):
                with self.assertRaisesRegex(slot.SlotError, why):
                    slot.signer_key(self.key(text))

    def test_normalize_accepts_the_usual_spellings(self):
        upper = FINGERPRINT.upper()
        for spelling in (FINGERPRINT, "SHA256:" + upper, "sha256 Fingerprint=" + ":".join(
                upper[i:i + 2] for i in range(0, 64, 2)), "  " + FINGERPRINT + " "):
            self.assertEqual(slot.normalize(spelling), FINGERPRINT, spelling)
        for bad in ("SHA256:" + FINGERPRINT[:-1], "md5:" + FINGERPRINT, ""):
            with self.assertRaisesRegex(slot.SlotError, "is not a SHA-256 fingerprint"):
                slot.normalize(bad)
        self.assertEqual(slot.shown("ab"), "SHA256:ab")


class Bundle(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name in (f"{ANCHOR}-cert.pem", f"{ANCHOR}-key.pem", "fabric-ca-root.pem"):
            (self.dir / name).write_text(f"synthetic {name}\n")
        (self.dir / "NOTES.md").write_text("Router endpoint: tls/fabric.example.invalid:7447\n")

    def test_finds_each_file_and_the_unauthenticated_endpoint(self):
        s = slot.slot_material(self.dir)
        self.assertEqual((s.anchor, s.ca.name, s.notes_endpoint, s.profile, s.signer),
                         (ANCHOR, "fabric-ca-root.pem", "tls/fabric.example.invalid:7447", None, None))

    def test_a_ca_file_without_ca_root_in_its_name_is_found_by_elimination(self):
        (self.dir / "fabric-ca-root.pem").rename(self.dir / "fabric-ca.pem")
        (self.dir / "NOTES.md").write_text("no endpoint here\n")
        s = slot.slot_material(self.dir)
        self.assertEqual((s.ca.name, s.notes_endpoint), ("fabric-ca.pem", ""))

    def test_layout_refusals(self):
        with self.assertRaisesRegex(slot.SlotError, "no such directory"):
            slot.slot_material(self.dir / "absent")
        (self.dir / "spare-cert.pem").write_text("x\n")
        with self.assertRaisesRegex(slot.SlotError, "exactly one \\*-cert.pem"):
            slot.slot_material(self.dir)
        (self.dir / "spare-cert.pem").unlink()
        (self.dir / "other-ca-root.pem").write_text("x\n")
        with self.assertRaisesRegex(slot.SlotError, "expected one CA roots file"):
            slot.slot_material(self.dir)
        (self.dir / "other-ca-root.pem").unlink()
        (self.dir / f"{ANCHOR}-goat-cli.cbor").write_bytes(b"x")
        with self.assertRaisesRegex(slot.SlotError, "a signed profile is one"):
            slot.slot_material(self.dir)
        for p in self.dir.glob(f"{ANCHOR}*"):
            p.rename(self.dir / p.name.replace(ANCHOR, "not-hex"))
        with self.assertRaisesRegex(slot.SlotError, "which is not hex"):
            slot.slot_material(self.dir)


class Trust(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name in (f"{ANCHOR}-cert.pem", f"{ANCHOR}-key.pem", "fabric-ca-root.pem"):
            (self.dir / name).write_text(f"synthetic {name}\n")
        self.roots = hashlib.sha256((self.dir / "fabric-ca-root.pem").read_bytes()).hexdigest()

    def unsigned(self):
        return slot.slot_material(self.dir)

    def signed(self, profile=b"not cbor"):
        (self.dir / f"{ANCHOR}-goat-cli-ca.pem").write_text(pem(slot.P256_SPKI + POINT))
        (self.dir / f"{ANCHOR}-goat-cli.cbor").write_bytes(profile)
        return slot.slot_material(self.dir)

    def test_an_unsigned_bundle(self):
        with self.assertRaisesRegex(slot.SlotError, "nothing to check --ca-fingerprint against"):
            slot.trust(self.unsigned(), expected="SHA256:" + FINGERPRINT)
        t = slot.trust(self.unsigned(), pinned_roots=self.roots.upper())
        self.assertEqual((t.ca_fingerprint, t.roots_sha256, t.warnings), ("", self.roots, []))
        with self.assertRaisesRegex(slot.SlotError, "checks its bundles against the goatnet's"):
            slot.trust(self.unsigned(), pinned_ca=FINGERPRINT)
        with self.assertRaisesRegex(slot.SlotError, "only you can vouch for its CA roots"):
            slot.trust(self.unsigned())
        with self.assertRaisesRegex(slot.SlotError, "not confirmed"):
            slot.trust(self.unsigned(), ask=lambda prompt: "no")
        asked = []
        t = slot.trust(self.unsigned(), ask=lambda prompt: asked.append(prompt) or "YES\n")
        self.assertIn(self.roots, asked[0])
        self.assertIn("trusted on your word", t.warnings[0])

    def test_a_signed_bundle_is_checked_against_the_fingerprint_first(self):
        other = "b" * 64
        with self.assertRaisesRegex(slot.SlotError, "not the SHA256:" + other):
            slot.trust(self.signed(), expected=other)
        with self.assertRaisesRegex(slot.SlotError, "but this pod pinned SHA256:" + other):
            slot.trust(self.signed(), pinned_ca=other)
        with self.assertRaisesRegex(slot.SlotError, "there is no fingerprint to check"):
            slot.trust(self.signed())
        with self.assertRaisesRegex(slot.SlotError, "not confirmed"):
            slot.trust(self.signed(), ask=lambda prompt: "")
        # The fingerprint matches, so the profile is read next, and this one is not CBOR.
        with self.assertRaisesRegex(slot.SlotError, "is not a valid signed profile"):
            slot.trust(self.signed(), expected=FINGERPRINT, pinned_ca=other)

    def test_record_pins_once_and_says_what_changed(self):
        pod = self.dir / "10-pod.yml"
        pod.write_text('pen_id: "pod-x"\nfabric_ca_roots_sha256: old   # pinned by hand\n')
        trusted = slot.Trusted(FINGERPRINT, self.roots, "tls/x:1", [])
        said = slot.record(pod, trusted)
        self.assertEqual(said, [f"pinned goatnet_ca_fingerprint SHA256:{FINGERPRINT} in 10-pod.yml",
                                f"pinned fabric_ca_roots_sha256 {self.roots} in 10-pod.yml"])
        text = pod.read_text()
        self.assertEqual(text.count("fabric_ca_roots_sha256:"), 1)
        self.assertIn("# pinned by hand", text)
        self.assertEqual(slot.record(pod, trusted), [])
        self.assertEqual(slot.record(pod, slot.Trusted("", self.roots, "", [])), [])

    def test_no_terminal_means_no_prompt(self):
        self.assertIsNone(slot.terminal_ask())


if __name__ == "__main__":
    unittest.main(verbosity=2)
