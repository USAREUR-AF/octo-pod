<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Roadmap

What octo-pod intends to do, and not do, from November 2026 to November 2027. The
maintainers review this page at least every three months and with each minor release.
Last reviewed: 2026-11-06.

The roadmap states direction, not dates. A change still lands through a pull request and,
for an architectural change, an ADR. To propose a change to the roadmap, open an issue.

## Planned

### Supply Chain and Releases

- **Verify image signatures on the pod.** `images.yml` already attaches signed build
  provenance to every arm image. A pod that pulls from a registry will check that
  provenance before it runs an image, as `tools/install-to-pod.sh` already checks the
  release signature.
- **Reproducible builds.** Two builds of the same commit produce the same release files,
  and CI checks this on each change.
- **Pin updates from the image workflow.** After `images.yml` builds and signs new arm
  images on `main`, it opens the pin pull request itself, instead of a maintainer
  running `tools/pin-images.sh`.
- **Required checks.** Make the `signed-pins` check required on `main`.

### Quality

- **Measured test coverage.** Measure statement and branch coverage for the Rust arms and
  the Python CLI in CI, then raise it to at least 90 percent of statements and 80
  percent of branches.
- **A license and copyright statement in every source file**, in SPDX form, checked in CI.
- **Lint Python in CI** against PEP 8, as CI already checks Rust and shell.

### Operation

- **One supply declaration.** Replace the separate airgap and mirror settings with the
  single `supply:` declaration that
  [the supply posture design](docs/design-supply-posture.md) proposes.
- **A local health record for a pod with no upstream slot**, so that the first person to
  visit such a pod can read its history. See the open questions in
  [engineering principles](docs/engineering-principles.md#open-questions).
- **Container hardening.** Run each arm with a read-only root file system and no Linux
  capabilities that it doesn't use.

### Egress

The open questions in [the egress design](docs/design-egress-gateway.md#open-questions)
set the direction:

- A fabric identity for each destination, so that one pod can feed receivers that are
  cleared for different data.
- A replacement policy for serve destinations that replaces only an idle or failed
  connection.
- Zenoh session hardening, as that section describes.

### Decisions

- Accept, revise or reject the proposed ADRs listed in
  [the ADR index](docs/adr/INDEX.md), based on field use.

## Not Planned

These are deliberate non-goals. [Engineering principles](docs/engineering-principles.md#non-goals)
gives the reasons.

- **Translating sensor data.** Producers and external translators own decoding and format
  conversion. Byte arms relay what arrives.
- **Store and forward.** The pod discards stale data instead of queuing it to disk.
- **High availability inside one pod.** One router runs per pod; arms reconnect after it
  restarts.
- **Applying configuration changes automatically.** Timers repair runtime faults and never
  apply a declaration change.
- **Managing other pods.** A pod converges itself, and nothing reaches into it.
- **Running on Kubernetes**, or publishing an apt repository.
