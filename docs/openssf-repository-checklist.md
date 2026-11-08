<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# OpenSSF checklist for an organization repository

Copy this file for each repository. This is an assessment worksheet, not a badge or a certification.
It covers the 67 Passing criteria and all 64 OSPS Baseline controls in v2026.08.28, assessed separately below. Silver and Gold require separate assessments.
Recheck the [current criteria](https://www.bestpractices.dev/en/criteria/0) before submitting.

## Repository record

- Repository / public URL:
- Assessed commit and date:
- Product/release scope (including shipped dependencies and delegated services):
- Maintainer and reviewer:
- Badge project URL / entry language:
- License(s) and implementation languages:
- CPE: leave blank unless an actual assigned CPE is verified.
- Reporting-history window and evidence custodian:
- Next review date / trigger:

## How to use this checklist

1. Inventory public/private repositories and identify the maintained product, mirror, tutorial or archive.
2. For each row, record `Met`, `Unmet`, `?`, or `N/A` only where the live criterion permits it.
3. A checked box means the assessment is complete, not that the requirement is met. Replace `?` with an honest status and link evidence at the assessed revision. Record an owner and a concrete action for each gap.
4. Check actual CI results, release artifacts and repository settings; a workflow file or policy promise alone is insufficient.
5. Keep embargoed findings, reporter details and credentials in the private security project/advisories. Public justifications should cite safe policy and aggregate evidence, never private issue titles or operational data.
6. Human knowledge, responsiveness and remediation claims need a maintainer attestation or historical records. Do not infer them from generated documentation, a clean scan or repository age.
7. Use narrowly justified N/A values. Optional/SUGGESTED gaps may not prevent Passing; use the badge site's current evaluation rather than inventing a percentage.
8. Review and submit the badge entry as an authorized maintainer; retain the submission date and reassess after material changes.

## Repository setup and release verification

- [ ] Public repository/license/issue access verified anonymously; private findings stay private.
- [ ] One authoritative static-analysis setup; no conflicting default/advanced CodeQL uploads.
- [ ] Tests, lint, dependency and redacted secret scans succeed on the assessed commit.
- [ ] Protected default/release branches, real independent review, and justified bypass policy recorded.
- [ ] Release candidate has recorded static and dynamic analysis before publication.
- [ ] Human-written versioned changes, upgrade impact and applicable public vulnerability IDs are in release notes.
- [ ] Release signatures/checksums/provenance verified using negative controls where supported.
- [ ] Installed/shipped defaults match the reviewed crypto, authentication and input-validation evidence.
- [ ] Owner verifies historical response/remediation and developer-knowledge criteria privately.

## Passing criteria

Fill the evidence and follow-up cells; do not place private links in a publicly committed copy.


### Basics

| Assessed | Criterion | Level | Check | Status | Evidence / date | Owner / follow-up |
|---|---|---|---|---|---|---|
| [ ] | `description_good` | MUST | Describe the software purpose and the problem it solves. | ? | | |
| [ ] | `interact` | MUST | Link obtaining/installing, ordinary feedback, and contributing instructions. | ? | | |
| [ ] | `contribution` | MUST | Document the contribution process and give its public URL. | ? | | |
| [ ] | `contribution_requirements` | SHOULD | Document acceptable contributions, coding standards, review and tests; give a URL. | ? | | |
| [ ] | `floss_license` | MUST | Verify the project and distributed components have FLOSS licenses. | ? | | |
| [ ] | `floss_license_osi` | SUGGESTED | Use OSI-approved licenses where required. | ? | | |
| [ ] | `license_location` | MUST | Publish actual license texts in standard source locations; give URLs. | ? | | |
| [ ] | `documentation_basics` | MUST | Provide usable installation and basic-use documentation; justify any allowed N/A. | ? | | |
| [ ] | `documentation_interface` | MUST | Document external inputs, outputs, configuration and interfaces; justify any allowed N/A. | ? | | |
| [ ] | `sites_https` | MUST | Verify project, source and download endpoints support HTTPS/TLS. | ? | | |
| [ ] | `discussion` | MUST | Provide searchable public discussions with stable URLs and open participation. | ? | | |
| [ ] | `english` | SHOULD | Provide English documentation and accept English reports/comments. | ? | | |
| [ ] | `maintained` | MUST | Show current maintenance activity; do not confuse this with Scorecard's repository-age heuristic. | ? | | |

### Change control

| Assessed | Criterion | Level | Check | Status | Evidence / date | Owner / follow-up |
|---|---|---|---|---|---|---|
| [ ] | `repo_public` | MUST | Verify anonymous read access to the version-controlled source repository. | ? | | |
| [ ] | `repo_track` | MUST | Preserve who changed what and when in version-control history. | ? | | |
| [ ] | `repo_interim` | MUST | Publish reviewable interim changes and PRs between releases. | ? | | |
| [ ] | `repo_distributed` | SUGGESTED | Use a common distributed VCS such as Git. | ? | | |
| [ ] | `version_unique` | MUST | Give each user release a unique version and refuse accidental reuse. | ? | | |
| [ ] | `version_semver` | SUGGESTED | Document and follow SemVer or CalVer, including applicable micro versions. | ? | | |
| [ ] | `version_tags` | SUGGESTED | Identify releases in the VCS, normally with tags. | ? | | |
| [ ] | `release_notes` | MUST | Provide human-readable changes and upgrade impact for each release; raw commit logs do not suffice. | ? | | |
| [ ] | `release_notes_vulns` | MUST | Identify publicly known project vulnerabilities fixed in each release using identifiers known then; use N/A only under the criterion's rules, not to conceal private reports. | ? | | |

### Reporting

| Assessed | Criterion | Level | Check | Status | Evidence / date | Owner / follow-up |
|---|---|---|---|---|---|---|
| [ ] | `report_process` | MUST | Publish a bug-reporting process and URL, separate from confidential vulnerability reporting. | ? | | |
| [ ] | `report_tracker` | SHOULD | Use an issue tracker for individual reports. | ? | | |
| [ ] | `report_responses` | MUST | Review a 2–12 month report window and demonstrate acknowledgement of the majority of bug reports; record counts and dates. | ? | | |
| [ ] | `enhancement_responses` | SHOULD | Demonstrate responses to more than half of enhancement requests in the selected 2–12 month window. | ? | | |
| [ ] | `report_archive` | MUST | Provide a searchable public archive of ordinary reports/responses; give its URL. | ? | | |
| [ ] | `vulnerability_report_process` | MUST | Publish an actionable vulnerability reporting process and URL. | ? | | |
| [ ] | `vulnerability_report_private` | MUST | Document and verify a private reporting channel; avoid public report details and justify any allowed N/A. | ? | | |
| [ ] | `vulnerability_report_response` | MUST | Privately audit all reports received in the last six months: initial response within 14 days; a policy target alone is not evidence. | ? | | |

### Quality

| Assessed | Criterion | Level | Check | Status | Evidence / date | Owner / follow-up |
|---|---|---|---|---|---|---|
| [ ] | `build` | MUST | Demonstrate a working automated source build for software that needs building. | ? | | |
| [ ] | `build_common_tools` | SUGGESTED | Use documented common build tools. | ? | | |
| [ ] | `build_floss_tools` | SHOULD | Prove that building requires only FLOSS tools; hosted services may be optional conveniences. | ? | | |
| [ ] | `test` | MUST | Publish a FLOSS automated test suite and instructions to run it. | ? | | |
| [ ] | `test_invocation` | SHOULD | Use standard language test commands where practical. | ? | | |
| [ ] | `test_most` | SUGGESTED | Measure branches, input fields and functionality; explain exclusions and integration gaps rather than citing only a headline percentage. | ? | | |
| [ ] | `test_continuous_integration` | SUGGESTED | Run automated tests on integrated code and proposed changes. | ? | | |
| [ ] | `test_policy` | MUST | Require automated tests for major new functionality. | ? | | |
| [ ] | `tests_are_added` | MUST | Inspect recent major changes and link the tests actually added for them. | ? | | |
| [ ] | `tests_documented_added` | SUGGESTED | Put the new-functionality test policy in contribution instructions. | ? | | |
| [ ] | `warnings` | MUST | Enable available compiler warnings, safe language modes or linters. | ? | | |
| [ ] | `warnings_fixed` | MUST | Fix warnings or document justified exclusions; do not disable checks to pass. | ? | | |
| [ ] | `warnings_strict` | SUGGESTED | Use strict warning settings where practical and record justified exceptions. | ? | | |

### Security

| Assessed | Criterion | Level | Check | Status | Evidence / date | Owner / follow-up |
|---|---|---|---|---|---|---|
| [ ] | `know_secure_design` | MUST | A primary developer attests to the secure-design knowledge specified in the detailed criterion; documentation or AI review cannot substitute for a human. | ? | | |
| [ ] | `know_common_errors` | MUST | A primary developer attests to relevant vulnerability classes and at least one mitigation for each. | ? | | |
| [ ] | `crypto_published` | MUST | Inventory default crypto protocols/algorithms and verify they are public and expert-reviewed, or justify N/A. | ? | | |
| [ ] | `crypto_call` | SHOULD | Use established cryptographic implementations instead of implementing primitives unless that is the project's purpose. | ? | | |
| [ ] | `crypto_floss` | MUST | Ensure every crypto-dependent feature can use FLOSS implementations. | ? | | |
| [ ] | `crypto_keylength` | MUST | Check generated and accepted keys against the criterion's NIST minimums; prove weaker sizes can be disabled. | ? | | |
| [ ] | `crypto_working` | MUST | Exclude broken algorithms/modes by default; document narrowly applicable interoperability exceptions and mitigations. | ? | | |
| [ ] | `crypto_weaknesses` | SHOULD | Avoid defaults with serious known cryptographic weaknesses. | ? | | |
| [ ] | `crypto_pfs` | SHOULD | Verify forward secrecy in supported key-agreement paths; document scope and exceptions. | ? | | |
| [ ] | `crypto_password_storage` | MUST | Use salted, stretched password hashes for external-user authentication; justify N/A if the project never stores such passwords. | ? | | |
| [ ] | `crypto_random` | MUST | Use a cryptographically secure RNG for all keys/nonces, including delegated library paths. | ? | | |
| [ ] | `delivery_mitm` | MUST | Protect delivery with HTTPS or SSH and verify the intended source. | ? | | |
| [ ] | `delivery_unsigned` | MUST | Never trust an unsigned hash fetched over HTTP; verify signatures before trusting artifact hashes. | ? | | |
| [ ] | `vulnerabilities_fixed_60_days` | MUST | Privately audit current product/dependency findings: no applicable unpatched medium-or-higher vulnerability public for over 60 days. | ? | | |
| [ ] | `vulnerabilities_critical_fixed` | SHOULD | Demonstrate rapid response/fixes for critical vulnerabilities; a policy alone is not retrospective proof. | ? | | |
| [ ] | `no_leaked_credentials` | MUST | Scan public content and history with redacted output, investigate findings and revoke any genuine exposed credential; never test a credential by using it. | ? | | |

### Analysis

| Assessed | Criterion | Level | Check | Status | Evidence / date | Owner / follow-up |
|---|---|---|---|---|---|---|
| [ ] | `static_analysis` | MUST | Run suitable FLOSS static analysis on each proposed major production release before publication; retain exact-revision evidence. | ? | | |
| [ ] | `static_analysis_common_vulnerabilities` | SUGGESTED | Enable rules for common vulnerabilities relevant to the languages/environment. | ? | | |
| [ ] | `static_analysis_fixed` | MUST | Privately verify timely remediation of confirmed exploitable medium-or-higher static findings; justify any allowed N/A from actual history. | ? | | |
| [ ] | `static_analysis_often` | SUGGESTED | Run static source analysis on every commit or at least daily. | ? | | |
| [ ] | `dynamic_analysis` | SUGGESTED | Run suitable dynamic analysis before each proposed major production release, with exact-revision evidence. | ? | | |
| [ ] | `dynamic_analysis_unsafe` | SUGGESTED | For memory-unsafe project code, routinely combine dynamic analysis with memory-error detection; justify N/A only after defining source/FFI scope. | ? | | |
| [ ] | `dynamic_analysis_enable_assertions` | SUGGESTED | Enable extensive assertions in at least some testing/fuzzing configurations. | ? | | |
| [ ] | `dynamic_analysis_fixed` | MUST | Privately verify timely remediation of confirmed exploitable medium-or-higher dynamic findings; justify any allowed N/A from actual history. | ? | | |

## Release and reassessment log

| Date | Commit/release | Assessor | Open MUST gaps | Private evidence reviewed by | Badge entry updated | Next action |
|---|---|---|---|---|---|---|
| | | | | | | |

## Completion decision

- [ ] Every criterion has a supported disposition and required URLs/justifications.
- [ ] No unknown or unmet MUST requirement is presented as met.
- [ ] No private vulnerability material, credentials, or invented approvals appear in the entry.
- [ ] An authorized maintainer reviewed the proposed answers and accepted the site's submission terms.
- [ ] The badge's actual status is checked after submission; no passing badge is advertised early.

Scorecard is separate: its repository-age and historical-review scores are not the Passing questionnaire's maintenance and reporting criteria. An admin merge may be authorized without counting as an independent review. Do not rewrite history, weaken a useful control, or manufacture activity to improve a score.

## OSPS Baseline assessment (separate series)

Baseline v2026.08.28 has Levels 1, 2 and 3. Assess the lower levels before claiming a higher level; Passing is not a substitute for any Baseline level. Start with Level 1 for each repository and record higher-level gaps as follow-up work. Use the [versioned official catalog](https://baseline.openssf.org/versions/2026-08-28) for every applicable control; the 67 rows above cover Passing only.

| Target | Status | Evidence / control worksheet | Owner | Remaining work |
|---|---|---|---|---|
| Baseline Level 1, v2026.08.28 | ? | | | |
| Baseline Level 2, v2026.08.28 | ? | | | |
| Baseline Level 3, v2026.08.28 | ? | | | |

- [ ] Record each applicable OSPS control ID and its version separately from Passing criteria.
- [ ] Verify inherited lower-level controls and actual operating evidence.
- [ ] Confirm governance, access, risk-management and review practices with their human owners.
- [ ] Keep unknowns open; do not infer Level 3 from a high Scorecard score or a Passing badge.

The following control text is adapted from the OpenSSF Security Baseline contributors’ [versioned checklist](https://github.com/ossf/security-baseline/blob/main/docs/versions/2026-08-28-checklist.md), under [Apache-2.0](https://github.com/ossf/security-baseline/blob/main/LICENSE). Changes: HTML entities decoded and assessment/evidence/owner fields added. The official catalog contains the rationale, recommendations and applicability details.

### Baseline Level 1 controls

| Assessed | Control | Requirement | Status | Evidence / date | Owner / follow-up |
|---|---|---|---|---|---|
| [ ] | `OSPS-AC-01.01` | When a user attempts to read or modify a sensitive resource in the project's authoritative repository, the system MUST require the user to complete a multi-factor authentication process. | ? | | |
| [ ] | `OSPS-AC-02.01` | When a new collaborator is added, the version control system MUST require manual permission assignment, or restrict the collaborator permissions to the lowest available privileges by default. | ? | | |
| [ ] | `OSPS-AC-03.01` | When a direct commit is attempted on the project's primary branch, an enforcement mechanism MUST prevent the change from being applied. | ? | | |
| [ ] | `OSPS-AC-03.02` | When an attempt is made to delete the project's primary branch, the version control system MUST treat this as a sensitive activity and require explicit confirmation of intent. | ? | | |
| [ ] | `OSPS-BR-01.01` | When a CI/CD pipeline operates on untrusted metadata, those parameters MUST be sanitized and validated prior to use in the pipeline. | ? | | |
| [ ] | `OSPS-BR-01.03` | When a CI/CD pipeline operates on untrusted code snapshots, it MUST prevent access to privileged CI/CD credentials and assets. | ? | | |
| [ ] | `OSPS-BR-03.01` | When the project lists a URI as an official project channel, that URI MUST be exclusively delivered using encrypted channels. | ? | | |
| [ ] | `OSPS-BR-03.02` | When the project lists a URI as an official distribution channel, that channel MUST be protected from adversary-in-the-middle attacks using cryptographically authenticated channels. | ? | | |
| [ ] | `OSPS-BR-07.01` | The project MUST prevent the unintentional storage of unencrypted sensitive data, such as secrets and credentials, in the version control system. | ? | | |
| [ ] | `OSPS-DO-01.01` | When the project has made a release, the project documentation MUST include user guides for all basic functionality. | ? | | |
| [ ] | `OSPS-DO-02.01` | When the project has made a release, the project documentation MUST include a guide for reporting defects. | ? | | |
| [ ] | `OSPS-GV-02.01` | The project MUST have one or more mechanisms for public discussions about proposed changes and usage obstacles. | ? | | |
| [ ] | `OSPS-GV-03.01` | The project documentation MUST include an explanation of the  contribution process, or clearly state that public contributions are not accepted | ? | | |
| [ ] | `OSPS-LE-02.01` | The license for the source code MUST meet the OSI Open Source Definition or the FSF Free Software Definition. | ? | | |
| [ ] | `OSPS-LE-02.02` | The license for the released software assets MUST meet the OSI Open Source Definition or the FSF Free Software Definition. | ? | | |
| [ ] | `OSPS-LE-03.01` | The license for the source code MUST be maintained in the corresponding repository's LICENSE file, COPYING file, LICENSES/ directory, or LICENSE/ directory. | ? | | |
| [ ] | `OSPS-LE-03.02` | The license for the released software assets MUST be included in the released source code, or in a LICENSE file, COPYING file, or LICENSE/ directory alongside the corresponding release assets. | ? | | |
| [ ] | `OSPS-QA-01.01` | The project's source code repository MUST be publicly readable at a static URL. | ? | | |
| [ ] | `OSPS-QA-01.02` | The version control system MUST contain a publicly readable record of all changes made, who made the changes, and when the changes were made. | ? | | |
| [ ] | `OSPS-QA-02.01` | When the package management system supports it, the source code repository MUST contain a dependency list that accounts for the direct language dependencies. | ? | | |
| [ ] | `OSPS-QA-04.01` | Projects with multiple repositories MUST document a list of codebases that are part of the project. | ? | | |
| [ ] | `OSPS-QA-05.01` | The version control system MUST NOT contain generated executable artifacts. | ? | | |
| [ ] | `OSPS-QA-05.02` | The version control system MUST NOT contain unreviewable binary artifacts. | ? | | |
| [ ] | `OSPS-VM-02.01` | The project documentation MUST contain security contacts. | ? | | |

### Baseline Level 2 controls

| Assessed | Control | Requirement | Status | Evidence / date | Owner / follow-up |
|---|---|---|---|---|---|
| [ ] | `OSPS-AC-04.01` | When a CI/CD task is executed with no permissions specified, the CI/CD system MUST default the task's permissions to the lowest permissions granted in the pipeline. | ? | | |
| [ ] | `OSPS-BR-02.01` | When an official release is created, that release MUST be assigned a unique version identifier. | ? | | |
| [ ] | `OSPS-BR-04.01` | When an official release is created, that release MUST contain a descriptive log of functional and security modifications. | ? | | |
| [ ] | `OSPS-BR-05.01` | When a build and release pipeline ingests dependencies, it MUST use standardized tooling where available. | ? | | |
| [ ] | `OSPS-BR-06.01` | When an official release is created, that release MUST be signed or accounted for in a signed manifest including each asset's cryptographic hashes. | ? | | |
| [ ] | `OSPS-DO-06.01` | When the project has made a release, the project documentation MUST include a description of how the project selects, obtains, and tracks its dependencies. | ? | | |
| [ ] | `OSPS-DO-07.01` | The project documentation MUST include instructions on how to build the software, including required libraries, frameworks, SDKs, and dependencies. | ? | | |
| [ ] | `OSPS-GV-01.01` | The project documentation MUST include a list of project members with  access to sensitive resources. | ? | | |
| [ ] | `OSPS-GV-01.02` | The project documentation MUST include descriptions of the roles and  responsibilities for members of the project | ? | | |
| [ ] | `OSPS-GV-03.02` | The project documentation MUST include a guide for code contributors that includes requirements for acceptable contributions. | ? | | |
| [ ] | `OSPS-LE-01.01` | The version control system MUST require all code contributors to assert that they are legally authorized to make the associated contributions on every commit. | ? | | |
| [ ] | `OSPS-QA-03.01` | When a commit is made to the primary branch, any automated status checks for commits MUST pass or be manually bypassed. | ? | | |
| [ ] | `OSPS-QA-06.01` | Prior to a commit being accepted, the project's CI/CD pipelines MUST run at least one automated test suite to ensure the changes meet expectations. | ? | | |
| [ ] | `OSPS-SA-01.01` | When the project has made a release, the project documentation MUST include design documentation demonstrating all actions and actors within the system. | ? | | |
| [ ] | `OSPS-SA-02.01` | When the project has made a release, the project documentation MUST include descriptions of all external software interfaces of the released software assets. | ? | | |
| [ ] | `OSPS-SA-03.01` | When the project has made a release, the project MUST perform a security assessment to understand the most likely and impactful potential security problems that could occur within the software. | ? | | |
| [ ] | `OSPS-VM-01.01` | The project documentation MUST include a policy for coordinated vulnerability disclosure (CVD), with a clear timeframe for response. | ? | | |
| [ ] | `OSPS-VM-03.01` | The project documentation MUST provide a means for private vulnerability reporting directly to the security contacts within the project. | ? | | |
| [ ] | `OSPS-VM-04.01` | The project documentation MUST publicly publish data about discovered vulnerabilities. | ? | | |

### Baseline Level 3 controls

| Assessed | Control | Requirement | Status | Evidence / date | Owner / follow-up |
|---|---|---|---|---|---|
| [ ] | `OSPS-AC-04.02` | When a job is assigned permissions in a CI/CD pipeline, the source code or configuration MUST only assign the minimum privileges necessary for the corresponding activity. | ? | | |
| [ ] | `OSPS-BR-01.04` | CI/CD pipelines which accept trusted collaborator input MUST sanitize and validate that input prior to use in the pipeline. | ? | | |
| [ ] | `OSPS-BR-02.02` | When an official release is created, all assets within that release MUST be clearly associated with the release identifier or another unique identifier for the asset. | ? | | |
| [ ] | `OSPS-BR-07.02` | The project MUST define a policy for managing secrets and credentials used by the project. The policy should include guidelines for storing, accessing, and rotating secrets and credentials. | ? | | |
| [ ] | `OSPS-DO-03.01` | When the project has made a release, the project documentation MUST contain instructions to verify the integrity and authenticity of the release assets. | ? | | |
| [ ] | `OSPS-DO-03.02` | When the project has made a release, the project documentation MUST contain instructions to verify the expected identity of the person or process authoring the software release. | ? | | |
| [ ] | `OSPS-DO-04.01` | When the project has made a release, the project documentation MUST include a descriptive statement about the scope and duration of support for each release. | ? | | |
| [ ] | `OSPS-DO-05.01` | When the project has made a release, the project documentation MUST provide a descriptive statement when releases or versions will no longer receive security updates. | ? | | |
| [ ] | `OSPS-GV-04.01` | The project documentation MUST have a policy that code collaborators are reviewed prior to granting escalated permissions to sensitive resources. | ? | | |
| [ ] | `OSPS-QA-02.02` | When the project has made a release, all compiled released software assets MUST be delivered with a software bill of materials. | ? | | |
| [ ] | `OSPS-QA-04.02` | When the project has made a release comprising multiple source code repositories, all subprojects MUST enforce security requirements that are as strict or stricter than the primary codebase. | ? | | |
| [ ] | `OSPS-QA-06.02` | The project documentation MUST clearly document when and how tests are run. | ? | | |
| [ ] | `OSPS-QA-06.03` | The project documentation MUST include a policy that all major changes to the software produced by the project should add or update tests of the functionality in an automated test suite. | ? | | |
| [ ] | `OSPS-QA-07.01` | When a commit is made to the primary branch, the project's version control system MUST require at least one non-author human approval of the changes before merging. | ? | | |
| [ ] | `OSPS-SA-03.02` | When the project has made a release, the project MUST perform a threat modeling and attack surface analysis to understand and protect against attacks on critical code paths, functions, and interactions within the system. | ? | | |
| [ ] | `OSPS-VM-04.02` | Any vulnerabilities in the software components not affecting the project MUST be accounted for in a VEX document, augmenting the vulnerability report with non-exploitability details. | ? | | |
| [ ] | `OSPS-VM-05.01` | The project documentation MUST include a policy that defines a threshold for remediation of SCA findings related to vulnerabilities and licenses. | ? | | |
| [ ] | `OSPS-VM-05.02` | The project documentation MUST include a policy to address SCA violations prior to any release. | ? | | |
| [ ] | `OSPS-VM-05.03` | All changes to the project's codebase MUST be automatically evaluated against a documented policy for malicious dependencies and known vulnerabilities in dependencies, then blocked in the event of violations, except when declared and suppressed as non-exploitable. | ? | | |
| [ ] | `OSPS-VM-06.01` | The project documentation MUST include a policy that defines a threshold for remediation of SAST findings. | ? | | |
| [ ] | `OSPS-VM-06.02` | All changes to the project's codebase MUST be automatically evaluated against a documented policy for security weaknesses and blocked in the event of violations except when declared and suppressed as non-exploitable. | ? | | |
