# syntax=docker/dockerfile:1.7
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
FROM cgr.dev/chainguard/rust@sha256:cb5e0b10cd15f5a6f7503f11dcd86a7aa71815037269d61cf3fff7d628953cfe AS rust-builder
USER root
WORKDIR /build
COPY . .
RUN cargo build --release --locked -p octo-health-pub

# upstream tag at the time this digest was pinned: latest
FROM cgr.dev/chainguard/glibc-dynamic@sha256:671a621016fb33812136dea6182b1db957996d3c8c618187aaf86abd9b14af53
WORKDIR /
COPY --from=rust-builder /build/target/release/octo-health-pub /usr/local/bin/octo-health-pub
COPY --from=rust-builder /build/LICENSE /build/NOTICE /build/THIRD_PARTY_LICENSES.md /usr/share/doc/octo-pod/
# Stated rather than inherited, for the same reason the arms state it: the
# playbook places private keys 0640 root:65532 and relies on the runtime being
# nonroot. A base-image bump must not be able to flip that silently.
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/octo-health-pub"]
