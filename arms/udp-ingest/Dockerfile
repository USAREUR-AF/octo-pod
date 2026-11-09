# syntax=docker/dockerfile:1.7
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
#
# udp-ingest container image (octo-pod arm #1).
#
# BUILD CONTEXT IS arms/ (the workspace root), not this directory:
#   docker build -f udp-ingest/Dockerfile .
# — the arm depends on the workspace `common` crate.
#
# Two-stage build, the same shape as GOAT's shims (Chainguard-first): cargo release build → single binary on glibc-dynamic.
# Arm cert + key + CA root are mounted at deploy time; the image carries no
# embedded identity material.

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
RUN cargo build --release --locked -p udp-ingest

# upstream tag at the time this digest was pinned: latest
FROM cgr.dev/chainguard/glibc-dynamic@sha256:aab5cd0b4cc5edd9c87b6e7aa9ddaa3fb6ffcec559b9e5473c55bbc937b65399
WORKDIR /
COPY --from=rust-builder /build/target/release/udp-ingest /usr/local/bin/udp-ingest
COPY --from=rust-builder /build/LICENSE /build/NOTICE /build/THIRD_PARTY_LICENSES.md /usr/share/doc/octo-pod/
# Drop privilege EXPLICITLY, and to the uid the playbook already builds around:
# configure-octo-pod.yml places each arm's private key 0640 root:65532 and makes the
# TLS bind-mount 0711 so a nonroot runtime can traverse it. The playbook says three
# times that "the arms run nonroot" — but nothing here said so, which left the property
# inherited from whatever the base image happens to default to. A base bump could flip
# it silently, and an arm parses attacker-influenced bytes off a socket with
# network_mode: host. State it, so it is true by construction rather than by luck.
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/udp-ingest"]
