#!/usr/bin/env python3
"""
What ends up on a pod. No network, no docker, no dpkg — this stages the release payload
and asserts on the tree.

WHY THIS EXISTS
    A pod does not clone this repository; it installs an artifact built by
    tools/build-release.sh. Everything true of the repository is therefore not
    automatically true of what a site actually runs, and the differences are the
    dangerous kind:

      · a credential swept in by a broad copy reaches every pod in the fleet at once
      · arm SOURCE in the payload puts a pod back on the build-on-the-box path, with
        crates.io in a border site's deployment path
      · a missing platform/ or bin/ is not discovered until an operator runs the thing

    None of that is visible in a diff of the repository, so it is asserted here.

RUN
    python3 tests/test_release.py          # from platform/
"""
import pathlib
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

HERE = pathlib.Path(__file__).resolve().parent
PLATFORM = HERE.parent
REPO = PLATFORM.parent
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))
        FAILURES.append(label)


def release_docs_checks():
    """A release can carry the documentation site, signed like every other artifact."""
    import yaml
    print("\nrelease documentation site: tools/build-release.sh --docs")
    build = REPO / "tools" / "build-release.sh"
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([str(build), "9.9.9", "--docs", tmp], capture_output=True, text=True)
    check("--docs refuses a directory that is not a built site, before building anything",
          r.returncode == 2 and "not a built documentation site" in r.stderr, r.stderr.strip())
    text = build.read_text()
    check("the site is signed only when this run was given --docs, never a stale file",
          'if [ -n "$DOCS_DIR" ]; then ARTIFACTS+=("octo-pod-docs-$VERSION.tar.gz"); fi' in text)
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())
    steps = wf["jobs"]["build"]["steps"]
    docs = next((s for s in steps if s.get("id") == "docs"), {})
    check("the site is built only where the builder exists, on a tag or when asked",
          "hashFiles('tools/build-docs.py')" in docs.get("if", "")
          and "refs/tags/octo-pod-v" in docs.get("if", "") and "inputs.build_docs" in docs.get("if", ""))
    order = [s.get("id") or s.get("name") for s in steps]
    check("the site is built before the release, so SHA256SUMS lists it",
          "docs" in order and "build" in order and order.index("docs") < order.index("build"))
    b = next(s for s in steps if s.get("name") == "build")
    check("the build passes the site to build-release.sh",
          b.get("env", {}).get("DOCS_DIR") == "${{ steps.docs.outputs.dir }}" and '--docs "$DOCS_DIR"' in b["run"])
    publish = next(s for s in steps if s.get("id") == "publish")
    check("the site is published with the release",
          "dist/octo-pod-docs-*.tar.gz" in publish["with"]["files"].split())
    gate = next(s["run"] for s in steps if s.get("name") == "the release is complete and verifiable")
    check("a site that was built but not published fails the release",
          'dist/octo-pod-docs-${v}.tar.gz' in gate and "documentation site was built but is not in the release" in gate)


