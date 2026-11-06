#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
tools/sigstore-trusted-root.py writes, or checks, the sigstore keys every release carries.

No network: the tool runs from a copy in a temporary tree, with a stub `gh` that prints
canned `gh attestation trusted-root` output, so the real platform/sigstore file is never
touched.

WHY THIS EXISTS
    A pod verifies the next release offline against platform/sigstore/trusted_root.json.
    Keys from the wrong sigstore instance, or a --check that passes on a stale file, would
    leave pods unable to verify a release, or would ship keys nobody chose.

RUN
    python3 platform/tests/test_sigstore_keys.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
TOOL = REPO / "tools" / "sigstore-trusted-root.py"
MEDIA = "application/vnd.dev.sigstore.trustedroot+json;version=0.1"
FAILURES = []

PUBLIC = {"mediaType": MEDIA,
          "tlogs": [{"baseUrl": "https://rekor.sigstore.dev"}],
          "certificateAuthorities": [{"uri": "https://fulcio.sigstore.dev"}],
          "ctlogs": [{"baseUrl": "https://ctfe.sigstore.dev/test"}]}
GITHUB = {"mediaType": MEDIA, "tlogs": [],
          "certificateAuthorities": [{"uri": "fulcio.githubapp.com"}]}


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))
        FAILURES.append(label)


class Tree:
    """A copy of the tool in a temporary repository, and a stub gh beside it."""

    def __init__(self, t):
        self.t = t
        (t / "tools").mkdir()
        shutil.copy(TOOL, t / "tools" / TOOL.name)
        self.keys = t / "platform" / "sigstore" / "trusted_root.json"
        self.bin = t / "bin"
        self.bin.mkdir()
        self.out = t / "gh.out"
        self.rc = t / "gh.rc"
        gh = self.bin / "gh"
        gh.write_text(f"""#!/bin/sh
[ "$1 $2" = "attestation trusted-root" ] || exit 64
cat "{self.out}"
echo "gh: canned failure" >&2
exit "$(cat "{self.rc}" 2>/dev/null || echo 0)"
""")
        gh.chmod(0o755)

    def gh_prints(self, *docs, rc=0):
        self.out.write_text("".join(json.dumps(d) + "\n" for d in docs))
        self.rc.write_text(f"{rc}\n")

    def run(self, *args, path=None):
        env = {**os.environ, "PATH": path or f"{self.bin}:{os.environ['PATH']}"}
        r = subprocess.run([sys.executable, str(self.t / "tools" / TOOL.name), *args],
                           env=env, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr


def main():
    print("\nsigstore keys: tools/sigstore-trusted-root.py")
    with tempfile.TemporaryDirectory() as t:
        tree = Tree(pathlib.Path(t))
        tree.gh_prints(GITHUB, PUBLIC)
        rc, out = tree.run()
        written = tree.keys.read_text() if tree.keys.exists() else ""
        check("it writes the public sigstore instance's keys, not GitHub's",
              rc == 0 and json.loads(written or "{}") == PUBLIC, out)
        check("...as sorted, indented JSON, so a change reads as a small diff",
              written == json.dumps(PUBLIC, indent=2, sort_keys=True) + "\n", written)

        rc, out = tree.run("--check")
        check("--check passes when the file is sigstore's current one",
              rc == 0 and "ok:" in out, out)

        changed = {**PUBLIC, "ctlogs": [{"baseUrl": "https://ctfe.sigstore.dev/2027"}]}
        tree.gh_prints(changed, GITHUB)
        rc, out = tree.run("--check")
        check("--check fails when sigstore's keys changed, and names the fix",
              rc == 1 and "tools/sigstore-trusted-root.py" in out
              and json.loads(tree.keys.read_text()) == PUBLIC, out)

        tree.keys.unlink()
        rc, out = tree.run("--check")
        check("--check fails when there is no file at all", rc == 1, out)

    with tempfile.TemporaryDirectory() as t:
        tree = Tree(pathlib.Path(t))
        tree.gh_prints(GITHUB)
        rc, out = tree.run()
        check("no public instance in gh's output is a refusal, and nothing is written",
              rc != 0 and "public sigstore instance" in out and not tree.keys.exists(), out)

        tree.gh_prints({**PUBLIC, "mediaType": "application/unexpected"})
        rc, out = tree.run()
        check("an unexpected document type is a refusal, and nothing is written",
              rc != 0 and "unexpected mediaType" in out and not tree.keys.exists(), out)

        tree.gh_prints(PUBLIC, rc=1)
        rc, out = tree.run()
        check("a gh failure is a refusal with gh's own words, and nothing is written",
              rc != 0 and "gh: canned failure" in out and not tree.keys.exists(), out)

        empty = pathlib.Path(t) / "empty-path"
        empty.mkdir()
        rc, out = tree.run(path=str(empty))
        check("no gh at all is a refusal, and nothing is written",
              rc != 0 and "gh attestation trusted-root failed" in out
              and not tree.keys.exists(), out)

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): " + ", ".join(FAILURES))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
