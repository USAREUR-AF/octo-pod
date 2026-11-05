# syntax=docker/dockerfile:1.7
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
FROM cgr.dev/chainguard/rust@sha256:353b720c94045a0b19c2f09e9a8ec84479075b40fbfb6badc7c40884d63f3f6f AS rust-builder
USER root
WORKDIR /build
COPY . .
RUN cargo build --release --locked -p udp-ingest

# upstream tag at the time this digest was pinned: latest
FROM cgr.dev/chainguard/glibc-dynamic@sha256:00696e56634cf72d966ee0e0f26224c83a192569035caf1e31297f77b0e85212
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
