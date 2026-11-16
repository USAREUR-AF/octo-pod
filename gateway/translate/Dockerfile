# syntax=docker/dockerfile:1.7
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
#
# octo-translate container image: the translation gateway (ADR 0004, Proposed).
#
# BUILD CONTEXT IS THE REPOSITORY ROOT, not this directory and not arms/:
#   docker build -f gateway/translate/Dockerfile .
# The crate is its own workspace and takes the pen session code from arms/common by
# path, so both directories must be in the context. Nothing else of arms/ is compiled.
#
# The image carries the binary and the pinned pack bundle it was tested with
# (packs/MANIFEST.json names the goat-transformers commit and every file's SHA-256; the
# binary checks them at start). Certificates, keys and CA roots are mounted at deploy
# time; the image carries no identity material.
#
# The same pinned bases as the arms (Chainguard-first). No cargo-chef layer here: its
# recipe covers one workspace, and this build spans two.

# upstream tag at the time this digest was pinned: latest-dev
FROM cgr.dev/chainguard/rust@sha256:5cf99a94c5e94cb6741988f77aeb0976ad9d4eb379db80e0ee980cc95c5e5fe6 AS rust-builder
USER root
WORKDIR /build
COPY arms/Cargo.toml arms/Cargo.lock arms/
COPY arms/common arms/common
COPY arms/LICENSE arms/NOTICE arms/THIRD_PARTY_LICENSES.md arms/
COPY gateway/translate gateway/translate
WORKDIR /build/gateway/translate
RUN cargo build --release --locked

# upstream tag at the time this digest was pinned: latest
FROM cgr.dev/chainguard/glibc-dynamic@sha256:671a621016fb33812136dea6182b1db957996d3c8c618187aaf86abd9b14af53
WORKDIR /
COPY --from=rust-builder /build/gateway/translate/target/release/octo-translate /usr/local/bin/octo-translate
COPY --from=rust-builder /build/gateway/translate/packs /usr/share/octo-translate/packs
# Every third-party crate in this image is in the arm images at the same version
# (platform/tests/test_translate.py checks the two lock files), so the arms' notices
# cover it. goat-transformers is this organization's own, under the same license; its
# NOTICE names the public documents the packs were written from.
COPY --from=rust-builder /build/arms/LICENSE /build/arms/NOTICE /build/arms/THIRD_PARTY_LICENSES.md /usr/share/doc/octo-pod/
COPY --from=rust-builder /build/gateway/translate/NOTICE /usr/share/doc/octo-translate/NOTICE
# Nonroot by construction, to the uid the playbook builds around (65532), as the arms do.
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/octo-translate"]
