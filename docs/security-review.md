<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Security review

This page records each security review of octo-pod: when it happened, what it covered,
how it was done, and what changed as a result. Vulnerabilities are handled under
[SECURITY.md](../SECURITY.md). A finding that is still being fixed in private is not
listed here until its advisory is published.

## Review of November 2026

| | |
|---|---|
| Date | 2026-11-05 to 2026-11-06 |
| Release line | 0.5, reviewed on `main` after release 0.5.0 |
| Reviewers | The maintainers |
| Next review | Within 12 months, and before a 1.0 release |

### Scope

- The security requirements and trust boundaries in the
  [assurance case](assurance-case.md), checked against the code.
- The Rust arms and bridges, the CLI, the Ansible playbooks and templates, and the host
  firewall.
- The release workflow, the image workflow, the installer and the upgrade command: the
  path from a commit to a running pod.
- Third-party dependencies, the GitHub Actions that the workflows use, and the
  repository settings that protect `main` and release tags.

### Method

- **Manual review** of each trust boundary in the assurance case: who can send what
  across it, which mechanism decides, and which test proves that the mechanism refuses
  what it must refuse.
- **Static analysis** on every change: Clippy with warnings as errors, CodeQL, Semgrep
  (security audit, secrets and command injection rules), and ShellCheck on every shell
  script and rendered template.
- **Dynamic analysis**: ClusterFuzzLite runs five fuzz targets on each change to the arms
  (TCP framing, SAPIENT framing, SAPIENT messages, SAPIENT JSON and the downlink gate),
  and a longer batch run on a schedule.
- **Dependency checks**: `cargo-deny` (licenses, banned crates and sources),
  `cargo-audit`, `pip-audit`, and OpenSSF Scorecard's vulnerability check. Each advisory
  that the project accepts records its reason and an expiry date in `arms/deny.toml` and
  `arms/osv-scanner.toml`.
- **Secret scanning**: gitleaks over the full history on every change.
- **Supply chain checks**: the release signature, the SLSA provenance and the image
  provenance attestations, each verified with a negative control (a changed file and a
  wrong signer must both fail).

### Results

Changes made as a result of this review, all on `main`:

- Every GitHub Action is pinned by commit, and workflow tokens are read-only unless a job
  needs more.
- Each image carries a signed build provenance attestation, and the `signed-pins` check
  refuses an image pin that this repository's image workflow did not sign on `main`.
- Each release carries SLSA Build Level 3 provenance and a signature in the standard
  Sigstore bundle format.
- A release needs a signed tag that GitHub verifies, and an approval from a second
  person.
- The release signer is accepted in any letter case of the owner and repository names,
  so the published verification commands work as written.
- The uplink bridge pins Zenoh transport compression off, which keeps the decompression
  path named in RUSTSEC-2026-0041 unreachable on the leg that faces the fabric.
- The test dependencies require Jinja2 3.1.6 or later.
- The watchdog no longer reports a running arm as drift, and can no longer mistake a
  listening router for a silent one. A shell pipeline under `pipefail` failed when the
  name it searched for was found.
- Fuzzing found that a SAPIENT status interval shorter than one nanosecond was reported
  as zero instead of absent. It is fixed, and the input is a regression test.

Each accepted advisory, with its reason, is in `arms/deny.toml` and
`arms/osv-scanner.toml`. Remaining design risks are listed under
[Residual Risks](assurance-case.md#residual-risks).
