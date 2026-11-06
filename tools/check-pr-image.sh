#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
# Inspect a locally loaded PR image and invoke its installed binaries without networking.
set -euo pipefail
image=${1:?image tag required}
arch=${2:?expected architecture required}
dockerfile=${3:?Dockerfile required}
actual_arch=$(docker image inspect "$image" --format '{{.Architecture}}')
[ "$actual_arch" = "$arch" ] || { echo "wrong image architecture: $actual_arch" >&2; exit 1; }
image_user=$(docker image inspect "$image" --format '{{.Config.User}}')
[ "$image_user" = '65532:65532' ] || { echo "unexpected image user: $image_user" >&2; exit 1; }
# Read the actual final-image copy contract; egress has three binaries, not one.
binaries=$(sed -nE 's|^COPY --from=[^ ]+ /build/target/release/[^ ]+ (/usr/local/bin/[^ ]+)$|\1|p' "$dockerfile")
[ -n "$binaries" ] || { echo 'no installed binaries found in Dockerfile' >&2; exit 1; }
while IFS= read -r binary; do
  echo "== loader and CLI startup: $binary ($arch, uid 65532)"
  out=$(timeout 30s docker run --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges --entrypoint "$binary" "$image" --help)
  printf '%s\n' "$out"
  # Every arm parses its arguments with clap, so --help prints a usage line. A placeholder
  # main left over from the dependency layer would exit 0 and print nothing.
  grep -q '^Usage: ' <<< "$out" || { echo "$binary printed no usage: not the arm's own build" >&2; exit 1; }
done <<< "$binaries"
