#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Exercise release refusal and changelog extraction without GitHub or releases."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/release-evidence.py"
spec = importlib.util.spec_from_file_location("release_evidence", SCRIPT)
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)
SHA = "a" * 40
REPO = "example/project"


def run_record(path, **overrides):
    run = dict(path=path, head_sha=SHA, head_branch="main", event="push",
               repository={"full_name": REPO}, run_number=1, run_attempt=1,
               status="completed", conclusion="success", html_url="https://example.invalid/run")
    return dict(run, **overrides)


class ReleaseEvidenceTests(unittest.TestCase):
    def test_exact_success_across_pages(self):
        pages = [{"workflow_runs": [run_record(path)]} for path in evidence.REQUIRED]
        self.assertEqual(set(evidence.check_runs(pages, SHA, REPO)), evidence.REQUIRED)

    def test_wrong_commit_branch_repository_event_and_missing(self):
        for change in [{"head_sha": "b" * 40}, {"head_branch": "feature"},
                       {"repository": {"full_name": "other/project"}},
                       {"event": "pull_request"}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                evidence.check_runs([{"workflow_runs": [run_record(p, **change)
                                    for p in evidence.REQUIRED]}], SHA, REPO)
        with self.assertRaises(ValueError):
            evidence.check_runs([{"workflow_runs": [run_record("other.yml")]}], SHA, REPO)
        with self.assertRaises(ValueError):
            evidence.check_runs([], SHA, REPO)

    def test_latest_failure_or_pending_overrides_old_success(self):
        for status, conclusion in [("in_progress", None), ("completed", "failure"),
                                   ("completed", "skipped"), ("completed", "cancelled")]:
            runs = [run_record(p) for p in evidence.REQUIRED]
            runs += [run_record(next(iter(evidence.REQUIRED)), run_number=2,
                                status=status, conclusion=conclusion)]
            with self.subTest(conclusion=conclusion), self.assertRaises(ValueError):
                evidence.check_runs([{"workflow_runs": runs}], SHA, REPO)

    def test_successful_retry_and_out_of_order_results(self):
        runs = [run_record(p, run_attempt=2) for p in evidence.REQUIRED]
        runs += [run_record(p, conclusion="failure") for p in evidence.REQUIRED]
        self.assertEqual(len(evidence.check_runs([{"workflow_runs": runs}], SHA, REPO)), 2)

    def test_notes_keep_security_and_exclude_other_versions(self):
        text = "## [Unreleased]\nFuture\n## [1.2.3] - 2026-01-01\nChange\n### Security\nCVE-example\n## [1.2.2]\nOld\n"
        self.assertEqual(evidence.release_notes(text, "1.2.3"),
                         "## Changes in 1.2.3\n\nChange\n### Security\nCVE-example\n")
        self.assertIn("Last", evidence.release_notes("## [1.0.0-rc.1]\nLast", "1.0.0-rc.1"))

    def test_notes_refuse_empty_missing_duplicate_and_unsafe_version(self):
        for text, version in [("", "1.0.0"), ("## [1.0.0]\n### Fixed\n", "1.0.0"),
                              ("## [1.0.0]\na\n## [1.0.0]\nb", "1.0.0"), ("", "$(id)")]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                evidence.release_notes(text, version)

    def test_cli_notes_and_api_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            changelog, output = work / "CHANGELOG.md", work / "notes.md"
            changelog.write_text("## [1.2.3]\nHuman-written changes\n")
            cmd = [sys.executable, str(SCRIPT)]
            self.assertEqual(subprocess.run(cmd + ["notes", "--version", "1.2.3",
                             "--changelog", str(changelog), "--output", str(output)]).returncode, 0)
            self.assertIn("Human-written", output.read_text())
            gh = work / "gh"
            gh.write_text("#!/bin/sh\nexit 23\n")
            gh.chmod(0o755)
            env = dict(os.environ, PATH=str(work) + os.pathsep + os.environ["PATH"])
            args = cmd + ["checks", "--repository", REPO, "--sha", SHA]
            self.assertNotEqual(subprocess.run(args, env=env, capture_output=True).returncode, 0)
            pages = [{"workflow_runs": [run_record(p) for p in evidence.REQUIRED]}]
            gh.write_text("#!/bin/sh\ncat <<'RESULT'\n" + json.dumps(pages) + "\nRESULT\n")
            self.assertEqual(subprocess.run(args, env=env, capture_output=True).returncode, 0)
            for tail in [["--repository", "../bad", "--sha", SHA],
                         ["--repository", REPO, "--sha", "short"]]:
                self.assertNotEqual(subprocess.run(cmd + ["checks"] + tail,
                                    env=env, capture_output=True).returncode, 0)
            gh.write_text("#!/bin/sh\necho not-json\n")
            self.assertNotEqual(subprocess.run(args, env=env, capture_output=True).returncode, 0)
            self.assertNotEqual(subprocess.run(cmd + ["notes", "--version", "9.9.9",
                                "--changelog", str(changelog), "--output", str(output)],
                                capture_output=True).returncode, 0)

    def test_workflow_enforces_evidence_before_publication(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
        build = workflow["jobs"]["build"]
        self.assertEqual(build["needs"], "release-fuzz")
        self.assertIn("needs.release-fuzz.result == 'success'", build["if"])
        self.assertIn("github.event_name != 'push'", build["if"])
        self.assertEqual(workflow["jobs"]["release-fuzz"]["uses"],
                         "./.github/workflows/cflite_batch.yml")
        steps = build["steps"]
        gate = next(i for i, s in enumerate(steps) if s.get("name") == "exact release commit passed CI and CodeQL")
        notes = next(i for i, s in enumerate(steps) if s.get("name") == "human-written release notes")
        publish = next(i for i, s in enumerate(steps) if s.get("id") == "publish")
        self.assertLess(gate, publish)
        self.assertLess(notes, publish)
        self.assertEqual(steps[gate]["if"], "github.event_name == 'push'")
        self.assertIn('--sha "$GITHUB_SHA"', steps[gate]["run"])
        self.assertEqual(steps[publish]["with"]["body_path"], "${{ runner.temp }}/release-notes.md")
        self.assertNotIn("continue-on-error", steps[gate])
        self.assertNotIn("continue-on-error", steps[notes])


if __name__ == "__main__":
    unittest.main()
