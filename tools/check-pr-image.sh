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
# pen-zenoh is not built from the arms workspace: it is upstream zenohd 1.9.0 on
# upgraded Alpine packages (arms/pen-zenoh/Dockerfile). It keeps the base image's
# user, because the pen router unit sets no User= and the pod's rocksdb volume is
# owned by the user the router has always run as; moving the image to 65532 is a
# deployment change, not a package update. So it has its own contract: the zenohd
# binary from the pinned base starts and reports its version.
if [ "$(basename "$(dirname "$dockerfile")")" = 'pen-zenoh' ]; then
  echo "== loader and CLI startup: /zenohd ($arch)"
  out=$(timeout 30s docker run --rm --network none --read-only --cap-drop ALL     --security-opt no-new-privileges --entrypoint /zenohd "$image" --version)
  printf '%s\n' "$out"
  grep -q 'zenohd v1\.9\.0' <<< "$out" || { echo '/zenohd did not report v1.9.0' >&2; exit 1; }
  exit 0
fi
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
