# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
#
# ClusterFuzzLite build image for the fuzz targets in arms/fuzz. The base image carries
# the nightly Rust toolchain, cargo-fuzz and the sanitizer runtimes, and build.sh runs in
# it. BUILD CONTEXT IS the repository root; WORKDIR tells ClusterFuzzLite where the
# repository is, so it can put the pull request's checkout there.

# upstream tag at the time this digest was pinned: latest
FROM gcr.io/oss-fuzz-base/base-builder-rust@sha256:3428d0a90b1e7efbe2ba947bd1d0c9a70594986b4b6553804547f1fb24cfc92f
COPY . $SRC/octo-pod
WORKDIR $SRC/octo-pod
COPY .clusterfuzzlite/build.sh $SRC/
