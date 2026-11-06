#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
# build-airgap-bundle.sh — everything a pod needs to run with no route to anywhere.
#
#   tools/build-airgap-bundle.sh --out ./octo-stage
#   rsync -a ./octo-stage/ pod:/opt/octo-stage/
#   # on the pod:
#   sudo octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000
#
# WHY THIS CARRIES A REGISTRY AND NOT JUST IMAGES. A `docker save` tar cannot satisfy a
# digest-pinned reference. Measured on Docker 29.6.2 with the containerd snapshotter:
# pull by the multi-arch INDEX digest, save, remove, load — the reference no longer
# resolves; pull by the arm64 PLATFORM digest instead and it still does not. RepoDigests
# is registry metadata and `docker load` cannot reconstruct it.
#
# TAGS do survive a save/load, which is the crack this gets through: the registry image
# itself is carried by tag, started on the pod, and seeded from an OCI layout that
# preserves the exact manifest bytes. The digest is content-addressed, so what the pod
# then pulls from localhost:5000 is byte-identical to what CI published — same digest,
# same verification, no internet.
#
# THE WHOLE INDEX, NOT ONE PLATFORM. The references in group_vars are index digests. An
# index containing one platform hashes differently from the index containing all of
# them, so filtering by architecture to save space silently invalidates every pin. That
# is why there is no --arch here: correctness costs the extra platform.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GROUP_VARS="$REPO/platform/group_vars/all/10-infrastructure.yml"
OUT="$REPO/dist/octo-stage"
# The two tool images the bundle carries. A pod loads them BY TAG (see below), so the
# tag is the name it knows them by; the digest decides the bytes behind that name, so a
# tag that moves upstream cannot change what a bundle built from this commit contains.
SKOPEO_IMAGE="quay.io/skopeo/stable:latest"
SKOPEO_DIGEST="sha256:2681eccb8b02c495b013c2669cbf7f41d5c0b0b0e644e7c69bd3ea9bcb20641f"   # skopeo 1.22.3
REGISTRY_IMAGE="registry:2"
REGISTRY_DIGEST="sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
# repository@digest: the tag is dropped, the digest is what is pulled.
pinned() { printf '%s@%s' "${1%:*}" "$2"; }

die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
say() { printf '  %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --out) if [ $# -lt 2 ] || [ -z "$2" ]; then die "$1 needs a value"; fi; OUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

command -v docker >/dev/null || die "docker is required (skopeo runs as a container)"
docker info >/dev/null 2>&1 || die "the docker daemon is not reachable"
[ -f "$GROUP_VARS" ] || die "cannot find $GROUP_VARS"

# The pins are the source of truth, so a bundle cannot drift from what apply will ask
# for. _upstream, not the derived vars: those are folded scalars that resolve through
# image_mirror, and the bundle is what MAKES the mirror possible.
read_pin() { sed -n "s/^$1_upstream: *\"\([^\"]*\)\".*/\1/p" "$GROUP_VARS" | head -1; }

declare -a TAGS REFS
add() {
  local ref; ref="$(read_pin "$2")"
  [ -n "$ref" ] || die "${2}_upstream is not set in $GROUP_VARS"
  case "$ref" in
    *@sha256:*) : ;;
    *) die "$2 is not digest-pinned ($ref). A bundle built from a mutable tag is not
  reproducible: the bytes carried and the reference resolved can differ." ;;
  esac
  TAGS+=("$1"); REFS+=("$ref")
}

add zenoh        zenoh_image
add audit-sink   audit_image
add udp-ingest   udp_ingest_image
add tcp-ingest   tcp_ingest_image
add tcp-connect  tcp_connect_image
# The pod's own health publisher. It runs as its own compose project, so a bundle without
# it produces a pod that captures normally and is merely invisible, which is the whole
# point of separating it from the pen. Carried anyway: an airgapped pod is exactly the one
# whose health nobody can see any other way, so it is the pod that needs it most.
add health-pub   health_pub_image
# Egress and SAPIENT are part of the release, even if a capture-only pod does not
# declare them. Empty pins refuse the bundle rather than silently leaving a fielded
# destination unable to start; publish and pin both images before cutting a release.
add egress       egress_image
add sapient      sapient_image
# The uplink bridge: only a pod that holds a slot runs it, and that pod needs it offline
# exactly as much as it needs its arms.
add uplink-bridge uplink_bridge_image

