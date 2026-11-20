#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
#
# Records site/assets/packets.json, which the first screen replays: synthetic
# datagrams through two copies of the real udp-ingest arm into a Zenoh router on
# loopback (the arms' own test pen). Nothing is mocked; nothing leaves the machine.
#
#   site/captures/capture-packets.sh
#
# Needs a Rust toolchain. Builds in arms/target, so a second run is quick.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
export CARGO_TARGET_DIR="$root/arms/target"
(cd "$root/arms" && cargo build -q -p udp-ingest)
cd "$root/site/captures/packets"
cp "$root/arms/Cargo.lock" Cargo.lock   # the arms' own versions; not kept here
cargo run -q -- "$CARGO_TARGET_DIR/debug/udp-ingest" "$root/site/assets/packets.json"
