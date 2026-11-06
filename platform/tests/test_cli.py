#!/usr/bin/env python3
"""
The declaration CLIs end to end against a scratch identity: remove-arm, egress, declare.

Each script runs in its own interpreter with the root check answered by a one-line shim,
OCTO_POD_ETC and OCTO_PEN_STATE pointed at a temporary directory, and podman and
systemctl replaced by stubs that record what they were asked to do. Nothing here can
touch a real pod's files.

    python3 platform/tests/test_cli.py [repo]     standalone; repo defaults to this checkout
"""
import importlib.machinery
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin"))

# The scripts insist on root because /etc/octo-pod is root-owned. Answer that check in
# the child, then run the script exactly as `octo-pod` would, so its own sys.exit code is
# the code the test sees.
SHIM = ("import os, runpy, sys; os.geteuid = lambda: 0; p = sys.argv[1]; "
        "sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(p)); runpy.run_path(p, run_name='__main__')")

# podman holds its containers in a state file: `ps` lists it, `rm -f` takes one out.
PODMAN = """#!/bin/sh
echo "podman $*" >> "$OCTO_TEST_STATE/calls"
case "$1" in
  ps) cat "$OCTO_TEST_STATE/containers" 2>/dev/null ;;
  rm) awk -F '\\t' -v n="$3" '$1 != n' "$OCTO_TEST_STATE/containers" > "$OCTO_TEST_STATE/c.new"
      mv "$OCTO_TEST_STATE/c.new" "$OCTO_TEST_STATE/containers" ;;
esac
exit 0
"""
SYSTEMCTL = """#!/bin/sh
echo "systemctl $*" >> "$OCTO_TEST_STATE/calls"
exit 0
"""

POD_YML = 'pen_id: "pod-x"\nfabric_slot_anchor: ""\nbridge_allow: ""\ndownlink_allow: ""\n'
# Ids no real pod uses: remove-arm also looks under /etc for an arm's own directory.
ARM_A, ARM_B, PEER = "cli-test-arm-a", "cli-test-arm-b", "cli-test-peer"
ARMS_YML = f"""arms:
  - id: {ARM_A}
    kind: udp-ingest
    description: "{ARM_A}, udp publisher"
    cert_cn: octo-arm-{ARM_A}
    namespace: "raw/{ARM_A}/**"
    enabled: true
    feed: feed1
    transport: udp
    port: 48021
    allow_sources: "192.0.2.3"
    trust_tier: T1
    onboarded: "2026-10-01"
    release:
      to_fabric: false   # shut until you have seen their data. Opening it also needs
                       # raw/{ARM_A}/** added to bridge_allow in 10-pod.yml.
  - id: {ARM_B}
    kind: udp-ingest
    description: "{ARM_B}, udp publisher"
    cert_cn: octo-arm-{ARM_B}
    namespace: "raw/{ARM_B}/**"
    enabled: true
    feed: feed1
    transport: udp
    port: 48022
    allow_sources: "192.0.2.4"
    trust_tier: T1
    onboarded: "2026-10-01"
    release:
      to_fabric: false
"""
PEERS_YML = f"""peers:
  - id: {PEER}
    kind: zenoh-native
    description: "{PEER}, zenoh peer"
    cert_cn: octo-peer-{PEER}
    namespace: "raw/{PEER}/**"
    enabled: true
    connects: client
    trust_tier: T2
    onboarded: "2026-10-01"
    release:
      to_fabric: false
"""
KEY = "1d18de66e8a09a5e68170b3b3de6563b/sensors/radar1/asterix/data"


class Scratch:
    """One pod identity, one pen state directory and stubbed podman/systemctl."""

    def __init__(self, repo, tmp):
        self.repo = pathlib.Path(repo)
        self.root = pathlib.Path(tmp)
        self.pod = self.root / "etc" / "host_vars" / "pod-x"
        self.pod.mkdir(parents=True)
        self.pen = self.root / "pen"
        self.state = self.root / "state"
        self.state.mkdir()
        stubs = self.root / "stubs"
        stubs.mkdir()
        for name, body in (("podman", PODMAN), ("systemctl", SYSTEMCTL)):
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        self.env = dict(os.environ, OCTO_POD_ETC=str(self.root / "etc"), OCTO_PEN_STATE=str(self.pen),
                        OCTO_QUADLET_DIR=str(self.root / "quadlet"),
                        OCTO_EGRESS_ETC=str(self.root / "egress-etc"),
                        OCTO_TEST_STATE=str(self.state), PATH=f"{stubs}:{os.environ['PATH']}")
        self.write("10-pod.yml", POD_YML)
        self.write("05-fabric.yml", "fabric_endpoint: ''\nfabric_host_pins: []\n")
        self.write("20-peers.yml", "peers: []\n")
        self.write("30-arms.yml", "arms: []\n")
        self.write("40-egress.yml", "egress: []\n")

    def write(self, name, text):
        (self.pod / name).write_text(text)

    def read(self, name):
        return (self.pod / name).read_text()

    def run(self, script, *argv):
        r = subprocess.run([sys.executable, "-c", SHIM, str(self.repo / "bin" / script), *argv],
                           capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL,
                           timeout=120)
        return r.returncode, r.stdout + r.stderr


