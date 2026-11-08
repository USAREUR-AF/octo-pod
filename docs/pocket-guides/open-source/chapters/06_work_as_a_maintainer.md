<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Work as a Repository Maintainer

## Accept Responsibility for the Whole Project

A maintainer decides what belongs in the project and preserves the ability of other people to use and change it. Writing code is part of the role. So are reviewing contributions, explaining decisions, keeping releases usable, responding to problems, and arranging continuity when you are unavailable.

Document the authority that comes with each role. A contributor can propose a change; a reviewer evaluates it; a maintainer may accept it; a release owner publishes an artifact; a security contact coordinates confidential reports. One person may hold several roles, but the decision and evidence should remain clear. A project maintainer is not automatically the operator of every user's deployment.

Before accepting the role, understand the product, its users, supported environments, trust boundaries, release procedure, recovery path, contribution policy, and current unresolved risks. Read actual code and failed tests alongside the documentation. Ask who can recover repository and package ownership if the usual maintainer is unavailable.

## Triage Without Promising a Fix You Cannot Deliver

Acknowledge a report, establish its impact, and ask for the smallest safe reproducer. Distinguish a defect, a support question, an enhancement, a duplicate, and a potential vulnerability. Move sensitive details into the private reporting process rather than asking for them in a public issue.

Assign a priority based on impact, exposure, affected users, and available mitigations. Explain the next decision: reproduce, accept, defer, reject, or request more information. An acknowledgement is useful even when you cannot immediately supply a fix.

Close an issue with a reason and a verifiable outcome. “Fixed” should identify the release that users can obtain, or clearly say that the fix has merged but is not yet released. “Migrated” should link a verified destination without implying resolution.

## Review for the Future Maintainer

Read the problem and acceptance criteria before the patch. Trace affected callers, formats, storage, permissions, and operational procedures. Verify that tests challenge the changed behavior and that documentation describes the result users will encounter.

A review should distinguish blockers from suggestions. Explain the consequence of a requested change and identify the evidence that would satisfy it. Avoid making contributors guess whether you are asking for correctness, project policy, or a personal preference.

Require independent review where the project's policy calls for it, especially for trust boundaries and releases. Do not turn an administrator override into routine practice. If an emergency exception is authorized, record who decided, why it was necessary, what was checked, and what must happen afterward.

You may decline a technically sound feature because its long-term maintenance cost exceeds the project's scope. Explain the boundary respectfully and suggest a narrower contribution when one is useful. Accepting code also accepts responsibility for its compatibility, documentation, dependencies, and future defects.

## Build a Sustainable Routine

Use a routine that matches the project's risk and capacity. The following is a starting point, not a universal service-level promise.

| Cadence | Maintainer Work |
|---|---|
| Each Change | Triage, review behavior, inspect tests, check compatibility, and preserve documentation. |
| Each Release | Verify the candidate, artifacts, notes, support decision, and recovery instructions. |
| Weekly | Review reports, failed automation, dependency alerts, stalled PRs, and private security work. |
| Periodically | Review access, credentials, exceptions, support commitments, dependency health, and restore evidence. |
| On Handover | Transfer documented ownership, verify access and recovery, and remove obsolete privileges. |

Publish realistic response expectations. A volunteer or small team should not imply continuous staffed support unless that arrangement exists. Provide a backup for security reports and releases, and explain where users should go when the project cannot provide help.

## Help Other People Become Maintainers

Give contributors bounded work, usable development instructions, timely review, and credit. Document decisions so knowledge does not remain in private conversations. Pair on difficult reviews and releases before granting wider privileges.

Grant access for a demonstrated responsibility, with a review date and an offboarding path. Do not make every regular contributor an administrator. Maintain an accurate list of people with sensitive access and establish how new maintainers are approved.

Your succession plan should cover repository ownership, package namespaces, signing and publication identities, domains, security contacts, and documentation. Practice the handover while the outgoing maintainer is available.

## End Support Deliberately

If the team can no longer maintain a release or repository, announce the support change, identify alternatives where appropriate, and preserve useful source and documentation. Remove misleading claims about security updates or response targets. A clear archived status serves users better than an unattended project that still promises active support.

**Evidence:** responsibilities have owners and backups, reports receive dispositions, reviews explain decisions, releases remain reproducible, and another maintainer can complete the documented workflow.
