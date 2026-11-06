#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
Which way an upgrade goes, and what it trusts. No network, no root, no real pod:
bin/octo-pod-upgrade runs against a temporary release tree and a file:// mirror, with a
cosign that records what it was asked and says yes or no as told.

WHY THIS EXISTS
    A version string decides two things on a pod: whether a release may be installed at
    all, and whether it is newer than what runs. Both used to be wrong in the field's
    direction:

      · a lab or rc build (0.4.0~lab2) was refused outright, so a pod that had run one
        could not --rollback to it
      · "newer" was a string test, so a LATEST below the running version was offered
        and installed, and a backport tag would walk the fleet back

      · --expect-sha256 replaced the signature instead of adding to it, and on --images
        nothing compared the digest to anything: any 64 hex characters unpacked an
        unsigned bundle
      · the signer was any workflow in the repository, on any ref

    The order is Debian's, through dpkg where it exists and a bash copy where it does
    not. This holds the copy to dpkg and the upgrader to the order, and holds every
    install path to a signature from the release workflow on a release tag.

RUN
    python3 tests/test_upgrade.py [path/to/octo-pod-upgrade]     # from platform/
    test_release.py runs it too, so CI does.
"""
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent

VALID = ["0.4.0", "10.20.30", "0.4.0~lab2", "0.4.1~rc1", "1.0.0~beta12", "0.4.0~lab10"]
INVALID = ["", "0.4", "0.4.0.1", "v0.4.0", "0.4.0~", "0.4.0~lab", "0.4.0~2", "0.4.0~Lab2",
           "0.4.0-lab.1", "0.4.0+img1", "0.4.0~lab2~rc1", "0.4.0~lab-2", "../0.4.0",
           "0.4.0/../../etc", "0.4.0~lab2/x", "0.4.0 ", " 0.4.0", "0.4.0\n0.4.1",
           "0.4.0~lab2;rm", "0.4.0?x", "0.4.0#x", "0.4.0%2f"]

# (a, b, a compared with b), checked against dpkg itself wherever dpkg is installed.
ORDER = [
    ("0.4.0~lab2", "0.4.0", "lt"),
    ("0.4.0", "0.4.0~lab2", "gt"),
    ("0.4.0~lab2", "0.4.0~lab10", "lt"),
    ("0.4.0~lab9", "0.4.0~lab10", "lt"),
    ("0.4.0~lab2", "0.4.0~rc1", "lt"),
    ("0.4.0~rc1", "0.4.0~lab2", "gt"),
    ("0.4.0~a1", "0.4.0~aa1", "lt"),
    ("0.4.0~rc1", "0.4.0", "lt"),
    ("0.3.9", "0.4.0~lab1", "lt"),
    ("0.4.1~rc1", "0.4.0", "gt"),
    ("0.4.1~rc1", "0.4.1", "lt"),
    ("0.3.5", "0.4.0", "lt"),
    ("0.4.0", "0.4.0", "eq"),
    ("0.4.0~lab2", "0.4.0~lab2", "eq"),
    ("0.4.00", "0.4.0", "eq"),
    ("0.4.0~lab02", "0.4.0~lab2", "eq"),
    ("0.10.0", "0.9.0", "gt"),
    ("0.4.10", "0.4.9", "gt"),
    ("1.0.0", "0.99.99", "gt"),
    ("0.4.08", "0.4.7", "gt"),
    ("99999999999999999999.0.0", "99999999999999999998.0.0", "gt"),
]


def versions_block(script):
    text = script.read_text()
    if "# >>> versions\n" not in text:
        return None
    return text.split("# >>> versions\n", 1)[1].split("# <<< versions\n", 1)[0]


def make_release(d, version, script, repo="example/octo-pod", trusted_root=False):
    """A release tree as build-release.sh lays it out, as far as the upgrader looks."""
    (d / "bin").mkdir(parents=True)
    shutil.copy(script, d / "bin" / "octo-pod-upgrade")
    (d / "platform").mkdir()
    if trusted_root:
        # The sigstore keys a release from 0.6.1 carries. The stub cosign reads nothing.
        (d / "platform" / "sigstore").mkdir()
        (d / "platform" / "sigstore" / "trusted_root.json").write_text("{}\n")
    (d / "bin" / "octo-pod").write_text("#!/bin/sh\nexit 0\n")
    (d / "bin" / "octo-pod").chmod(0o755)
    (d / "platform" / "configure-octo-pod.yml").write_text("---\n")
    (d / "VERSION").write_text(version + "\n")
    (d / "RELEASE").write_text(f"repository={repo}\n")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(pod, version, latest=False, deb=False, images=False, sigstore=False,
            cosign_bundle=True):
    """One version on the mirror: its own SHA256SUMS, a (fake) signature, the artifacts."""
    mirror = pod.mirror
    vd = mirror / version
    vd.mkdir(parents=True)
    src = mirror.parent / f"src-{version}"
    make_release(src, version, pod.script)
    tar = vd / f"octo-pod-{version}.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        for p in sorted(src.rglob("*")):
            tf.add(p, arcname=str(p.relative_to(src)), recursive=False)
    names = [tar]
    if deb:
        d = vd / f"octo-pod_{version}_all.deb"
        d.write_bytes(b"not really a deb " + version.encode())
        names.append(d)
    if images:
        stage = mirror.parent / f"img-{version}" / "images"
        stage.mkdir(parents=True)
        (stage / "MAP").write_text(version + "\n")
        b = vd / f"octo-pod-images-{version}.tar.gz"
        with tarfile.open(b, "w:gz") as tf:
            tf.add(stage, arcname="images")
        names.append(b)
    (vd / "SHA256SUMS").write_text("".join(f"{sha256(n)}  {n.name}\n" for n in names))
    if cosign_bundle:
        (vd / "SHA256SUMS.cosign-bundle").write_text("{}\n")
    if sigstore:
        # The standard Sigstore bundle, which releases carry from 0.6.0.
        (vd / "SHA256SUMS.sigstore.json").write_text("{}\n")
    if latest:
        (mirror / "LATEST").write_text(version + "\n")


class Pod:
    """A temporary pod: a release (or package) install, a mirror, and stub binaries."""

    def __init__(self, t, script, running, kind="release", installed=(), sort_stub=False,
                 keep=3, dpkg_broken=False, repo="example/octo-pod", trusted_root=False):
        self.t = t
        self.kind = kind
        self.script = script
        self.mirror = t / "mirror"
        self.mirror.mkdir()
        self.opt = t / "opt"
        self.opt.mkdir()
        self.root = self.opt / "octo-pod"
        self.state = t / "state"
        self.stage = t / "stage"
        self.etc = t / "etc"
        fabric = self.etc / "host_vars" / "pod-a" / "05-fabric.yml"
        fabric.parent.mkdir(parents=True)
        fabric.write_text(f'octo_repo_url: "{self.mirror.as_uri()}"\n')

        if kind == "release":
            for v in (running, *installed):
                make_release(self.opt / f"octo-pod-{v}", v, script, repo, trusted_root)
            self.root.symlink_to(self.opt / f"octo-pod-{running}")
            self.entry = self.root / "bin" / "octo-pod-upgrade"
        else:
            # A package owns /opt/octo-pod outright: no symlink, no version directories.
            self.root = t / "pkg"
            make_release(self.root, running, script, repo, trusted_root)
            self.entry = self.root / "bin" / "octo-pod-upgrade"

        self.fakebin = t / "fakebin"
        self.fakebin.mkdir()
        # Every call's arguments, one per line, then "--". Fails, as the real one does,
        # when the --bundle or --trusted-root it was handed is missing; otherwise prints
        # cosign.err, if any, to stderr and exits with cosign.rc. A call with
        # --trusted-root (offline) answers from cosign-offline.rc instead, when that exists.
        self.cosign_log = t / "cosign.log"
        self.cosign_rc = t / "cosign.rc"
        self.cosign_err = t / "cosign.err"
        self.cosign_offline_rc = t / "cosign-offline.rc"
        self.stub("cosign", f"""#!/bin/sh
