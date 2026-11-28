#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Refresh the pinned pack bundle from a goat-transformers checkout.

Usage, from the repository root:

    python3 gateway/translate/tools/refresh-packs.py <goat-transformers checkout>
    python3 gateway/translate/tools/refresh-packs.py --check

The first form copies the model modules and packs named in FORMATS below from the
checkout into gateway/translate/packs/, and writes MANIFEST.json there: the commit the
files came from and the SHA-256 of each one. It refuses a checkout with uncommitted
changes under model/ or packs/, a commit that differs from the `rev` the crate's
Cargo.toml pins, and a pack that is not `distribution public`.

`--check` reads no checkout. It recomputes every hash and fails if a file differs from
the manifest, is missing, or is present and not listed. The gateway makes the same check
when it starts.
"""
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys

# The formats the gateway ships. A pack's `uses` lines name the model modules it needs;
# `core` is always needed.
FORMATS = ["cot-xml", "geojson", "sapient"]
# Per-pack files that are copied. Vectors stay in the library, where its engines run them.
PACK_FILES = ["pack.def", "pack.md", "PROVENANCE.md"]

HERE = pathlib.Path(__file__).resolve().parent.parent
BUNDLE = HERE / "packs"
MANIFEST = BUNDLE / "MANIFEST.json"
REPOSITORY = "https://github.com/usareur-af/goat-transformers"


def die(message):
    sys.exit(f"refresh-packs: {message}")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def directive(text, name):
    """Every value of one directive in a .def file."""
    return re.findall(rf"^{name}\s+(\S+)\s*$", text, flags=re.M)


def pinned_rev():
    cargo = (HERE / "Cargo.toml").read_text()
    found = re.search(r'^goat-transformers\s*=.*\brev\s*=\s*"([0-9a-f]{40})"', cargo, flags=re.M)
    if not found:
        die("Cargo.toml does not pin goat-transformers by a 40-character rev")
    return found.group(1)


def bundle_files():
    return sorted(p.relative_to(BUNDLE).as_posix() for p in BUNDLE.rglob("*")
                  if p.is_file() and p != MANIFEST)


def check():
    if not MANIFEST.is_file():
        die(f"{MANIFEST} is missing")
    manifest = json.loads(MANIFEST.read_text())
    listed = {f["path"]: f["sha256"] for f in manifest["files"]}
    problems = []
    for path in bundle_files():
        if path not in listed:
            problems.append(f"{path}: present, not in the manifest")
        elif sha256(BUNDLE / path) != listed[path]:
            problems.append(f"{path}: differs from the manifest")
    problems += [f"{path}: in the manifest, missing" for path in listed
                 if not (BUNDLE / path).is_file()]
    if manifest["commit"] != pinned_rev():
        problems.append(f"manifest commit {manifest['commit']} is not the rev Cargo.toml pins")
    if problems:
        die("the bundle does not match its manifest:\n  " + "\n  ".join(problems))
    print(f"refresh-packs: {len(listed)} files match the manifest (commit {manifest['commit']})")


def git(checkout, *args):
    return subprocess.run(["git", "-C", str(checkout), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def refresh(checkout):
    checkout = pathlib.Path(checkout).resolve()
    if not (checkout / "packs").is_dir() or not (checkout / "model").is_dir():
        die(f"{checkout} is not a goat-transformers checkout (no packs/ or model/)")
    commit = git(checkout, "rev-parse", "HEAD")
    if git(checkout, "status", "--porcelain", "--", "model", "packs"):
        die("the checkout has uncommitted changes under model/ or packs/")
    if commit != pinned_rev():
        die(f"the checkout is at {commit}; Cargo.toml pins {pinned_rev()}. Move the rev "
            "and the bundle in the same change, so the engine and its packs agree")

    wanted = {}
    modules = {"core"}
    for fmt in FORMATS:
        source = checkout / "packs" / fmt
        text = (source / "pack.def").read_text()
        if directive(text, "distribution") != ["public"]:
            die(f"pack {fmt} is not `distribution public`")
        modules.update(directive(text, "uses"))
        for name in PACK_FILES:
            if (source / name).is_file():
                wanted[f"packs/{fmt}/{name}"] = source / name
    pending = sorted(modules)
    while pending:
        module = pending.pop()
        source = checkout / "model" / f"{module}.def"
        if not source.is_file():
            die(f"model module {module} is not in the checkout")
        text = source.read_text()
        if directive(text, "distribution") != ["public"]:
            die(f"model module {module} is not `distribution public`")
        wanted[f"model/{module}.def"] = source
        for used in directive(text, "uses"):
            if used not in modules:
                modules.add(used)
                pending.append(used)

    for stale in ("model", "packs"):
        shutil.rmtree(BUNDLE / stale, ignore_errors=True)
    files = []
    for path in sorted(wanted):
        target = BUNDLE / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(wanted[path], target)
        files.append({"path": path, "sha256": sha256(target)})
    MANIFEST.write_text(json.dumps({
        "repository": REPOSITORY,
        "commit": commit,
        "formats": FORMATS,
        "files": files,
    }, indent=2) + "\n")
    print(f"refresh-packs: wrote {len(files)} files from {commit}")


def main(argv):
    if argv == ["--check"]:
        check()
    elif len(argv) == 1 and not argv[0].startswith("-"):
        refresh(argv[0])
    else:
        die("usage: refresh-packs.py <goat-transformers checkout> | --check")


if __name__ == "__main__":
    main(sys.argv[1:])
