#!/usr/bin/env bash
# vm-image-provision.sh: turn a fresh Ubuntu 24.04 build VM into the octo-pod golden image.
#
# Runs INSIDE the build VM, as root, started by tools/build-vm-image.sh. It expects the
# source bundle that script pushes, unpacked at /root/build:
#
#   /root/build/octo-pod/          this repository at the commit being imaged (git archive)
#   /root/build/packages.manifest  the base image package list
#   /root/build/BUILD_INFO         version and commits, written by the builder
#
# WHAT THE IMAGE CARRIES is every dependency that needs a network to obtain: the substrate
# packages, NetBird, cosign, the octo-pod package, and every container image a pod runs,
# staged as the airgap bundle bootstrap already knows how to install (an OCI layout plus
# the registry and skopeo it is served with). The octo-pod images are built here from
# source, so no registry credential ever reaches the build VM or the image.
#
# WHAT IT NEVER CARRIES is identity. No pod name, no setup key, no certificate, no mesh
# enrolment, no site address. Those arrive per instance: cloud-init, then
#   octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000
#   octo-pod set-mesh --mirror localhost:5000 ...
#   octo-pod join ...
# The last step of this script checks that, and refuses to hand back an image that fails.
#
# ONE ARCHITECTURE PER IMAGE: the build VM's own. Everything that differs by architecture
# (cosign, the registry and skopeo tars, the images built from source) follows
# `dpkg --print-architecture`, so the same script makes an amd64 image on an amd64 host
# and an arm64 image on an arm64 host. The public images are copied whole (every platform
# in their index), because their pins are index digests.
set -euo pipefail

readonly BUILD=/root/build
readonly SRC="${BUILD}/octo-pod"
readonly MANIFEST="${BUILD}/packages.manifest"
readonly STAGE=/opt/octo-stage
readonly OCI="${STAGE}/images/oci"
readonly IMAGE_INFO_DIR=/etc/octo-pod-image
readonly GROUP_VARS="${SRC}/platform/group_vars/all/10-infrastructure.yml"
ARCH="$(dpkg --print-architecture)"
readonly ARCH
readonly COSIGN_VERSION="2.4.1"
readonly REGISTRY_IMAGE="docker.io/library/registry:2"
readonly SKOPEO_IMAGE="quay.io/skopeo/stable:latest"
# What octo-pod's playbooks install on a pod, plus its CLI's runtime: python3-yaml and
# python3-jinja2 for the CLI, openssl for the pen CA, fake-hwclock for the clock an
# airgapped bootstrap cannot install, python3-venv and python3-pip for verify's zenoh
# client. An airgapped pod can fetch none of it, so the image carries all of it;
# test_templates.py fails when a playbook installs a package that is not listed here.
readonly -a PLATFORM_PKGS=(python3-yaml python3-jinja2 openssl fake-hwclock ca-certificates
  gnupg netcat-openbsd nftables logrotate python3-venv python3-pip)
VERIFY_ZENOH=""
# Arms built from arms/<name>/Dockerfile, and the repository path each is pinned under.
readonly -a ARMS=(udp-ingest tcp-ingest tcp-connect health-pub egress sapient uplink-bridge)

log() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# The value of `<name>_upstream:` in the shipped group_vars: the pinned reference.
read_pin() {
  sed -n "s/^$1_upstream: *\"\([^\"]*\)\".*/\1/p" "${GROUP_VARS}" | head -1
}

# Replace one `<name>: "<value>"` line in the BUILD TREE's group_vars. The repository is
# never touched: this is the release the image carries, pinned to the images it carries,
# exactly as CI pins a release to the images it published.
set_var() {
  local name="$1" value="$2"
  grep -q "^${name}: " "${GROUP_VARS}" || die "no ${name} line in ${GROUP_VARS}"
  sed -i "s|^${name}: .*|${name}: \"${value}\"   # pinned by the image build|" "${GROUP_VARS}"
}

