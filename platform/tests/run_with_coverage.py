#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
Run every Python suite under coverage and hold the CLI and tools to a floor.

WHAT IS MEASURED
    The Python in bin/ (the octo-pod CLI and its modules) and in tools/ (what builds,
    gates and publishes a release), with branch coverage. .coveragerc lists the files.
    Most of that code runs as a subprocess of a suite, or of a script a suite runs;
    coverage's subprocess patch measures those too, wherever the test copied them.

    Every Python file in scope must be executed by at least one suite. A file nobody
    runs would otherwise be missing from the report instead of counting as 0%.

FLOORS
    STATEMENT_FLOOR and BRANCH_FLOOR are a ratchet: set them to what the suites reach,
    and raise them when tests are added. The job fails below either one.

RUN
    python3 -m pip install --require-hashes -r tools/requirements/coverage.txt
    python3 platform/tests/run_with_coverage.py            # from the repository root

    Writes coverage/coverage.json, coverage/html/ and, under GitHub Actions, a table in
    the job summary.
"""
import configparser
import fnmatch
import json
import os
import pathlib
import re
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
OUT = REPO / "coverage"

# What coverage-python measured on its runner (94.2% and 89.8%), rounded down to the half
# point: a few lines are reached only when a suite runs slowly enough, so a run can read
# a tenth or two lower without any test changing.
# 93.9 measured on this repository's own tree (3217 of 3424 statements): the floor
# follows what this tree's suites reach. Raise it when tests are added.
STATEMENT_FLOOR = 93.9
BRANCH_FLOOR = 89.5

# Every suite in platform/tests runs, once, so a new one is measured without being
# listed. test_release.py imports and runs several others in-process; those run there
# and not again. Three suites expect to run from platform/, as ci.yml and their
# docstrings run them. The translation suites are left out: the public export drops
# them, and .github/workflows/i18n.yml runs them.
FROM_PLATFORM = {"test_upgrade.py", "test_templates.py", "test_release.py"}
TRANSLATION = {"test_i18n.py", "test_docs_i18n.py"}
TESTS = REPO / "platform" / "tests"
RUN_BY_RELEASE = {f"{m}.py" for m in re.findall(r"^\s*from (test_\w+) import run_checks",
                                                (TESTS / "test_release.py").read_text(), re.M)}
SUITES = [(str(p.relative_to(REPO)), REPO / "platform" if p.name in FROM_PLATFORM else REPO)
          for p in sorted(TESTS.glob("test_*.py"))
          if p.name not in TRANSLATION | RUN_BY_RELEASE]


def in_scope():
    """Every Python file .coveragerc measures, as a path relative to the repository."""
    rc = configparser.ConfigParser()
    rc.read(REPO / ".coveragerc")
    patterns = lambda key: [p.strip() for p in rc["run"][key].splitlines()  # noqa: E731
                            if p.strip() and not p.strip().startswith("#")]
    include, omit = patterns("include"), patterns("omit")
    files = []
    tracked = subprocess.run(["git", "ls-files", "bin", "tools"], cwd=REPO, check=True,
                             capture_output=True, text=True).stdout.split()
    for rel in tracked:
        path = REPO / rel
        probe = "/repo/" + rel
        if not path.is_file() or not any(fnmatch.fnmatch(probe, p) for p in include):
            continue
        if any(fnmatch.fnmatch(probe, p) for p in omit):
            continue
        with path.open("rb") as f:
            first = f.readline()
        if rel.endswith(".py") or (first.startswith(b"#!") and b"python" in first):
            files.append(rel)
    return files


def main():
    env = dict(os.environ, COVERAGE_RCFILE=str(REPO / ".coveragerc"),
               COVERAGE_FILE=str(OUT / "data" / ".coverage"))
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    for stale in (OUT / "data").glob(".coverage*"):
        stale.unlink()

    failed = []
    for suite, cwd in SUITES:
        path = REPO / suite
        print(f"== {suite}", flush=True)
        started = time.monotonic()
        r = subprocess.run([sys.executable, "-m", "coverage", "run", str(path)], cwd=cwd, env=env)
        print(f"== {suite}: exit {r.returncode} in {time.monotonic() - started:.0f}s", flush=True)
        if r.returncode != 0:
            failed.append(suite)
    if failed:
        print("suites failed: " + ", ".join(failed), file=sys.stderr)
        return 1

    cov = [sys.executable, "-m", "coverage"]
    subprocess.run(cov + ["combine", "--quiet"], cwd=REPO, env=env, check=True)
    subprocess.run(cov + ["json", "--quiet", "-o", str(OUT / "coverage.json")],
                   cwd=REPO, env=env, check=True)
    subprocess.run(cov + ["html", "--quiet", "-d", str(OUT / "html")], cwd=REPO, env=env,
                   check=True)
    subprocess.run(cov + ["report"], cwd=REPO, env=env, check=True)

    data = json.loads((OUT / "coverage.json").read_text())
    measured = {str(pathlib.Path(f)) for f in data["files"]}
    missing = [f for f in in_scope() if f not in measured]

    t = data["totals"]
    stmt = 100.0 * t["covered_lines"] / max(t["num_statements"], 1)
    branch = 100.0 * t["covered_branches"] / max(t["num_branches"], 1)

    rows = ["| File | Statements | Branches |", "|---|---:|---:|"]
    for f, d in sorted(data["files"].items()):
        s = d["summary"]
        branches = (f"{100.0 * s['covered_branches'] / s['num_branches']:.1f}%"
                    if s["num_branches"] else "no branches")
        rows.append(f"| `{f}` | {100.0 * s['covered_lines'] / max(s['num_statements'], 1):.1f}% "
                    f"| {branches} |")
    rows.append(f"| **Total** | **{stmt:.1f}%** (floor {STATEMENT_FLOOR}%) "
                f"| **{branch:.1f}%** (floor {BRANCH_FLOOR}%) |")
    table = "\n".join(rows)
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## Python coverage\n\n" + table + "\n")

    ok = True
    if missing:
        print("no suite runs these files: " + ", ".join(missing), file=sys.stderr)
        ok = False
    if stmt < STATEMENT_FLOOR:
        print(f"statement coverage {stmt:.1f}% is below the floor {STATEMENT_FLOOR}%",
              file=sys.stderr)
        ok = False
    if branch < BRANCH_FLOOR:
        print(f"branch coverage {branch:.1f}% is below the floor {BRANCH_FLOOR}%",
              file=sys.stderr)
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
