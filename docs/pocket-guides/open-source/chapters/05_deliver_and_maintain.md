<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Deliver a Release You Can Support

## Treat CI as Privileged Software

*Continuous integration*, or CI, builds and tests changes automatically. Its workflows also process untrusted code and metadata. Give each job only the permissions it needs. Separate contributor tests from publishing jobs, and keep release credentials out of untrusted pull-request execution.

Pass external values as data through quoted environment variables or structured arguments. Do not interpolate issue titles, branch names, or other user-controlled strings into shell source. Pin dependencies and actions deliberately, review updates, and document any compatibility exception instead of suppressing a warning without explanation.

Protect the primary branch, require meaningful checks and independent review, and control who can change those rules. Check actual effective settings, including bypass actors. A CODEOWNERS file routes review; verify whether the platform requires that review.

*Inspect the Proposed Change*

```console
$ gh pr view <pr-number> --repo <owner>/<repo>
$ gh pr checks <pr-number> --repo <owner>/<repo>
$ gh run view <run-id> --repo <owner>/<repo>
```

## Build What Users Actually Install

Build in a clean supported environment from a recorded source revision and dependency set. Test the package, image, or binary that users receive, including install, startup, permissions, default configuration, upgrade, and removal. Source tests and package tests answer different questions.

A *software bill of materials*, or SBOM, records components in an artifact. Generate it from the shipped artifact or its actual build inputs, including operating-system packages where relevant. Check it against the artifact; merely uploading a file called `sbom.json` proves nothing about completeness.

*Provenance* records how an artifact was built. A signature authenticates a statement or artifact under a verification policy. A digest detects a content change when the expected digest is trusted. None establishes that the application is free of vulnerabilities. Use the [SLSA specification](https://slsa.dev/spec/v1.2/) to define the supply-chain claim you can actually support.

Require the relevant tests and analysis on the release candidate's exact source revision. Test verification with the correct artifact, a modified artifact, and the wrong signer identity. Never teach users to disable verification to get an install working.

## Explain the Upgrade

Give releases unique versions and human-written notes: features, fixes, breaking changes, migrations, support limits, and publicly disclosed vulnerability identifiers where applicable. A raw commit log makes users reconstruct the impact themselves.

Document rollback limits before the upgrade. A database migration may make an old binary unusable even when the old package is available. Prove restoration with a disposable copy of representative data; a backup file existing is not a restore test.

Separate development, release candidates, and supported stable versions. Document which branch accepts changes, how fixes reach supported releases, and when support ends. Avoid promising more release lines than the team can maintain.

## Operate the Public Project

Name maintainers, backups, decision rules, access-grant procedures, and an incident owner. Require strong account authentication, review access periodically, remove access during offboarding, and document credential rotation and recovery.

Publish a private vulnerability-reporting route, supported versions, and achievable response targets. Test the route without posting a real vulnerability publicly. Track receipt, acknowledgement, triage, fix, release, and disclosure dates privately. Publish coordinated advisories when appropriate; a public roadmap should not reveal embargoed work.

Plan for dependency advisories, license changes, compromised packages, unavailable registries, and abandoned dependencies. Set reviewable update and remediation policies, with documented applicability decisions and expiring exceptions. Do not dismiss a finding solely to improve a dashboard.

## Keep the Work Affordable

Automate repetitive checks, preserve useful templates, and avoid duplicating evidence across spreadsheets. Run fast checks on each change, expensive tests where their risk justifies the cost, and complete candidate checks before release. Document the gaps created by any reduced test scope.

Review documentation and support commitments regularly. Watch issue response times, flaky tests, stale exceptions, dependency drift, and restoration failures. Archive or transfer a project honestly if maintenance stops. Open source creates an ongoing relationship with users, not a one-time visibility task.