def release_provenance_checks():
    """The release's provenance describes exactly what the signed manifest lists.

    Runs the workflow's own subjects step against a synthetic dist/ rather than reading
    it, so an edit that changes the subject list or its encoding fails here.
    """
    import base64
    import os
    import yaml
    print("\nrelease provenance: .github/workflows/release.yml")
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())
    jobs = wf["jobs"]
    build_steps = jobs["build"]["steps"]
    step = next((s for s in build_steps if s.get("id") == "subjects"), None)
    check("the build job produces provenance subjects", step is not None)
    if step is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        dist = pathlib.Path(tmp) / "dist"
        dist.mkdir()
        manifest = ("a" * 64 + "  octo-pod-9.9.9.tar.gz\n"
                    + "b" * 64 + "  octo-pod_9.9.9_all.deb\n"
                    + "c" * 64 + "  octo-pod-images-9.9.9.tar.gz\n")
        (dist / "SHA256SUMS").write_text(manifest)
        out = pathlib.Path(tmp) / "github_output"
        out.touch()
        # GNU base64 takes -w0; the BSD one on a developer Mac does not wrap by default.
        script = step["run"]
        if subprocess.run(["base64", "-w0"], input=b"", capture_output=True).returncode != 0:
            script = script.replace("base64 -w0", "base64")
        r = subprocess.run(["bash", "-c", script], cwd=tmp, capture_output=True, text=True,
                           env=dict(os.environ, GITHUB_OUTPUT=str(out)))
        check("the subjects step runs", r.returncode == 0, r.stderr.strip())
        line = next((ln for ln in out.read_text().splitlines() if ln.startswith("subjects=")), "")
        decoded = base64.b64decode(line.partition("=")[2] or "").decode()
        check("the subjects are exactly the signed manifest, every artifact it lists",
              decoded == manifest, repr(decoded[:120]))
    check("the build job exports the subjects and version to the provenance job",
          jobs["build"].get("outputs", {}).get("subjects", "").endswith("steps.subjects.outputs.subjects }}")
          and "version" in jobs["build"].get("outputs", {}))
    prov = jobs.get("provenance", {})
    uses = prov.get("uses", "")
    check("provenance comes from the SLSA generic generator, referenced by a release tag",
          re.fullmatch(r"slsa-framework/slsa-github-generator/\.github/workflows/"
                       r"generator_generic_slsa3\.yml@v\d+\.\d+\.\d+", uses) is not None, uses)
    check("provenance runs only for a tag, after the build",
          prov.get("needs") == ["build"] and "refs/tags/octo-pod-v" in prov.get("if", ""))
    check("the provenance asset is named <name>.intoto.jsonl and attached to the release",
          prov.get("with", {}).get("provenance-name", "").endswith(".intoto.jsonl")
          and prov.get("with", {}).get("upload-assets") is True)
    check("only the provenance job holds the write scopes it needs",
          prov.get("permissions") == {"actions": "read", "id-token": "write", "contents": "write"})
    verify = jobs.get("provenance-verified", {})
    vrun = "\n".join(s.get("run", "") for s in verify.get("steps", []))
    check("published provenance is verified from the release, for tarball and package",
          set(verify.get("needs", [])) == {"build", "provenance"}
          and "gh release download" in vrun and "slsa-verifier verify-artifact" in vrun
          and "--source-tag" in vrun and "octo-pod_${V}_all.deb" in vrun)
    gate = next(s["run"] for s in build_steps if s.get("name") == "the release is complete and verifiable")
    check("the standard Sigstore bundle is a required, verified release asset",
          "SHA256SUMS.sigstore.json LATEST" in gate and "--new-bundle-format --bundle SHA256SUMS.sigstore.json" in gate)


