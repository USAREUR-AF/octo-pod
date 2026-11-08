<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Contributing to octo-pod

Thanks for helping. This guide covers how to set up, how to prepare a change so it can be
merged, and how releases are published. Report security issues privately, as
[SECURITY.md](SECURITY.md) describes, never in a public issue.

## Before You Start

Open an issue before a large change, so the approach is agreed before the work. Small
fixes can go straight to a pull request.

Submit ordinary bugs and enhancement requests through the
[issue tracker](https://github.com/USAREUR-AF/octo-pod/issues). Include the version,
expected and actual behavior, and a minimal example without site data or credentials.
English reports and comments are welcome. Maintainers aim to acknowledge ordinary
reports within 14 days; issues and responses remain searchable in the public archive.
Use the private process in SECURITY.md for vulnerabilities.

The [OpenSSF evidence worksheet](docs/openssf-passing.md) records assessed practices
and outstanding evidence. It is not a claim that the project has earned a badge.

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

## Coding Standards

Contributions follow the style guide for their language. Where a tool can check the
guide, CI runs it and a change that fails doesn't merge.

| Language | Style guide | How it is checked |
|---|---|---|
| Rust | The [Rust Style Guide](https://doc.rust-lang.org/style-guide/), as `rustfmt` formats it. Clippy's default lints, with no warnings. No `unsafe` code. | `cargo fmt --check` and `cargo clippy -- -D warnings` in CI; `unsafe_code = "forbid"` in `arms/Cargo.toml` |
| Shell | The [Google Shell Style Guide](https://google.github.io/styleguide/shellguide.html). Scripts start with `set -euo pipefail`. | ShellCheck in CI, on every script and every rendered shell template |
| Python | [PEP 8](https://peps.python.org/pep-0008/). | Review. CI doesn't lint Python yet |
| Ansible | Fully qualified module names, such as `ansible.builtin.file`. | `ansible-playbook --syntax-check` in CI, and review |
| Documentation | The [Google developer documentation style guide](https://developers.google.com/style), in clear, literal English. See [Documentation language](#documentation-language). | Review |

## Test Policy

New functionality and bug fixes come with automated tests, in the same pull request.

- A bug fix includes a test that fails without the fix and passes with it.
- Put tests on the security boundary: the release and signature checks, the firewall,
  identity and certificates, and the access rules that a declaration generates. Test
  that the boundary refuses what it must refuse, not only that it accepts what it must
  accept.
- Put Rust tests with the crate that they test, under `arms/`. Put platform, CLI and
  release tests in `platform/tests/`.
- List every command that you ran in the pull request's Test Plan, so that a reviewer can
  run the same commands.
- If a change can't have an automated test, for example a change to documentation only,
  say so in the Test Plan and give the reason.

Maintainers don't merge a change to functionality without its tests.

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

A change to a shell script also must pass `shellcheck`; CI fails on any shell script it
doesn't lint.

### Fuzzing

The code that reads bytes from a peer or from the pen has fuzz targets in `arms/fuzz`:
the TCP framing, the SAPIENT framing and messages, and the downlink gate.
ClusterFuzzLite runs them on each pull request that changes `arms/`. To run a target on
your computer, you need a nightly Rust toolchain and cargo-fuzz.

Install the nightly toolchain:

```zsh
rustup toolchain install nightly
```

Install cargo-fuzz:

```zsh
cargo install cargo-fuzz --locked
```

Run the remaining commands from `arms/fuzz`. The fuzz crate is a separate workspace
without a lock file of its own. Copy the workspace lock file, so that the fuzz build uses
the same dependency versions as the arms:

```zsh
cp ../Cargo.lock Cargo.lock
```

List the targets:

```zsh
cargo +nightly fuzz list
```

Run a target for 60 seconds. New inputs go to the first directory, and the committed seed
inputs are in the second:

```zsh
cargo +nightly fuzz run tcp_frame corpus/tcp_frame seeds/tcp_frame -- -max_total_time=60
```

The `sapient_json` target starts from the SAPIENT reference vectors instead:

```zsh
cargo +nightly fuzz run sapient_json corpus/sapient_json ../sapient-receiver/tests/vectors/true ../sapient-receiver/tests/vectors/false -- -max_total_time=60
```

When a target finds a crash, cargo-fuzz saves the input in `artifacts/<target>/`. Run the
target on that file to reproduce the crash:

```zsh
cargo +nightly fuzz run tcp_frame artifacts/tcp_frame/crash-<hash>
```

Add the input as a regression test in the crate that owns the code, then fix the code.
After you change the reference vectors or the input format of a target, regenerate the
seed inputs:

```zsh
cargo run --example make_seeds
```

## Pull Requests

Fill in the template: what changed and why, how you tested it, and any known gaps. Keep
one concern per pull request.

Never commit site values: addresses, hostnames, enrollment output, keys or tokens. Site
values belong in an operator's ignored identity files, and the examples use
documentation addresses (RFC 5737) only.

## Code Review

Every change reaches `main` through a pull request, and a person other than the author
reviews and approves it before it merges. This is the two-person rule. Branch protection
on `main` requires an approval from a code owner, dismisses that approval when new
commits arrive, and requires every review thread to be resolved.

### Who Approves

- Approvals come from the maintainers that [.github/CODEOWNERS](.github/CODEOWNERS)
  names. [MAINTAINERS.md](MAINTAINERS.md) lists who holds each role.
- The author never approves their own change.
- A change to the firewall, the release gates, identity, certificates or signing needs a
  second reviewer in addition to the approver.
- An administrator can merge without an approval. Use this only to restore service or to
  repair a broken `main`, and say why in the pull request. A second person then reviews
  the change after it merges.

### What a Reviewer Checks

Read the tests first, then the code. Check that:

- The pull request does one thing, and its description says what changed and why.
- A bug fix includes a test that fails without the fix. New functionality includes tests,
  and a change on the security boundary tests that the boundary refuses what it must
  refuse. See [Test Policy](#test-policy).
- The commands in the Test Plan reproduce the result that the author reports.
- Input from a sensor, peer, receiver or file is bounded and checked against what is
  allowed before the code uses it.
- No new path lets data leave the pod without a declaration and an `apply`.
- No credential, key, real address or hostname is committed.
- A new dependency is pinned, has a license that `cargo-deny` accepts, and is needed. A
  new GitHub Action is pinned by commit.
- A generated file was regenerated with its generator, not edited by hand.
- Documentation changes in the same pull request as the behavior that it describes. An
  architectural decision has an ADR.
- Every commit is signed and carries a DCO sign-off, and the title follows Conventional
  Commits.

### What Makes a Change Acceptable

A change merges when all of these are true:

- A code owner who is not the author approved it.
- Every required check passes on a branch that is up to date with `main`.
- Every review thread is resolved.
- Every commit is signed and signed off.

A reviewer who requests changes gives the reason and, where possible, the fix. The
author answers each comment with a change or with a reason.

## Small Tasks for New Contributors

Issues labeled
[`good first issue`](https://github.com/usareur-af/octo-pod/issues?q=is%3Aopen+label%3A%22good+first+issue%22)
are small tasks for new or occasional contributors. Each one names the files to change
and the test that shows the change works, and needs no access to a pod or a fabric.
Comment on the issue before you start, so that two people don't do the same work.
Maintainers add the label when they find a task of this size.

## Documentation language

Write documentation in clear, literal English so it can be translated and used across
countries. Avoid idioms, metaphors, colloquialisms and culture-specific references.
State the actor, action and condition directly, and define project-specific terms when
first used. Keep commands, flags, file paths and configuration identifiers exact.

## Decisions

Record an architectural decision as an ADR in [docs/adr/](docs/adr/), from
[the template](docs/adr/TEMPLATE.md), with a row in [the index](docs/adr/INDEX.md) in the same
pull request.

## Releases

Maintainers publish releases. The version is recorded in `VERSION` and follows
[Semantic Versioning](https://semver.org/); while it is below 1.0, a minor version may
break compatibility. A release is a signed tag `octo-pod-v<version>`, and a release
candidate is `octo-pod-v<version>-rc.<n>`.

Before tagging, wait for CI and default CodeQL analysis to succeed on the exact `main`
commit. Tag releases also require the batch fuzz suite with AddressSanitizer and
debug assertions before the build can proceed. Fix a failed candidate and rerun the
checks; do not disable the gates. Manual dry runs do not publish releases.

Write a nonempty version section in CHANGELOG.md describing major changes and upgrade
impact. Include the identifiers of publicly known project vulnerabilities fixed by
that release when those identifiers were assigned at release time. Keep embargoed
details private until coordinated disclosure. The release page includes this section
before its verification and installation instructions.

Create the tag with your signing key. GitHub must be able to verify the signature, so
the key must be registered on your GitHub account as a signing key:

```zsh
git tag -s octo-pod-v<version> -m "octo-pod <version>"
```

Check the signature before you push the tag:

```zsh
git tag -v octo-pod-v<version>
```

The release workflow refuses a lightweight tag, a tag that GitHub does not verify as
signed, and a tag that differs from `VERSION`. It then waits until a release approver
other than the person who pushed the tag approves. After that, it builds the artifacts,
signs `SHA256SUMS` and publishes them with the changelog entry. See
[Release pattern](docs/release-pattern.md) for each gate.

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
| `/etc/octo-pod` | **the pod's identity**. Written by `join` or `init`, never modified by an upgrade |

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

Pin the new images with `tools/pin-images.sh`. It writes each arm's `*_image_upstream`
value in `platform/group_vars/all/10-infrastructure.yml`, but only for a digest that
`images.yml` built and signed on `main`, and regenerates `docs/images.md`. Commit the
result through a pull request; the `signed-pins` check refuses any pin that `images.yml`
did not sign. Build and verify the package and container bundle from that pinned
checkout. Publishing a new image does not change existing pins or running pods.
Third-party images are listed in `platform/pins-external.txt` and have separate pins.

See [Release pattern](docs/release-pattern.md) for every step from a commit to a running pod, which gate checks it, and how to verify a release.
