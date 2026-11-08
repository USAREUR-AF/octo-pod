<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Cheat Sheet

Run from the repository root. Replace the placeholders defined in the opening chapter. API reads may require additional permissions.

*Twenty Inspection and Test Commands*

```console
$ git status --short
$ git rev-parse HEAD
$ git remote -v
$ git branch -a
$ git tag --list
$ git ls-files
$ git submodule status
$ git log --all --oneline -20
$ git diff --check
$ git diff --stat
$ gh repo view <owner>/<repo>
$ gh pr list --repo <owner>/<repo>
$ gh pr view <pr-number> --repo <owner>/<repo>
$ gh pr checks <pr-number> --repo <owner>/<repo>
$ gh run list --repo <owner>/<repo>
$ gh run view <run-id> --repo <owner>/<repo>
$ gh release list --repo <owner>/<repo>
$ gh release view <tag> --repo <owner>/<repo>
$ python3 -m unittest discover -s docs/pocket-guides/test-lab -v
$ python3 docs/pocket-guides/test-lab/validate_tests.py
```
