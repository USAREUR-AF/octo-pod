#!/usr/bin/env bash
# build-vm-image.sh: build the octo-pod golden VM image on an LXD host, from this commit.
#
#   tools/build-vm-image.sh --host <ssh-target> --manifest <packages.manifest> \
#       [--alias octo-pod-gateway] [--keep]
#
# Run from a clone on your laptop. The LXD host does the work: it launches a temporary
# Ubuntu 24.04 build VM, which installs the substrate, builds every container image from
# source, stages the airgap bundle and installs octo-pod (tools/vm-image-provision.sh),
# then the host publishes the result as the LXD image <alias>/<version> and deletes the
# build VM.
#
# ONLY SOURCE CROSSES THE LINK. The laptop sends this repository as a few-megabyte
# archive of its committed tree; every download (packages, crates,
# base images) happens on the host's own uplink. That matters where the laptop reaches the
# host over a mesh that moves a fraction of a megabyte a second.
#
# PROVENANCE IS THE COMMIT. The image is built from HEAD, so a tree with uncommitted
# changes is refused: what the manifest names must be what was built. The version is
# VERSION-<short sha>-<arch>, and an existing image with that alias is never overwritten.
#
# THE HOST'S ARCHITECTURE IS THE IMAGE'S. LXD runs VMs of its own architecture only, and
# every image is compiled natively, as GOAT builds per-architecture images: an amd64 host
# makes the amd64 image, an arm64 host makes the arm64 one, from the same commit.
#
# NO IDENTITY GOES IN. No pod name, setup key, certificate or site address is sent to the
# host or baked into the image; the provisioning script checks that before it finishes.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO
HOST=""
MANIFEST=""
ALIAS="octo-pod-gateway"
KEEP=0
CPUS=12
MEMORY="24GiB"
DISK="60GiB"
BUILDER=""
STARTED=0

die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
say() { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

usage() { sed -n '2,6p' "$0"; }

remote() { ssh -o BatchMode=yes -o ConnectTimeout=30 "${HOST}" "$@"; }

# On any failure after the build VM exists: delete it, unless --keep asked to inspect it.
on_exit() {
  local rc=$?
  if [[ "${rc}" -ne 0 && "${STARTED}" -eq 1 ]]; then
    if [[ "${KEEP}" -eq 1 ]]; then
      printf '\nBuild failed; kept %s on %s for inspection:\n  ssh %s lxc exec %s -- bash\n' \
        "${BUILDER}" "${HOST}" "${HOST}" "${BUILDER}" >&2
    else
      printf '\nBuild failed; deleting %s (re-run with --keep to inspect it).\n' "${BUILDER}" >&2
      remote "lxc delete --force '${BUILDER}'" >/dev/null 2>&1 || true
    fi
  fi
  [[ -n "${TMP:-}" ]] && rm -rf "${TMP}"
  exit "${rc}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --host) if [ $# -lt 2 ] || [ -z "$2" ]; then die "$1 needs a value"; fi; HOST="$2"; shift 2 ;;
      --manifest) if [ $# -lt 2 ] || [ -z "$2" ]; then die "$1 needs a value"; fi; MANIFEST="$2"; shift 2 ;;
      --alias) if [ $# -lt 2 ] || [ -z "$2" ]; then die "$1 needs a value"; fi; ALIAS="$2"; shift 2 ;;
      --cpus) if [ $# -lt 2 ] || [ -z "$2" ]; then die "$1 needs a value"; fi; CPUS="$2"; shift 2 ;;
      --memory) if [ $# -lt 2 ] || [ -z "$2" ]; then die "$1 needs a value"; fi; MEMORY="$2"; shift 2 ;;
      --disk) if [ $# -lt 2 ] || [ -z "$2" ]; then die "$1 needs a value"; fi; DISK="$2"; shift 2 ;;
      --keep) KEEP=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
  done
  [[ -n "${HOST}" ]] || die "--host <ssh-target> is required: the LXD host that builds the image"
  [[ -f "${MANIFEST}" ]] || die "--manifest <file> is required: the substrate packages.manifest"
  [[ "${ALIAS}" =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "--alias must be lowercase [a-z0-9-]"
}

preflight() {
  step "preflight"
  [[ -z "$(git -C "${REPO}" status --porcelain)" ]] \
    || die "uncommitted changes in ${REPO}: the image is built from HEAD, so commit first"
  local version sha machine arch
  version="$(tr -d '[:space:]' < "${REPO}/VERSION")"
  sha="$(git -C "${REPO}" rev-parse --short=7 HEAD)"
  machine="$(remote "uname -m")" || die "${HOST} does not answer over ssh"
  case "${machine}" in
    x86_64) arch=amd64 ;;
    aarch64) arch=arm64 ;;
    *) die "${HOST} is ${machine}; pods run on amd64 or arm64" ;;
  esac
  IMAGE_VERSION="${version}-${sha}-${arch}"
  IMAGE_ALIAS="${ALIAS}/${IMAGE_VERSION}"
  BUILDER="octo-image-build-${sha}-${arch}"
  say "image      ${IMAGE_ALIAS}"
  say "octo-pod   $(git -C "${REPO}" rev-parse HEAD)"

  remote "command -v lxc >/dev/null" || die "${HOST} does not answer, or has no lxc"
  if remote "lxc image info '${IMAGE_ALIAS}' >/dev/null 2>&1"; then
    die "${IMAGE_ALIAS} already exists on ${HOST}; an image is never overwritten"
  fi
  if remote "lxc info '${BUILDER}' >/dev/null 2>&1"; then
    die "${BUILDER} already exists on ${HOST}; delete it or wait for the build using it"
  fi
  say "host ok, alias and build VM name are free"
}

