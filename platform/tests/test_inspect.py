#!/usr/bin/env python3
"""
bin/octo-pod-inspect (get, describe, logs, certs) against a scratch pod.

The pod is test_top.py's: a real declaration, a fake podman and netbird that record
every call, and stats lines in the shapes the containers print. On top of it this adds
a fake systemctl and journalctl, and real certificates made by openssl in the temporary
directory: one valid, one ending in five days, one expired, a CA roots file holding two,
a slot certificate, the downlink bridge's copy and a retired destination's leaf.

    python3 platform/tests/test_inspect.py         standalone
    test_release.py runs run_checks(), so CI does too.
"""
import contextlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_top import build_pod, stamp  # the same scratch pod as top's tests

REPO = pathlib.Path(__file__).resolve().parents[2]
INSPECT = REPO / "bin" / "octo-pod-inspect"
SECRET = "A1B2-THIS-MUST-NOT-APPEAR-C3D4"
ROOT = os.geteuid() == 0   # root reads through every mode bit, so those checks skip

FAKE_SYSTEMCTL = r'''#!/usr/bin/env python3
import os, pathlib, sys
fx = pathlib.Path(os.environ["FAKE_DIR"])
with open(fx / "calls.log", "a") as f:
    f.write("systemctl " + " ".join(sys.argv[1:]) + "\n")
if sys.argv[1:2] != ["show"]:
    sys.exit(1)
sys.stdout.write("""Id=pen-cert-renew.timer
LoadState=loaded
ActiveState=active
NextElapseUSecRealtime=Fri 2026-10-16 00:47:12 UTC
LastTriggerUSec=Thu 2026-10-15 00:12:01 UTC

Id=pen-cert-expiry.timer
LoadState=not-found
ActiveState=inactive
NextElapseUSecRealtime=
LastTriggerUSec=
""")
'''

FAKE_JOURNALCTL = r'''#!/usr/bin/env python3
import os, pathlib, sys
fx = pathlib.Path(os.environ["FAKE_DIR"])
with open(fx / "calls.log", "a") as f:
    f.write("journalctl " + " ".join(sys.argv[1:]) + "\n")
a = sys.argv[1:]
p = fx / "journal" / (a[a.index("-u") + 1] if "-u" in a else "none")
sys.stdout.write(p.read_text() if p.exists() else "")
'''


def openssl(*args, cwd):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


def leaf(work, out, cn, start, end):
    """A leaf signed by the test CA with explicit dates. `openssl ca` rather than
    `x509 -days`, because only it takes an end date in the past on every openssl a CI
    runner might have."""
    def at(t):
        return time.strftime("%Y%m%d%H%M%SZ", time.gmtime(t))
    name = pathlib.Path(out).name.removesuffix("-cert.pem").removesuffix(".pem")
    openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key", "-subj",
            f"/CN={cn}", "-out", f"{name}.csr", cwd=work)
    openssl("ca", "-batch", "-notext", "-config", "ca.cnf", "-cert", "ca.pem", "-keyfile",
            "ca.key", "-in", f"{name}.csr", "-out", str(out), "-startdate", at(start),
            "-enddate", at(end), cwd=work)


def make_certs(work, tls):
    """A CA, a second root in the same roots file, and three leaves in the pen's dir."""
    work, tls = pathlib.Path(work), pathlib.Path(tls)
    work.mkdir(parents=True)
    tls.mkdir(parents=True)
    (work / "newcerts").mkdir()
    (work / "index.txt").write_text("")
    (work / "serial").write_text("1000\n")
    (work / "ca.cnf").write_text(
        f"[ca]\ndefault_ca = d\n[d]\ndir = {work}\ndatabase = {work}/index.txt\n"
        f"new_certs_dir = {work}/newcerts\nserial = {work}/serial\ndefault_md = sha256\n"
        "policy = p\nunique_subject = no\n[p]\ncommonName = supplied\n")
    for name, cn in (("ca", "octo-test-ca"), ("old", "octo-test-old-root")):
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key",
                "-subj", f"/CN={cn}", "-days", "3650", "-out", f"{name}.pem", cwd=work)
    (tls / "ca-roots.pem").write_text((work / "ca.pem").read_text() + (work / "old.pem").read_text())
    now = time.time()
    for name, (cn, start, end) in {
            "radar1-arm": ("octo-arm-radar1", now - 86400, now + 400 * 86400),
            "c2-tcp-egress": ("octo-egress-c2-tcp", now - 86400, now + 5 * 86400 + 3600),
            "field1": ("octo-peer-field1", now - 900 * 86400, now - 10 * 86400)}.items():
        leaf(work, tls / f"{name}-cert.pem", cn, start, end)
    # A private key beside its certificate, as on a pod. It must never be read or listed.
    (tls / "radar1-arm-key.pem").write_text("-----BEGIN PRIVATE KEY-----\nnot-read\n")


def add_fakes(env, fake):
    fakebin = pathlib.Path(env["PATH"].split(os.pathsep)[0])
    for name, body in (("systemctl", FAKE_SYSTEMCTL), ("journalctl", FAKE_JOURNALCTL)):
        (fakebin / name).write_text(body)
        (fakebin / name).chmod(0o755)
    (fake / "journal").mkdir(exist_ok=True)
    return fakebin


def run(*args, env, binary=False):
    r = subprocess.run([sys.executable, str(INSPECT), *args], env=env, capture_output=True,
                       timeout=60, check=False)
    if binary:
        return r
    return subprocess.CompletedProcess(r.args, r.returncode, r.stdout.decode(errors="replace"),
                                       r.stderr.decode(errors="replace"))