for a in "$@"; do printf '%s\\n' "$a"; done >> "{self.cosign_log}"
echo -- >> "{self.cosign_log}"
b=""; r=""; prev=""
for a in "$@"; do
  [ "$prev" = --bundle ] && b="$a"
  [ "$prev" = --trusted-root ] && r="$a"
  prev="$a"
done
[ -n "$b" ] && [ -f "$b" ] || exit 1
if [ -n "$r" ]; then
  [ -f "$r" ] || exit 1
  if [ -f "{self.cosign_offline_rc}" ]; then
    echo "offline: no matching key in the trusted root" >&2
    exit "$(cat "{self.cosign_offline_rc}")"
  fi
fi
cat "{self.cosign_err}" >&2 2>/dev/null
exit "$(cat "{self.cosign_rc}" 2>/dev/null || echo 0)"
""")
        self.apt_log = t / "apt-get.log"
        self.stub("apt-get", f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.apt_log}"\nexit 0\n')
        if kind == "package":
            # dpkg owns the install. Ordering goes to a real dpkg when the box has one and
            # to the upgrader's own bash order when it has not.
            block = t / "versions.sh"
            block.write_text(versions_block(script) or "")
            real = shutil.which("dpkg") or ""
            # A dpkg that cannot answer, as when it is missing a library or killed.
            broken = "exit 2" if dpkg_broken else ":"
            self.stub("dpkg", f"""#!/usr/bin/env bash
case "$1" in
  -S) exit 0 ;;
  --compare-versions)
    {broken}
    [ -n "{real}" ] && exec "{real}" "$@"
    . "{block}"
    c="$(version_cmp_bash "$2" "$4")" || exit 2
    [ "$c" = "$3" ] ;;
  *) exit 1 ;;