def load_egress(repo):
    path = str(pathlib.Path(repo) / "bin" / "octo-pod-egress")
    loader = importlib.machinery.SourceFileLoader("octo_pod_egress_under_test", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def check_remove(repo, check):
    print("\nremove-arm / remove-peer: end to end on a scratch identity")
    with tempfile.TemporaryDirectory() as tmp:
        s = Scratch(repo, tmp)
        s.write("30-arms.yml", ARMS_YML)
        s.write("20-peers.yml", PEERS_YML)
        tls = s.pen / "zenoh" / "tls"
        tls.mkdir(parents=True)
        key_a = tls / f"{ARM_A}-arm-key.pem"
        cert_a = tls / f"{ARM_A}-arm-cert.pem"
        key_a.write_text("synthetic key\n")
        cert_a.write_text("synthetic cert\n")
        (tls / f"{ARM_B}-arm-key.pem").write_text("synthetic key\n")
        bundle = s.pen / "bundles" / PEER
        (bundle / "tls").mkdir(parents=True)
        (bundle / "tls" / f"octo-peer-{PEER}-key.pem").write_text("synthetic key\n")
        (s.state / "containers").write_text(
            f"octo-udp-ingest-{ARM_A}\tUp 2 hours\nocto-udp-ingest-{ARM_B}\tUp 2 hours\n")

        code, out = s.run("octo-pod-remove-arm", "arm", ARM_A, "--yes")
        check("remove-arm runs to the end, no traceback", code == 0 and "Traceback" not in out, out)
        check("remove-arm prints its plan before acting",
              "this will:" in out and f"stop + delete container  octo-udp-ingest-{ARM_A}" in out
              and f"delete PRIVATE KEY       {key_a}" in out, out)
        containers = (s.state / "containers").read_text()
        check("remove-arm removes that arm's container and only that one",
              f"octo-udp-ingest-{ARM_A}\t" not in containers
              and f"octo-udp-ingest-{ARM_B}\t" in containers, containers)
        check("remove-arm deletes the pen's copy of the key and keeps the certificate",
              not key_a.exists() and cert_a.exists())
        arms = s.read("30-arms.yml")
        check("remove-arm drops the declaration and leaves its sibling alone",
              f"id: {ARM_A}" not in arms and f"id: {ARM_B}" in arms, arms)

        code, out = s.run("octo-pod-remove-arm", "peer", ARM_B, "--yes")
        check("remove-peer on an arm is refused, naming the right verb",
              code != 0 and f"remove-arm {ARM_B}" in out and "Traceback" not in out, out)

        code, out = s.run("octo-pod-remove-arm", "peer", PEER, "--yes")
        check("remove-peer runs to the end, no traceback", code == 0 and "Traceback" not in out, out)
        check("remove-peer prints its plan and says the grant is live until apply",
              "this will:" in out and "(bundle copy)" in out and "CONVERGE NOW" in out, out)
        check("remove-peer deletes the bundle that holds the peer's key", not bundle.exists())
        check("removing the last peer leaves an empty list, not a bare key",
              s.read("20-peers.yml").strip() == "peers: []", s.read("20-peers.yml"))

        code, out = s.run("octo-pod-remove-arm", "arm", "cli-test-nothing", "--yes")
        check("an id with nothing to remove is refused with a reason",
              code != 0 and "nothing to remove" in out and "Traceback" not in out, out)


def check_egress_types(repo, check):
    print("\negress: hand-edited values of the wrong type are refused, not a traceback")
    eg = load_egress(repo)
    base = {"id": "srv1", "dest": "tcp-server://192.0.2.10:9000", "allow_dest": "192.0.2.10:9000",
            "allow_sources": "192.0.2.20", "keys": [KEY]}
    check("a well-formed tcp-server:// destination has no problems", eg.problems_with(base) == [],
          str(eg.problems_with(base)))
    for field, value in (("max_clients", "lots"), ("max_clients", None), ("max_clients", True),
                         ("max_clients", 2.5), ("replay", "many"), ("replay", None),
                         ("replay", False)):
        try:
            problems = eg.problems_with(dict(base, **{field: value}))
        except Exception as exc:  # noqa: BLE001 - any exception is the failure under test
            problems = [f"raised {type(exc).__name__}: {exc}"]
        check(f"{field}: {value!r} is refused naming the field",
              any(p.startswith(field) for p in problems), str(problems))
    for value in ("open", "yes", ["from_fabric"], True):
        dest = dict(base, release=value)
        try:
            opened, problems = eg.released(dest), eg.problems_with(dest)
        except Exception as exc:  # noqa: BLE001
            opened, problems = None, [f"raised {type(exc).__name__}: {exc}"]
        check(f"release: {value!r} reads as shut and is refused naming release",
              opened is False and any(p.startswith("release") for p in problems), str(problems))
    for addr in ("192.0.2.1:²", "192.0.2.1:٣"):
        try:
            ok = eg.literal(addr)
        except Exception as exc:  # noqa: BLE001
            ok = f"raised {type(exc).__name__}"
        check(f"a non-ASCII digit port is not a literal address: {addr!a}", ok is False, str(ok))

    with tempfile.TemporaryDirectory() as tmp:
        s = Scratch(repo, tmp)
        s.write("40-egress.yml", f"""egress:
  - id: srv1
    cert_cn: octo-egress-srv1
    dest: "tcp-server://192.0.2.10:9000"
    allow_dest: "192.0.2.10:9000"
    allow_sources: "192.0.2.20"
    max_clients: lots
    keys:
      - "{KEY}"
    release: open
""")
        code, out = s.run("octo-pod-egress", "check")
        check("check on a hand-edited 'release: open' reports, not a traceback",
              "Traceback" not in out and "srv1" in out, out)
        code, out = s.run("octo-pod-egress", "release", "srv1")
        check("release refuses wrong-typed values with a reason and changes nothing",
              code != 0 and "Traceback" not in out and "max_clients" in out
              and "release must be" in out and "release: open" in s.read("40-egress.yml"), out)


def check_egress_gates(repo, check):
    print("\negress check: the two gates disagreeing is an error")
    with tempfile.TemporaryDirectory() as tmp:
        s = Scratch(repo, tmp)
        code, out = s.run("octo-pod-egress", "add", "c2", "--dest", "udp://192.0.2.9:8600",
                          "--key", KEY)
        check("add declares the destination", code == 0, out)
        code, out = s.run("octo-pod-egress", "release", "c2")
        check("release opens both gates", code == 0 and KEY in s.read("10-pod.yml"), out)
        code, out = s.run("octo-pod-egress", "check")
        check("check reports a destination with both gates open as RELEASED, exit 0",
              code == 0 and "RELEASED" in out and "DISAGREE" not in out, out)

        released_pod = s.read("10-pod.yml")
        s.write("10-pod.yml", released_pod.replace(f'downlink_allow: "{KEY}"', 'downlink_allow: ""'))
        code, out = s.run("octo-pod-egress", "check")
        check("open flag with its keys missing from downlink_allow is DISAGREE, exit 1",
              code == 1 and "DISAGREE" in out and "RELEASED" not in out, out)

        stray = "1d18de66e8a09a5e68170b3b3de6563b/sensors/stray/x"
        s.write("10-pod.yml", released_pod.replace(f'downlink_allow: "{KEY}"',
                                                   f'downlink_allow: "{KEY},{stray}"'))
        code, out = s.run("octo-pod-egress", "check")
        check("downlink_allow naming a key no released destination declares is reported, exit 1",
              code == 1 and "no released destination or peer declares" in out and stray in out, out)

        code, out = s.run("octo-pod-egress", "revoke", "c2")
        check("revoke succeeds before the orphan-key fixture", code == 0, out)
        s.write("10-pod.yml", s.read("10-pod.yml").replace('downlink_allow: ""',
                                                           f'downlink_allow: "{KEY}"'))
        code, out = s.run("octo-pod-egress", "check")
        check("a shut destination whose key is still in downlink_allow is an orphan, exit 1",
              code == 1 and "no released destination or peer declares" in out, out)


def check_egress_ports(repo, check):
    print("\negress add: destination port bounds")
    eg = load_egress(repo)
    for port, want in (("0", False), ("65536", False), ("1", True), ("65535", True)):
        check(f"literal 192.0.2.1:{port} is {'accepted' if want else 'refused'}",
              eg.literal(f"192.0.2.1:{port}") is want)
    with tempfile.TemporaryDirectory() as tmp:
        s = Scratch(repo, tmp)
        for i, (port, want) in enumerate((("0", False), ("65536", False), ("1", True),
                                          ("65535", True))):
            code, out = s.run("octo-pod-egress", "add", f"p{i}", "--dest",
                              f"udp://192.0.2.9:{port}", "--key", KEY)
            declared = f"id: p{i}" in s.read("40-egress.yml")
            if want:
                check(f"add accepts --dest udp://192.0.2.9:{port}", code == 0 and declared, out)
            else:
                check(f"add refuses --dest udp://192.0.2.9:{port} and writes nothing",
                      code != 0 and "literal" in out and not declared, out)


def check_declare_refusal(repo, check):
    print("\ndeclarations: unprivileged refusal propagates to the process and wrapper")
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Scratch(repo, tmp)
        before = {p.relative_to(scratch.pod): p.read_bytes()
                  for p in scratch.pod.rglob("*") if p.is_file()}
        nonroot = SHIM.replace("lambda: 0", "lambda: 1000")
        for args in ([], ["add-arm", "fixture-arm", "--udp", "5000"],
                     ["set-lan", "--iface", "eth1", "--addr", "192.0.2.10/24"],
                     ["release", "fixture-arm", "--slot", "unused-fixture"]):
            result = subprocess.run(
                [sys.executable, "-c", nonroot, str(pathlib.Path(repo) / "bin/octo-pod-declare"),
                 *args], env=scratch.env, capture_output=True, text=True, timeout=30)
            check(f"nonroot declaration {args[:1]} exits 2 and explains sudo",
                  result.returncode == 2 and "run with sudo" in result.stderr,
                  result.stdout + result.stderr)
        # The wrapper invokes the helper's env-python3 shebang. Force its root check
        # off in the child so this also runs safely when CI itself happens to be root.
        interpreter = scratch.root / "stubs/python3"
        interpreter.write_text(f"#!{sys.executable}\n" +
                               "import os, runpy, sys\nos.geteuid = lambda: 1000\n" +
                               "sys.argv = sys.argv[1:]\nsys.path.insert(0, os.path.dirname(sys.argv[0]))\nrunpy.run_path(sys.argv[0], run_name='__main__')\n")
        interpreter.chmod(0o755)
        result = subprocess.run(
            ["bash", str(pathlib.Path(repo) / "bin/octo-pod"), "add-arm", "fixture-arm",
             "--udp", "5000"], env=scratch.env, capture_output=True, text=True, timeout=30)
        check("wrapper propagates refusal without reporting a declaration",
              result.returncode != 0 and "run with sudo" in result.stderr
              and "Declared." not in result.stdout, result.stdout + result.stderr)
        after = {p.relative_to(scratch.pod): p.read_bytes()
                 for p in scratch.pod.rglob("*") if p.is_file()}
        check("all unprivileged refusals leave the identity unchanged", before == after)


def check_declare(repo, check):
    print("\nadd-arm: port range and ids taken anywhere on the pod")
    with tempfile.TemporaryDirectory() as tmp:
        s = Scratch(repo, tmp)
        s.write("40-egress.yml", f"""egress:
  - id: shared-name
    cert_cn: octo-egress-shared-name
    dest: "udp://192.0.2.9:8601"
    allow_dest: "192.0.2.9:8601"
    keys:
      - "{KEY}"
    release:
      from_fabric: false
""")
        before = s.read("30-arms.yml")
        for i, (flag, port) in enumerate((("--udp", "0"), ("--udp", "65536"), ("--udp", "99999"),
                                          ("--tcp", "65536"), ("--tcp", "-1"))):
            code, out = s.run("octo-pod-declare", "add-arm", f"bigport{i}", flag, port,
                              "--from", "192.0.2.3")
            check(f"add-arm {flag} {port} is refused naming the range, and writes nothing",
                  code != 0 and "1-65535" in out and s.read("30-arms.yml") == before, out)
        for i, (flag, port) in enumerate((("--udp", "1"), ("--tcp", "65535"))):
            code, out = s.run("octo-pod-declare", "add-arm", f"edge{i}", flag, port,
                              "--from", "192.0.2.3")
            check(f"add-arm {flag} {port} is accepted",
                  code == 0 and f"port: {port}" in s.read("30-arms.yml"), out)

        before = (s.read("30-arms.yml"), s.read("20-peers.yml"))
        code, out = s.run("octo-pod-declare", "add-arm", "shared-name", "--udp", "48021",
                          "--from", "192.0.2.3")
        check("add-arm refuses an id an egress destination already holds",
              code != 0 and "already declared" in out
              and (s.read("30-arms.yml"), s.read("20-peers.yml")) == before, out)
        s.write("30-arms.yml", before[0])   # each refusal stands on its own
        code, out = s.run("octo-pod-declare", "add-peer", "shared-name")
        check("add-peer refuses an id an egress destination already holds",
              code != 0 and "already declared" in out
              and (s.read("30-arms.yml"), s.read("20-peers.yml")) == before, out)
        print("\nset-lan: the LAN address is IPv4")
        code, out = s.run("octo-pod-declare", "set-lan", "--iface", "eth1", "--addr", "fe80::1/24")
        site = s.pod / "00-site.yml"
        check("set-lan refuses an IPv6 address and writes nothing",
              code != 0 and "IPv4" in out and not site.exists(), out)
        code, out = s.run("octo-pod-declare", "set-lan", "--iface", "eth1", "--addr", "192.0.2.10/24")
        check("set-lan accepts an IPv4 address",
              code == 0 and site.exists() and 'lan_ip: "192.0.2.10"' in site.read_text(), out)


def check_status(repo, check):
    import contextlib
    import io
    path = str(pathlib.Path(repo) / "bin" / "octo-pod-status")
    loader = importlib.machinery.SourceFileLoader("status_under_test", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as tmp:
        mod.PEN_STATE = tmp
        tls = pathlib.Path(tmp) / "zenoh/tls"
        tls.mkdir(parents=True)
        (tls / "fixture-cert.pem").write_text("fixture: openssl output stubbed below")
        for num, month in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1):
            mod.sh = lambda cmd, m=month: f"notAfter={m} 13 10:02:07 2028 GMT"
            check("status parses certificate month " + month,
                  mod.cert_expiry("fixture") == f"2028-{num:02d}-13")
        (tls.parent / "escape-cert.pem").write_text("fixture")
        check("status refuses certificate path traversal", mod.cert_expiry("../escape") is None)
        (tls / "destination-egress-cert.pem").write_text("fixture")
        mod.pod_name = lambda: "pod-a"
        fixture = {
            "40-egress.yml": {"egress": [{"id": "destination", "cert_cn": "octo-egress-destination"}]},
            "10-pod.yml": {"fabric_slot_anchor": "slot", "bridge_allow": "raw/a/**"},
        }
        mod.load = lambda p, default=None: fixture.get(pathlib.Path(p).name, default)
        mod.containers = lambda: {"octo-downlink-bridge": "Up 1 hour"}
        mod.sh = lambda cmd: "notAfter=Jan 13 10:02:07 2028 GMT"
        state, collected = mod.collect()
        check("downlink alone never reports a running uplink", state["bridge_container"] is None, str(state))
        check("egress expiry uses the installed destination certificate name", collected[0]["cert"] == "2028-01-13", str(collected))
        mod.containers = lambda: {"octo-uplink-bridge": "Up 1 hour", "octo-downlink-bridge": "Up 1 hour"}
        state, _ = mod.collect()
        check("status reports the actual running uplink", state["bridge_container"] == "octo-uplink-bridge (Up 1 hour)")
    info = dict(pod="pod-a", router="up", slot="slot", endpoint="tls/example.invalid:7447",
                bridge_container="up", bridge_allow="raw/a/**", orphans=[])
    rows = [dict(type=typ, id=typ, state="up", transport="udp", topic="raw/a/**",
                 sink="yes", released=True, cert="2028-01-13") for typ in ["arm", "egress"]]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.render(info, rows)
    check("upstream summary excludes egress destinations", "1 of 1 publisher(s) released" in buf.getvalue(), buf.getvalue())


def run_checks(repo, check):
    check_remove(repo, check)
    check_egress_types(repo, check)
    check_egress_gates(repo, check)
    check_egress_ports(repo, check)
    check_declare_refusal(repo, check)
    check_declare(repo, check)
    check_status(repo, check)


def main():
    failures = []

    def check(label, condition, detail=""):
        if condition:
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))
            failures.append(label)

    run_checks(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else REPO, check)
    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): " + ", ".join(failures))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
