<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Governance

How decisions are made in this project, who holds which role, and how the project
continues when a person leaves.

## Goals

octo-pod is a site appliance that brings sensor feeds onto a GOAT data fabric and
delivers released fabric data to systems that can't join it. Decisions favor: nothing
leaves a site unless it is declared twice, one identity per purpose, shared implementation
with site values supplied at deployment, a host that converges itself, and failures that
produce explicit error reports.

## Decision-Making

- **Day-to-day technical decisions** are made by whoever does the work, through pull
  request review.
- **Architectural decisions** are recorded as ADRs in [docs/adr/](docs/adr/), with a row in
  [docs/adr/INDEX.md](docs/adr/INDEX.md) in the same pull request. ADRs are superseded,
  never rewritten.
- **Project decisions** (governance, license, scope, releases) are made by rough consensus
  among maintainers. A disagreement that discussion can't settle goes to the project lead.

## Roles and Responsibilities

[MAINTAINERS.md](MAINTAINERS.md) lists who holds each role.

| Role | Responsibilities |
|---|---|
| Contributor | Anyone who opens an issue or a pull request. Follows [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). |
| Code owner | A member of the team that [.github/CODEOWNERS](.github/CODEOWNERS) names. Reviews and approves pull requests against the [review standards](CONTRIBUTING.md#code-review). Never approves their own change. |
| Maintainer | A code owner who also triages issues, answers security reports under [SECURITY.md](SECURITY.md), keeps dependencies and documentation current, and takes part in project decisions. |
| Release approver | Approves a tagged release in the `release` environment before the release workflow publishes it. Can't approve a release whose tag they pushed. |
| Project lead | Settles a disagreement that the maintainers can't settle, and owns the roadmap and this document. |
| Administrator | Holds administrator access to the repository: settings, branch protection, environments and security advisories. Every administrator is also a maintainer. |

## Maintainers

New maintainers come from sustained, high-quality contributions and sound review
judgment, are nominated by an existing maintainer, and are confirmed when the
maintainers reach rough consensus with no objection within 7 days. A maintainer who
steps down, or who has been inactive for six months, moves to an emeritus list in
[MAINTAINERS.md](MAINTAINERS.md), and the remaining maintainers remove their access.

## Access Continuity

The project must keep working if any one person can't continue. Within a week of losing
any one maintainer, the others can still triage issues, review and merge changes, and
publish a release:

- At least three maintainers hold administrator access to the repository, so no setting,
  environment or advisory depends on one person.
- At least two people approve releases, and an approver can't approve their own tag. If
  one person is lost, another maintainer pushes the tag and the remaining approver
  approves it.
- Releases are signed with keyless Sigstore certificates issued to the release
  workflow. No long-lived signing key exists, so there is no key to hand over.
- Each maintainer signs commits and tags with their own key. A maintainer who leaves
  takes nothing that the project needs.
- Registry packages and documentation are published by the project's own workflows,
  not from a person's account.

The bus factor, the number of people who would have to leave before the project stalls,
is at least two.

## Account Security

Everyone with write access to this repository must use two-factor authentication on
their GitHub account; the organization requires it. Use a security key, a passkey or an
authenticator app (TOTP). Don't use SMS as your second factor, because a text message
can be intercepted or redirected to another phone. The same rule applies to anyone who
can read private vulnerability reports.

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License and Contributions

The project is licensed under Apache 2.0 (see [LICENSE](LICENSE)). Contributions are
accepted under that license with a Developer Certificate of Origin sign-off; see
[CONTRIBUTING.md](CONTRIBUTING.md).
