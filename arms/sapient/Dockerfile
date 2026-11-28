# syntax=docker/dockerfile:1.7
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
#
# sapient container image (the SAPIENT session arm).
# BUILD CONTEXT IS arms/ (the workspace root): docker build -f sapient/Dockerfile .
# Same shape as udp-ingest / tcp-ingest; see udp-ingest's Dockerfile for the
# rationale. No ports are published in the ASM role — the arm dials out.
#
# NOTHING IS ADDED TO THE BUILDER for the protobuf definitions. They are vendored under
# sapient-proto/proto/ and compiled by that crate's build.rs with protox, a pure-Rust
# compiler, so this stage needs no protoc and CI needs no extra tooling on either
# architecture.

# upstream tag at the time this digest was pinned: latest-dev
FROM cgr.dev/chainguard/rust@sha256:0f1f402298c87daffc740afe67cd42f87b4398fb717904e42c1273257622cf28 AS chef
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
RUN cargo build --release --locked -p sapient

# upstream tag at the time this digest was pinned: latest
FROM cgr.dev/chainguard/glibc-dynamic@sha256:3359c6ba6b3bcf3b1d2179849f782365564cb262789e732e2833b25b9a8c00f1
WORKDIR /
COPY --from=rust-builder /build/target/release/sapient /usr/local/bin/sapient
COPY --from=rust-builder /build/LICENSE /build/NOTICE /build/THIRD_PARTY_LICENSES.md /usr/share/doc/octo-pod/
# Drop privilege EXPLICITLY, and to the uid the playbook already builds around:
# configure-octo-pod.yml places each arm's private key 0640 root:65532 and makes the
# TLS bind-mount 0711 so a nonroot runtime can traverse it. This arm also WRITES one
# file — the node id it registers under — so its state directory must be writable by
# this uid, or the peer sees a new node after every restart.
USER 65532:65532
# No entrypoint, as in the egress image: each unit names the binary it runs
# (Exec=/usr/local/bin/sapient), so one image serves every way the arm is declared.
ENTRYPOINT []
