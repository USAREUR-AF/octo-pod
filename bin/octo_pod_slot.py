# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""The GOAT slot bundle, for octo-pod's CLI: which file is which, and whether the bundle
really came from the goatnet.

A bundle reaches a pod over a channel, and a channel can be wrong. Its files agree with
each other by construction, so checking them against each other proves nothing. GOAT
signs a profile into the bundle, <anchor>-goat-cli.cbor, that carries the slot's
certificate, private key, CA roots and router endpoint, and ships the public key that
signs it, <anchor>-goat-cli-ca.pem. The goatnet's operators give that key's fingerprint
out of band; it is the fingerprint `goat ca add` prints. So one value the operator
already has authenticates every file octo-pod installs:

    fingerprint (out of band) -> signing key -> signed profile -> cert, key, CA roots, endpoint

The first release pins the signing key's fingerprint and the CA roots' SHA-256 in
10-pod.yml. Later releases check the signing key against the pin, and every apply refuses
CA roots whose digest differs from the pinned one.

A bundle without a signed profile (an older bundle, or a goatnet that hands out roots
another way) has nothing to check a fingerprint against. Its CA roots are trusted only
when a person at a terminal confirms their digest, or when they match the pinned digest.

The signature is ECDSA P-256 over SHA-256 of the profile's canonical CBOR without its
"signature" entry, as goat-cli verifies it. openssl checks it; this module only finds the
bytes. The fingerprint is SHA-256 over the key's uncompressed point, written
SHA256:<hex>, and compared without regard to case, colons, spaces or the prefix.
"""
from octo_pod_i18n import critical as _critical
import base64
import hashlib
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from typing import NamedTuple, Optional

PIN_CA = "goatnet_ca_fingerprint"
PIN_ROOTS = "fabric_ca_roots_sha256"
PROFILE_MAX = 1 << 20        # a signed profile is a few KiB; refuse anything absurd unparsed
# SubjectPublicKeyInfo DER up to the point, for a P-256 key with an uncompressed point.
P256_SPKI = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")


class SlotError(Exception):
    """A bundle that cannot be used, with the reason in the message."""


class Slot(NamedTuple):
    anchor: str
    cert: pathlib.Path
    key: pathlib.Path
    ca: pathlib.Path                  # the fabric's CA roots, which signed the router's cert
    notes_endpoint: str               # NOTES.md's router endpoint: unauthenticated
    profile: Optional[pathlib.Path]   # <anchor>-goat-cli.cbor, the signed profile
    signer: Optional[pathlib.Path]    # <anchor>-goat-cli-ca.pem, the key that signs it


class Trusted(NamedTuple):
    ca_fingerprint: str   # 64 hex characters; empty for a bundle without a signed profile
    roots_sha256: str     # 64 hex characters: the CA roots file, as release checked it
    endpoint: str         # from the signed profile when there is one, else NOTES.md
    warnings: list


def slot_material(slot):
    """The files of a GOAT bundle directory, or raise naming what is wrong.

    The fabric's CA roots, not the portal's: a bundle carries both a *ca-root*.pem, which
    signed the router's certificate, and a *goat-cli-ca.pem, the public key that signs the
    profile. Exactly one set of roots must be found, or nothing is chosen."""
    slot = pathlib.Path(slot).expanduser()
    if not slot.is_dir():
        raise SlotError(_critical('no such directory: {v0}', v0=slot))
    certs = sorted(slot.glob("*-cert.pem"))
    keys = sorted(slot.glob("*-key.pem"))
    if len(certs) != 1 or len(keys) != 1:
        raise SlotError(_critical('expected exactly one *-cert.pem and one *-key.pem in {v0}, found {v1} and {v2}', v0=slot, v1=len(certs), v2=len(keys)))
    signers = sorted(slot.glob("*-goat-cli-ca.pem"))
    profiles = sorted(slot.glob("*-goat-cli.cbor"))
    roots = sorted(c for c in slot.glob("*.pem") if "ca-root" in c.name.lower())
    # An anchor is hex, so "ca" can occur inside it: the fallback excludes every file
    # whose role is already known, not only the ones named like a CA.
    known = set(certs) | set(keys) | set(signers)
    cas = roots or sorted(c for c in slot.glob("*.pem")
                          if "ca" in c.name.lower() and c not in known)
    if len(cas) != 1:
        raise SlotError(_critical('expected one CA roots file in {v0} (a *ca-root*.pem), found {v1}', v0=slot, v1=[c.name for c in cas]))
    anchor = certs[0].name[: -len("-cert.pem")]
    if not re.match(r"^[0-9a-f]{8,}$", anchor):
        raise SlotError(_critical('cert filename gives anchor {v0!r}, which is not hex; is this the right bundle?', v0=anchor))
    if len(signers) > 1 or len(profiles) > 1 or len(signers) != len(profiles):
        raise SlotError(_critical('a signed profile is one *-goat-cli.cbor and the one *-goat-cli-ca.pem that signs it; {v0} has {v1}. Copy the whole bundle again.', v0=slot, v1=[p.name for p in profiles + signers]))
    endpoint = ""
    notes = slot / "NOTES.md"
    if notes.exists():
        m = re.search(r"Router endpoint:\s*(tls/\S+)", notes.read_text())
        endpoint = m.group(1) if m else ""
    return Slot(anchor, certs[0], keys[0], cas[0], endpoint,
                profiles[0] if profiles else None, signers[0] if signers else None)


def normalize(fingerprint):
    """The 64 hex characters of a fingerprint written any of the usual ways, or raise."""
    # SHA256:<hex>, openssl's "sha256 Fingerprint=AA:BB:..." and bare hex all reduce to the
    # same 64 characters; "sha256" cannot start a hex string, so dropping it is unambiguous.
    s = re.sub(r"[\s:]", "", str(fingerprint)).lower()
    s = re.sub(r"^(sha256)?(fingerprint)?=?", "", s)
    if not re.fullmatch(r"[0-9a-f]{64}", s):
        raise SlotError(_critical('{v0!r} is not a SHA-256 fingerprint (64 hex characters, colons allowed, optional SHA256: prefix)', v0=fingerprint))
    return s


def shown(hex64):
    return f"SHA256:{hex64}"


def file_sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def signer_key(pem_path):
    """(fingerprint, clean PEM) of the P-256 public key that signs a profile.

    goat writes a CA-ID: header into some of these files. The fingerprint is over the key,
    not the file, so the header changes nothing; it is dropped from the copy openssl reads."""
    text = pathlib.Path(pem_path).read_text(errors="replace")
    blocks = re.findall(r"-----BEGIN PUBLIC KEY-----(.*?)-----END PUBLIC KEY-----", text, re.S)
    if len(blocks) != 1:
        raise SlotError(_critical('{v0} must hold exactly one public key', v0=pem_path))
    body = "".join(ln.strip() for ln in blocks[0].splitlines() if ln.strip() and ":" not in ln)
    try:
        der = base64.b64decode(body, validate=True)
    except ValueError as exc:
        raise SlotError(_critical('{v0} is not valid base64', v0=pem_path)) from exc
    if len(der) != len(P256_SPKI) + 65 or not der.startswith(P256_SPKI) or der[len(P256_SPKI)] != 4:
        raise SlotError(_critical('{v0} is not an uncompressed P-256 public key, which is what goat signs profiles with', v0=pem_path))
    pem = "-----BEGIN PUBLIC KEY-----\n" + "\n".join(
        body[i:i + 64] for i in range(0, len(body), 64)) + "\n-----END PUBLIC KEY-----\n"
    return hashlib.sha256(der[len(P256_SPKI):]).hexdigest(), pem


# ------------------------------------------------------------------ CBOR, read-only
# Just enough CBOR to find the signed bytes and read the profile's fields. The signed
# bytes are the issuer's own, with the signature entry cut out: re-encoding them would
# have to reproduce the issuer's canonical form exactly, and cutting cannot get that wrong.

def _head(b, i):
    mt, ai = b[i] >> 5, b[i] & 31
    i += 1
    if ai < 24:
        return mt, ai, i
    size = {24: 1, 25: 2, 26: 4, 27: 8}.get(ai)
    if size is None or i + size > len(b):
        raise ValueError("indefinite length, reserved value or truncated head")
    return mt, int.from_bytes(b[i:i + size], "big"), i + size


def _skip(b, i):
    mt, n, i = _head(b, i)
    if mt in (2, 3):
        if i + n > len(b):
            raise ValueError("truncated string")
        return i + n
    if mt in (4, 5):
        for _ in range(n * (2 if mt == 5 else 1)):
            i = _skip(b, i)
        return i
    if mt == 6:
        return _skip(b, i)
    return i                          # integers and simple values carry nothing more


def _decode(b, i):
    mt, n, j = _head(b, i)
    if mt == 0:
        return n, j
    if mt == 1:
        return -1 - n, j
    if mt in (2, 3):
        if j + n > len(b):
            raise ValueError("truncated string")
        raw = bytes(b[j:j + n])
        return (raw if mt == 2 else raw.decode()), j + n
    if mt == 4:
        out = []
        for _ in range(n):
            x, j = _decode(b, j)
            out.append(x)
        return out, j
    if mt == 5:
        out = {}
        for _ in range(n):
            k, j = _decode(b, j)
            x, j = _decode(b, j)
            if not isinstance(k, (str, int)) or k in out:
                raise ValueError("map key that is not a text or integer, or appears twice")
            out[k] = x
        return out, j
    if mt == 6:
        return _decode(b, j)          # tags (timestamps) are read as their content
    return {20: False, 21: True, 22: None}.get(n, n), j


def _map_head(n):
    if n < 24:
        return bytes([0xA0 | n])
    for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * size):
            return bytes([0xA0 | ai]) + n.to_bytes(size, "big")
    raise ValueError("map too large")


def read_profile(path):
    """(signed bytes, signature, fields) of a signed profile, or raise."""
    data = pathlib.Path(path).read_bytes()
    if len(data) > PROFILE_MAX:
        raise SlotError(_critical('{v0} is {v1} bytes, too large to be a signed profile', v0=path, v1=len(data)))
    try:
        mt, n, i = _head(data, 0)
        if mt != 5:
            raise ValueError("not a CBOR map")
        kept, signature, fields = [], None, {}
        for _ in range(n):
            start = i
            key, i = _decode(data, i)
            if not isinstance(key, (str, int)):
                raise ValueError("map key that is not a text or integer")
            value_at = i
            i = _skip(data, i)
            if key in fields or (key == "signature" and signature is not None):
                raise ValueError(f"{key!r} appears twice")
            if key == "signature":
                signature = _decode(data, value_at)[0]
            else:
                kept.append(data[start:i])
                fields[key] = _decode(data, value_at)[0]
        if i != len(data):
            raise ValueError("bytes after the map")
    except (IndexError, ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise SlotError(_critical('{v0} is not a valid signed profile ({v1})', v0=path, v1=exc)) from None
    if not isinstance(signature, bytes) or not signature:
        raise SlotError(_critical('{v0} carries no signature', v0=path))
    return _map_head(len(kept)) + b"".join(kept), signature, fields


def verify_signature(pem, signed, signature, path):
    """openssl's verdict on an ECDSA P-256 signature over SHA-256(signed), or raise.

    The signed bytes hold the slot's private key and a portal token, so they reach openssl
    on stdin and never touch a file."""
    with tempfile.TemporaryDirectory() as tmp:
        key, sig = pathlib.Path(tmp) / "signer.pem", pathlib.Path(tmp) / "signature.der"
        key.write_text(pem)
        sig.write_bytes(signature)
        try:
            r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(key),
                                "-signature", str(sig)],
                               input=signed, capture_output=True, check=False)
        except FileNotFoundError:
            raise SlotError(_critical("openssl is needed to check the signed profile, and it is not "
                            "installed")) from None
    if r.returncode != 0:
        raise SlotError(_critical('{v0} does not carry a valid signature from the key beside it. The profile was changed after it was signed, or the key is not the one that signed it. Nothing was changed.', v0=path.name))


# ------------------------------------------------------------------ the decision

def _confirm(ask, what, value):
    answer = ask(
        f"\n  {what}: {value}\n"
        "  Compare it with the value the goatnet's operators gave you through another\n"
        "  channel, not the bundle this file came from.\n"
        "  Type yes if they match exactly: ")
    if answer.strip().lower() != "yes":
        raise SlotError(_critical("not confirmed. Nothing was changed."))


def trust(slot, expected=None, pinned_ca="", pinned_roots="", ask=None, now=None):
    """What release may pin, once the bundle is authenticated; raise SlotError otherwise.

    expected:     the operator's --ca-fingerprint, checked first.
    pinned_ca:    the signing key an earlier release pinned; a different key needs
                  --ca-fingerprint.
    pinned_roots: the CA roots digest an earlier release pinned.
    ask:          a function that shows a prompt and returns the answer, used only when
                  there is nothing to check against and a person is at a terminal."""
    roots = file_sha256(slot.ca)
    if slot.profile is None:
        return _trust_unsigned(slot, roots, expected, pinned_ca, pinned_roots, ask)

    actual, pem = signer_key(slot.signer)
    warnings = []
    who = f"{slot.signer.name}, the key that signs this bundle,"
    if expected:
        want = normalize(expected)
        if want != actual:
            raise SlotError(
                _critical("{v0} has fingerprint {v1}, not the {v2} you passed. Either the bundle is from another goatnet or someone substituted the key. Ask the goatnet's operators which key signs your bundle. Nothing was changed.", v0=who, v1=shown(actual), v2=shown(want)))
        if pinned_ca and normalize(pinned_ca) != actual:
            warnings.append(f"the goatnet's signing key changes from "
                            f"{shown(normalize(pinned_ca))} to {shown(actual)}, as you passed")
    elif pinned_ca:
        if normalize(pinned_ca) != actual:
            raise SlotError(
                _critical('{v0} has fingerprint {v1}, but this pod pinned {v2}. If the goatnet has changed its signing key, pass the new fingerprint with --ca-fingerprint. Nothing was changed.', v0=who, v1=shown(actual), v2=shown(normalize(pinned_ca))))
    elif ask is not None:
        _confirm(ask, "Signing key fingerprint", shown(actual))
    else:
        raise SlotError(
            _critical("there is no fingerprint to check this bundle's signing key against. Its fingerprint is {v0}. Compare it with the one the goatnet's operators gave you through another channel, then re-run with --ca-fingerprint {v1}. Nothing was changed.", v0=shown(actual), v1=shown(actual)))

    signed, signature, fields = read_profile(slot.profile)
    verify_signature(pem, signed, signature, slot.profile)
    extras = fields.get("goat_cli_extras")
    if not isinstance(extras, dict):
        raise SlotError(_critical('{v0} is signed but carries no profile (goat_cli_extras). Ask for the bundle again.', v0=slot.profile.name))
    for label, path, field in (("certificate", slot.cert, "mtls_cert"),
                               ("private key", slot.key, "mtls_key"),
                               ("CA roots", slot.ca, "ca_roots")):
        if extras.get(field) != path.read_bytes():
            raise SlotError(
                _critical('{v0} is not the {v1} the signed profile carries. It was changed after the bundle was signed, or it comes from another bundle. Nothing was changed.', v0=path.name, v1=label))
    endpoint = extras.get("router_endpoint")
    if not isinstance(endpoint, str) or not endpoint.startswith("tls/"):
        raise SlotError(_critical('{v0} carries no tls/ router endpoint', v0=slot.profile.name))
    if pinned_roots and normalize(pinned_roots) != roots:
        warnings.append(f"the fabric's CA roots change from {normalize(pinned_roots)} to "
                        f"{roots}; the signed profile vouches for the new ones")
    expires = fields.get("expires_at")
    if isinstance(expires, int) and expires < (time.time() if now is None else now):
        warnings.append(f"the signed profile expired on "
                        f"{time.strftime('%Y-%m-%d', time.gmtime(expires))}; ask the goatnet's "
                        "operators for a fresh bundle before its certificate stops working")
    return Trusted(actual, roots, endpoint, warnings)


def _trust_unsigned(slot, roots, expected, pinned_ca, pinned_roots, ask):
    name = f"{slot.anchor}-goat-cli.cbor and {slot.anchor}-goat-cli-ca.pem"
    if expected:
        raise SlotError(
            _critical("this bundle has no signed profile ({v0}), so there is nothing to check --ca-fingerprint against. Ask the goatnet's operators for the whole bundle. Nothing was changed.", v0=name))
    if pinned_roots and normalize(pinned_roots) == roots:
        return Trusted("", roots, slot.notes_endpoint, [])
    if pinned_ca:
        raise SlotError(
            _critical("this pod checks its bundles against the goatnet's signing key, and this bundle has no signed profile ({v0}) and CA roots it has not seen. Ask the goatnet's operators for the whole bundle. Nothing was changed.", v0=name))
    if ask is None:
        raise SlotError(
            _critical("this bundle has no signed profile ({v0}), so only you can vouch for its CA roots. Their SHA-256 is {v1}. Run release at a terminal to compare it with the digest the goatnet's operators gave you, or ask them for the whole bundle. Nothing was changed.", v0=name, v1=roots))
    _confirm(ask, f"{slot.ca.name} SHA-256", roots)
    return Trusted("", roots, slot.notes_endpoint,
                   ["this bundle has no signed profile: its CA roots are trusted on your word"])


def record(pod_file, trusted):
    """Write the pins into 10-pod.yml, and return what changed for the CLI to print."""
    pins = [(PIN_ROOTS, trusted.roots_sha256)]
    if trusted.ca_fingerprint:
        pins.insert(0, (PIN_CA, shown(trusted.ca_fingerprint)))
    text = pod_file.read_text()
    said = []
    for key, value in pins:
        line = f'{key}: "{value}"'
        m = re.search(rf"^{key}:[ \t]*(\"[^\"\n]*\"|'[^'\n]*'|[^\s#]*)", text, re.M)
        if m and m.group(0) == line:
            continue
        text = (text[: m.start()] + line + text[m.end():]) if m else \
               (text.rstrip("\n") + "\n" + line + "\n")
        said.append(f"pinned {key} {value} in 10-pod.yml")
    pod_file.write_text(text)
    return said


def terminal_ask():
    """A prompt function when a person is at a terminal, else None."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    return input
