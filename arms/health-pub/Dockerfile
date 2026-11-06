# syntax=docker/dockerfile:1.7
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
#
# health-pub container image — publishes this pod's health onto the fabric.
#
# BUILD CONTEXT IS arms/ (the workspace root), not this directory:
#   docker build -f health-pub/Dockerfile .
# — it depends on the workspace `common` crate for the mTLS session idiom.
#
# Two-stage build, identical in shape to the arms: cargo release build → single
# binary on glibc-dynamic. Certificate, key and CA root are mounted at deploy
# time; the image carries no embedded identity material.
#
# THIS CONTAINER IS DELIBERATELY POWERLESS. It holds no socket, no docker
# access and no host mount beyond one read-only status file. Computing a pod's
# health means asking docker what is running, which means the docker socket,
# which means a container that can escape to root — far too much privilege for
# a thing whose only job is to report. The privileged half stays on the host.

# upstream tag at the time this digest was pinned: latest-dev
FROM cgr.dev/chainguard/rust@sha256:353b720c94045a0b19c2f09e9a8ec84479075b40fbfb6badc7c40884d63f3f6f AS chef
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
RUN cargo build --release --locked -p octo-health-pub

# upstream tag at the time this digest was pinned: latest
FROM cgr.dev/chainguard/glibc-dynamic@sha256:00696e56634cf72d966ee0e0f26224c83a192569035caf1e31297f77b0e85212
WORKDIR /
COPY --from=rust-builder /build/target/release/octo-health-pub /usr/local/bin/octo-health-pub
COPY --from=rust-builder /build/LICENSE /build/NOTICE /build/THIRD_PARTY_LICENSES.md /usr/share/doc/octo-pod/
# Stated rather than inherited, for the same reason the arms state it: the
# playbook places private keys 0640 root:65532 and relies on the runtime being
# nonroot. A base-image bump must not be able to flip that silently.
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/octo-health-pub"]
