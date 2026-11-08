<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Apply the Method and Find a Command

## Read the Octo-Pod Example

Octo-Pod keeps its contribution process in `CONTRIBUTING.md`, private reporting in `SECURITY.md`, release behavior in `docs/release-pattern.md`, and its assessment in `docs/openssf-passing.md`. The reusable `docs/openssf-repository-checklist.md` records the individual Passing and versioned Baseline requirements.

The release-evidence work in PR 20 adds an exact-commit CI and CodeQL gate, tag-release fuzzing, and changelog extraction. Until the PR merges and the new release path runs, those changes are candidate controls, not proof of an operating release process. Read the current PR state and candidate tests before using it as evidence for another repository.

*Inspect the Worked Example*

```console
$ gh pr view 20 --repo USAREUR-AF/octo-pod
$ gh pr checks 20 --repo USAREUR-AF/octo-pod
$ python3 platform/tests/test_release_evidence.py
```

The lesson is transferable: name the claim, implement a check, demonstrate refusal for a broken condition, and connect the result to the release decision. Reuse the reasoning; adapt the tooling and applicability to each repository.

## Diagnose Assessment Gaps

| Observation | Action |
|---|---|
| SECURITY exists | Verify that its reporting route works and has an owner. |
| Branch protection scores well | Inspect actual bypasses and human review enforcement. |
| Tests report success | Inspect discovery, assertions, failures, and exact revision. |
| No current scanner alerts | Check scope, freshness, supported releases, and prior findings. |
| A response policy promises 14 days | Audit actual response timestamps for the required period. |
| A badge form accepts Met | Supply evidence; form acceptance is not technical verification. |
| Public repo is new | Preserve relevant inherited history in the assessment. |
| An exception was accepted | Recheck applicability and its expiry. |

## Sources and Review Triggers

The authoritative assessment sources are the [Baseline v2026.08.28 catalog](https://baseline.openssf.org/versions/2026-08-28), [Best Practices criteria](https://www.bestpractices.dev/en/criteria/0), and [Scorecard documentation](https://scorecard.dev/). Recheck them when the framework changes. Reassess the repository when ownership, access, workflows, dependencies, supported versions, release tooling, or security boundaries change.