def rows(out):
    return [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip()]


@contextlib.contextmanager
def loaded(env):
    """bin/octo-pod-inspect as a module, with the scratch pod's environment in force."""
    saved = dict(os.environ)
    os.environ.update(env)
    try:
        loader = importlib.machinery.SourceFileLoader("octo_pod_inspect", str(INSPECT))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        yield mod
    finally:
        os.environ.clear()
        os.environ.update(saved)


def run_checks(repo, check):
    if not shutil.which("openssl"):
        check("openssl is installed (needed to make test certificates)", False)
        return
    with tempfile.TemporaryDirectory() as t:
        main_pod(repo, check, pathlib.Path(t))
    with tempfile.TemporaryDirectory() as t:
        broken_pods(repo, check, pathlib.Path(t))


def main_pod(repo, check, root):
    env, fake = build_pod(root)
    add_fakes(env, fake)
    with open(fake / "ps.txt", "a") as f:   # the pod's own containers, not publishers
        f.write("octo-health-pub\tUp 3 hours\nocto-pen-audit-sink\tUp 3 hours\n"
                "octo-pen-audit-sink-field1\tUp 3 hours\n")
    (fake / "journal" / "octo-udp-ingest-radar2.service").write_text(
        "2026-10-15T09:00:00Z ERROR arm: bind 0.0.0.0:8601: address in use\n")
    hv = pathlib.Path(env["OCTO_POD_ETC"]) / "host_vars" / "pod-test"
    (hv / "20-peers.yml").write_text(
        "peers:\n  - id: field1\n    namespace: raw/octo/field1\n"
        f"    setup_key: {SECRET}\n    setupKey: {SECRET}\n    apiKey: {SECRET}\n"
        f"    tokens: [{SECRET}]\n    passphrase: {SECRET}\n    psk: {SECRET}\n"
        f"    url: tcp://ops:{SECRET}@192.0.2.1:9000\n"
        f"    auth:\n      api_token: {SECRET}\n      password: {SECRET}\n"
        f"    tls:\n      client: |\n        -----BEGIN PRIVATE KEY-----\n        {SECRET}\n"
        "        -----END PRIVATE KEY-----\n    keys: [aabbcc/field1/**]\n")
    tls = pathlib.Path(env["OCTO_PEN_STATE"]) / "zenoh" / "tls"
    work = root / "ca-work"
    make_certs(work, tls)
    now = time.time()
    leaf(work, tls / "pod-cert.pem", "octo-pen-router", now - 86400, now + 800 * 86400)
    # A removed destination: its certificate stays for the audit trail, its key is gone.
    leaf(work, tls / "gone-egress-cert.pem", "octo-egress-gone", now - 86400, now + 5 * 86400)
    copy = root / "copies" / "octo-egress-c2-tcp" / "tls"
    copy.mkdir(parents=True)
    shutil.copy(tls / "c2-tcp-egress-cert.pem", copy / "egress-cert.pem")
    down = root / "octo-downlink" / "tls"
    down.mkdir(parents=True)
    leaf(work, down / "downlink-cert.pem", "octo-downlink-bridge", now - 86400, now + 800 * 86400)
    shutil.copy(work / "ca.pem", down / "ca-root.pem")
    (down / "downlink-key.pem").write_text("not-read\n")
    slot = root / "octo-uplink"   # the anchor is aabbcc, from test_top's 10-pod.yml
    slot.mkdir()
    leaf(work, slot / "aabbcc-cert.pem", "octo-slot-aabbcc", now - 86400, now + 20 * 86400)
    shutil.copy(work / "old.pem", slot / "fabric-ca-root.pem")
    (slot / "aabbcc-key.pem").write_text("not-read\n")
    env["OCTO_POD_CERT_COPIES"] = f"{root}/copies/octo-egress-*/tls:{down}:{slot}"

    def r(*args, **kw):
        return run(*args, env=kw.get("env", env))

    def calls():
        return (fake / "calls.log").read_text().splitlines()

    print("\ninspect: get, every kind")
    p = r("get")
    check("get with no kind lists every flow, header first",
          p.returncode == 0 and p.stdout.startswith("NAME") and
          {"radar1", "radar2", "field1", "uplink", "downlink", "c2-tcp"} <= set(rows(p.stdout)),
          p.stdout + p.stderr)
    if not ROOT:
        check("without root, every verb says the view is incomplete and how to rerun it",
              "not root" in p.stderr and "sudo octo-pod get" in p.stderr, p.stderr)
    want = {"arms": ["radar1", "radar2", "c2-tcp"], "ingress": ["radar1", "radar2"], "peers": ["field1"], "egress": ["c2-tcp"],
            "bridges": ["uplink", "downlink"], "mesh": ["consumer-a", "laptop-b"]}
    for kind, ids in want.items():
        p = r("get", kind)
        check(f"get {kind} prints one row per item", p.returncode == 0
              and rows(p.stdout) == ids, p.stdout + p.stderr)
        p = r("get", kind, "-o", "json")
        try:
            doc = json.loads(p.stdout)
        except ValueError:
            doc = {}
        got = [i.get("id", i.get("name")) for i in doc.get("items", [])]
        check(f"get {kind} -o json is the same items", doc.get("kind") == kind
              and got == ids, p.stdout[-400:] + p.stderr)
    check("a singular kind works", rows(r("get", "arm").stdout) == ["radar1", "radar2", "c2-tcp"])
    arm_doc = json.loads(r("get", "arms", "-o", "json").stdout)
    check("all arms have explicit direction", {i['id']: i.get('direction') for i in arm_doc['items']} == {'radar1':'ingress','radar2':'ingress','c2-tcp':'egress'})
    check("arm table labels direction", 'DIRECTION' in r('get','arms').stdout)
    arms_file = hv/'30-arms.yml'
    arms_before = arms_file.read_text()
    try:
        arms_file.write_text('arms: []\n')
        check("egress-only pod still lists its arms", rows(r('get','arms').stdout) == ['c2-tcp'])
        check("egress-only pod says no ingress", r('get','ingress').stdout.strip() == '' and 'no ingress arms on this pod' in r('get','ingress').stderr)
    finally:
        arms_file.write_text(arms_before)
    check("destination is egress", rows(r("get", "destination").stdout) == ["c2-tcp"])
    p = r("get", "arms", "-o", "wide")
    check("-o wide adds the topic and transport", "TOPIC" in p.stdout
          and "raw/octo/radar1" in p.stdout and "udp :8600 from 192.0.2.10" in p.stdout, p.stdout)
    line = next((ln for ln in r("get", "flows").stdout.splitlines()
                 if ln.startswith("radar1 ")), "")
    check("an arm's row has its rates, drops and stats age",
          line.split()[:7] == ["radar1", "arm", "up", "3h", "10", "9.8", "3"], line)
    doc = json.loads(r("get", "mesh", "-o", "json").stdout or "{}")
    check("get mesh -o json carries management and signal", doc.get("management") is True
          and doc.get("signal") is True, str(doc)[-300:])
    p = r("get", "problems")
    check("get problems lists them, worst first", p.returncode == 0
          and "radar2: declared, not running" in p.stdout
          and p.stdout.splitlines()[1].startswith("bad"), p.stdout)
    doc = json.loads(r("get", "problems", "-o", "json").stdout or "{}")
    check("get problems -o json", any(i["text"] == "c2-tcp: no consumer connected"
                                      for i in doc.get("items", [])), str(doc)[-300:])
    doc = json.loads(r("get", "certs", "-o", "json").stdout or "{}")
    check("get certs -o json lists every certificate", len(doc.get("items", [])) == 12,
          str([i.get("cert") for i in doc.get("items", [])]))
    p = r("get", "certs", "-o", "wide")
    check("get certs -o wide adds the subject CN and the full path",
          "SUBJECT CN" in p.stdout and "octo-slot-aabbcc" in p.stdout
          and str(slot / "aabbcc-cert.pem") in p.stdout, p.stdout)
    p = r("get", "nope")
    check("an unknown kind exits 2 and names the kinds", p.returncode == 2
          and "flows, arms, ingress, peers, egress, bridges, mesh, problems, certs" in p.stderr, p.stderr)

    print("\ninspect: describe")
    for resource, name, expected in (("flows", "radar1", "arm"),
                                     ("arms", "radar1", "arm"),
                                     ("arms", "c2-tcp", "egress"),
                                     ("ingress", "radar1", "arm"),
                                     ("bridges", "uplink", "bridge"),
                                     ("egress", "c2-tcp", "egress")):
        p = r("describe", resource, name)
        check("typed describe " + resource, p.returncode == 0 and f"Kind:   {expected}" in p.stdout)
    check("ingress lookup refuses egress", r("describe", "ingress", "c2-tcp").returncode != 0)
    check("typed describe refuses wrong resource", r("describe", "bridges", "radar1").returncode != 0)
    check("typed describe refuses unknown resource", r("describe", "nonsense", "radar1").returncode == 2)

    p = r("describe", "radar1")
    out = p.stdout
    check("describe an arm: every section", p.returncode == 0 and all(s in out for s in (
        "Declaration:", "Container:", "Flow:", "Certificate:", "Problems:",
        "Recent log lines (last 10, stats lines left out):", "Dig further (copy and run):")),
        out + p.stderr)
    check("describe an arm: the container, its rates and counters",
          "octo-udp-ingest-radar1" in out and "In:          10/s" in out
          and "received             1000" in out and "CPU:     1.25%" in out, out)
    check("describe an arm: its certificate from the pen",
          "pen/radar1-arm-cert.pem  CN=octo-arm-radar1" in out and "ok)" in out, out)
    recent = out.split("Recent log lines")[-1].split("Dig further")[0]
    check("describe an arm: recent lines leave out the stats Flow already shows, and colour",
          "not a stats line" in recent and "stats received" not in recent
          and "\x1b" not in out, recent)
    check("describe an arm: the commands to dig further",
          "sudo podman logs -f --tail 50 octo-udp-ingest-radar1" in out
          and "sudo octo-pod verify" in out, out)
    if not ROOT:
        check("describe without root says so first under Problems",
              out.split("Problems:")[1].lstrip().startswith("!! not root"), out)
    p = r("describe", "radar2")
    check("describe a stopped arm reads its unit's journal and says how to",
          "address in use" in p.stdout and "sudo journalctl -u octo-udp-ingest-radar2" in p.stdout,
          p.stdout)
    p = r("describe", "field1")
    out = p.stdout
    check("describe a peer: its audit sink, its expired certificate",
          p.returncode == 0 and "octo-pen-audit-sink-field1" in out
          and "CN=octo-peer-field1" in out and "EXPIRED" in out
          and "pod-cert.pem" not in out, out + p.stderr)
    check("describe never prints a credential, whatever the key looks like",
          SECRET not in out and "PRIVATE" not in out and "namespace: raw/octo/field1" in out
          and "aabbcc/field1/**" in out and "tcp://<redacted>@192.0.2.1:9000" in out, out)
    out = r("describe", "c2-tcp").stdout
    check("describe an egress destination: keys shown, both copies of its certificate",
          "aabbcc/sensors/**" in out and "pen/c2-tcp-egress-cert.pem" in out
          and "egress/c2-tcp/egress-cert.pem" in out
          and "renew soon" in out and "sudo octo-pod egress check" in out, out)
    p = r("describe", "uplink")
    check("describe a bridge: its flow and the slot certificate it presents",
          p.returncode == 0 and "Kind:   bridge" in p.stdout and "octo-uplink-bridge" in p.stdout
          and "slot/aabbcc-cert.pem" in p.stdout, p.stdout)
    check("a bridge's recent lines are not its stats lines",
          "uplink stats" not in p.stdout.split("Recent log lines")[-1], p.stdout)
    for name in ("octo-udp-ingest-oldarm", "udp-ingest-oldarm", "container/udp-ingest-oldarm"):
        p = r("describe", name)
        check(f"describe a container by name ({name})", p.returncode == 0
              and "Kind:   container" in p.stdout and "nothing declares this" in p.stdout
              and "orphan octo-udp-ingest-oldarm" in p.stdout, p.stdout + p.stderr)
    for name, cert in (("pen-zenoh-router", "pen/pod-cert.pem"),
                       ("octo-pen-audit-sink-field1", "pen/pod-cert.pem"), ("health-pub", None)):
        out = r("describe", name).stdout
        check(f"describe pod infrastructure ({name}): built in, quiet by design",
              "Kind:   infra" in out and "pod infrastructure" in out
              and "this container logs no stats line" in out
              and "nothing declares" not in out and (not cert or cert in out), out)
    check("a kind-qualified id works", "Kind:   arm" in r("describe", "arm/radar1").stdout)
    p = r("describe", "radr1")
    check("an unknown id exits 1 with the closest ids and how to list them",
          p.returncode == 1 and "radar1" in p.stderr and "octo-pod get" in p.stderr
          and not p.stdout, p.stderr)
    p = r("describe", "oldarm")
    check("a partial name suggests the container", p.returncode == 1
          and "octo-udp-ingest-oldarm" in p.stderr, p.stderr)
    p = r("describe", "sidelink")
    check("a weak match suggests nothing rather than an unrelated bridge",
          p.returncode == 1 and "Did you mean" not in p.stderr, p.stderr)

    print("\ninspect: logs")

    def last_logs():
        return [c for c in calls() if c.startswith(("logs --tail", "journalctl -u"))][-1:]

    p = r("logs", "arms", "radar1", "--tail", "3")
    check("typed logs resolves its resource", p.returncode == 0 and last_logs() == ["logs --tail 3 -- octo-udp-ingest-radar1"])
    check("typed logs refuses wrong resource", r("logs", "egress", "radar1").returncode != 0)
    p = r("logs", "radar1")
    check("logs runs podman logs for the arm's container, 100 lines by default",
          p.returncode == 0 and last_logs() == ["logs --tail 100 -- octo-udp-ingest-radar1"]
          and "octo_arm_common::stats" in p.stdout, f"{last_logs()} {p.stderr}")
    r("logs", "radar1", "-f", "--tail", "5", "--since", "10m")
    check("-f, --tail and --since pass through",
          last_logs() == ["logs --tail 5 -f --since 10m -- octo-udp-ingest-radar1"],
          str(last_logs()))
    p = r("logs", "field1")
    check("a peer's logs are its audit sink's, and it says so first",
          last_logs() == ["logs --tail 100 -- octo-pen-audit-sink-field1"]
          and "audit sink" in p.stderr, f"{last_logs()} {p.stderr}")
    r("logs", "uplink")
    check("a bridge's logs", last_logs() == ["logs --tail 100 -- octo-uplink-bridge"])
    p = r("logs", "radar2", "-f", "--tail", "5", "--since", "10m")
    check("a stopped container's logs come from its unit's journal, and it says so",
          last_logs() == ["journalctl -u octo-udp-ingest-radar2.service -n 5 --no-pager -o cat "
                          "-f --since=-10m"] and "not running" in p.stderr,
          f"{last_logs()} {p.stderr}")
    r("logs", "radar2", "--since", "2026-01-31T10:00:00Z")
    check("an RFC 3339 --since is translated for journalctl",
          last_logs()[0].endswith("--since=2026-01-31 10:00:00 UTC"), str(last_logs()))
    before = len(calls())
    for args, code in ((["--", "-f"], 1), (["--", "--help"], 1), (["-f"], 2),
                       (["radar1", "--since=--all"], 2), (["nope"], 1)):
        p = r("logs", *args)
        new = [c for c in calls()[before:] if c.startswith(("logs", "journalctl"))]
        check(f"logs {' '.join(args)} is refused, and podman never sees it",
              p.returncode == code and not new, f"rc={p.returncode} {new} {p.stderr}")
        before = len(calls())

    print("\ninspect: through bin/octo-pod")

    def cli(*args):
        return subprocess.run(["bash", str(repo / "bin" / "octo-pod"), *args], env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    p = cli("get", "arms", "-o", "json")
    try:
        got = [i["id"] for i in json.loads(p.stdout)["items"]]
    except (ValueError, KeyError):
        got = None
    check("octo-pod get reaches inspect with its verb", got == ["radar1", "radar2", "c2-tcp"],
          p.stdout[-300:] + p.stderr)
    check("octo-pod describe reaches inspect", "Kind:   arm" in cli("describe", "radar1").stdout)
    cli("logs", "radar1", "--tail", "5")
    check("octo-pod logs reaches podman with the same argv",
          last_logs() == ["logs --tail 5 -- octo-udp-ingest-radar1"], str(last_logs()))
    check("octo-pod certs reaches inspect", "pen/field1-cert.pem" in cli("certs").stdout)

    print("\ninspect: certs")
    p = r("certs")
    lines = {}
    for ln in p.stdout.splitlines()[1:]:
        w = ln.split()
        if w and "/" in w[0]:
            lines[w[0] + (w[1] if len(w) > 1 and w[1].startswith("[") else "")] = ln

    def row(key):
        return lines.get(key, "")

    check("certs exits 0 without --check, whatever it finds", p.returncode == 0, p.stderr)
    check("a CA roots file with two certificates is two rows",
          row("pen/ca-roots.pem[1/2]") and row("pen/ca-roots.pem[2/2]"), p.stdout)
    check("a valid leaf is ok, owned by its arm",
          " arm/radar1 " in row("pen/radar1-arm-cert.pem")
          and row("pen/radar1-arm-cert.pem").endswith("ok"), row("pen/radar1-arm-cert.pem"))
    soon = row("pen/c2-tcp-egress-cert.pem")
    check("a leaf ending in five days needs renewing soon",
          "egress/c2-tcp" in soon and soon.endswith("renew soon") and " 5d " in soon, soon)
    gone = row("pen/field1-cert.pem")
    check("an expired leaf is EXPIRED, owned by its peer",
          "peer/field1" in gone and gone.endswith("EXPIRED") and " -10d " in gone, gone)
    check("the copy apply placed for the destination is listed with its owner",
          " egress/c2-tcp " in row("egress/c2-tcp/egress-cert.pem"), p.stdout)
    check("the downlink bridge's copy is listed", " bridge/downlink " in
          row("downlink/downlink-cert.pem") and " ca " in row("downlink/ca-root.pem"), p.stdout)
    check("the slot certificate and the fabric's root are listed",
          " slot " in row("slot/aabbcc-cert.pem") and row("slot/aabbcc-cert.pem")
          .endswith("renew soon") and " fabric-ca " in row("slot/fabric-ca-root.pem"), p.stdout)
    check("the router's own leaf belongs to the pen router",
          " pen-router " in row("pen/pod-cert.pem"), p.stdout)
    check("a removed destination's keyless leaf is retired, not due",
          row("pen/gone-egress-cert.pem").endswith("retired"), p.stdout)
    check("a private key is never listed", "key.pem" not in p.stdout, p.stdout)
    check("the table and the timers fit an 80-column console",
          max(len(ln) for ln in p.stdout.splitlines()) <= 80, p.stdout)
    check("the renewal timers and when they next run",
          "pen-cert-renew.timer: active, next Fri 2026-10-16 00:47:12 UTC" in p.stdout
          and "pen-cert-expiry.timer: not installed" in p.stdout, p.stdout)
    p = r("certs", "--check")
    check("--check exits 1 when one is due, with what to run for each kind",
          p.returncode == 1 and "pen-cert-renew.service" in p.stderr
          and "minted off-box" in p.stderr, p.stderr)
    p = r("certs", "--days", "3")
    check("--days moves the line", next((ln for ln in p.stdout.splitlines()
                                         if "c2-tcp-egress" in ln), "").endswith("ok"), p.stdout)

    def pen_with(name, *files, copies=""):
        pen = root / name
        (pen / "zenoh" / "tls").mkdir(parents=True)
        for f in files:
            shutil.copy(tls / f, pen / "zenoh" / "tls")
        return dict(env, OCTO_PEN_STATE=str(pen), OCTO_POD_CERT_COPIES=copies), pen

    ok_env, _ = pen_with("pen-ok", "radar1-arm-cert.pem", "gone-egress-cert.pem")
    p = r("certs", "--check", env=ok_env)
    check("--check exits 0 when nothing is due; a retired leaf is not due", p.returncode == 0,
          p.stdout + p.stderr)
    p = r("certs", "--check", env=dict(ok_env, OCTO_POD_CERT_COPIES=str(slot)))
    check("--check fails on the slot certificate, which only its owner can renew",
          p.returncode == 1 and "fabric operator" in p.stderr, p.stderr)
    empty_env, empty_pen = pen_with("pen-empty", "radar1-arm-cert.pem")
    (empty_pen / "zenoh" / "tls" / "pod-cert.pem").write_text("")
    p = r("certs", "--check", env=empty_env)
    check("an empty certificate file is an unreadable row, and --check fails",
          p.returncode == 1 and row_status(p.stdout, "pen/pod-cert.pem") == "unreadable",
          p.stdout + p.stderr)
    p = r("certs", "--check", env=pen_with("pen-bare")[0])
    check("--check fails when it read no certificate at all", p.returncode == 1
          and "no certificate was read" in p.stderr, p.stdout + p.stderr)
    p = r("certs", "--check", env=dict(env, OCTO_PEN_STATE=str(root / "no-such-pen"),
                                       OCTO_POD_CERT_COPIES=""))
    check("--check fails when the pen was never built", p.returncode == 1
          and "missing" in p.stdout, p.stdout + p.stderr)
    if not ROOT:
        closed_env, closed = pen_with("pen-closed", "field1-cert.pem",
                                      copies=str(root / "copies" / "octo-egress-*" / "tls"))
        os.chmod(closed, 0o000)
        try:
            p = r("certs", "--check", env=closed_env)
        finally:
            os.chmod(closed, 0o700)   # back to the owner, so the temp dir can go
        check("--check fails closed when it cannot read the pen's directory",
              p.returncode == 1 and "cannot read" in p.stderr and "sudo" in p.stderr,
              p.stdout + p.stderr)
    renewal_renews_an_expired_leaf(repo, check, root, work, tls)

    print("\ninspect: the pod's infrastructure when it is down")
    (fake / "ps.txt").write_text((fake / "ps.txt").read_text().replace(
        "octo-pen-zenoh-router\tUp 3 hours (healthy)\n", ""))
    p = r("describe", "pen-zenoh-router")
    check("a stopped router still resolves, with its problem and its journal",
          p.returncode == 0 and "State:   not running" in p.stdout
          and "pen router is not running" in p.stdout
          and "sudo journalctl -u octo-pen-zenoh-router" in p.stdout, p.stdout + p.stderr)
    r("logs", "octo-pen-zenoh-router", "--tail", "5")
    check("its logs come from the journal",
          last_logs() == ["journalctl -u octo-pen-zenoh-router.service -n 5 --no-pager -o cat"],
          str(last_logs()))

    print("\ninspect: read-only, and in step with the playbook")
    seen = calls()
    podman = {c.split()[0] for c in seen if not c.startswith(("netbird", "systemctl",
                                                               "journalctl"))}
    check("podman is only asked for ps, stats, logs and inspect",
          podman <= {"ps", "stats", "logs", "inspect"}, str(podman))
    check("netbird is only asked for status",
          {c.split()[1] for c in seen if c.startswith("netbird")} == {"status"})
    check("systemctl is only asked to show or list timers",
          {c.split()[1] for c in seen if c.startswith("systemctl")}
          <= {"show", "list-timers"}, str([c for c in seen if c.startswith("systemctl")]))
    writes = ("--vacuum", "--rotate", "--flush", "--sync", "--relinquish", "--setup-keys",
              "--update-catalog")
    journal = [c for c in seen if c.startswith("journalctl")]
    check("journalctl only reads one unit", journal and all(
        " -u " in c and not any(w in c for w in writes) for c in journal), str(journal))
    in_step_with_the_playbook(repo, check, env)

    for shell in ('bash', 'zsh'):
        executable=shutil.which(shell)
        if not executable:
            continue
        def complete(*words):
            init='autoload -Uz compinit bashcompinit; compinit -D; bashcompinit\n' if shell=='zsh' else ''
            script=(init + 'source ' + shlex.quote(str(repo/'completions/octo-pod.bash')) + '\n'
                    + ('emulate -L sh; setopt kshglob noshglob braceexpand\n' if shell=='zsh' else '')
                    + 'COMP_WORDS=(octo-pod ' + ' '.join(map(shlex.quote,words)) + '); '
                    + f'COMP_CWORD={len(words)}; _octo_pod; printf "%s\\n" "${{COMPREPLY[@]}}"')
            result=subprocess.run([executable,'-c',script],env=env,capture_output=True,text=True,timeout=10)
            check(shell+' completion has no errors',result.returncode==0 and not result.stderr,result.stderr)
            # bashcompinit's compgen supplies candidates; ZLE's compadd filters the
            # prefix later. Emulate that final matching step outside a live ZLE widget.
            return [word for word in result.stdout.split() if word.startswith(words[-1])]
        check(shell+' completion offers kinds and output formats','certs' in complete('get','c')
              and complete('get','-o','') == ['table','wide','json'])
        check(shell+' completed get kind offers only flags', set(complete('get','flows','')) == {'-o','--output'})
        check(shell+' typed completion filters names', complete('describe','bridges','') == ['uplink','downlink'])
        check(shell+' typed arm completion filters names', set(complete('describe','arms','')) == {'radar1','radar2','c2-tcp'})
        check(shell+' completed describe offers no extra names', complete('describe','bridges','uplink','') == [])
        check(shell+' completed logs offers flags only', set(complete('logs','bridges','uplink','')) == {'--follow','-f','--tail','--since'})
        check(shell+' edit completes types and names', complete('edit','') == ['arms'] and set(complete('edit','arms','')) == {'radar1','radar2'})
        check(shell+' edit completes endpoint flags', set(complete('edit','arms','radar1','')) == {'--from','--bind','--udp','--tcp','--dial','--dry-run','--acknowledge-trust-change'})
        check(shell+' ingress completion filters names', set(complete('describe','ingress','')) == {'radar1','radar2'})
        check(shell+' arms log completion includes egress', 'c2-tcp' in complete('logs','arms','c'))
        ids=complete('describe','r'); logs=complete('logs','c')
        check(shell+' completion offers declared ids',set(ids)=={'radar1','radar2'}
              and 'c2-tcp' in logs,repr((ids,logs)))
        check(shell+' peer completion offers subcommands',
              set(complete('peer','')) == {'allow-from','allow-read','revoke-read','bundle','check'})
        for verb, flags in (('allow-from', {'--from','--clear'}),
                            ('allow-read', {'--key','--slot','--router-ip','--ca-fingerprint'}),
                            ('revoke-read', {'--key'}), ('bundle', {'--out'})):
            check(shell+' peer '+verb+' completes peer ids and flags',
                  set(complete('peer',verb,'')) == {'field1'} | flags)


def row_status(out, key):
    ln = next((ln for ln in out.splitlines() if ln.startswith(key + " ")), "")
    return ln.split()[-1] if ln else None


def renewal_renews_an_expired_leaf(repo, check, root, work, tls):
    """--check tells the operator to start pen-cert-renew. That has to renew an expired
    leaf too: a spare pod switched on after two years has nothing but expired leaves."""
    try:
        import jinja2
    except ImportError:
        check("jinja2 is installed (platform/tests/requirements.txt)", False)
        return
    pen = root / "renew"
    (pen / "ca").mkdir(parents=True)
    (pen / "zenoh" / "tls").mkdir(parents=True)
    shutil.copy(work / "ca.key", pen / "ca" / "ca-key.pem")
    shutil.copy(work / "ca.pem", pen / "zenoh" / "tls" / "ca-roots.pem")
    cert = pen / "zenoh" / "tls" / "field1-cert.pem"
    shutil.copy(tls / "field1-cert.pem", cert)
    shutil.copy(work / "field1.key", pen / "zenoh" / "tls" / "field1-key.pem")
    # Rendered the way ansible and CI render it (trim_blocks), into a scratch pen.
    templates = jinja2.Environment(loader=jinja2.FileSystemLoader(str(repo / "platform" / "templates")),
                                   trim_blocks=True, keep_trailing_newline=True)
    (pen / "renew.sh").write_text(templates.get_template("pen/cert-renew.sh.j2").render(
        pen_state_dir=str(pen), sub_leaf_dir=str(root / "none"), arm_tls_dir=str(root / "none")))
    stubs = root / "renew-bin"   # it restarts the router after a renewal; these do nothing
    stubs.mkdir()
    for tool in ("systemctl", "podman"):
        (stubs / tool).write_text("#!/bin/sh\nexit 0\n")
        (stubs / tool).chmod(0o755)
    p = subprocess.run(["bash", str(pen / "renew.sh")], capture_output=True, text=True,
                       env=dict(os.environ, PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}"),
                       timeout=60, check=False)
    valid = subprocess.run(["openssl", "x509", "-checkend", "0", "-noout", "-in", str(cert)],
                           capture_output=True, check=False).returncode == 0
    check("pen-cert-renew renews an expired leaf the pen signed", p.returncode == 0 and valid,
          p.stdout + p.stderr)


def in_step_with_the_playbook(repo, check, env):
    """The places certs reads and the infrastructure describe knows are lists the
    playbook also holds. These fail when one side changes and the other does not."""
    import yaml
    play = (repo / "platform" / "configure-octo-pod.yml").read_text()
    gv = yaml.safe_load((repo / "platform" / "group_vars" / "all" /
                         "10-infrastructure.yml").read_text())
    wanted = set()
    for task in play.split("\n\n"):
        if ".pem" not in task:
            continue
        # The pen's own dir is always read; its bundles/ are copies handed to partners.
        wanted |= {gv[v] for v in re.findall(r"(?:dest:|-out)\s*\"?\{\{ (\w+_dir) \}\}/", task)
                   if v != "pen_state_dir"}
        wanted |= {f"/etc/octo-{k}-*/tls" for k in re.findall(r"/etc/octo-(\w+)-\{\{", task)}
    infra = re.search(r"\n(\s+)_infra:\n((?:\1  - .+\n)+)", play)
    with loaded(env) as mod:
        have = set(mod.DEFAULT_COPIES.split(":"))
        check("certs reads every directory the playbook writes a certificate to",
              len(wanted) >= 6 and wanted <= have, f"missing {sorted(wanted - have)}")
        check("describe's pod infrastructure is the playbook's _infra list", infra and
              set(re.findall(r'"([^"]+)"', infra.group(2))) == mod.STATUS.INFRA,
              str(infra and infra.group(2)))
        # A collector that throws is a problem row, not a traceback.
        mod.TOP.Collector.poll_once = lambda self: 1 / 0
        try:
            items = mod.items_of("problems")[0]
        except ZeroDivisionError as e:
            items = [{"text": f"raised {e!r}"}]
        check("a collector that fails becomes a problem, not a crash",
              items[:1] and items[0]["text"].startswith("could not read the pod"), str(items))


def broken_pods(repo, check, base):
    """Pods with one thing wrong each: what a container may write, a missing bridge, no
    root, a hand-edited certificate name, a long mesh name."""
    def pod(name):
        env, fake = build_pod(base / name)
        add_fakes(env, fake)
        env["OCTO_POD_CERT_COPIES"] = ""
        return env, fake

    print("\ninspect: what a container writes is shown, never obeyed")
    env, fake = pod("escapes")
    log = fake / "logs" / "octo-udp-ingest-radar1"
    log.write_text(log.read_text() + f"{stamp(time.time())}  WARN arm: dropped "
                   "\x1b]52;c;ZWNobyBwd25lZA==\x07\x1b[?1049h\x1bPq\x1b\\\x1bc\u009b\n")
    blob = json.dumps({"\x1b]0;x\x07k": 1, "sent": 3})
    now = time.time()
    (fake / "logs" / "octo-egress-ghost").write_text("".join(
        f"{stamp(t)}  INFO egress_arm: egress stats dest=udp://192.0.2.9:1\x1b]52;c;QQ==\x07 "
        f"stats={blob}\n" for t in (now - 60, now)))
    with open(fake / "ps.txt", "a") as f:
        f.write("octo-egress-ghost\tUp 1 hour\n")
    for args in (["describe", "radar1"], ["describe", "egress-ghost"],
                 ["get", "flows", "-o", "wide"]):
        p = run(*args, env=env, binary=True)
        check(f"{' '.join(args)}: no escape or C1 control reaches the terminal",
              p.returncode == 0 and b"\x1b" not in p.stdout and b"\xc2\x9b" not in p.stdout
              and b"\\x1b]" in p.stdout, p.stdout[-400:])
    p = subprocess.run([sys.executable, str(repo / "bin" / "octo-pod-top"), "--once"], env=env,
                       capture_output=True, check=False, timeout=60)
    check("top --once: the same", b"\x1b" not in p.stdout and b"\\x1b]" in p.stdout,
          p.stdout[-400:])

    print("\ninspect: bytes that are not UTF-8")
    env, fake = pod("utf8")
    podman = pathlib.Path(env["PATH"].split(os.pathsep)[0]) / "podman"
    podman.write_text(podman.read_text().replace(
        'sys.stdout.write(p.read_text() if p.exists() else "")',
        'sys.stdout.flush(); sys.stdout.buffer.write(p.read_bytes() if p.exists() else b"")'))
    (fake / "logs" / "octo-udp-ingest-oldarm").write_bytes(
        b"2026-10-15T10:00:00Z  WARN arm: bad frame \xff\n")
    for args, want in ((["get", "arms", "-o", "json"], '"drops": 3'),
                       (["describe", "radar1"], "In:          10/s"),
                       (["get", "problems", "-o", "json"], '"orphan octo-udp-ingest-oldarm')):
        p = run(*args, env=env)
        check(f"{' '.join(args)} reads a log line that is not UTF-8 and carries on",
              p.returncode == 0 and want in p.stdout and "could not read the pod" not in p.stdout,
              p.stdout[-300:] + p.stderr[-300:])
    p = subprocess.run([sys.executable, str(repo / "bin" / "octo-pod-top"), "--json"], env=env,
                       capture_output=True, text=True, check=False, timeout=60)
    check("top --json survives it too", p.returncode == 0, p.stderr[-300:])

    print("\ninspect: a stopped uplink beside a running downlink")
    env, fake = pod("uplink")
    (fake / "ps.txt").write_text((fake / "ps.txt").read_text().replace(
        "octo-uplink-bridge\tUp 3 hours\n", ""))
    p = run("get", "bridges", env=env)
    check("get bridges shows the uplink NOT RUNNING instead of dropping it",
          any(ln.split()[:3] == ["uplink", "NOT", "RUNNING"] for ln in p.stdout.splitlines()),
          p.stdout)
    check("get problems names it", "uplink bridge is not running" in run(
        "get", "problems", env=env).stdout)
    out = run("describe", "uplink", env=env).stdout
    check("describe uplink lists it under Problems",
          "uplink bridge is not running" in out.split("Problems:")[1], out)

    if not ROOT:
        print("\ninspect: without root")
        env, fake = pod("nonroot")
        (fake / "ps.txt").write_text("")   # what podman shows a user for root's containers
        p = run("describe", "radar1", env=env)
        check("describe says first that podman hid the containers, and to rerun with sudo",
              "!! not root" in p.stdout and "sudo octo-pod describe radar1" in p.stderr,
              p.stdout + p.stderr)

    print("\ninspect: a hand-edited cert_cn")
    env, fake = pod("certcn")
    tls = pathlib.Path(env["OCTO_PEN_STATE"]) / "zenoh" / "tls"
    make_certs(base / "certcn-ca", tls)
    (tls / "radar1-arm-cert.pem").unlink()
    (base / "certcn" / "outside").mkdir()   # pen/zenoh/tls/../../../outside
    shutil.copy(tls / "field1-cert.pem", base / "certcn" / "outside" / "planted-cert.pem")
    hv = pathlib.Path(env["OCTO_POD_ETC"]) / "host_vars" / "pod-test"
    (hv / "30-arms.yml").write_text("arms:\n  - id: radar1\n    kind: udp-ingest\n"
                                    "    cert_cn: ../../../outside/planted\n")
    check("a cert_cn that climbs out of the TLS dir is not read",
          "radar1: certificate expired" not in run("get", "problems", env=env).stdout)

    print("\ninspect: a long mesh name")
    env, fake = pod("mesh")
    nb = json.loads((fake / "netbird.json").read_text())
    nb["peers"]["details"][0]["fqdn"] = "a-very-long-mesh-peer-name-for-field-laptop-one.example"
    (fake / "netbird.json").write_text(json.dumps(nb))
    p = run("get", "mesh", env=env)
    check("get mesh caps a name, so the table fits 80 columns",
          max(len(ln) for ln in p.stdout.splitlines()) <= 80, p.stdout)
    check("-o wide keeps the full name", "field-laptop-one" in run(
        "get", "mesh", "-o", "wide", env=env).stdout)


def main():
    failures = []

    def check(label, condition, detail=""):
        if condition:
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))
            failures.append(label)

    run_checks(REPO, check)
    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): " + ", ".join(failures))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
