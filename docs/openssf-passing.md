<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# OpenSSF Passing evidence worksheet

Assessment date: 2026-11-08. Public source baseline: `fe48297e2c7932e85b7f586b6aa20b9964385a53`.
Badge entry: [octo-pod 16460](https://www.bestpractices.dev/en/projects/16460).
This is a draft evidence assessment, not a certification or completed badge submission.

`Supported` means evidence was found for a proposed answer. `Pending` means a repository change or operational verification is still required. `?` requires further evidence or a human attestation. Do not copy all rows as Met. Links to changed files describe this proposed revision until merged; recheck the assessed commit and actual CI results before submitting.

Use the [reusable organization checklist](openssf-repository-checklist.md) for other repositories. The [official Passing criteria](https://www.bestpractices.dev/en/criteria/0) control the final answers and permitted N/A values.

## Remaining work and owners

- Release maintainers: merge and verify the new release gates; verify the first release using the new workflow. Existing v0.5.0 and v0.6.0 release pages were updated from their immutable tags' changelogs, preserving verification instructions and assets.
- Security maintainers: review the complete cryptographic and password-storage inventory, then confirm supported defaults and applicable exceptions.
- Private tracker custodian: audit response and remediation histories, including inherited project reports. Publish only safe aggregate evidence; retain details privately.
- A primary developer: provide dated secure-design and common-error knowledge attestations against the detailed criteria.
- Badge owner: review all answers and submission terms; submit only supported answers. Track remaining work in [issue 19](https://github.com/USAREUR-AF/octo-pod/issues/19).

## Evidence

Current baseline CI: [run 42516375414](https://github.com/USAREUR-AF/octo-pod/actions/runs/42516375414).
Default CodeQL: [run 42516375456](https://github.com/USAREUR-AF/octo-pod/actions/runs/42516375456).
Successful runs establish behavior at that revision, not a guarantee about future changes or every release.


### Basics

| Criterion | Assessment | Evidence / remaining action |
|---|---|---|
| `description_good` | Supported | [README](../README.md), [operations](operations.md), [deployment](deployment.md) and [contributing](../CONTRIBUTING.md) document purpose, use, configuration and feedback. |
| `interact` | Supported | [README](../README.md), [operations](operations.md), [deployment](deployment.md) and [contributing](../CONTRIBUTING.md) document purpose, use, configuration and feedback. |
| `contribution` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md) describes PRs, acceptable changes, standards and tests. This change makes ordinary reporting and English participation explicit. |
| `contribution_requirements` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md) describes PRs, acceptable changes, standards and tests. This change makes ordinary reporting and English participation explicit. |
| `floss_license` | Supported | [LICENSE](../LICENSE), [LICENSES](../LICENSES) and dependency license checks document Apache-2.0 and bundled license obligations. |
| `floss_license_osi` | Supported | [LICENSE](../LICENSE), [LICENSES](../LICENSES) and dependency license checks document Apache-2.0 and bundled license obligations. |
| `license_location` | Supported | [LICENSE](../LICENSE), [LICENSES](../LICENSES) and dependency license checks document Apache-2.0 and bundled license obligations. |
| `documentation_basics` | Supported | [README](../README.md), [operations](operations.md), [deployment](deployment.md) and [contributing](../CONTRIBUTING.md) document purpose, use, configuration and feedback. |
| `documentation_interface` | Supported | [README](../README.md), [operations](operations.md), [deployment](deployment.md) and [contributing](../CONTRIBUTING.md) document purpose, use, configuration and feedback. |
| `sites_https` | Supported | The [public Git repository](https://github.com/USAREUR-AF/octo-pod), [issues](https://github.com/USAREUR-AF/octo-pod/issues) and [PRs](https://github.com/USAREUR-AF/octo-pod/pulls) provide HTTPS access, searchable discussion and interim version history. |
| `discussion` | Supported | The [public Git repository](https://github.com/USAREUR-AF/octo-pod), [issues](https://github.com/USAREUR-AF/octo-pod/issues) and [PRs](https://github.com/USAREUR-AF/octo-pod/pulls) provide HTTPS access, searchable discussion and interim version history. |
| `english` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md) describes PRs, acceptable changes, standards and tests. This change makes ordinary reporting and English participation explicit. |
| `maintained` | Supported | [Recent commits](https://github.com/USAREUR-AF/octo-pod/commits/main/) and [releases](https://github.com/USAREUR-AF/octo-pod/releases) demonstrate maintenance. Scorecard repository age is a separate heuristic. |

### Change control

| Criterion | Assessment | Evidence / remaining action |
|---|---|---|
| `repo_public` | Supported | The [public Git repository](https://github.com/USAREUR-AF/octo-pod), [issues](https://github.com/USAREUR-AF/octo-pod/issues) and [PRs](https://github.com/USAREUR-AF/octo-pod/pulls) provide HTTPS access, searchable discussion and interim version history. |
| `repo_track` | Supported | The [public Git repository](https://github.com/USAREUR-AF/octo-pod), [issues](https://github.com/USAREUR-AF/octo-pod/issues) and [PRs](https://github.com/USAREUR-AF/octo-pod/pulls) provide HTTPS access, searchable discussion and interim version history. |
| `repo_interim` | Supported | The [public Git repository](https://github.com/USAREUR-AF/octo-pod), [issues](https://github.com/USAREUR-AF/octo-pod/issues) and [PRs](https://github.com/USAREUR-AF/octo-pod/pulls) provide HTTPS access, searchable discussion and interim version history. |
| `repo_distributed` | Supported | The [public Git repository](https://github.com/USAREUR-AF/octo-pod), [issues](https://github.com/USAREUR-AF/octo-pod/issues) and [PRs](https://github.com/USAREUR-AF/octo-pod/pulls) provide HTTPS access, searchable discussion and interim version history. |
| `version_unique` | Supported | [Release process](../CONTRIBUTING.md#releases), VERSION and signed version tags identify SemVer releases. |
| `version_semver` | Supported | [Release process](../CONTRIBUTING.md#releases), VERSION and signed version tags identify SemVer releases. |
| `version_tags` | Supported | [Release process](../CONTRIBUTING.md#releases), VERSION and signed version tags identify SemVer releases. |
| `release_notes` | Pending | Existing release pages now include their tag-specific changelog sections. This change makes future release pages include them and refuses missing/empty/duplicate sections. Verify the first release using this gate. |
| `release_notes_vulns` | ? | Maintainer must reconcile release-time public project advisory identifiers with each release. An empty current advisory list alone does not prove historical absence; do not expose embargoed findings. |

### Reporting

| Criterion | Assessment | Evidence / remaining action |
|---|---|---|
| `report_process` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md#before-you-start) gives the ordinary bug-reporting channel and required information. |
| `report_tracker` | Supported | The [public Git repository](https://github.com/USAREUR-AF/octo-pod), [issues](https://github.com/USAREUR-AF/octo-pod/issues) and [PRs](https://github.com/USAREUR-AF/octo-pod/pulls) provide HTTPS access, searchable discussion and interim version history. |
| `report_responses` | ? | Audit a defined 2–12 month window, including inherited reports: record eligible counts, acknowledgements and dates. Repository creation date does not erase earlier project history. |
| `enhancement_responses` | ? | Audit a defined 2–12 month window, including inherited reports: record eligible counts, acknowledgements and dates. Repository creation date does not erase earlier project history. |
| `report_archive` | Supported | The [public Git repository](https://github.com/USAREUR-AF/octo-pod), [issues](https://github.com/USAREUR-AF/octo-pod/issues) and [PRs](https://github.com/USAREUR-AF/octo-pod/pulls) provide HTTPS access, searchable discussion and interim version history. |
| `vulnerability_report_process` | Supported | [SECURITY.md](../SECURITY.md) documents private GitHub reporting and a fallback contact process. Private reporting was enabled at assessment. |
| `vulnerability_report_private` | Supported | [SECURITY.md](../SECURITY.md) documents private GitHub reporting and a fallback contact process. Private reporting was enabled at assessment. |
| `vulnerability_report_response` | ? | Private tracker custodian must confirm initial response within 14 days for every report received in the past six months, or document the criterion-permitted N/A. Policy targets alone are insufficient. |

### Quality

| Criterion | Assessment | Evidence / remaining action |
|---|---|---|
| `build` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md), [CI](../.github/workflows/ci.yml) and [release build](../tools/build-release.sh) use Cargo, Python, shell and FLOSS tools. Main CI succeeded at the assessed SHA; Clippy warnings fail CI, with ShellCheck and security analysis also applied. |
| `build_common_tools` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md), [CI](../.github/workflows/ci.yml) and [release build](../tools/build-release.sh) use Cargo, Python, shell and FLOSS tools. Main CI succeeded at the assessed SHA; Clippy warnings fail CI, with ShellCheck and security analysis also applied. |
| `build_floss_tools` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md), [CI](../.github/workflows/ci.yml) and [release build](../tools/build-release.sh) use Cargo, Python, shell and FLOSS tools. Main CI succeeded at the assessed SHA; Clippy warnings fail CI, with ShellCheck and security analysis also applied. |
| `test` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md), [CI](../.github/workflows/ci.yml) and [release build](../tools/build-release.sh) use Cargo, Python, shell and FLOSS tools. Main CI succeeded at the assessed SHA; Clippy warnings fail CI, with ShellCheck and security analysis also applied. |
| `test_invocation` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md), [CI](../.github/workflows/ci.yml) and [release build](../tools/build-release.sh) use Cargo, Python, shell and FLOSS tools. Main CI succeeded at the assessed SHA; Clippy warnings fail CI, with ShellCheck and security analysis also applied. |
| `test_most` | Supported with scope | [Rust coverage](../.github/workflows/coverage-rust.yml) and [Python coverage](../.github/workflows/coverage-python.yml) passed at the assessed SHA. Their thresholds and exclusions are explicit; measured source coverage does not establish every live-host or hardware behavior. |
| `test_continuous_integration` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md), [CI](../.github/workflows/ci.yml) and [release build](../tools/build-release.sh) use Cargo, Python, shell and FLOSS tools. Main CI succeeded at the assessed SHA; Clippy warnings fail CI, with ShellCheck and security analysis also applied. |
| `test_policy` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md) describes PRs, acceptable changes, standards and tests. This change makes ordinary reporting and English participation explicit. |
| `tests_are_added` | Supported | [PR 17](https://github.com/USAREUR-AF/octo-pod/pull/17) includes deprovision regression tests; this change adds release-gate negative controls and extraction tests. Retain evidence for each subsequent major change. |
| `tests_documented_added` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md) describes PRs, acceptable changes, standards and tests. This change makes ordinary reporting and English participation explicit. |
| `warnings` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md), [CI](../.github/workflows/ci.yml) and [release build](../tools/build-release.sh) use Cargo, Python, shell and FLOSS tools. Main CI succeeded at the assessed SHA; Clippy warnings fail CI, with ShellCheck and security analysis also applied. |
| `warnings_fixed` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md), [CI](../.github/workflows/ci.yml) and [release build](../tools/build-release.sh) use Cargo, Python, shell and FLOSS tools. Main CI succeeded at the assessed SHA; Clippy warnings fail CI, with ShellCheck and security analysis also applied. |
| `warnings_strict` | Supported | [CONTRIBUTING.md](../CONTRIBUTING.md), [CI](../.github/workflows/ci.yml) and [release build](../tools/build-release.sh) use Cargo, Python, shell and FLOSS tools. Main CI succeeded at the assessed SHA; Clippy warnings fail CI, with ShellCheck and security analysis also applied. |

### Security

| Criterion | Assessment | Evidence / remaining action |
|---|---|---|
| `know_secure_design` | ? | A primary maintainer must attest to the knowledge in the detailed criterion, with relevant examples and date. AI-generated documentation cannot attest to a human’s knowledge. |
| `know_common_errors` | ? | A primary maintainer must attest to the knowledge in the detailed criterion, with relevant examples and date. AI-generated documentation cannot attest to a human’s knowledge. |
| `crypto_published` | Supported | [SECURITY.md](../SECURITY.md) inventories OpenSSL, rustls/ring and Sigstore: published protocols implemented by FLOSS cryptographic libraries rather than custom primitives. |
| `crypto_call` | Supported | [SECURITY.md](../SECURITY.md) inventories OpenSSL, rustls/ring and Sigstore: published protocols implemented by FLOSS cryptographic libraries rather than custom primitives. |
| `crypto_floss` | Supported | [SECURITY.md](../SECURITY.md) inventories OpenSSL, rustls/ring and Sigstore: published protocols implemented by FLOSS cryptographic libraries rather than custom primitives. |
| `crypto_keylength` | ? | [SECURITY.md](../SECURITY.md) records intended algorithms, key sizes and TLS behavior. Complete a reviewed source/runtime inventory of generated and accepted keys, delegated libraries, nonce generation and all supported transport paths before certifying defaults. |
| `crypto_working` | ? | [SECURITY.md](../SECURITY.md) records intended algorithms, key sizes and TLS behavior. Complete a reviewed source/runtime inventory of generated and accepted keys, delegated libraries, nonce generation and all supported transport paths before certifying defaults. |
| `crypto_weaknesses` | ? | [SECURITY.md](../SECURITY.md) records intended algorithms, key sizes and TLS behavior. Complete a reviewed source/runtime inventory of generated and accepted keys, delegated libraries, nonce generation and all supported transport paths before certifying defaults. |
| `crypto_pfs` | ? | [SECURITY.md](../SECURITY.md) records intended algorithms, key sizes and TLS behavior. Complete a reviewed source/runtime inventory of generated and accepted keys, delegated libraries, nonce generation and all supported transport paths before certifying defaults. |
| `crypto_password_storage` | ? | Confirm whether any shipped component stores external-user authentication passwords. If none does, justify N/A with the scope; otherwise verify salted, stretched hashes. |
| `crypto_random` | ? | [SECURITY.md](../SECURITY.md) records intended algorithms, key sizes and TLS behavior. Complete a reviewed source/runtime inventory of generated and accepted keys, delegated libraries, nonce generation and all supported transport paths before certifying defaults. |
| `delivery_mitm` | Supported | [Release verification](release-pattern.md) uses HTTPS and verifies the Sigstore signature before trusting SHA256SUMS; [release tests](../platform/tests/test_release.py) exercise fail-closed trust controls. |
| `delivery_unsigned` | Supported | [Release verification](release-pattern.md) uses HTTPS and verifies the Sigstore signature before trusting SHA256SUMS; [release tests](../platform/tests/test_release.py) exercise fail-closed trust controls. |
| `vulnerabilities_fixed_60_days` | ? | Private security custodian must reconcile applicable findings, confirmation dates, public disclosure dates and fixes in supported releases. Empty scanner queues or a policy promise do not establish historical remediation. |
| `vulnerabilities_critical_fixed` | ? | Private security custodian must reconcile applicable findings, confirmation dates, public disclosure dates and fixes in supported releases. Empty scanner queues or a policy promise do not establish historical remediation. |
| `no_leaked_credentials` | Supported at assessment | Main CI gitleaks passed; the current secret-scanning alert queue was empty at assessment. Continue history scanning and investigate/revoke any real exposure; do not validate a leaked credential by using it. |

### Analysis

| Criterion | Assessment | Evidence / remaining action |
|---|---|---|
| `static_analysis` | Pending | CI already runs Semgrep and other static tools. The new [release gate](../tools/release-evidence.py) requires successful latest CI and default CodeQL runs at the exact main SHA; merge and retain release-run evidence before claiming this new enforcement is deployed. |
| `static_analysis_common_vulnerabilities` | Supported | [CI](../.github/workflows/ci.yml) runs security analysis on pushes/PRs; GitHub default CodeQL scans Actions, Python and Rust. Default setup is service configuration, not a checked-in codeql.yml. |
| `static_analysis_fixed` | ? | Private security custodian must reconcile applicable findings, confirmation dates, public disclosure dates and fixes in supported releases. Empty scanner queues or a policy promise do not establish historical remediation. |
| `static_analysis_often` | Supported | [CI](../.github/workflows/ci.yml) runs security analysis on pushes/PRs; GitHub default CodeQL scans Actions, Python and Rust. Default setup is service configuration, not a checked-in codeql.yml. |
| `dynamic_analysis` | Pending | [Batch fuzzing](../.github/workflows/cflite_batch.yml) uses AddressSanitizer; this change makes it a prerequisite for tag releases. Record a successful run of the new release path before claiming it is operating. |
| `dynamic_analysis_unsafe` | ? | Rust project code forbids unsafe code, but confirm the project-produced native/FFI scope before choosing N/A. Dependency code and sanitizers should be described separately. |
| `dynamic_analysis_enable_assertions` | Supported | [ClusterFuzzLite build](../.clusterfuzzlite/build.sh) enables debug assertions for cargo-fuzz targets. |
| `dynamic_analysis_fixed` | ? | Private security custodian must reconcile applicable findings, confirmation dates, public disclosure dates and fixes in supported releases. Empty scanner queues or a policy promise do not establish historical remediation. |