esac
""")
        if sort_stub:
            # A sort -V that knows nothing of '~' (strverscmp, as busybox and older
            # toolchains have it): 0.4.0~lab2 sorts AFTER 0.4.0.
            self.stub("sort", r'''#!/usr/bin/env python3
import re, sys
flags = "".join(a.lstrip("-") for a in sys.argv[1:] if a.startswith("-"))
lines = sys.stdin.read().splitlines()
if "u" in flags:
    lines = sorted(set(lines))
if "V" in flags:
    key = lambda s: [(0, int(x)) if x.isdigit() else (1, x) for x in re.split(r"(\d+)", s) if x]
    lines = sorted(lines, key=key)
else:
    lines = sorted(lines)
if "r" in flags:
    lines.reverse()
sys.stdout.write("".join(l + "\n" for l in lines))
''')
        self.env = {
            "PATH": f"{self.fakebin}:{os.environ['PATH']}",
            "HOME": str(t),
            "OCTO_POD_ETC": str(self.etc),
            "OCTO_POD_ROOT": str(self.root),
            "OCTO_POD_STATE": str(self.state),
            "OCTO_POD_KEEP": str(keep),
            "OCTO_STAGE_DIR": str(self.stage),
        }

    def stub(self, name, body):
        p = self.fakebin / name
        p.write_text(body)
        p.chmod(0o755)

    def run(self, *args, env=None):
        r = subprocess.run([shutil.which("bash"), str(self.entry), *args],
                           env={**self.env, **(env or {})},
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    def cosign_says(self, rc, stderr=""):
        self.cosign_rc.write_text(f"{rc}\n")
        self.cosign_err.write_text(stderr)

    def cosign_calls(self):
        """Each cosign call as {flag: value}, its other words in order under "args"."""
        calls, argv = [], []
        lines = self.cosign_log.read_text().splitlines() if self.cosign_log.exists() else []
        for ln in lines:
            if ln != "--":
                argv.append(ln)
                continue
            call, i = {"args": []}, 0
            while i < len(argv):
                if argv[i] == "--new-bundle-format":
                    call[argv[i]] = True
                    i += 1
                elif argv[i].startswith("--") and i + 1 < len(argv):
                    call[argv[i]] = argv[i + 1]
                    i += 2
                else:
                    call["args"].append(argv[i])
                    i += 1
            calls.append(call)
            argv = []
        return calls

    def without_cosign(self):
        """A PATH with every program this box has except cosign, the stub included."""
        farm = self.t / "farm-no-cosign"
        farm.mkdir()
        for d in self.env["PATH"].split(os.pathsep):
            if d == str(self.fakebin):
                for e in self.fakebin.iterdir():
                    if e.name != "cosign":
                        (farm / e.name).symlink_to(e)
                continue
            try:
                entries = sorted(pathlib.Path(d).iterdir())
            except OSError:
                continue
            for e in entries:
                if e.name == "cosign" or (farm / e.name).is_symlink():
                    continue
                # An entry this user may not stat (macOS ships some in /usr/sbin) is not
                # a program it could run. Before Python 3.14, is_file() raises on it.
                try:
                    runnable = e.is_file() and os.access(e, os.X_OK)
                except OSError:
                    continue
                if runnable:
                    (farm / e.name).symlink_to(e)
        return {"PATH": str(farm)}

    def runs(self):
        return os.readlink(self.root).rsplit("octo-pod-", 1)[-1] if self.root.is_symlink() else None

    def releases(self):
        return sorted(p.name[len("octo-pod-"):] for p in self.opt.glob("octo-pod-*"))

    def previous(self):
        p = self.state / "upgrade-previous"
        return p.read_text().strip() if p.exists() else None

    def apt(self):
        return self.apt_log.read_text() if self.apt_log.exists() else ""


def run_checks(repo, check, script=None):
    script = pathlib.Path(script) if script else repo / "bin" / "octo-pod-upgrade"
    if shutil.which("curl") is None:
        check("curl is installed (the upgrader fetches with it, so these checks need it)", False)
        return
    bash = shutil.which("bash")
    dpkg = shutil.which("dpkg")
    block = versions_block(script)

    print("\nupgrade: which versions are versions, and which way is up")
    check("the version functions are marked out for testing", block is not None)
    block = block or ""

    # Pass the string as an argument so nothing in it is ever shell syntax.
    def valid(v):
        r = subprocess.run([bash, "-c", "set -uo pipefail\n" + block + 'valid_version "$1"', "-", v],
                           capture_output=True, text=True, env={"PATH": ""})
        return r.returncode == 0
    accepted = [v for v in VALID if not valid(v)]
    check("valid_version accepts X.Y.Z and X.Y.Z~<word><N>", not accepted, f"refused {accepted}")
    let_in = [v for v in INVALID if valid(v)]
    check("valid_version refuses anything else, including path and URL syntax",
          not let_in, f"accepted {let_in!r}")

    def compare(path):
        out = {}
        for a, b, _ in ORDER:
            r = subprocess.run([bash, "-c", "set -uo pipefail\n" + block
                                + 'version_cmp "$1" "$2" || echo FAIL', "-", a, b],
                               capture_output=True, text=True, env={"PATH": path})
            out[(a, b)] = r.stdout.strip()
        return out

    # PATH empty: no dpkg, so the bash order answers. It needs no other program.
    got = compare("")
    wrong = [(a, b, got[(a, b)], want) for a, b, want in ORDER if got[(a, b)] != want]
    check("without dpkg, the bash order puts ~ below the release and numbers in order",
          not wrong, f"(a, b, got, want) {wrong}")
    # dpkg would happily order the Debian forms; the upgrader must not, with or without it.
    ordered = []
    for path in ("", os.environ["PATH"]) if dpkg else ("",):
        for other in ("junk", "0.4.0-1", "0.4.0+1", "0.4.0~lab2-1"):
            r = subprocess.run([bash, "-c", "set -uo pipefail\n" + block
                                + 'version_cmp 0.4.0 "$1" && echo NOFAIL', "-", other],
                               capture_output=True, text=True, env={"PATH": path})
            if r.returncode == 0 or "NOFAIL" in r.stdout:
                ordered.append((other, path or "no dpkg", r.stdout.strip()))
    check("a string that is not a version is never ordered", not ordered, str(ordered))

    if dpkg:
        truth = {}
        for a, b, _ in ORDER:
            for op in ("lt", "eq", "gt"):
                if subprocess.run([dpkg, "--compare-versions", a, op, b]).returncode == 0:
                    truth[(a, b)] = op
                    break
        table = [(a, b, truth.get((a, b)), want) for a, b, want in ORDER if truth.get((a, b)) != want]
        check("the expected order is dpkg's order", not table, f"(a, b, dpkg, table) {table}")
        disagree = [(a, b, got[(a, b)], truth.get((a, b))) for a, b, _ in ORDER
                    if got[(a, b)] != truth.get((a, b))]
        check("the bash fallback agrees with dpkg on every case", not disagree,
              f"(a, b, bash, dpkg) {disagree}")
        via = compare(os.environ["PATH"])
        off = [(a, b, via[(a, b)], want) for a, b, want in ORDER if via[(a, b)] != want]
        check("with dpkg installed, version_cmp gives dpkg's answer", not off, str(off))
    else:
        print("  SKIP  dpkg is not installed here; the bash order was checked against the table")

    mixed = "0.3.9\n0.4.0\njunk\n0.4.0~lab2\n0.4.0~lab10\n0.4.1~rc1\n0.4.1\n"
    want = "0.4.1 0.4.1~rc1 0.4.0 0.4.0~lab10 0.4.0~lab2 0.3.9 junk"
    for label, path in (("bash order", ""), ("dpkg", os.environ["PATH"] if dpkg else None)):
        if path is None:
            continue
        r = subprocess.run([bash, "-c", "set -uo pipefail\n" + block + "newest_first"],
                           input=mixed, capture_output=True, text=True, env={"PATH": path})
        got_order = " ".join(r.stdout.split())
        check(f"newest_first orders newest first, non-versions last ({label})",
              got_order == want, f"got {got_order!r}")

    print("\nupgrade: prerelease versions install, and roll back")
    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0~lab2")
        publish(pod, "0.4.0~lab3")
        rc, out = pod.run("--to", "0.4.0~lab3")
        check("--to a prerelease installs it", rc == 0 and pod.runs() == "0.4.0~lab3", out[-400:])
        check("the prerelease it came from is recorded for --rollback",
              pod.previous() == "0.4.0~lab2", str(pod.previous()))
        rc, out = pod.run("--rollback")
        check("--rollback returns to the prerelease", rc == 0 and pod.runs() == "0.4.0~lab2",
              out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0~lab2")
        publish(pod, "0.4.0", latest=True)
        rc, out = pod.run()
        check("a release supersedes its prerelease", rc == 0 and pod.runs() == "0.4.0", out[-400:])
        rc, out = pod.run("--rollback")
        check("--rollback from the release returns to the prerelease",
              rc == 0 and pod.runs() == "0.4.0~lab2", out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.4.1~rc1", latest=True)
        rc, out = pod.run("--check")
        check("a newer prerelease in LATEST is offered", rc == 0 and "available  0.4.1~rc1" in out
              and pod.runs() == "0.4.0", out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0", kind="package")
        (pod.state).mkdir()
        (pod.state / "upgrade-previous").write_text("0.4.0~lab2\n")
        publish(pod, "0.4.0~lab2", deb=True)
        # The stub cosign accepts any signer, so this stands in for a lab signer named by
        # OCTO_POD_CERT_IDENTITY. A real prerelease has no octo-pod-v* tag ('~' is not
        # allowed in one), so the release.yml pin alone refuses its .deb.
        rc, out = pod.run("--rollback")
        check("a package --rollback to a prerelease installs that .deb",
              rc == 0 and "octo-pod_0.4.0~lab2_all.deb" in pod.apt(), out[-400:])

    print("\nupgrade: never backwards by accident")
    for latest in ("0.3.5", "0.4.0~lab2"):
        with tempfile.TemporaryDirectory() as t:
            pod = Pod(pathlib.Path(t), script, "0.4.0")
            publish(pod, latest, latest=True)
            rc, out = pod.run("--check")
            check(f"a LATEST of {latest} under 0.4.0 is nothing to do, not offered",
                  rc == 0 and "Nothing to do" in out and "available" not in out, out[-400:])
            rc, out = pod.run()
            check(f"upgrade with a LATEST of {latest} under 0.4.0 installs nothing",
                  rc == 0 and pod.runs() == "0.4.0" and pod.releases() == ["0.4.0"]
                  and pod.previous() is None, out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0", kind="package")
        publish(pod, "0.3.5", latest=True, deb=True)
        rc, out = pod.run("--check")
        check("package: a lower LATEST is nothing to do, not offered",
              rc == 0 and "Nothing to do" in out and "available" not in out, out[-400:])
        rc, out = pod.run()
        check("package: upgrade with a lower LATEST never reaches apt",
              rc == 0 and pod.apt() == "", out[-400:] + pod.apt())

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0", kind="package", dpkg_broken=True)
        publish(pod, "0.3.5", latest=True, deb=True)
        rc, out = pod.run()
        check("a dpkg that cannot compare is not read as 'newer'",
              rc != 0 and pod.apt() == "", out[-400:] + pod.apt())

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "1.2.3junk")
        publish(pod, "0.4.0", latest=True)
        rc, out = pod.run()
        check("a running version that cannot be ordered takes nothing from LATEST",
              rc != 0 and "--allow-downgrade" in out and pod.runs() == "1.2.3junk"
              and pod.releases() == ["1.2.3junk"], out[-400:])
        rc, out = pod.run("--to", "0.4.0", "--allow-downgrade")
        check("...and takes a named version when asked on purpose",
              rc == 0 and pod.runs() == "0.4.0", out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.3.5")
        rc, out = pod.run("--to", "0.3.5")
        check("--to an older version is refused without --allow-downgrade",
              rc != 0 and "--allow-downgrade" in out and "--rollback" in out
              and pod.runs() == "0.4.0" and pod.releases() == ["0.4.0"], out[-400:])
        rc, out = pod.run("--check", "--to", "0.3.5", "--allow-downgrade")
        check("--check names the exact command for a deliberate downgrade",
              rc == 0 and "sudo octo-pod upgrade --to 0.3.5 --allow-downgrade" in out
              and pod.runs() == "0.4.0", out[-400:])
        rc, out = pod.run("--to", "0.3.5", "--allow-downgrade")
        check("--to an older version with --allow-downgrade goes there",
              rc == 0 and pod.runs() == "0.3.5" and pod.previous() == "0.4.0", out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        rc, out = pod.run("--allow-downgrade")
        check("--allow-downgrade alone is refused: it needs --to", rc != 0
              and "--to" in out and pod.runs() == "0.4.0", out[-400:])
        rc, out = pod.run("--to", "0.4.0")
        check("--to the running version is nothing to do", rc == 0 and "Already on 0.4.0" in out,
              out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.3.5", latest=True, images=True)
        rc, out = pod.run("--images")
        check("--images with a LATEST under the running version unpacks nothing",
              rc == 0 and "Nothing to do" in out and not (pod.stage / "images").exists(),
              out[-400:])

    # Not known to be older is not newer: the bundle is what the next airgap bootstrap runs.
    for running, kw in (("0.4.0", {"kind": "package", "dpkg_broken": True}),
                        ("1.2.3junk", {})):
        with tempfile.TemporaryDirectory() as t:
            pod = Pod(pathlib.Path(t), script, running, **kw)
            publish(pod, "0.3.5", latest=True, images=True)
            rc, out = pod.run("--images")
            how = "a dpkg that cannot compare" if kw else "an unorderable running version"
            check(f"--images with {how} refuses LATEST and stages nothing",
                  rc != 0 and "cannot order 0.3.5" in out and not (pod.stage / "images").exists()
                  and pod.cosign_calls() == [], out[-400:])

    print("\nupgrade: the signature is always checked, and --expect-sha256 only adds to it")
    zeros = "0" * 64

    def tar_digest(pod, v):
        return sha256(pod.mirror / v / f"octo-pod-{v}.tar.gz")

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.4.1")
        pod.cosign_says(1)
        rc, out = pod.run("--to", "0.4.1", "--expect-sha256", tar_digest(pod, "0.4.1"))
        check("a bad signature is refused even with the right --expect-sha256",
              rc != 0 and "could not verify SHA256SUMS" in out and pod.runs() == "0.4.0"
              and pod.releases() == ["0.4.0"] and len(pod.cosign_calls()) == 1, out[-400:])

        rc, out = pod.run("--to", "0.4.1", "--expect-sha256", tar_digest(pod, "0.4.1"),
                          env=pod.without_cosign())
        check("no cosign is a refusal even with --expect-sha256, and says nothing of skipping",
              rc != 0 and "cosign is not installed" in out and "skip only" not in out
              and "adds a check" in out and pod.releases() == ["0.4.0"], out[-600:])

        pod.cosign_says(0)
        rc, out = pod.run("--to", "0.4.1", "--expect-sha256", zeros)
        check("a signed release whose digest is not the one supplied is refused",
              rc != 0 and "digest mismatch" in out and pod.runs() == "0.4.0"
              and pod.releases() == ["0.4.0"], out[-400:])

        before = len(pod.cosign_calls())
        rc, out = pod.run("--to", "0.4.1", "--expect-sha256", tar_digest(pod, "0.4.1").upper())
        check("a signed release that matches the supplied digest installs, after cosign",
              rc == 0 and pod.runs() == "0.4.1" and "digest matches" in out
              and len(pod.cosign_calls()) == before + 1, out[-400:])

    # An offline pod with a cold sigstore cache fails here too. What cosign said is the
    # only thing that tells that apart from a forgery.
    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.4.1", latest=True)
        tuf = ("Error: getting Rekor public keys: updating local metadata and targets: "
               "error updating to TUF remote mirror: tuf: failed to download 10.root.json: "
               "dial tcp 127.0.0.1:9: connect: connection refused\n")
        pod.cosign_says(1, "noise that scrolls away\n" * 8 + tuf)
        rc, out = pod.run()
        check("a cosign failure is refused with cosign's own last lines, and the TUF hint",
              rc != 0 and "tuf: failed to download 10.root.json" in out
              and "tuf-repo-cdn.sigstore.dev" in out and out.count("noise that scrolls") <= 4
              and pod.runs() == "0.4.0" and pod.releases() == ["0.4.0"], out[-800:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.4.1")
        good = tar_digest(pod, "0.4.1")
        sums = pod.mirror / "0.4.1" / "SHA256SUMS"
        sums.write_text(sums.read_text().replace(good, "f" * 64))
        rc, out = pod.run("--to", "0.4.1", "--expect-sha256", good)
        check("the signed manifest is still checked when the supplied digest matches",
              rc != 0 and "checksum mismatch" in out and pod.releases() == ["0.4.0"], out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0", kind="package")
        publish(pod, "0.4.1", deb=True)
        deb = pod.mirror / "0.4.1" / "octo-pod_0.4.1_all.deb"
        good = sha256(deb)
        pod.cosign_says(1)
        rc, out = pod.run("--to", "0.4.1", "--expect-sha256", good)
        check("package: a bad signature is refused even with the right --expect-sha256",
              rc != 0 and "could not verify SHA256SUMS" in out and pod.apt() == "", out[-400:])
        pod.cosign_says(0)
        sums = pod.mirror / "0.4.1" / "SHA256SUMS"
        sums.write_text(sums.read_text().replace(good, "e" * 64))
        rc, out = pod.run("--to", "0.4.1", "--expect-sha256", good)
        check("package: the signed manifest is still checked with --expect-sha256",
              rc != 0 and "checksum mismatch" in out and pod.apt() == "", out[-400:])
        sums.write_text(sums.read_text().replace("e" * 64, good))
        rc, out = pod.run("--to", "0.4.1", "--expect-sha256", zeros)
        check("package: a .deb that is not the supplied digest never reaches apt",
              rc != 0 and "digest mismatch" in out and pod.apt() == "", out[-400:])
        before = len(pod.cosign_calls())
        rc, out = pod.run("--to", "0.4.1", "--expect-sha256", good)
        check("package: signed, listed and matching, it installs, after cosign",
              rc == 0 and "octo-pod_0.4.1_all.deb" in pod.apt()
              and len(pod.cosign_calls()) == before + 1, out[-400:])

    print("\nupgrade: --images checks the bundle before it unpacks anything")

    def images_pod(t):
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.4.1", latest=True, images=True)
        # What a previous --images left for bootstrap must survive a refusal.
        (pod.stage / "images").mkdir(parents=True)
        (pod.stage / "images" / "MAP").write_text("staged earlier\n")
        return pod, pod.mirror / "0.4.1" / "octo-pod-images-0.4.1.tar.gz"

    def untouched(pod):
        return (pod.stage / "images" / "MAP").read_text() == "staged earlier\n"

    with tempfile.TemporaryDirectory() as t:
        pod, bundle = images_pod(t)
        pod.cosign_says(1)
        rc, out = pod.run("--images", "--expect-sha256", sha256(bundle))
        check("--images refuses an unsigned bundle even with the right --expect-sha256",
              rc != 0 and "could not verify SHA256SUMS" in out and untouched(pod), out[-400:])
        pod.cosign_says(0)
        rc, out = pod.run("--images", "--expect-sha256", zeros)
        check("--images refuses a signed bundle that is not the supplied digest",
              rc != 0 and "digest mismatch" in out and untouched(pod), out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod, bundle = images_pod(t)
        with tarfile.open(bundle, "w:gz") as tf:   # replaced after signing
            evil = pathlib.Path(t) / "evil" / "images"
            evil.mkdir(parents=True)
            (evil / "MAP").write_text("replaced\n")
            tf.add(evil, arcname="images")
        rc, out = pod.run("--images")
        check("--images refuses a bundle that differs from the signed manifest",
              rc != 0 and "checksum mismatch" in out and untouched(pod), out[-400:])
        rc, out = pod.run("--images", "--expect-sha256", sha256(bundle))
        check("...and the supplied digest of that bundle does not talk it past the manifest",
              rc != 0 and "checksum mismatch" in out and untouched(pod), out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod, bundle = images_pod(t)
        rc, out = pod.run("--images", "--expect-sha256", sha256(bundle))
        check("--images unpacks a signed, listed bundle that matches the supplied digest",
              rc == 0 and (pod.stage / "images" / "MAP").read_text() == "0.4.1\n"
              and len(pod.cosign_calls()) == 1, out[-400:])

    print("\nupgrade: who may sign is the release workflow, on a release tag")
    pinned = (r"^https://github\.com/(?i:example/octo-pod)/\.github/workflows/release\.yml"
              r"@refs/tags/octo-pod-v")
    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.4.1", latest=True)
        rc, out = pod.run()
        call = (pod.cosign_calls() or [{}])[-1]
        check("cosign is asked for the release workflow on a release tag, and nothing wider",
              rc == 0 and call.get("--certificate-identity-regexp") == pinned
              and call.get("--certificate-oidc-issuer")
              == "https://token.actions.githubusercontent.com"
              and call.get("args", [None])[0] == "verify-blob"
              and "OCTO_POD_CERT_IDENTITY" not in out, f"{call}\n{out[-300:]}")
    # Go's regexp and Python's agree on everything this pattern uses.
    rx = re.compile(call.get("--certificate-identity-regexp") or "(?!)")
    gh = "https://github.com/example/octo-pod/.github/workflows/"
    takes = [gh + "release.yml@refs/tags/octo-pod-v0.4.1",
             # GitHub writes its own spelling of owner and repository into the certificate.
             "https://github.com/EXAMPLE/Octo-Pod/.github/workflows/release.yml@refs/tags/octo-pod-v0.4.1"]
    refuses = [gh + "ci.yml@refs/heads/main", gh + "release.yml@refs/heads/main",
               gh + "release.yml@refs/tags/v0.4.1", gh + "release.yml@refs/pull/7/merge",
               gh + "evil.yml@refs/tags/octo-pod-v0.4.1",
               ("https://github.com/example/octo-pod-fork/.github/workflows/release.yml"
                "@refs/tags/octo-pod-v0.4.1"),
               ("https://githubXcom/example/octo-pod/.github/workflows/release.yml"
                "@refs/tags/octo-pod-v0.4.1")]
    wrong = [u for u in takes if not rx.search(u)] + [u for u in refuses if rx.search(u)]
    check("the pinned pattern takes a release-tag signature and no other workflow or ref",
          not wrong, str(wrong))

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0", repo="example/octo.pod")
        publish(pod, "0.4.1", latest=True)
        rc, out = pod.run()
        got = (pod.cosign_calls() or [{}])[-1].get("--certificate-identity-regexp", "")
        check("a '.' in the repository name is escaped, not a wildcard",
              rc == 0 and got.startswith(r"^https://github\.com/(?i:example/octo\.pod)/\.github/")
              and not re.search(got, "https://github.com/example/octoXpod/.github/workflows/"
                                     "release.yml@refs/tags/octo-pod-v0.4.1"), got)

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.4.1", latest=True)
        override = r"^https://github\.com/other/octo-pod/"
        rc, out = pod.run(env={"OCTO_POD_CERT_IDENTITY": override})
        got = (pod.cosign_calls() or [{}])[-1].get("--certificate-identity-regexp")
        check("OCTO_POD_CERT_IDENTITY is what cosign is asked for, and the run says so",
              rc == 0 and got == override and "OCTO_POD_CERT_IDENTITY" in out
              and override in out and pod.runs() == "0.4.1", f"{got}\n{out[-400:]}")

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0", repo="")
        publish(pod, "0.4.1")
        rc, out = pod.run("--to", "0.4.1", "--expect-sha256", tar_digest(pod, "0.4.1"))
        check("a release that names no signer refuses, --expect-sha256 or not, and names the fix",
              rc != 0 and "names no repository" in out and "OCTO_POD_CERT_IDENTITY=" in out
              and "release\\.yml@refs/tags/octo-pod-v" in out and pod.cosign_calls() == []
              and "--expect-sha256" not in out
              and pod.releases() == ["0.4.0"], out[-600:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        rc, out = pod.run("--help")
        check("--help documents --expect-sha256 as an extra check and the signer override",
              rc == 0 and "--expect-sha256" in out and "signature is still required" in out
              and "OCTO_POD_CERT_IDENTITY" in out and "skip" not in out.lower(), out)

    print("\nupgrade: offline, against the sigstore keys this pod's release carries")
    keys_tail = "octo-pod-0.6.1/platform/sigstore/trusted_root.json"

    def offline(call):
        return "--trusted-root" in call

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.6.1", trusted_root=True)
        publish(pod, "0.6.2", latest=True, sigstore=True)
        rc, out = pod.run()
        calls = pod.cosign_calls()
        call = (calls or [{}])[0]
        check("a release with a Sigstore bundle is verified once, offline, with this pod's keys",
              rc == 0 and pod.runs() == "0.6.2" and len(calls) == 1
              and call.get("--new-bundle-format") is True
              and call.get("--bundle", "").endswith("/SHA256SUMS.sigstore.json"),
              f"{calls}\n{out[-400:]}")
        check("...against the keys the running release carries, with the same pinned signer",
              call.get("--trusted-root", "").endswith(keys_tail)
              and call.get("--certificate-identity-regexp") == pinned
              and call.get("--certificate-oidc-issuer")
              == "https://token.actions.githubusercontent.com"
              and "offline" in out, f"{call}\n{out[-400:]}")

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.6.0")      # carries no keys
        publish(pod, "0.6.1", latest=True, sigstore=True)
        rc, out = pod.run()
        calls = pod.cosign_calls()
        check("a pod whose release carries no keys verifies online, as before",
              rc == 0 and pod.runs() == "0.6.1" and len(calls) == 1
              and not offline(calls[0])
              and calls[0].get("--bundle", "").endswith("/SHA256SUMS.cosign-bundle"),
              f"{calls}\n{out[-400:]}")

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.6.1", trusted_root=True)
        publish(pod, "0.5.0")                              # before the Sigstore bundle
        rc, out = pod.run("--to", "0.5.0", "--allow-downgrade")
        calls = pod.cosign_calls()
        check("a release with only the older bundle is verified online, once",
              rc == 0 and pod.runs() == "0.5.0" and len(calls) == 1
              and not offline(calls[0]), f"{calls}\n{out[-400:]}")

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.6.1", trusted_root=True)
        publish(pod, "0.6.2", latest=True, sigstore=True)
        pod.cosign_offline_rc.write_text("1\n")
        rc, out = pod.run()
        calls = pod.cosign_calls()
        check("keys that do not verify it send the check online, which decides it",
              rc == 0 and pod.runs() == "0.6.2" and len(calls) == 2
              and offline(calls[0]) and not offline(calls[1])
              and "asking sigstore online" in out, f"{calls}\n{out[-400:]}")

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.6.1", trusted_root=True)
        publish(pod, "0.6.2", latest=True, sigstore=True)
        pod.cosign_offline_rc.write_text("1\n")
        pod.cosign_says(1, "Error: tuf: failed to download 10.root.json: connection refused\n")
        rc, out = pod.run()
        check("refused when neither verifies, with both answers and the laptop path",
              rc != 0 and pod.runs() == "0.6.1" and "could not verify SHA256SUMS" in out
              and "no matching key in the trusted root" in out
              and "failed to download 10.root.json" in out
              and "tools/install-to-pod.sh" in out, out[-900:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.6.1", trusted_root=True)
        publish(pod, "0.6.2", latest=True, sigstore=True, cosign_bundle=False)
        pod.cosign_offline_rc.write_text("1\n")
        rc, out = pod.run()
        calls = pod.cosign_calls()
        check("a release with only the Sigstore bundle goes online with that bundle",
              rc == 0 and len(calls) == 2 and not offline(calls[1])
              and calls[1].get("--new-bundle-format") is True
              and calls[1].get("--bundle", "").endswith("/SHA256SUMS.sigstore.json"),
              f"{calls}\n{out[-400:]}")

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.6.1", trusted_root=True)
        publish(pod, "0.6.2", latest=True)
        (pod.mirror / "0.6.2" / "SHA256SUMS.cosign-bundle").unlink()
        rc, out = pod.run()
        check("a release with no signature at all is refused before cosign",
              rc != 0 and "carries no signature" in out and pod.cosign_calls() == []
              and pod.runs() == "0.6.1", out[-400:])

    with tempfile.TemporaryDirectory() as t:
        tp = pathlib.Path(t)
        pod = Pod(tp, script, "0.6.1", trusted_root=True)
        publish(pod, "0.6.2", latest=True, sigstore=True)
        other = tp / "keys.json"
        other.write_text("{}\n")
        rc, out = pod.run(env={"OCTO_POD_TRUSTED_ROOT": str(other)})
        call = (pod.cosign_calls() or [{}])[0]
        check("OCTO_POD_TRUSTED_ROOT is the file cosign is given, and the run says so",
              rc == 0 and call.get("--trusted-root") == str(other)
              and "OCTO_POD_TRUSTED_ROOT" in out and str(other) in out, f"{call}\n{out[-400:]}")

    with tempfile.TemporaryDirectory() as t:
        tp = pathlib.Path(t)
        pod = Pod(tp, script, "0.6.1", trusted_root=True)
        publish(pod, "0.6.2", latest=True, sigstore=True)
        rc, out = pod.run(env={"OCTO_POD_TRUSTED_ROOT": str(tp / "missing.json")})
        check("an OCTO_POD_TRUSTED_ROOT that is not a file is refused, not skipped",
              rc != 0 and "OCTO_POD_TRUSTED_ROOT" in out and pod.cosign_calls() == []
              and pod.runs() == "0.6.1", out[-400:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.6.1", kind="package", trusted_root=True)
        publish(pod, "0.6.2", deb=True, sigstore=True)
        rc, out = pod.run("--to", "0.6.2")
        calls = pod.cosign_calls()
        check("package: the .deb is verified offline the same way",
              rc == 0 and "octo-pod_0.6.2_all.deb" in pod.apt() and len(calls) == 1
              and offline(calls[0]), f"{calls}\n{out[-400:]}")

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.6.1", trusted_root=True)
        publish(pod, "0.6.2", latest=True, images=True, sigstore=True)
        rc, out = pod.run("--images")
        calls = pod.cosign_calls()
        check("--images: the bundle is verified offline the same way",
              rc == 0 and (pod.stage / "images" / "MAP").read_text() == "0.6.2\n"
              and len(calls) == 1 and offline(calls[0]), f"{calls}\n{out[-400:]}")

    print("\nupgrade: the manifest names the artifact exactly once")
    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.4.1", latest=True)
        sums = pod.mirror / "0.4.1" / "SHA256SUMS"
        h, n = sums.read_text().split()
        sums.write_text(f"{h}  other/{n}\n")
        rc, out = pod.run()
        check("a manifest entry for another path with the same file name is not this artifact",
              rc != 0 and "not listed" in out and pod.releases() == ["0.4.0"], out[-300:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.4.1", latest=True)
        sums = pod.mirror / "0.4.1" / "SHA256SUMS"
        good = sums.read_text()
        sums.write_text(good + good.replace(good.split()[0], "a" * 64))
        rc, out = pod.run()
        check("an artifact listed twice in the manifest is refused, right hash first or not",
              rc != 0 and "exactly once" in out and pod.releases() == ["0.4.0"], out[-300:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0")
        publish(pod, "0.4.1", latest=True)
        (pod.mirror / "0.4.1" / "SHA256SUMS").write_text("")
        env = pod.without_cosign()
        farm = pathlib.Path(env["PATH"])
        for name in ("sha256sum", "shasum"):
            if (farm / name).is_symlink():
                (farm / name).unlink()
        (farm / "cosign").symlink_to(pod.fakebin / "cosign")
        rc, out = pod.run(env=env)
        check("with no hash tool, an unlisted artifact is a refusal, not an empty match",
              rc != 0 and pod.releases() == ["0.4.0"], out[-300:])

    print("\nupgrade: messages name the case and the exact command")
    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "1.2.3junk")
        publish(pod, "0.4.0", latest=True)
        rc, out = pod.run()
        check("an unorderable running version is named as that, not as a downgrade",
              rc != 0 and "cannot order" in out, out[-300:])

    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0", kind="package")
        publish(pod, "0.3.5", deb=True)
        rc, out = pod.run("--check", "--to", "0.3.5", "--allow-downgrade")
        check("package --check names the exact command for a deliberate downgrade",
              rc == 0 and "sudo octo-pod upgrade --to 0.3.5 --allow-downgrade" in out
              and pod.apt() == "", out[-300:])

    with tempfile.TemporaryDirectory() as t:
        tp = pathlib.Path(t)
        pod = Pod(tp, script, "0.4.0", installed=("0.3.5",))
        make_release(tp / "elsewhere" / "evil", "evil", script)
        pod.state.mkdir()
        (pod.state / "upgrade-previous").write_text("0.4.0/../../elsewhere/evil\n")
        rc, out = pod.run("--rollback")
        check("release --rollback refuses a recorded previous version that is a path",
              rc != 0 and "is not a version" in out and pod.runs() == "0.4.0", out[-300:])

    print("\nupgrade: what is kept, in dpkg's order whatever sort -V the box has")
    with tempfile.TemporaryDirectory() as t:
        pod = Pod(pathlib.Path(t), script, "0.4.0~lab10",
                  installed=("0.3.9", "0.4.0~lab2", "0.4.0~lab9"), sort_stub=True, keep=2)
        publish(pod, "0.4.0", latest=True)
        rc, out = pod.run()
        check("pruning keeps the newest releases, prereleases below their release",
              rc == 0 and pod.releases() == ["0.4.0", "0.4.0~lab10"],
              f"left {pod.releases()}\n{out[-400:]}")
        rc, out = pod.run("--list")
        kept = next((ln for ln in out.splitlines() if ln.startswith("kept")), "")
        check("--list shows kept releases newest first", kept.split()[1:] == ["0.4.0", "0.4.0~lab10"],
              kept)


def main():
    failures = []

    def check(label, condition, detail=""):
        if condition:
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))
            failures.append(label)

    run_checks(REPO, check, sys.argv[1] if len(sys.argv) > 1 else None)
    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): " + ", ".join(failures))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
