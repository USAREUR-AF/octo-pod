# Contributing to octo-pod

Thanks for helping. This guide covers how to set up, how to shape a change so it can be
merged, and how releases are cut. Report security issues privately, as
[SECURITY.md](SECURITY.md) describes, never in a public issue.

## Before You Start

Open an issue before a large change, so the approach is agreed before the work. Small
fixes can go straight to a pull request.

## First-Time Setup

Every commit on `main` is signed, and every commit carries a Developer Certificate of
Origin sign-off.

Sign your commits with SSH (or GPG), using a key registered with GitHub as a signing key:

```zsh
git config commit.gpgsign true
```

Add the sign-off to each commit with `-s`. It certifies the
[Developer Certificate of Origin](https://developercertificate.org/): that you wrote the
change, or have the right to submit it under the project's license:

```zsh
git commit -s
```

## Branches

Branch from `main`, name the branch `<type>/<slug>` (for example `fix/firewall-relay-ports`),
and keep it short-lived. Pull requests target `main` and are squash-merged; stacked pull
requests aren't accepted.

## Commits and Pull Request Titles

The pull request title becomes the commit on `main`, and it follows
[Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <subject>
```

- Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `ci`, `build`,
  `revert`, `adr`. Add `!` after the type or scope for a breaking change.
- Scope: one, lowercase (`a-z 0-9 . / _ -`), no commas. For example `egress`, `firewall`,
  `release`, `arms`, `docs`.
- Subject: what changes, in the present tense, within 100 characters in all.

Examples:

```text
feat(egress): serve a destination that dials the pod
fix(firewall): admit the mesh's relay ports on the WAN leg
docs(readme): say where the slot bundle comes from
```

## Run the Checks

Run what CI runs before you push. The Rust checks run from `arms/`.

Check the formatting:

```zsh
cargo fmt --all --check
```

Lint with warnings as errors:

```zsh
cargo clippy --workspace --all-targets -- -D warnings
```

Run the Rust tests:

```zsh
cargo test --workspace --locked
```

The platform checks run from `platform/`. Run the template and CLI tests:

```zsh
python3 tests/test_templates.py
```

Run the release tests:

```zsh
python3 tests/test_release.py
```

A change to a shell script also needs `shellcheck` clean; CI fails on any shell script it
doesn't lint.

## Pull Requests

Fill in the template: what changed and why, how you tested it, and any known gaps. Keep
one concern per pull request. Reviewers come from [.github/CODEOWNERS](.github/CODEOWNERS);
a change to the firewall, the release gates, identity or signing needs a second
reviewer.

Never commit site values: addresses, hostnames, enrollment output, keys or tokens. Site
values belong in an operator's ignored identity files, and the examples use
documentation addresses (RFC 5737) only.

## Decisions

Record an architectural decision as an ADR in [docs/adr/](docs/adr/), from
[the template](docs/adr/TEMPLATE.md), with a row in [the index](docs/adr/INDEX.md) in the same
pull request.

## Releases

Maintainers cut releases. The version lives in `VERSION` and follows
[Semantic Versioning](https://semver.org/); while it is below 1.0, a minor version may
break compatibility. A release is a signed tag `octo-pod-v<version>`, and a release
candidate is `octo-pod-v<version>-rc.<n>`. The release workflow refuses a tag that
differs from `VERSION`, builds the artifacts, signs `SHA256SUMS` and publishes them with
the changelog entry.

## License

By contributing, you agree that your contribution is licensed under the Apache License,
Version 2.0 (see [LICENSE](LICENSE)).

## Layout

| | |
|---|---|
| `bin/` | the CLI |
| `platform/` | Ansible playbooks and templates. Identical on every pod, replaced on upgrade |
| `arms/` | the Rust ingest, egress, SAPIENT session and health binaries |
| `platform/templates/pen/` | Quadlet units for the router and audit sinks |
| `platform/templates/arm/` | Quadlet units for ingest arms |
| `platform/templates/bridge/` and `platform/templates/egress/` | Uplink/downlink bridge and egress units |
| `tools/` | `mint-key.sh`, `install-to-pod.sh`, `deprovision-pod.sh`, release builders |
| `docs/` | design notes and the reasoning |
| `/etc/octo-pod` | **the pod's identity**. Written by `join` or `init`, never touched by an upgrade |

`mint-key.sh`, `install-to-pod.sh` and `deprovision-pod.sh` run from a clone on your laptop,
against a pod. `octo-pod remote <install|deprovision|mint-key>` wraps the same three and
reads `OCTO_POD_TARGET`, so the address is typed once.

## Rebuild the project images

`images.yml` builds on `main` when arm source or the image workflow changes. For an
explicit rebuild of the reviewed main revision:

```zsh
gh workflow run images.yml --repo usareur-af/octo-pod --ref main
gh run list --repo usareur-af/octo-pod --workflow images.yml --limit 5
```

Watch the selected run and collect its manifest-list digests:

```zsh
image_run_id=123456789  # replace with the run ID
gh run watch "$image_run_id" --repo usareur-af/octo-pod --exit-status
gh run view "$image_run_id" --repo usareur-af/octo-pod --log | grep 'PIN-THIS'
```

The workflow builds UDP ingest, TCP ingest, TCP connect, health, egress and SAPIENT
images on native amd64 and arm64 runners. Each image gets a `sha-<short-commit>` tag
and a verified multi-architecture manifest. Main builds also promote `latest`;
feature-branch builds do not. Deployments use digest pins, not `latest`.

New image builds attach an SPDX SBOM and build provenance to each architecture's
manifest in GHCR. Before promoting `latest`, the workflow downloads both platform
SBOMs and requires OS package and Rust crate entries. The Rust crate inventory relies
on the dependency metadata embedded by the Chainguard Rust builder's `cargo-auditable`
wrapper; an OS-only scan fails this check. BuildKit attestations are metadata attached
to an image, not independent cryptographic signatures.

Each image also has a 90-day workflow artifact named `sbom-<arm>-<commit>` containing
`linux-amd64.spdx.json`, `linux-arm64.spdx.json`, and `image.json` with the immutable
image reference. With Docker Buildx and registry read access, download and check the
registry copies after the workflow artifact expires:

```zsh
image_ref='ghcr.io/usareur-af/octo-udp-ingest:sha-<short-commit>'
python3 tools/check-image-sboms.py "$image_ref" --output-dir sboms
```

Use a real commit tag or the reviewed image digest in that command. Older images are
not updated by this workflow change. Third-party images and air-gap bundle metadata
are outside this image-build check. SBOMs support vulnerability and licence review;
they do not establish redistribution permission or complete licence compliance.

Review all six `PIN-THIS` values and commit the matching `*_image_upstream` values in
`platform/group_vars/all/10-infrastructure.yml`. Build and verify the package and
container bundle from that pinned checkout. Publishing a new image does not change
existing pins or running pods. Bridge and third-party images have separate pins.
