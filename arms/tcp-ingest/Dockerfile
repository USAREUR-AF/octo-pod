# syntax=docker/dockerfile:1.7
# SPDX-License-Identifier: Apache-2.0
#
# tcp-ingest container image (octo-pod arm #2).
# BUILD CONTEXT IS arms/ (the workspace root): docker build -f tcp-ingest/Dockerfile .
# Same shape as udp-ingest; see that Dockerfile for the rationale.

# upstream tag at the time this digest was pinned: latest-dev
FROM cgr.dev/chainguard/rust@sha256:cb5e0b10cd15f5a6f7503f11dcd86a7aa71815037269d61cf3fff7d628953cfe AS rust-builder
USER root
WORKDIR /build
COPY . .
RUN cargo build --release --locked -p tcp-ingest

# upstream tag at the time this digest was pinned: latest
FROM cgr.dev/chainguard/glibc-dynamic@sha256:671a621016fb33812136dea6182b1db957996d3c8c618187aaf86abd9b14af53
WORKDIR /
COPY --from=rust-builder /build/target/release/tcp-ingest /usr/local/bin/tcp-ingest
COPY --from=rust-builder /build/LICENSE /build/NOTICE /build/THIRD_PARTY_LICENSES.md /usr/share/doc/octo-pod/
# Drop privilege EXPLICITLY, and to the uid the playbook already builds around:
# configure-octo-pod.yml places each arm's private key 0640 root:65532 and makes the
# TLS bind-mount 0711 so a nonroot runtime can traverse it. The playbook says three
# times that "the arms run nonroot" — but nothing here said so, which left the property
# inherited from whatever the base image happens to default to. A base bump could flip
# it silently, and an arm parses attacker-influenced bytes off a socket with
# network_mode: host. State it, so it is true by construction rather than by luck.
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/tcp-ingest"]
