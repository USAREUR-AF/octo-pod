#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Require exact-commit analysis and extract human-written release notes."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


REQUIRED = {".github/workflows/ci.yml", "dynamic/github-code-scanning/codeql"}


def check_runs(pages, sha, repository):
    latest = {}
    for page in pages:
        for run in page["workflow_runs"]:
            path = run.get("path")
            if (path not in REQUIRED or run.get("head_sha") != sha
                    or run.get("head_branch") != "main"
                    or run.get("repository", {}).get("full_name", "").lower()
                    != repository.lower()
                    or run.get("event") not in {"push", "dynamic"}):
                continue
            order = (run["run_number"], run.get("run_attempt", 1))
            old = latest.get(path)
            if old is None or order > (old["run_number"], old.get("run_attempt", 1)):
                latest[path] = run
    missing = sorted(REQUIRED - latest.keys())
    bad = sorted(path for path, run in latest.items()
                 if run.get("status") != "completed" or run.get("conclusion") != "success")
    if missing or bad:
        raise ValueError("Release refused. Missing exact-main-commit analysis: "
                         + ", ".join(missing) + "; incomplete/failed latest analysis: "
                         + ", ".join(bad) + ". Finish CI/CodeQL on this main commit and rerun.")
    return latest


def release_notes(changelog, version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("Expected a release version X.Y.Z or X.Y.Z-prerelease")
    matches = list(re.finditer(r"^## \[" + re.escape(version) + r"\](?:[^\n]*)\n", changelog, re.M))
    if len(matches) != 1:
        raise ValueError("Changelog must contain exactly one section for " + version)
    start = matches[0].end()
    end = re.search(r"^## ", changelog[start:], re.M)
    body = changelog[start:start + end.start()] if end else changelog[start:]
    if not body.strip() or not any(line.strip() and not line.startswith("#")
                                   for line in body.splitlines()):
        raise ValueError("Release changelog section is empty")
    return "## Changes in " + version + "\n\n" + body.strip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    checks = commands.add_parser("checks")
    checks.add_argument("--repository", required=True)
    checks.add_argument("--sha", required=True)
    notes = commands.add_parser("notes")
    notes.add_argument("--version", required=True)
    notes.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    notes.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "notes":
            args.output.write_text(release_notes(args.changelog.read_text(), args.version))
        else:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
                raise ValueError("Invalid repository")
            if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
                raise ValueError("Expected a full commit SHA")
            result = subprocess.run(
                ["gh", "api", "--paginate", "--slurp",
                 f"repos/{args.repository}/actions/runs?head_sha={args.sha}&per_page=100"],
                check=True, capture_output=True, text=True, timeout=120)
            runs = check_runs(json.loads(result.stdout), args.sha, args.repository)
            for path, run in sorted(runs.items()):
                print(f"Verified {path} at {args.sha}: {run['html_url']}")
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
