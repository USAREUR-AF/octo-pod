#!/usr/bin/env python3
"""
Prove a peer's grants against a REAL Zenoh router, not a reading of the config.

Renders the pen ACL from platform/templates/pen/router-acl.json5.j2 with the test
fixtures, runs it in a Zenoh router on localhost with mutual TLS and freshly minted
certificates, then connects as each identity and checks every allow AND every deny:

    india    a peer released to read downlink/<sensor2>      (allow-read)
    juliet   a peer whose read is declared but NOT released
    alpha    a peer with no read at all
    bridge   octo-downlink-bridge, the only writer of downlink/**
    firstp   octo-pen-router, first party

A denied put or subscription in Zenoh is silent on both sides, so every deny here is
asserted the only way it can be: the sample that must not arrive, does not.

Needs: python3 with eclipse-zenoh (the pen's pinned version), pyyaml, jinja2; openssl.

    python3 -m venv /tmp/zv && /tmp/zv/bin/pip install eclipse-zenoh==1.9.0 pyyaml jinja2
    /tmp/zv/bin/python tools/prove-peer-access.py
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

import zenoh

REPO = pathlib.Path(__file__).resolve().parent.parent
PORT = 17447
SETTLE = 1.0
FAILS = []


def harness():
    spec = importlib.util.spec_from_file_location("tt", REPO / "platform" / "tests" / "test_templates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mint(d, cns):
    """A CA, the router's leaf (SAN 127.0.0.1) and one client leaf per CN."""
    def sh(*a):
        subprocess.run(a, check=True, capture_output=True, cwd=d)
    sh("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
       "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "2", "-subj", "/CN=proof-ca")
    for name, cn, ext in [("router", "octo-pen-router-listen", "subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth,clientAuth")] + \
                         [(cn, cn, f"subjectAltName=DNS:{cn}\nextendedKeyUsage=clientAuth") for cn in cns]:
        (d / f"{name}.ext").write_text(ext + "\nbasicConstraints=CA:FALSE\n")
        sh("openssl", "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
           "-keyout", f"{name}.key", "-out", f"{name}.csr", "-subj", f"/CN={cn}")
        sh("openssl", "x509", "-req", "-in", f"{name}.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
           "-CAcreateserial", "-days", "2", "-extfile", f"{name}.ext", "-out", f"{name}.pem")


def router_config(cfg, d):
    # PROOF_DISABLE_ACL=1 is the control: with access control off, every deny check below
    # must FAIL. If one still passes, it was passing on timing or a dead link, not the ACL.
    if os.environ.get("PROOF_DISABLE_ACL") == "1":
        cfg["access_control"]["enabled"] = False
        print("CONTROL RUN: access control DISABLED — every deny check should FAIL")
    cfg["listen"] = {"endpoints": [f"tls/127.0.0.1:{PORT}"]}
    tls = cfg["transport"]["link"]["tls"]
    tls.update({"listen_certificate": str(d / "router.pem"), "listen_private_key": str(d / "router.key"),
                "connect_certificate": str(d / "router.pem"), "connect_private_key": str(d / "router.key"),
                "root_ca_certificate": str(d / "ca.pem")})
    return zenoh.Config.from_json5(json.dumps(cfg))


def client(d, cn):
    c = zenoh.Config()
    c.insert_json5("mode", '"client"')
    c.insert_json5("connect/endpoints", f'["tls/127.0.0.1:{PORT}"]')
    c.insert_json5("scouting/multicast/enabled", "false")
    c.insert_json5("transport/link/tls", json.dumps({
        "root_ca_certificate": str(d / "ca.pem"), "connect_certificate": str(d / f"{cn}.pem"),
        "connect_private_key": str(d / f"{cn}.key"), "enable_mtls": True,
        "verify_name_on_connect": False}))
    return zenoh.open(c)


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n          {detail}"))
    if not ok:
        FAILS.append(label)


def received(session, key_expr, publisher, put_key):
    """Subscribe as `session`, put as `publisher`, return what arrived."""
    got, lock = [], threading.Lock()

    def cb(s):
        with lock:
            got.append(str(s.key_expr))
    sub = session.declare_subscriber(key_expr, cb)
    time.sleep(SETTLE)
    publisher.put(put_key, b"proof")
    time.sleep(SETTLE)
    sub.undeclare()
    return got


def main():
    tt = harness()
    ctx = tt.load_ctx()
    acl_text = tt.render("pen/router-acl.json5.j2", ctx)
    cfg = tt.json5_to_obj(acl_text)
    sensor2 = "downlink/1d18de66e8a09a5e68170b3b3de6563b/sensors/sensor2"
    tpq50 = "downlink/1d18de66e8a09a5e68170b3b3de6563b/sensors/tpq50"
    radar1 = "downlink/1d18de66e8a09a5e68170b3b3de6563b/sensors/radar1/asterix/data"
    cns = ["octo-peer-india", "octo-peer-juliet", "octo-peer-alpha", "octo-downlink-bridge", "octo-pen-router"]

    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        mint(d, cns)
        print(f"router: Zenoh {getattr(zenoh, '__version__', '1.9.x')} on tls/127.0.0.1:{PORT}, "
              "ACL rendered from router-acl.json5.j2 with the test fixtures")
        router = zenoh.open(router_config(cfg, d))
        time.sleep(SETTLE)
        india, juliet, alpha, bridge, firstp = (client(d, cn) for cn in cns)
        time.sleep(SETTLE)

        print("\nwrite — a peer writes its own namespace and nothing else")
        got = received(firstp, "raw/india/**", india, "raw/india/x")
        check("india's put to raw/india/x reaches first party", got == ["raw/india/x"], got)
        got = received(firstp, "raw/alpha/**", india, "raw/alpha/x")
        check("india's put to raw/alpha/x (not its namespace) is dropped", got == [], got)
        got = received(firstp, "downlink/**", india, f"{sensor2}/x")
        check("india cannot write the downlink it reads (only the bridge may)", got == [], got)

        print("\nread — a released read, and nothing beside it")
        got = received(india, f"{sensor2}/**", bridge, f"{sensor2}/track1")
        check("india receives what the bridge brings down on its released key",
              got == [f"{sensor2}/track1"], got)
        got = received(india, f"{radar1}", bridge, radar1)
        check("india receives nothing on a downlink key released to someone else", got == [], got)
        got = received(india, "raw/alpha/**", alpha, "raw/alpha/y")
        check("india cannot read another peer's raw namespace", got == [], got)

        print("\nread — declared but shut, and never declared")
        got = received(juliet, f"{tpq50}/**", bridge, f"{tpq50}/track1")
        check("juliet (read declared, NOT released) receives nothing", got == [], got)
        got = received(alpha, f"{sensor2}/**", bridge, f"{sensor2}/track2")
        check("alpha (no read declared) receives nothing on india's key", got == [], got)

        print("\npositive control — the router delivers when the ACL allows")
        got = received(firstp, "raw/alpha/**", alpha, "raw/alpha/z")
        check("alpha's own write reaches first party", got == ["raw/alpha/z"], got)

        for s in (india, juliet, alpha, bridge, firstp, router):
            s.close()

    print("\n" + ("All checks passed." if not FAILS else f"FAILED — {len(FAILS)}: {', '.join(FAILS)}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