mkdir -p "$OUT/images/oci" "$OUT/deb"
: > "$OUT/images/MAP"

echo
echo "building an airgap bundle"
echo "  pins from $GROUP_VARS"
echo "  into      $OUT"
echo

# CREDENTIALS HAVE TO BE HANDED TO SKOPEO EXPLICITLY. `docker login` writes
# ~/.docker/config.json for the DOCKER daemon; skopeo runs here as its own container and
# sees none of it, so a private image failed with "unauthorized" while the shell it ran
# from was perfectly well logged in. That is not a permissions problem and no amount of
# scope fixes it — the file has to be mounted in and named.
AUTHFILE="${REGISTRY_AUTH_FILE:-$HOME/.docker/config.json}"
AUTH_ARGS=()
AUTH_MOUNT=()
if [ -f "$AUTHFILE" ]; then
  AUTH_MOUNT=(-v "$AUTHFILE:/auth.json:ro")
  AUTH_ARGS=(--src-authfile /auth.json)
  say "using credentials from $AUTHFILE"
else
  say "no $AUTHFILE — only public images will copy"
fi

# skopeo as a container so this needs nothing installed but docker. --preserve-digests
# makes it refuse to alter the manifest rather than silently re-encoding it, which is
# the one thing that would break every pin in the bundle.
# --user, so the OCI layout belongs to whoever ran this. Without it skopeo writes the
# blobs as root through the bind mount, and the caller cannot read, tar or delete its
# own output — which surfaced as `rm: Permission denied` on a CI runner AFTER every
# image had copied successfully: the work was done and the cleanup could not happen.
skopeo() {
  docker run --rm --network host \
    --user "$(id -u):$(id -g)" \
    -v "$OUT/images/oci:/oci" \
    "${AUTH_MOUNT[@]}" \
    "$(pinned "$SKOPEO_IMAGE" "$SKOPEO_DIGEST")" "$@"
}

say "fetching skopeo"
docker pull -q "$(pinned "$SKOPEO_IMAGE" "$SKOPEO_DIGEST")" >/dev/null \
  || die "could not pull $SKOPEO_IMAGE at $SKOPEO_DIGEST"

for i in "${!TAGS[@]}"; do
  tag="${TAGS[$i]}"; ref="${REFS[$i]}"
  digest="${ref##*@}"
  # The repository PATH the pod will ask the mirror for: the reference minus any
  # leading registry host. It must match the rewrite in group_vars exactly, or the
  # mirror will hold the bytes under a name nothing asks for.
  path="$(printf '%s' "${ref%@*}" | sed -E 's#^[a-z0-9-]+[.:][a-z0-9.:-]*/##')"
  say "copy  $ref"
  skopeo copy --all --preserve-digests "${AUTH_ARGS[@]}" "docker://$ref" "oci:/oci:$tag" >/dev/null \
    || die "could not copy $ref into the OCI layout.

  'unauthorized' with a private package means skopeo could not read a credential.
  It does NOT share the docker daemon's login: it needs $AUTHFILE to exist and to
  hold a plaintext auth entry for that registry. A docker credential HELPER stores
  the secret outside that file, so 'docker login' can succeed and this still fail —
  check for a credsStore line. In CI, docker/login-action writes it in plaintext."
  printf '%s %s %s\n' "$tag" "$path" "$digest" >> "$OUT/images/MAP"
done

