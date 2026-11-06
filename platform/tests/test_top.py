#!/usr/bin/env python3
"""
bin/octo-pod-top against a scratch pod: fake podman, fake netbird, a real declaration.

The stats lines in the fixture keep the exact shapes the arms, the goat bridges and the
egress arm print, ANSI colour included, with synthetic names and addresses. The
interactive screen runs under a pseudo-terminal and is driven through every view.

    python3 platform/tests/test_top.py            standalone
    test_release.py runs run_checks(), so CI does too.
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import select
import shutil
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin"))
TOP = REPO / "bin" / "octo-pod-top"


def load_top():
    loader = importlib.machinery.SourceFileLoader("octo_pod_top", str(TOP))
    spec = importlib.util.spec_from_loader("octo_pod_top", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".123456789Z"


def arm_line(t, received, forwarded, pending=0, drops=0, sent=0, skipped=0):
    k = "\x1b[3m{}\x1b[0m\x1b[2m=\x1b[0m{}"
    fields = " ".join(k.format(n, v) for n, v in (
        ("received", received), ("forwarded", forwarded), ("forward_errors", 0),
        ("sent", sent), ("send_errors", 0), ("dropped_source", 0),
        ("dropped_overflow", drops), ("dropped_stale", 0), ("dropped_egress", 0),
        ("dropped_invalid", 0), ("skipped", skipped), ("pen_outages", 0),
        ("pending", pending), ("outbox_capacity", 512)))
    return (f"\x1b[2m{stamp(t)}\x1b[0m \x1b[32m INFO\x1b[0m "
            f"\x1b[2mocto_arm_common::stats\x1b[0m\x1b[2m:\x1b[0m stats {fields}")


def bridge_line(t, leg, forwarded):
    return (f"\x1b[2m{stamp(t)}\x1b[0m \x1b[32m INFO\x1b[0m \x1b[2muplink_bridge\x1b[0m"
            f"\x1b[2m:\x1b[0m {leg} stats \x1b[3mforwarded\x1b[0m\x1b[2m=\x1b[0m{forwarded} "
            f"\x1b[3mforward_errors\x1b[0m\x1b[2m=\x1b[0m0")


def egress_line(t, dest, sent, consumers):
    blob = json.dumps({"audit_errors": 0, "consumers_connected": consumers, "dropped_key": 0,
                       "dropped_overflow": 0, "pending": 0, "received": sent, "send_errors": 0,
                       "sent": sent})
    return (f"\x1b[2m{stamp(t)}\x1b[0m \x1b[32m INFO\x1b[0m \x1b[2megress_arm\x1b[0m"
            f"\x1b[2m:\x1b[0m egress stats \x1b[3mdest\x1b[0m\x1b[2m=\x1b[0m{dest} "
            f"\x1b[3mstats\x1b[0m\x1b[2m=\x1b[0m{blob}")


NETBIRD = {
    "netbirdIp": "100.127.0.8/16",
    "management": {"url": "https://mgmt.example:443", "connected": True},
    "signal": {"url": "https://signal.example:443", "connected": True},
    "peers": {"total": 2, "connected": 1, "details": [
        {"fqdn": "consumer-a.mesh.example", "netbirdIp": "100.127.0.16", "status": "Connected",
         "connectionType": "P2P", "latency": 4200000,
         "lastWireguardHandshake": "", "transferReceived": 2048, "transferSent": 4096},
        {"fqdn": "laptop-b.mesh.example", "netbirdIp": "100.127.0.17", "status": "Connecting",
         "connectionType": "-", "latency": 0,
         "lastWireguardHandshake": "0001-01-01T00:00:00Z", "transferReceived": 0,
         "transferSent": 0}]},
}

FAKE_PODMAN = r'''#!/usr/bin/env python3
import os, pathlib, sys, time
fx = pathlib.Path(os.environ["FAKE_DIR"])
with open(fx / "calls.log", "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
a = sys.argv[1:]
if a[:1] == ["ps"]:
    time.sleep(float(os.environ.get("FAKE_PS_DELAY", "0")))
    sys.stdout.write((fx / "ps.txt").read_text())
elif a[:1] == ["stats"]:
    sys.stdout.write("octo-udp-ingest-radar1\t1.25%\t12.3MB / 8.2GB\n")
elif a[:1] == ["logs"]:
    p = fx / "logs" / a[-1]
    sys.stdout.write(p.read_text() if p.exists() else "")
else:
    sys.exit(125)
'''

FAKE_NETBIRD = r'''#!/usr/bin/env python3
import os, pathlib, sys
fx = pathlib.Path(os.environ["FAKE_DIR"])
with open(fx / "calls.log", "a") as f:
    f.write("netbird " + " ".join(sys.argv[1:]) + "\n")
sys.stdout.write((fx / "netbird.json").read_text())
'''


def build_pod(root):
    """A pod with two arms (one missing), a peer, an egress destination, both bridges
    and one orphan. Returns the environment to run octo-pod-top with."""
    root = pathlib.Path(root)
    etc = root / "etc"
    hv = etc / "host_vars" / "pod-test"
    hv.mkdir(parents=True)
    (etc / "inventory.ini").write_text("pod-test ansible_connection=local\n")
    (hv / "05-fabric.yml").write_text("fabric_endpoint: tls/fabric.example:7447\n")
    (hv / "10-pod.yml").write_text("fabric_slot_anchor: aabbcc\nbridge_allow: raw/octo/**\n")
    (hv / "30-arms.yml").write_text(
        "arms:\n"
        "  - id: radar1\n    kind: udp-ingest\n    port: 8600\n    namespace: raw/octo/radar1\n"
        "    allow_sources: 192.0.2.10\n"
        "  - id: radar2\n    kind: udp-ingest\n    port: 8601\n    namespace: raw/octo/radar2\n")
    (hv / "20-peers.yml").write_text("peers:\n  - id: field1\n    namespace: raw/octo/field1\n")
    (hv / "40-egress.yml").write_text(
        "egress:\n  - id: c2-tcp\n    dest: tcp-server://192.0.2.1:9000\n"
        "    keys: [aabbcc/sensors/**]\n    release:\n      from_fabric: true\n")

    fake = root / "fake"
    (fake / "logs").mkdir(parents=True)
    (fake / "ps.txt").write_text("\n".join([
        "octo-pen-zenoh-router\tUp 3 hours (healthy)",
        "octo-udp-ingest-radar1\tUp 3 hours",
        "octo-uplink-bridge\tUp 3 hours",
        "octo-downlink-bridge\tUp 2 hours",
        "octo-egress-c2-tcp\tUp 2 hours",
        "octo-udp-ingest-oldarm\tUp 9 days",
    ]) + "\n")
    now = time.time()
    t = [now - 125, now - 65, now - 5]
    logs = fake / "logs"
    (logs / "octo-udp-ingest-radar1").write_text("\n".join(
        ["\x1b[2mnot a stats line\x1b[0m"]
        + [arm_line(t[0], 100, 100), arm_line(t[1], 400, 400),
           arm_line(t[2], 1000, 990, pending=10, drops=3)]) + "\n")
    (logs / "octo-uplink-bridge").write_text("\n".join(
        [bridge_line(t[1], "uplink", 400), bridge_line(t[2], "uplink", 990)]) + "\n")
    (logs / "octo-downlink-bridge").write_text("\n".join(
        [bridge_line(t[1], "downlink", 10), bridge_line(t[2], "downlink", 70)]) + "\n")
    (logs / "octo-egress-c2-tcp").write_text("\n".join(
        [egress_line(t[1], "tcp-server://192.0.2.1:9000", 10, 1),
         egress_line(t[2], "tcp-server://192.0.2.1:9000", 70, 0)]) + "\n")
    (fake / "netbird.json").write_text(json.dumps(NETBIRD))

    fakebin = root / "bin"
    fakebin.mkdir()
    for name, body in (("podman", FAKE_PODMAN), ("netbird", FAKE_NETBIRD)):
        (fakebin / name).write_text(body)
        (fakebin / name).chmod(0o755)
    env = dict(os.environ, OCTO_POD_ETC=str(etc), FAKE_DIR=str(fake),
               OCTO_PEN_STATE=str(root / "pen"),
               PATH=f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}")
    return env, fake


def run_pty(env, keys, rows=40, cols=140, timeout=25):
    """Drive the screen under a pseudo-terminal. Returns (rc, everything it drew)."""
    import fcntl
    import pty
    import struct
    import termios
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    env = dict(env, TERM="xterm-256color", LINES=str(rows), COLUMNS=str(cols))
    p = subprocess.Popen([sys.executable, str(TOP)], stdin=slave, stdout=slave, stderr=slave,
                         env=env, close_fds=True, start_new_session=True)
    os.close(slave)
    out = bytearray()

    def drain(seconds):
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([master], [], [], 0.05)
            if r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    return
                if not chunk:
                    return
                out.extend(chunk)

    # The collector polls asynchronously. Sending 'l' before the first row exists
    # silently selects nothing; a fixed sleep races slow/contended CI machines.
    ready_by = time.monotonic() + timeout
    while b"radar1" not in out and p.poll() is None and time.monotonic() < ready_by:
        drain(0.1)
    if b"radar1" not in out:
        p.kill()
        p.wait()
        os.close(master)
        return None, out.decode("utf-8", "replace") + "\nfirst populated frame did not arrive"
    for k in keys:
        os.write(master, k.encode())
        drain(0.4)
    try:
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        rc = None
    drain(0.3)
    os.close(master)
    return rc, out.decode("utf-8", "replace")


def run_checks(repo, check):
    top = load_top()
    print("\ntop: reading the three stats line shapes")
    now = time.time()
    a = top.parse_stats(arm_line(now, 1000, 990, pending=10, drops=3, sent=7, skipped=4))
    check("an arm's stats line parses, colour and all",
          a is not None and a[1]["received"] == 1000 and a[1]["forwarded"] == 990
          and a[1]["pending"] == 10 and a[1]["dropped_overflow"] == 3 and abs(a[0] - now) < 1,
          str(a))
    b = top.parse_stats(bridge_line(now, "downlink", 70))
    check("a bridge's stats line parses", b is not None and b[1] == {"forwarded": 70,
                                                                       "forward_errors": 0},
          str(b))
    e = top.parse_stats(egress_line(now, "udp://192.0.2.1:8610", 5, 2))
    check("an egress stats line parses its JSON counters and keeps the destination",
          e is not None and e[1]["sent"] == 5 and e[1]["consumers_connected"] == 2
          and e[1]["_dest"] == "udp://192.0.2.1:8610", str(e))
    def parsed(line):
        try:
            return top.parse_stats(line)
        except Exception as e:   # noqa: BLE001  a raise is the failure being tested for
            return f"raised {e!r}"
    ignored = [parsed(s) for s in (
        "2026-10-15T10:00:00Z  INFO arm: listening on 0.0.0.0:8600",
        "2026-10-15T10:00:00Z  INFO arm: stats will follow",
        "egress stats dest=x stats={broken",
        "no timestamp stats received=1",
        "2026-10-15T10:00:00Z  WARN x: key=a/b stats received=²",   # isdigit, not int
        "2026-13-45T10:00:00Z  INFO arm: stats received=1")]             # a date's shape only
    check("lines that are not stats lines are ignored, and none of them raises",
          ignored == [None] * 6, str(ignored))

    print("\ntop: rates from cumulative counters")
    s = top.Series()
    s.add(now - 60, {"sent": 19151, "skipped": 19135})
    s.add(now, {"sent": 19163, "skipped": 19147})
    s.add(now - 30, {"sent": 1})   # out of order: ignored
    check("rate is the difference over the period", abs(s.rate("sent") - 0.2) < 1e-9,
          str(s.rate("sent")))
    s.add(now + 60, {"sent": 4})
    check("a counter that goes down is a restart: no rate, not a negative one",
          s.rate("sent") is None and s.delta(("sent",)) is None)
    check("the trend keeps the gap", len(s.trend("sent")) == 2 and s.trend("sent")[1] is None,
          str(s.trend("sent")))
    check("a missing counter is unknown, not zero", s.rate("received") is None)

    states = [top.short_state(x) for x in ("Up 27 hours (healthy)", "Up About a minute",
                                           "Up Less than a second", "Exited (1) 3 minutes ago",
                                           "up 2 days", "NOT RUNNING")]
    check("podman states are shortened, unknown ones kept",
          states == ["up 27h", "up 1m", "up 0s", "exited(1)", "up 2d", "NOT RUNNING"],
          str(states))
    check("rates read the way an operator says them",
          [top.fmt_rate(x) for x in (None, 0, 0.05, 0.2, 12.4, 2500)]
          == ["-", "0", "0.05", "0.2", "12", "2.5k"])
    sp = top.spark([0, 1, 2, None, 4], 5)
    check("the sparkline is relative, newest right, a gap is a space",
          len(sp) == 5 and sp[3] == " " and sp[4] == "█", repr(sp))
    mesh = top.parse_mesh(json.dumps(NETBIRD))
    check("netbird status --json gives management, signal and peers, connected first",
          mesh["management"] and mesh["connected"] == 1 and mesh["total"] == 2
          and mesh["peers"][0]["name"] == "consumer-a"
          and mesh["peers"][1]["handshake"] is None and top.parse_mesh("nope") is None,
          str(mesh))

    with tempfile.TemporaryDirectory() as t:
        env, fake = build_pod(t)
        print("\ntop: --json on a scratch pod")
        r = subprocess.run([sys.executable, str(TOP), "--json"], env=env, capture_output=True, check=False,
                           text=True, timeout=60)
        try:
            doc = json.loads(r.stdout)
        except ValueError:
            doc = {}
        check("--json exits 0 with a document", r.returncode == 0 and doc.get("pod") == "pod-test",
              r.stdout[-500:] + r.stderr[-500:])
        flows = {f["id"]: f for f in doc.get("flows", [])}
        r1 = flows.get("radar1", {})
        check("an arm's rates come from its last two stats lines",
              abs((r1.get("in") or 0) - 10.0) < 0.2 and abs((r1.get("out") or 0) - 9.83) < 0.2
              and r1.get("drops") == 3 and r1.get("pending") == 10, str(r1))
        check("a declared arm with no container is in the table as not running",
              flows.get("radar2", {}).get("state") == "NOT RUNNING", str(flows.get("radar2")))
        up, down = flows.get("uplink", {}), flows.get("downlink", {})
        check("both bridges are rows with their forwarded rate",
              up.get("stage") == "up" and abs((up.get("out") or 0) - 9.83) < 0.2
              and down.get("stage") == "down" and abs((down.get("out") or 0) - 1.0) < 0.1,
              f"{up}\n{down}")
        c2 = flows.get("c2-tcp", {})
        check("an egress destination shows sent/s and its consumers",
              c2.get("stage") == "out" and abs((c2.get("out") or 0) - 1.0) < 0.1
              and c2.get("consumers") == 0, str(c2))
        check("the flows run in, up, down, out",
              [f["stage"] for f in doc.get("flows", [])]
              == sorted((f["stage"] for f in doc.get("flows", [])),
                        key=lambda s: {"in": 0, "up": 1, "down": 2, "out": 3}.get(s, 4)))
        texts = [p["text"] for p in doc.get("problems", [])]
        want = ["radar2: declared, not running", "orphan octo-udp-ingest-oldarm",
                "radar1: 3 dropped", "c2-tcp: no consumer connected"]
        missing = [w for w in want if not any(w in x for x in texts)]
        check("problems name the missing arm, the orphan, the drops and the idle server",
              not missing, f"missing {missing} in {texts}")
        check("bad problems come before warnings",
              [p["severity"] for p in doc.get("problems", [])]
              == sorted((p["severity"] for p in doc.get("problems", [])),
                        key=lambda s: s != "bad"))
        check("podman stats gives the container's CPU and memory",
              r1.get("cpu") == "1.25%" and r1.get("mem") == "12.3MB", str(r1))
        check("the mesh is in the document", (doc.get("mesh") or {}).get("ip") == "100.127.0.8/16")

        r = subprocess.run([sys.executable, str(TOP), "--once"], env=env, capture_output=True, check=False,
                           text=True, timeout=60)
        check("--once prints the pipeline and every row",
              r.returncode == 0 and "arms 9.8/s" in r.stdout and "radar2" in r.stdout
              and "uplink" in r.stdout and "c2-tcp" in r.stdout, r.stdout + r.stderr)

        print("\ntop: the screen, under a pseudo-terminal")
        keys = ["l", "f", "\x1b", "j", "\n", "\x1b", "2", "3", "j", "4", "/", "radar",
                "\n", "1", "\x1b", "?", "?", ":", "mesh", "\n", ":", "q", "\n"]
        rc, drawn = run_pty(env, keys)
        check("every view draws and :q quits cleanly",
              rc == 0 and "Traceback" not in drawn and "radar1" in drawn
              and "consumer-a" in drawn and "management" in drawn, drawn[-800:])
        rc, drawn = run_pty(env, ["2", "3", "4", "q"], rows=12, cols=50)
        check("a small terminal still draws and quits", rc == 0 and "Traceback" not in drawn,
              drawn[-500:])

        calls = (fake / "calls.log").read_text().splitlines()
        verbs = sorted({c.split()[0] if not c.startswith("netbird") else " ".join(c.split()[:2])
                        for c in calls})
        check("read-only: podman is only asked for ps and logs, netbird only for status",
              set(verbs) <= {"ps", "stats", "logs", "inspect", "netbird status"}, str(verbs))
        check("the log view tails the selected container", any(
            c.startswith("logs --tail") for c in calls), str(calls[-5:]))

    with tempfile.TemporaryDirectory() as t:
        env, fake = build_pod(t)
        env['FAKE_PS_DELAY'] = '5'
        rc, drawn = run_pty(env, ['l', 'q', 'q'])
        calls = (fake/'calls.log').read_text().splitlines()
        check('slow first poll still opens the selected log view',
              rc == 0 and any(c.startswith('logs --tail') for c in calls), drawn[-500:])

    with tempfile.TemporaryDirectory() as t:
        env, fake = build_pod(t)
        shutil.rmtree(pathlib.Path(env["OCTO_POD_ETC"]))
        (fake / "ps.txt").write_text("")
        (fake / "netbird.json").write_text("not json")
        r = subprocess.run([sys.executable, str(TOP), "--json"], env=env, capture_output=True, check=False,
                           text=True, timeout=60)
        try:
            doc = json.loads(r.stdout)
        except ValueError:
            doc = {}
        check("a box that is not a pod yet gets a view, not a traceback",
              r.returncode == 0 and any("no pod identity" in p["text"]
                                        for p in doc.get("problems", [])),
              r.stdout[-400:] + r.stderr[-400:])


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
