# syntax=docker/dockerfile:1.7
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
FROM cgr.dev/chainguard/rust@sha256:cb5e0b10cd15f5a6f7503f11dcd86a7aa71815037269d61cf3fff7d628953cfe AS rust-builder
USER root
WORKDIR /build
COPY . .
RUN cargo build --release --locked -p octo-egress

# upstream tag at the time this digest was pinned: latest
FROM cgr.dev/chainguard/glibc-dynamic@sha256:671a621016fb33812136dea6182b1db957996d3c8c618187aaf86abd9b14af53
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