# VERIFIED BEFORE THE TOOL IMAGES ARE TOUCHED. The per-architecture pulls below
# overwrite the local skopeo image — a tag holds one platform at a time — so whichever
# is pulled last is what `skopeo` means afterwards. Verifying after them ran an arm64
# binary on an amd64 runner, every inspect failed, and a complete layout reported as
# "missing" all five images. The fix for the bundle's architecture broke the builder's.
say "verifying the layout carries every pinned digest"
missing=""
while read -r tag path digest; do
  skopeo inspect --raw "oci:/oci:$tag" >/dev/null 2>&1 || missing="$missing $tag"
done < "$OUT/images/MAP"
[ -z "$missing" ] || die "the OCI layout is missing:$missing"

# Carried BY TAG, because that is the one thing a save/load preserves — and these two
# are what make the digest-preserving path possible on the pod.
#
# PER ARCHITECTURE, and this is where it went wrong once. The payload is copied with
# --all so it carries every platform, but `docker save` stores only the platform the
# BUILDER had — and CI runs on amd64. The bundle then loaded cleanly on an arm64 Jetson
# and died with "exec format error" the moment it tried to run skopeo. The payload was
# guarded against exactly this and the tools were not.
#
# So both, named by architecture, and the pod loads the one matching its own. That costs
# one extra copy of each tool and removes an entire class of "works on my runner".
#
# THE LOCAL COPY GOES FIRST. Each tool is pinned by its multi-platform digest, and
# Docker's image store keeps one platform per name: pulling the same digest for a second
# architecture fails with "cannot overwrite digest". So the copy from the previous
# architecture is removed before each pull. Only the local copy; the pin is unchanged.
drop_local() {  # drop_local <repository:tag> <digest>
  docker image rm -f "$(pinned "$1" "$2")" "$1" >/dev/null 2>&1 || true
}
for arch in amd64 arm64; do
  for spec in "registry|$REGISTRY_IMAGE|$REGISTRY_DIGEST" "skopeo|$SKOPEO_IMAGE|$SKOPEO_DIGEST"; do
    IFS='|' read -r name ref digest <<<"$spec"
    say "saving $name for linux/$arch"
    drop_local "$ref" "$digest"
    docker pull -q --platform "linux/$arch" "$(pinned "$ref" "$digest")" >/dev/null \
      || die "could not pull $ref at $digest for linux/$arch"
    docker tag "$(pinned "$ref" "$digest")" "$ref" \
      || die "could not tag $ref"
    docker save "$ref" -o "$OUT/images/${name}-${arch}.tar" \
      || die "could not save $ref for linux/$arch"
  done
done
# Put the native-platform tools back. Leaving the last-pulled architecture in place
# would break anything that runs skopeo or the registry after this script.
for spec in "$REGISTRY_IMAGE|$REGISTRY_DIGEST" "$SKOPEO_IMAGE|$SKOPEO_DIGEST"; do
  IFS='|' read -r ref digest <<<"$spec"
  drop_local "$ref" "$digest"
  if docker pull -q "$(pinned "$ref" "$digest")" >/dev/null 2>&1; then
    docker tag "$(pinned "$ref" "$digest")" "$ref" >/dev/null 2>&1 || true
  fi
done

printf '%s\n' "$REGISTRY_IMAGE" > "$OUT/images/REGISTRY_IMAGE"
printf '%s\n' "$SKOPEO_IMAGE"   > "$OUT/images/SKOPEO_IMAGE"

sz="$(du -sh "$OUT" | cut -f1)"
cat <<NOTE

  bundle is $sz at $OUT

STILL TO STAGE BY HAND — apt artifacts, not ours, fetched on a box of the SAME
architecture as the pod:

  $OUT/deb/    podman (>= 4.7), skopeo, nftables, netbird, and the platform
               dependencies declared in the octo-pod package
               apt-get install --download-only -o Dir::Cache::archives="\$PWD" <those>

Then, on the pod:

  rsync -a "$OUT"/ <pod>:/opt/octo-stage/
  sudo octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000

bootstrap loads the registry from its tar, starts it, seeds it from the OCI layout,
and checks every pinned reference resolves before it hands the box over.
NOTE
