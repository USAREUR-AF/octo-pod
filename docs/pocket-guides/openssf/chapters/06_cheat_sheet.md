<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Cheat Sheet

Run from the repository root. Replace the placeholders defined in the opening chapter. API reads may require additional permissions.

*Twenty Inspection and Test Commands*

```console
$ gh repo view <owner>/<repo>
$ gh api repos/<owner>/<repo>
$ gh api repos/<owner>/<repo>/rulesets
$ gh api repos/<owner>/<repo>/branches/<branch>/protection
$ gh api repos/<owner>/<repo>/contents/SECURITY.md
$ gh api repos/<owner>/<repo>/contents/CONTRIBUTING.md
$ gh api repos/<owner>/<repo>/contents/LICENSE
$ gh api repos/<owner>/<repo>/private-vulnerability-reporting
$ git rev-parse HEAD
$ git log -1 --show-signature
$ gh pr view <pr-number> --repo <owner>/<repo>
$ gh pr checks <pr-number> --repo <owner>/<repo>
$ gh run list --repo <owner>/<repo>
$ gh run view <run-id> --repo <owner>/<repo>
$ gh api repos/<owner>/<repo>/commits/<sha>/check-runs
$ gh api --paginate repos/<owner>/<repo>/releases
$ gh release view <tag> --repo <owner>/<repo>
$ git diff --check
$ python3 -m unittest discover -s docs/pocket-guides/test-lab -v
$ python3 docs/pocket-guides/test-lab/validate_tests.py
```
