# syntax=docker/dockerfile:1.7
# SPDX-License-Identifier: Apache-2.0
#
# uplink-bridge container image (the pod's sync leg).
#
# BUILD CONTEXT IS arms/ (the workspace root), not this directory:
#   docker build -f uplink-bridge/Dockerfile .
#
# Same two-stage build and pinned bases as the arms. Both legs' certs, keys and roots
# are mounted at deploy time; the image carries no embedded identity material.

# upstream tag at the time this digest was pinned: latest-dev
FROM cgr.dev/chainguard/rust@sha256:cb5e0b10cd15f5a6f7503f11dcd86a7aa71815037269d61cf3fff7d628953cfe AS rust-builder
USER root
WORKDIR /build
COPY . .
RUN cargo build --release --locked -p uplink-bridge

# upstream tag at the time this digest was pinned: latest
FROM cgr.dev/chainguard/glibc-dynamic@sha256:671a621016fb33812136dea6182b1db957996d3c8c618187aaf86abd9b14af53
WORKDIR /
COPY --from=rust-builder /build/target/release/uplink-bridge /usr/local/bin/uplink-bridge
COPY --from=rust-builder /build/LICENSE /build/NOTICE /build/THIRD_PARTY_LICENSES.md /usr/share/doc/octo-pod/
# The playbook places the slot key readable by gid 65532; run nonroot by construction.
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/uplink-bridge"]