check_inputs() {
  log "inputs"
  [[ "$(id -u)" -eq 0 ]] || die "run as root"
  for p in "${SRC}/arms/Cargo.toml" "${MANIFEST}" "${BUILD}/BUILD_INFO"; do
    [[ -e "${p}" ]] || die "missing ${p}: was the source bundle unpacked at ${BUILD}?"
  done
  grep -q '^VERSION_ID="24.04"' /etc/os-release || die "this build expects Ubuntu 24.04"
  [[ "${ARCH}" == amd64 || "${ARCH}" == arm64 ]] \
    || die "pods run on amd64 or arm64; this build VM is ${ARCH}"
  note "architecture ${ARCH}"
  cat "${BUILD}/BUILD_INFO"
}

install_substrate() {
  log "substrate packages (${MANIFEST})"
  export DEBIAN_FRONTEND=noninteractive
  local -a pkgs=()
  local line
  while read -r line; do
    line="${line%%#*}"
    line="${line//[[:space:]]/}"
    [[ -n "${line}" && "${line}" != "netbird" ]] && pkgs+=("${line}")
  done < "${MANIFEST}"
  note "manifest: ${pkgs[*]} netbird"
  apt-get update -qq
  # The manifest, plus everything octo-pod's playbooks install (PLATFORM_PKGS).
  apt-get install -y -qq "${pkgs[@]}" "${PLATFORM_PKGS[@]}" >/dev/null

  log "NetBird agent (the one permitted WAN path; enrolment stays per instance)"
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://pkgs.netbird.io/debian/public.key -o /etc/apt/keyrings/netbird.asc
  echo "deb [signed-by=/etc/apt/keyrings/netbird.asc] https://pkgs.netbird.io/debian stable main" \
    > /etc/apt/sources.list.d/netbird.list
  apt-get update -qq
  apt-get install -y -qq netbird >/dev/null
  # Enabled, because join enrols through the running agent, but stopped and wiped: the
  # package starts the daemon, which writes a config holding a WireGuard private key.
  # Baked into the image, every instance would share that key. Wiped, each instance's
  # first boot mints its own.
  systemctl stop netbird >/dev/null 2>&1 || true
  rm -f /etc/netbird/config.json
  rm -rf /var/lib/netbird/*

  log "cosign ${COSIGN_VERSION} (octo-pod upgrade verifies releases with it)"
  local base="https://github.com/sigstore/cosign/releases/download/v${COSIGN_VERSION}"
  local bin="cosign-linux-${ARCH}"
  curl -fsSL "${base}/${bin}" -o "/tmp/${bin}"
  curl -fsSL "${base}/cosign_checksums.txt" -o /tmp/cosign_checksums.txt
  (cd /tmp && grep " ${bin}\$" cosign_checksums.txt | sha256sum -c -) \
    || die "cosign checksum did not match its release's checksum file"
  install -m 0755 "/tmp/${bin}" /usr/local/bin/cosign
}

build_images() {
  log "container images, built from source"
  local arm
  for arm in "${ARMS[@]}"; do
    note "building octo-${arm}"
    podman build -q -f "${SRC}/arms/${arm}/Dockerfile" -t "localhost/octo-${arm}:image" "${SRC}/arms" \
      >/dev/null || die "could not build octo-${arm}"
  done
}

stage_bundle() {
  log "airgap bundle at ${STAGE}"
  install -d -m 0755 "${OCI}"
  : > "${STAGE}/images/MAP"

  # Public images, by the digests octo-pod pins. --all keeps the whole index: the pins
  # are index digests, and an index of one platform hashes differently.
  local tag name ref path digest
  for spec in zenoh:zenoh_image audit-sink:audit_image; do
    tag="${spec%%:*}"; name="${spec#*:}"
    ref="$(read_pin "${name}")"
    [[ "${ref}" == *@sha256:* ]] || die "${name} is not digest-pinned in ${GROUP_VARS}"
    path="$(printf '%s' "${ref%@*}" | sed -E 's#^[a-z0-9-]+[.:][a-z0-9.:-]*/##')"
    note "copy ${ref}"
    skopeo copy -q --all --preserve-digests "docker://${ref}" "oci:${OCI}:${tag}" \
      || die "could not copy ${ref}"
    printf '%s %s %s\n' "${tag}" "${path}" "${ref##*@}" >> "${STAGE}/images/MAP"
  done

  # The images built here. The digest is that of the manifest written into the layout,
  # which the registry serves byte for byte after bootstrap seeds it.
  local arm var
  for arm in "${ARMS[@]}"; do
    skopeo copy -q "containers-storage:localhost/octo-${arm}:image" "oci:${OCI}:${arm}" \
      || die "could not stage octo-${arm}"
    digest="sha256:$(skopeo inspect --raw "oci:${OCI}:${arm}" | sha256sum | cut -d' ' -f1)"
    path="usareur-af/octo-${arm}"
    printf '%s %s %s\n' "${arm}" "${path}" "${digest}" >> "${STAGE}/images/MAP"
    var="${arm//-/_}_image_upstream"
    set_var "${var}" "ghcr.io/${path}@${digest}"
    note "${var} -> ${digest}"
  done

  # The registry and skopeo that serve the layout on the pod, carried by tag, which is
  # what a save/load preserves, for this image's architecture: bootstrap loads
  # <tool>-<arch>.tar for the architecture it runs on.
  local spec2 tool
  for spec2 in "registry:${REGISTRY_IMAGE}" "skopeo:${SKOPEO_IMAGE}"; do
    tool="${spec2%%:*}"; ref="${spec2#*:}"
    podman pull -q --platform "linux/${ARCH}" "${ref}" >/dev/null || die "could not pull ${ref}"
    podman save -q "${ref}" -o "${STAGE}/images/${tool}-${ARCH}.tar" || die "could not save ${ref}"
  done
  printf '%s\n' "${REGISTRY_IMAGE}" > "${STAGE}/images/REGISTRY_IMAGE"
  printf '%s\n' "${SKOPEO_IMAGE}" > "${STAGE}/images/SKOPEO_IMAGE"

  log "verifying the layout serves every entry in MAP"
  while read -r tag path digest; do
    got="sha256:$(skopeo inspect --raw "oci:${OCI}:${tag}" | sha256sum | cut -d' ' -f1)"
    [[ "${got}" == "${digest}" ]] || die "${tag}: layout holds ${got}, MAP says ${digest}"
  done < "${STAGE}/images/MAP"
}