make_bundle() {
  step "source bundle (committed trees only)"
  TMP="$(mktemp -d)"
  mkdir -p "${TMP}/build"
  git -C "${REPO}" archive --format=tar --prefix=octo-pod/ HEAD | tar -x -C "${TMP}/build"
  cp "${MANIFEST}" "${TMP}/build/packages.manifest"
  {
    printf 'image: %s\n' "${IMAGE_ALIAS}"
    printf 'octo_pod_commit: %s\n' "$(git -C "${REPO}" rev-parse HEAD)"
  } > "${TMP}/build/BUILD_INFO"
  # No extended attributes: macOS tar would otherwise carry its provenance xattrs, which
  # mean nothing on the host and make GNU tar warn about every file.
  COPYFILE_DISABLE=1 tar --no-xattrs -czf "${TMP}/bundle.tgz" -C "${TMP}" build
  say "$(du -h "${TMP}/bundle.tgz" | cut -f1) to send"
}

launch_builder() {
  step "build VM ${BUILDER} on ${HOST}"
  scp -q -o BatchMode=yes "${TMP}/bundle.tgz" "${HOST}:/tmp/${BUILDER}.tgz"
  remote "lxc launch ubuntu:24.04 '${BUILDER}' --vm -c limits.cpu='${CPUS}' \
    -c limits.memory='${MEMORY}' -d root,size='${DISK}'" >/dev/null
  STARTED=1
  say "waiting for the VM agent"
  local i
  for i in $(seq 1 60); do
    if remote "lxc exec '${BUILDER}' -- true" >/dev/null 2>&1; then break; fi
    [[ "${i}" -lt 60 ]] || die "${BUILDER} never answered lxc exec"
    sleep 5
  done
  remote "lxc exec '${BUILDER}' -- cloud-init status --wait" >/dev/null 2>&1 || true
  remote "lxc file push '/tmp/${BUILDER}.tgz' '${BUILDER}/root/bundle.tgz' && \
    lxc exec '${BUILDER}' -- tar xzf /root/bundle.tgz -C /root && \
    lxc exec '${BUILDER}' -- cp /root/build/octo-pod/tools/vm-image-provision.sh /root/provision.sh"
}

provision() {
  step "provisioning inside the build VM (the long step: every image compiles from source)"
  remote "lxc exec '${BUILDER}' -- bash /root/provision.sh"
}

publish() {
  step "publish ${IMAGE_ALIAS}"
  local outdir="${REPO}/dist/vm-image"
  mkdir -p "${outdir}"
  remote "lxc exec '${BUILDER}' -- cat /etc/octo-pod-image/MANIFEST" \
    > "${outdir}/${ALIAS}-${IMAGE_VERSION}.manifest"
  remote "lxc stop '${BUILDER}' && lxc publish '${BUILDER}' --alias '${IMAGE_ALIAS}' \
    description='octo-pod golden image ${IMAGE_VERSION}: substrate, octo-pod, images; no identity'" \
    >/dev/null
  local fp
  fp="$(remote "lxc image info '${IMAGE_ALIAS}'" | sed -n 's/^Fingerprint: //p')"
  remote "lxc delete '${BUILDER}' && rm -f '/tmp/${BUILDER}.tgz'"
  STARTED=0
  printf '\nPublished %s on %s\n  fingerprint %s\n  manifest    %s\n' \
    "${IMAGE_ALIAS}" "${HOST}" "${fp}" "${outdir}/${ALIAS}-${IMAGE_VERSION}.manifest"
}

main() {
  trap on_exit EXIT
  parse_args "$@"
  preflight
  make_bundle
  launch_builder
  provision
  publish
}

main "$@"