def main():
    version = (REPO / "VERSION").read_text().strip()
    print(f"\nrelease payload — tools/build-release.sh ({version})")
    check("VERSION is X.Y.Z", bool(re.fullmatch(r"\d+\.\d+\.\d+", version)), f"got {version!r}")

    with tempfile.TemporaryDirectory() as tmp:
        stage = pathlib.Path(tmp) / "stage"
        r = subprocess.run(
            [str(REPO / "tools" / "build-release.sh"), "--stage-only", str(stage)],
            capture_output=True, text=True)
        check("the payload stages cleanly", r.returncode == 0, r.stderr.strip())
        if r.returncode != 0:
            return finish()

        root = stage / "opt" / "octo-pod"
        files = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
        rel = {str(f) for f in files}

        # What an operator needs to run a pod at all.
        for needed in ("bin/octo-pod", "bin/octo-pod-join", "bin/octo-pod-declare",
                       "bin/octo-pod-egress", "bin/octo_pod_slot.py", "bin/octo_pod_mesh.py",
                       "bin/octo-pod-identity", "bin/octo-pod-upgrade",
                       "platform/configure-octo-pod.yml", "platform/verify-octo-pod.yml",
                       "platform/templates/pen/octo-pen-zenoh-router.container.j2",
                       "platform/templates/arm/octo-arm.container.j2",
                       "tools/install-to-pod.sh", "tools/mint-key.sh",
                       "tools/deprovision-pod.sh",
                       "VERSION", "README.md", "LICENSE", "NOTICE"):
            check(f"ships {needed}", needed in rel)

        # Start where a field operator starts. Follow local documentation links
        # through the actual artifact; moving instructions out of README must not
        # strand them in the source checkout or require a network at the site.
        documentation_root = root.resolve()
        pending_docs = [documentation_root / "README.md"]
        visited_docs = set()
        missing_docs = []
        while pending_docs:
            document = pending_docs.pop()
            if document in visited_docs:
                continue
            visited_docs.add(document)
            for target in re.findall(r"(?<!!)\[[^\]\n]+\]\(([^\s)]+)\)", document.read_text()):
                link = urlsplit(target)
                if link.scheme or link.netloc:
                    continue
                destination = (document.parent / unquote(link.path)).resolve() if link.path else document
                if not destination.is_relative_to(documentation_root) or not destination.exists():
                    missing_docs.append(f"{document.relative_to(documentation_root)}: {target}")
                elif destination.is_file() and destination.suffix == ".md":
                    pending_docs.append(destination)
        check("operator documentation links resolve inside the release payload",
              not missing_docs, str(missing_docs))

        check("the CLI is executable in the payload",
              (root / "bin" / "octo-pod").stat().st_mode & 0o111)
        check("VERSION inside the payload matches the tree",
              (root / "VERSION").read_text().strip() == version)

        # A pod PULLS digest-pinned arm images. Shipping the source puts it back on the
        # build-on-the-box path that made every pod's dependency tree a function of the
        # day it was built — and configure-octo-pod.yml decides which path it is on by
        # looking for exactly this file.

        # The seed case: one box at a site builds the bundle the others install from. A
        # pod that can pull needs no bundle, and a pod that cannot pull cannot build one,
        # so the only useful arrangement is one box that can, serving the rest — and it
        # needs the builder on disk to do it.
        check("ships the image-bundle builder (for the seed box)",
              "tools/build-airgap-bundle.sh" in rel,
              "a connected pod cannot build a bundle for the disconnected ones")

        check("does not ship the removed simulator", "tools/cat62-sim.py" not in rel)

        # The boundary: those two files, not the whole of tools/. build-release.sh
        # packages the software itself and has no business on a pod.
        check("does NOT ship the release packager",
              "tools/build-release.sh" not in rel,
              "a pod has no reason to be able to package a release")


        check("ships NO arm source (a pod pulls images, it does not build them)",
              not [f for f in rel if f.startswith("arms/")],
              str([f for f in rel if f.startswith("arms/")][:5]))

        # The worked identity is documentation and travels; a real one never should.
        check("ships the example identity to read",
              any(f.startswith("example/host_vars/") for f in rel))

        # THE ONE THAT MATTERS. A release goes to every site at once, so anything
        # credential-shaped in it is published to the whole fleet.
        secrets = [f for f in rel
                   if f.endswith((".pem", ".key", ".p12", ".jks"))
                   or pathlib.Path(f).name in (".env", "setup-key", "id_rsa")]
        check("no credential material in the payload", not secrets, str(secrets))

        # Build noise and local state.
        junk = [f for f in rel
                if "__pycache__" in f or f.endswith((".pyc", ".DS_Store"))
                or f.startswith((".git/", "dist/", "target/"))]
        check("no build output, caches or vcs metadata", not junk, str(junk[:5]))

        # The platform layer must still name no deployment once packaged — the same
        # rule test_templates.py applies to the repository, asserted on what ships.
        # 100.64.0.0: NetBird's default overlay range (RFC 6598), a default every mesh
        # shares unless mesh_cidr changes it, not one deployment's address.
        allowed = {"0.0.0.0", "127.0.0.1", "255.255.255.255", "100.64.0.0"}
        stray = []
        for f in files:
            if not str(f).startswith("platform/") or str(f).startswith("platform/tests/"):
                continue
            if f.suffix not in (".yml", ".yaml", ".j2"):
                continue
            for n, line in enumerate((root / f).read_text().splitlines(), 1):
                for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", line):
                    if ip not in allowed:
                        stray.append(f"{f}:{n} {ip}")
        check("the packaged platform layer names no site", not stray, str(stray[:5]))

        print(f"  ----  {len(files)} files, "
              f"{sum((root / f).stat().st_size for f in files) // 1024} KiB unpacked")

    # The upgrade path's trust chain, asserted where it is written rather than only in
    # the workflow that exercises it: one signature over SHA256SUMS, and a pod that
    # refuses rather than degrading to an unsigned hash when it cannot check it.
    print("\nrelease trust chain — bin/octo-pod-upgrade")
    up = (REPO / "bin" / "octo-pod-upgrade").read_text()
    check("the pod verifies the manifest's signature, not a bare sidecar hash",
          "SHA256SUMS.cosign-bundle" in up and "cosign verify-blob" in up)
    check("verification is pinned to an identity and an issuer, not just 'signed'",
          "--certificate-identity-regexp" in up and "--certificate-oidc-issuer" in up)
    check("a missing cosign refuses instead of falling back",
          "cosign is not installed" in up and "--expect-sha256" in up)
    # There must be NO path that installs a release on the say-so of the server it came
    # from. --expect-sha256 adds a digest obtained out of band on top of the signature;
    # a blanket "skip the check" flag left LATEST, SHA256SUMS and the artifact all
    # controlled by one party, so the hash check passed vacuously. test_upgrade.py runs
    # both against a cosign that says no.
    check("no flag disables verification outright",
          "--unverified" not in up,
          "a skip-verification flag is back; a hostile mirror is then trusted absolutely")
    check("the version from an unsigned LATEST is validated before it becomes a path",
          "valid_version" in up and "which is not a version (X.Y.Z or X.Y.Z~<word><N>)" in up)
    build = (REPO / "tools" / "build-release.sh").read_text()
    check("the builder emits the manifest the pod expects",
          "SHA256SUMS" in build and "LATEST" in build)
    depends = next((ln for ln in build.splitlines() if ln.startswith("Depends:")), "")
    check("the package declares the host contract, so apt refuses an older podman",
          all(d in depends for d in ("podman (>= 4.7)", "skopeo", "nftables", "openssl",
                                     "ansible-core", "bash-completion")), depends)

    release_provenance_checks()
    release_docs_checks()

    # NOTICES. The arm images ship LICENSE, NOTICE and the third-party notices; the image
    # build context is arms/, so arms/ holds checked copies of the first two.
    print("\nnotices: what every arm image carries")
    for f in ("LICENSE", "NOTICE"):
        check(f"arms/{f} is the same as {f}",
              (REPO / "arms" / f).read_text() == (REPO / f).read_text())
    allow = re.findall(r'"([^"]+)"', (REPO / "arms" / "deny.toml").read_text()
                       .split("[licenses]", 1)[1].split("]", 1)[0])
    accepted = re.findall(r'"([^"]+)"', (REPO / "arms" / "about.toml").read_text()
                          .split("accepted = [", 1)[1].split("]", 1)[0])
    check("cargo-about accepts exactly the licenses cargo-deny allows",
          sorted(allow) == sorted(accepted), f"deny {sorted(allow)} about {sorted(accepted)}")

    # ONE SET OF ADVISORY DECISIONS. deny.toml explains each ignored advisory,
    # osv-scanner.toml carries the same set to osv-scanner and OpenSSF Scorecard, and
    # the CI cargo-audit step names the vulnerabilities among them. A decision recorded
    # in one place and not the others is a scanner silently disagreeing with the review.
    import tomllib
    deny = tomllib.loads((REPO / "arms" / "deny.toml").read_text())
    deny_ids = set(deny.get("advisories", {}).get("ignore", []))
    osv = tomllib.loads((REPO / "arms" / "osv-scanner.toml").read_text())
    osv_entries = osv.get("IgnoredVulns", [])
    osv_ids = {e.get("id") for e in osv_entries}
    check("osv-scanner.toml ignores exactly the advisories deny.toml explains",
          deny_ids == osv_ids, f"deny {sorted(deny_ids)} osv {sorted(osv_ids)}")
    check("every osv-scanner ignore has a reason and an expiry date",
          all(e.get("reason") and e.get("ignoreUntil") for e in osv_entries))
    ci_text = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    audit_ids = set(re.findall(r"--ignore (RUSTSEC-\d{4}-\d{4})", ci_text))
    check("cargo-audit ignores only advisories deny.toml explains",
          audit_ids and audit_ids <= deny_ids, f"audit {sorted(audit_ids)}")
    tpl = (REPO / "arms" / "THIRD_PARTY_LICENSES.md").read_text()
    check("the third-party notices are generated and list licenses",
          tpl.startswith("# Third-Party Licenses") and tpl.count("\n## ") > 0)
    for df in sorted((REPO / "arms").glob("*/Dockerfile")):
        check(f"{df.parent.name}'s image carries LICENSE, NOTICE and the notices",
              "/build/LICENSE /build/NOTICE /build/THIRD_PARTY_LICENSES.md /usr/share/doc/octo-pod/"
              in df.read_text())

    # WHO SIGNS FOLLOWS WHO BUILT. No repository is written into the scripts: the
    # release records the repository whose workflow built it, the pod trusts that
    # repository's workflow, and the installer asks rather than guesses.
    print("\nrelease identity: the signer follows the repository that built the release")
    for f in ("bin/octo-pod-upgrade", "tools/install-to-pod.sh", "tools/build-release.sh"):
        check(f"{f} names no organization", "usareur-af" not in (REPO / f).read_text())
    import os
    import shutil
    with tempfile.TemporaryDirectory() as t:
        t = pathlib.Path(t)

        def stage(env_repo=None, flag=None):
            d = t / ("stage-" + str(len(list(t.iterdir()))))
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_REPOSITORY"}
            if env_repo:
                env["GITHUB_REPOSITORY"] = env_repo
            args = [str(REPO / "tools" / "build-release.sh"), "--stage-only", str(d)]
            if flag:
                args += ["--repo", flag]
            r = subprocess.run(args, capture_output=True, text=True, env=env)
            rel = d / "opt" / "octo-pod" / "RELEASE"
            return r, rel.read_text() if rel.exists() else ""

        r, rel = stage(env_repo="example/octo-pod")
        check("a release built in Actions names its repository",
              r.returncode == 0 and "repository=example/octo-pod\n" in rel, rel or r.stderr)
        r, rel = stage(flag="fork-owner/octo-pod")
        check("--repo names the repository outside Actions",
              r.returncode == 0 and "repository=fork-owner/octo-pod\n" in rel, rel or r.stderr)
        r, rel = stage()
        check("a local build names no repository, so it is unsigned",
              r.returncode == 0 and "repository=\n" in rel, rel or r.stderr)
        r, _ = stage(flag="not a repo")
        check("a malformed repository is refused", r.returncode == 2, r.stderr)

        up = (REPO / "bin" / "octo-pod-upgrade").read_text()
        signer_block = up.split("# >>> signer\n", 1)[1].split("# <<< signer\n", 1)[0]

        def signer(release=None, override=None):
            root = t / "root"
            shutil.rmtree(root, ignore_errors=True)
            root.mkdir()
            if release is not None:
                (root / "RELEASE").write_text(release)
            env = {"PATH": os.environ["PATH"], "ROOT": str(root)}
            if override:
                env["OCTO_POD_CERT_IDENTITY"] = override
            r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + signer_block
                                + '\nprintf %s "$CERT_IDENTITY"'],
                               capture_output=True, text=True, env=env)
            return r.stdout if r.returncode == 0 else f"exit {r.returncode}: {r.stderr}"

        got = signer("repository=example/octo-pod\n")
        check("the pod trusts the release workflow of the repository that built it",
              got == r"^https://github\.com/(?i:example/octo-pod)/\.github/workflows/release\.yml"
                     r"@refs/tags/octo-pod-v", got)
        got = signer("repository=evil/.*\n")
        check("a malformed repository in RELEASE names no signer", got == "", got)
        got = signer("repository=\n")
        check("a release that names no repository names no signer", got == "", got)
        got = signer(None)
        check("a pod with no RELEASE file names no signer", got == "", got)
        got = signer("repository=example/octo-pod\n", override="^https://example.invalid/")
        check("OCTO_POD_CERT_IDENTITY overrides the release's repository",
              got == "^https://example.invalid/", got)
        check("with no signer, the upgrade refuses rather than skips verification",
              "names no repository whose workflow signs releases" in up)

        bindir = t / "bin"
        bindir.mkdir()
        (bindir / "ssh").write_text("#!/bin/sh\nexit 255\n")
        (bindir / "ssh").chmod(0o755)
        lone = t / "unpacked" / "tools"
        lone.mkdir(parents=True)
        shutil.copy(REPO / "tools" / "install-to-pod.sh", lone / "install-to-pod.sh")
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", NO_COLOR="1")
        run_inst = lambda: subprocess.run(  # noqa: E731
            ["bash", str(lone / "install-to-pod.sh"), "pod@pod.invalid"],
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        r = run_inst()
        out = r.stdout + r.stderr
        check("the installer refuses to guess whose releases to trust",
              r.returncode != 0 and "Pass --repo" in out, out[-300:])
        (t / "unpacked" / "RELEASE").write_text("repository=example/octo-pod\n")
        r = run_inst()
        out = r.stdout + r.stderr
        check("an unpacked release names its own repository to the installer",
              "Pass --repo" not in out and "can we reach" in out, out[-300:])

    from test_release_images import run_checks
    run_checks(REPO, check)

    from test_upgrade import run_checks as run_upgrade_checks
    run_upgrade_checks(REPO, check)

    from test_edit import run_checks as run_edit_checks
    run_edit_checks(REPO, check)

    from test_cli import run_checks as run_cli_checks
    run_cli_checks(REPO, check)

    from test_audit_shell import run_checks as run_audit_shell_checks
    run_audit_shell_checks(REPO, check)

    from test_audit_tools import run_checks as run_audit_tools_checks
    run_audit_tools_checks(REPO, check)

    from test_audit_platform import run_checks as run_audit_platform_checks
    run_audit_platform_checks(REPO, check)
    from test_top import run_checks as run_top_checks
    run_top_checks(REPO, check)

    from test_inspect import run_checks as run_inspect_checks
    run_inspect_checks(REPO, check)

    return finish()


def finish():
    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s): " + ", ".join(FAILURES))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
