<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Maintain the Practice After Assessment

## Make Private Reporting Reach a Person

Give reporters an actionable private channel, supported versions, response expectations, and a fallback. Assign backup responders. Verify routing and access with a harmless test report, agreed with the receiving team, rather than inventing a vulnerability or sending sensitive details through a public issue.

Keep the reporter's information, exploit details, affected deployments, and embargoed fixes in the private workflow. Track severity, affected versions, owner, stage, next action, and timestamps. A public project board is not an appropriate place for private finding titles.

Coordinate a fixed release, advisory, identifiers when appropriate, acknowledgements, and remediation instructions. Preserve the distinction between reported, confirmed, fixed in source, released, and deployed. Closing an issue at merge time can hide the fact that users still cannot obtain the fix.

## Maintain the Trust Chain

Inventory who can merge, change workflows, access secrets, publish packages, or approve releases. Use strong authentication, least privilege, protected branches, independent review, and a deliberate offboarding process. Test account recovery without weakening ordinary controls.

Define how secrets are obtained, stored, scoped, rotated, revoked, and audited. Prefer short-lived credentials when the platform supports them. Review the permissions of third-party automation and isolate untrusted contributors from privileged release infrastructure.

For releases, make identity verification usable. A user should be able to check both artifact integrity and the expected publisher or build process. State the trust root and refusal behavior. Do not claim a SLSA level solely because a provenance file exists; assess the applicable track and requirements in the [SLSA specification](https://slsa.dev/spec/v1.2/).

## Review Risks at Changes That Matter

A *threat model* describes what you protect, who can act, where trust changes, and how failure could cause harm. Draw the data flow, identify entry points and privileges, and trace threats to controls and tests. Update it when you add a transport, identity provider, privileged installer, storage backend, or trust relationship.

Keep the review grounded in deployed defaults and supported configurations. An encryption library's presence does not prove certificate identity checks, key strength, nonce handling, password storage, or transport coverage are correct.

Review dependency health and end-of-life dates, recurring scanner findings, response performance, restore tests, access grants, expired exceptions, and maintainer capacity. Track improvements that reduce risk rather than activity that merely raises a score.

## Submit Supported Answers

Complete the official entry with evidence and required URLs. Save progress, then have an authorized maintainer review the answers and submission terms. Preserve unknown or unmet answers honestly. Reassess after material changes and keep the visible badge consistent with the service's actual result.

Automated scoring can lag current settings or summarize a historical window. Inspect the underlying finding and dated run before changing the repository. Never manufacture reviews, activity, or harmless commits to change a score.
