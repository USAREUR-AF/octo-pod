# syntax=docker/dockerfile:1.7
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
#
# octo-pen-zenoh: the pen's Zenoh router. Upstream zenohd 1.9.0, unchanged, on
# upgraded Alpine packages.
#
# Not an arm: like health-pub it rides the arms image matrix rather than growing a
# second pipeline. The pen router must run the same zenoh version as the GOAT
# fabric (arms/Cargo.toml pins the zenoh crate to it, `=1.9.0`), and upstream
# publishes no 1.9.x rebuilds: its current tags are 1.10.x, while the pinned 1.9.0
# digest ships Alpine packages with known vulnerabilities (openssl 3.5.5-r0, musl
# 1.2.5-r21, zlib 1.3.1-r2). So this image keeps the 1.9.0 zenohd binary byte for
# byte and upgrades only the OS packages around it. When the fabric moves to a
# newer zenoh, this image and the arms' zenoh crate pin move together.
#
# upstream tag at the time this digest was pinned: 1.9.0
FROM eclipse/zenoh@sha256:157965d71e0bfd0a044d76a985ff0e5c306ad3968929168fb9678cd2a7fec23f

# Alpine package updates only. /zenohd, the plugins and /entrypoint.sh come from the
# pinned base layers and are not rebuilt or replaced here.
RUN apk upgrade --no-cache
