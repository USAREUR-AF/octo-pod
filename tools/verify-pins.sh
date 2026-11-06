#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
# verify-pins.sh: every image this repository builds must be pinned to a digest that this
# repository's images.yml built from main and signed with a keyless provenance
# attestation. Images built elsewhere are listed by group_vars key in
# platform/pins-external.txt and must still be pinned by digest.
#
#   tools/verify-pins.sh     uses GITHUB_REPOSITORY, else the origin remote
set -euo pipefail
cd "$(dirname "$0")/.."

PINS=platform/group_vars/all/10-infrastructure.yml
EXTERNAL=platform/pins-external.txt
command -v gh >/dev/null || { echo "verify-pins: gh is required" >&2; exit 2; }
repo="${GITHUB_REPOSITORY:-$(git remote get-url origin | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##')}"
workflow="${repo}/.github/workflows/images.yml"
external=$(sed -E 's/#.*//; s/[[:space:]]+$//' "$EXTERNAL" | grep -v '^$' || true)

failed=0
while IFS= read -r line; do
  key=${line%%:*}
  ref=$(sed -nE 's/^[a-z0-9_]+:[[:space:]]*"([^"]+)".*/\1/p' <<<"$line")
  if [[ ! "$ref" =~ @sha256:[0-9a-f]{64}$ ]]; then
    echo "FAIL  $key is not pinned by digest: ${ref:-<empty>}"
    failed=1
    continue
  fi
  if grep -qx "$key" <<<"$external"; then
    echo "ok    $key  built elsewhere, pinned by digest (see $EXTERNAL)"
    continue
  fi
  if gh attestation verify "oci://$ref" --repo "$repo" --signer-workflow "$workflow" \
       --source-ref refs/heads/main --deny-self-hosted-runners >/dev/null 2>&1; then
    echo "ok    $key  signed by $workflow from main"
  else
    echo "FAIL  $key  ${ref#*@} has no attestation from $workflow on main"
    failed=1
  fi
done < <(grep -E '^[a-z0-9_]+_image_upstream:' "$PINS")

if [ "$failed" -ne 0 ]; then
  echo "Pin only digests that images.yml built and signed on main: tools/pin-images.sh" >&2
  exit 1
fi
