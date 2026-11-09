# syntax=docker/dockerfile:1.7
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
#
# octo-egress container image: the pod's outbound direction.
#
# BUILD CONTEXT IS arms/ (the workspace root), not this directory:
#   docker build -f egress/Dockerfile .
# The crate depends on the workspace `common` crate.
#
# One image, three binaries. All come from the one `octo-egress` package and share its
# trust logic, so they ship together, the way each other arm ships one package per image.
# The image sets no entrypoint: each unit names the binary it runs, for example
#   Exec=/usr/local/bin/egress-arm        (one unit per socket destination)
#   Exec=/usr/local/bin/zenoh-egress-arm  (one unit per partner's own Zenoh router)
#   Exec=/usr/local/bin/downlink-bridge   (one unit per pod)
# Certificates, keys and CA roots are mounted at deploy time; the image carries no
# identity material.
#
# Same two-stage build and the same pinned bases as the other arms
# (Chainguard-first): cargo release build, then the binaries on glibc-dynamic.

# upstream tag at the time this digest was pinned: latest-dev
FROM cgr.dev/chainguard/rust@sha256:fa05e1dee8bfe7efbd1a0e2a47bb450c7b1bcf23bf82212a68da3a1d3d1cf6e0 AS chef
USER root
# DEPENDENCIES COMPILE IN THEIR OWN LAYER. cargo-chef reduces the workspace to its
# manifests and Cargo.lock (recipe.json), and `cook` compiles every dependency from that
# alone. These three stages are the same in every arm's Dockerfile, so one cached layer
# of compiled dependencies serves every arm on a runner until a manifest or the lock
# changes; a source-only change rebuilds this workspace's crates and nothing else. The
# final `cargo build` is unchanged. reproducible.yml checks that a cached build produces
# the same image as an uncached one.
RUN cargo install --locked cargo-chef --version 0.1.78
WORKDIR /build

FROM chef AS planner
COPY . .
RUN cargo chef prepare --recipe-path recipe.json

FROM chef AS rust-builder
COPY --from=planner /build/recipe.json recipe.json
RUN cargo chef cook --release --locked --recipe-path recipe.json
COPY . .
RUN cargo build --release --locked -p octo-egress

# upstream tag at the time this digest was pinned: latest
FROM cgr.dev/chainguard/glibc-dynamic@sha256:aab5cd0b4cc5edd9c87b6e7aa9ddaa3fb6ffcec559b9e5473c55bbc937b65399
WORKDIR /
COPY --from=rust-builder /build/target/release/egress-arm /usr/local/bin/egress-arm
COPY --from=rust-builder /build/target/release/zenoh-egress-arm /usr/local/bin/zenoh-egress-arm
COPY --from=rust-builder /build/target/release/downlink-bridge /usr/local/bin/downlink-bridge
COPY --from=rust-builder /build/LICENSE /build/NOTICE /build/THIRD_PARTY_LICENSES.md /usr/share/doc/octo-pod/
# Nonroot by construction, to the uid the playbook builds around (65532), as the other
# arms do: an egress arm opens sockets toward external systems, and the bridge holds the
# pod's fabric session.
USER 65532:65532
ENTRYPOINT []