install_octo_pod() {
  log "octo-pod package, built from this commit with the pins above"
  (cd "${SRC}" && tools/build-release.sh >/dev/null) || die "tools/build-release.sh failed"
  local deb
  deb="$(ls "${SRC}"/dist/octo-pod_*_all.deb)"
  apt-get install -y -qq "${deb}" >/dev/null || die "could not install ${deb}"
  [[ -x /opt/octo-pod/bin/octo-pod ]] || die "/opt/octo-pod/bin/octo-pod is missing after install"
  grep -q 'pinned by the image build' /opt/octo-pod/platform/group_vars/all/10-infrastructure.yml \
    || die "the installed platform does not carry the image's pins"
  ln -sf /opt/octo-pod/bin/octo-pod /usr/local/sbin/octo-pod
}

# verify-octo-pod.yml tests the pen's ACL with a zenoh Python client in this venv. Built
# here, at the version the release pins, so an airgapped pod never reaches for PyPI.
build_verify_venv() {
  VERIFY_ZENOH="$(sed -n 's/^verify_zenoh_version: *"\([^"]*\)".*/\1/p' "${GROUP_VARS}")"
  [[ -n "${VERIFY_ZENOH}" ]] || die "no verify_zenoh_version in ${GROUP_VARS}"
  log "verify's zenoh client: eclipse-zenoh ${VERIFY_ZENOH} in /opt/octo-verify/venv"
  python3 -m venv /opt/octo-verify/venv || die "could not create /opt/octo-verify/venv"
  /opt/octo-verify/venv/bin/pip install -q --no-cache-dir "eclipse-zenoh==${VERIFY_ZENOH}" \
    || die "could not install eclipse-zenoh ${VERIFY_ZENOH}"
}

write_manifest() {
  log "image manifest at ${IMAGE_INFO_DIR}/MANIFEST"
  install -d -m 0755 "${IMAGE_INFO_DIR}"
  {
    cat "${BUILD}/BUILD_INFO"
    printf 'built_at: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'os: %s\n' "$(sed -n 's/^PRETTY_NAME="\(.*\)"$/\1/p' /etc/os-release)"
    printf 'arch: %s\n' "${ARCH}"
    printf 'substrate_manifest_sha256: %s\n' "$(sha256sum "${MANIFEST}" | cut -d' ' -f1)"
    printf 'podman: %s\n' "$(podman --version | awk '{print $3}')"
    printf 'skopeo: %s\n' "$(skopeo --version | awk '{print $3}')"
    printf 'ansible_core: %s\n' "$(dpkg-query -W -f='${Version}' ansible-core 2>/dev/null || echo none)"
    printf 'netbird: %s\n' "$(dpkg-query -W -f='${Version}' netbird)"
    printf 'cosign: %s\n' "${COSIGN_VERSION}"
    printf 'verify_zenoh: eclipse-zenoh==%s\n' "${VERIFY_ZENOH}"
    printf 'octo_pod_package: %s\n' "$(dpkg-query -W -f='${Version}' octo-pod)"
    printf 'images:\n'
    while read -r tag path digest; do printf '  %s: %s@%s\n' "${tag}" "${path}" "${digest}"; done \
      < "${STAGE}/images/MAP"
  } > "${IMAGE_INFO_DIR}/MANIFEST"
  cat "${IMAGE_INFO_DIR}/MANIFEST"
}

# Generic means generic: nothing in the image may name a pod, a mesh or a site.
check_generic() {
  log "purity: no identity, no enrolment, no site"
  local bad=0
  [[ ! -e /etc/octo-pod ]] || { note "FAIL /etc/octo-pod exists"; bad=1; }
  systemctl stop netbird >/dev/null 2>&1 || true
  rm -f /etc/netbird/config.json
  rm -rf /var/lib/netbird/*
  [[ -z "$(ls -A /var/lib/netbird 2>/dev/null)" && ! -e /etc/netbird/config.json ]] \
    || { note "FAIL NetBird holds state (a WireGuard key would be shared by every instance)"; bad=1; }
  [[ ! -e /root/nb.key ]] || { note "FAIL a setup key is present"; bad=1; }
  for cmd in podman skopeo ansible-playbook netbird cosign chronyd octo-pod; do
    command -v "${cmd}" >/dev/null || { note "FAIL ${cmd} is missing"; bad=1; }
  done
  [[ "${bad}" -eq 0 ]] || die "the image is not generic or not complete; see above"
  note "ok"
}

clean() {
  log "cleaning instance state"
  podman system reset -f >/dev/null 2>&1 || true
  apt-get clean
  rm -rf /var/lib/apt/lists/* "${BUILD}" /tmp/* /root/.bash_history
  rm -f /etc/ssh/ssh_host_*
  cloud-init clean --logs --seed >/dev/null 2>&1 || true
  journalctl --rotate >/dev/null 2>&1 || true
  journalctl --vacuum-time=1s >/dev/null 2>&1 || true
  truncate -s 0 /etc/machine-id
  rm -f /var/lib/dbus/machine-id
}

main() {
  check_inputs
  install_substrate
  build_images
  stage_bundle
  install_octo_pod
  build_verify_venv
  write_manifest
  check_generic
  clean
  log "done: power off and publish"
}

main "$@"
