# Governance

How decisions are made in this project.

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

## Maintainers

New maintainers come from sustained, high-quality contributions and sound review
judgment, are nominated by an existing maintainer, and are confirmed when the
maintainers reach rough consensus with no objection within 7 days. The roster is in
[.github/CODEOWNERS](.github/CODEOWNERS).

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License and Contributions

The project is licensed under Apache 2.0 (see [LICENSE](LICENSE)). Contributions are
accepted under that license with a Developer Certificate of Origin sign-off; see
[CONTRIBUTING.md](CONTRIBUTING.md).
