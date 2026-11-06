#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
# pin-images.sh: pin each arm to the digest that images.yml promoted to :latest after a
# verified main build, but only after checking that this repository's images.yml signed
# it from main. It refuses to write a pin it cannot verify.
#
#   tools/pin-images.sh                 every arm
#   tools/pin-images.sh egress sapient  only these arms
set -euo pipefail
cd "$(dirname "$0")/.."

PINS=platform/group_vars/all/10-infrastructure.yml
for tool in gh jq; do
  command -v "$tool" >/dev/null || { echo "pin-images: $tool is required" >&2; exit 2; }
done
repo="${GITHUB_REPOSITORY:-$(git remote get-url origin | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##')}"
owner=$(tr '[:upper:]' '[:lower:]' <<<"${repo%%/*}")
workflow="${repo}/.github/workflows/images.yml"

digest_of() {  # digest_of <image:tag>
  if command -v crane >/dev/null; then crane digest "$1"
  else docker buildx imagetools inspect "$1" --format '{{json .Manifest.Digest}}' | tr -d '"'
  fi
}

# The group_vars key for each arm, written out as images.yml does. A new arm fails here
# instead of pinning a plausible wrong line.
key_of() {
  case "$1" in
    udp-ingest) echo udp_ingest_image ;;      tcp-ingest) echo tcp_ingest_image ;;
    tcp-connect) echo tcp_connect_image ;;    health-pub) echo health_pub_image ;;
    egress) echo egress_image ;;              sapient) echo sapient_image ;;
    uplink-bridge) echo uplink_bridge_image ;;
    *) echo "pin-images: no group_vars key known for arm '$1'" >&2; return 1 ;;
  esac
}

arms=("$@")
[ ${#arms[@]} -gt 0 ] || arms=(udp-ingest tcp-ingest tcp-connect health-pub egress sapient uplink-bridge)
for arm in "${arms[@]}"; do
  key="$(key_of "$arm")_upstream"
  image="ghcr.io/${owner}/octo-${arm}"
  digest=$(digest_of "${image}:latest")
  if ! attestation=$(gh attestation verify "oci://${image}@${digest}" --repo "$repo" \
       --signer-workflow "$workflow" --source-ref refs/heads/main --deny-self-hosted-runners \
       --format json 2>/dev/null); then
    echo "pin-images: ${image}@${digest} is not signed by ${workflow} on main; not pinned" >&2
    exit 1
  fi
  # The commit images.yml built from, read from the verified signing certificate.
  commit=$(jq -r '.[0].verificationResult.signature.certificate.sourceRepositoryDigest // empty' \
    <<<"$attestation")
  [ -n "$commit" ] || { echo "pin-images: no source commit in the attestation for $arm" >&2; exit 1; }
  grep -qE "^${key}:" "$PINS" || { echo "pin-images: $key not found in $PINS" >&2; exit 1; }
  sed -i.bak -E "s#^(${key}:)[[:space:]]*\"[^\"]*\".*#\\1 \"${image}@${digest}\"   \# main@${commit:0:7}, signed by images.yml#" "$PINS"
  rm -f "$PINS.bak"
  echo "pinned $key -> ${image}@${digest} (main@${commit:0:7})"
done

# docs/images.md is generated from these pins; CI fails if it disagrees with them.
if [ -x tools/gen-images-doc.py ]; then
  tools/gen-images-doc.py
fi
