#!/usr/bin/env bash
# Build what a pod installs from. Run in a clone; writes dist/.
#
#   tools/build-release.sh                 version from ./VERSION
#   tools/build-release.sh 1.4.0           override the version
#   tools/build-release.sh --stage-only D  assemble the tree at D and stop (tests use this)
#   tools/build-release.sh --repo O/N      the repository whose release workflow signs it
#                                          (default: $GITHUB_REPOSITORY, set in Actions)
#   tools/build-release.sh --docs D        also package the built documentation site at D
#
# TWO ARTIFACTS FROM ONE TREE:
#   octo-pod_<v>_all.deb    for a pod that can reach apt. dpkg owns /opt/octo-pod, so
#                           the platform layer is not a directory anyone is invited to edit.
#   octo-pod-<v>.tar.gz     for everything else, and for a USB stick. One file is a far
#                           better airgap story than teaching someone to clone a repo.
#
# Architecture: all. The platform layer is text — yaml, jinja, bash, python. The only
# compiled things are the arms, and they ship as digest-pinned container images on their
# own schedule (see .github/workflows/images.yml).
#
# NO arms/ SOURCE IS SHIPPED. A pod pulls the pinned images; it does not build them.
# configure-octo-pod.yml already decides which it is by looking for arms/Cargo.toml, so
# leaving the source out is what puts a pod on the pulled path — and keeps crates.io out
# of a border site's deployment path.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE_ONLY=""
VERSION=""
DOCS_DIR=""
RELEASE_REPO="${GITHUB_REPOSITORY:-}"

# A value-taking flag with nothing after it must say so, not die on an unbound $2.
need_value() { [ $# -ge 2 ] || { echo "$1 needs a value" >&2; exit 2; }; }

while [ $# -gt 0 ]; do
  case "$1" in
    --stage-only) need_value "$@"; STAGE_ONLY="$2"; shift 2 ;;
    --with-images) WITH_IMAGES=1; shift ;;
    --docs)       need_value "$@"; DOCS_DIR="$2"; shift 2 ;;
    --repo)       need_value "$@"; RELEASE_REPO="$2"; shift 2 ;;
    -h|--help)    sed -n '2,12p' "$0"; exit 0 ;;
    *)            VERSION="$1"; shift ;;
  esac
done

[ -n "$VERSION" ] || VERSION="$(tr -d '[:space:]' < "$REPO/VERSION")"
if [ -n "$RELEASE_REPO" ] && ! [[ "$RELEASE_REPO" =~ ^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$ ]]; then
  echo "repository '$RELEASE_REPO' is not <owner>/<name>" >&2; exit 2
fi
# THE UPGRADER'S OWN TEST (valid_version in bin/octo-pod-upgrade), so a version this
# builds is one every pod accepts. A looser glob let 1.2.3junk through, and once that
# reached LATEST every pod refused every upgrade until another release replaced it.
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(~[a-z]+[0-9]+)?$ ]] \
  || { echo "version '$VERSION' is not X.Y.Z or X.Y.Z~<word><N>" >&2; exit 2; }
# --docs takes a documentation site that is already built. Refuse anything that is not
# one before building anything else.
if [ -n "$DOCS_DIR" ] && [ ! -f "$DOCS_DIR/index.html" ]; then
  echo "--docs $DOCS_DIR is not a built documentation site (no index.html)" >&2; exit 2
fi

# What a pod needs, and nothing else. Listed positively: a deny-list would ship whatever
# lands in the repo next.
PAYLOAD=(bin platform example README.md LICENSE NOTICE VERSION)

# The bundle builder ships too, so ONE connected box at a site can build the image
# bundle the disconnected ones install from — the seed case, and the only one where a
# pod building a bundle makes sense. A pod that can pull does not need a bundle, and a
# pod that cannot pull cannot build one; the useful arrangement is one box that can,
# serving the rest.
#
# Only this one file out of tools/. build-release.sh packages the software and has no
# business on a pod; the release workflow is not a thing a pod runs.
# The release must be the COMPLETE kit, workstation side included: a laptop
# that downloads the published tarball can install, key, and deprovision pods
# without ever cloning the repo ("the operator never types git" applies to
# the workstation too — install-to-pod living only in git was the chicken-egg:
# the tool that installs from a release was not in the release).
# Keep the README's operator guides available offline, without copying all of
# docs/ (design files and local editor backups are not release payload).
EXTRA_FILES=(tools/build-airgap-bundle.sh
             tools/install-to-pod.sh tools/mint-key.sh tools/deprovision-pod.sh docs/images.md
             CHANGELOG.md docs/deployment.md docs/operations.md docs/upgrading.md docs/faq.md
             docs/release-status.md docs/guide-zenoh-partners.md docs/mac-airgap-images.md)

WITH_IMAGES="${WITH_IMAGES:-0}"
DIST="$REPO/dist"
STAGE="${STAGE_ONLY:-$DIST/stage}"
PKGROOT="$STAGE/opt/octo-pod"

rm -rf "$STAGE"
mkdir -p "$PKGROOT"
for p in "${PAYLOAD[@]}"; do
  [ -e "$REPO/$p" ] || { echo "missing payload: $p" >&2; exit 1; }
  cp -R "$REPO/$p" "$PKGROOT/"
done

for f in "${EXTRA_FILES[@]}"; do
  mkdir -p "$PKGROOT/$(dirname "$f")"
  cp -a "$REPO/$f" "$PKGROOT/$f"
done
printf '%s\n' "$VERSION" > "$PKGROOT/VERSION"
# WHO BUILT IT. octo-pod upgrade checks the next release's signature against the
# release workflow of the repository named here, so a fork's pods trust the fork's
# releases and nobody's identity is written into the code. Empty means the release was
# built outside a release workflow, and so is unsigned.
cat > "$PKGROOT/RELEASE" <<EOF
repository=${RELEASE_REPO}
issuer=https://token.actions.githubusercontent.com
EOF

# Nothing generated, cached or secret rides along. __pycache__ is the routine one; the
# *.pem sweep is the one that matters, since a staged slot key in a release artifact
# would be published to every pod in the fleet.
find "$PKGROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PKGROOT" \( -name '*.pyc' -o -name '.DS_Store' \) -delete 2>/dev/null || true
# NUL-delimited: a path with a space in it must not become two paths, least of all in
# the check that exists to stop a credential shipping.
leaked=()
while IFS= read -r -d '' f; do
  leaked+=("$f")
done < <(find "$PKGROOT" \( -name '*.pem' -o -name '*.key' -o -name '.env' \) -print0)
if (( ${#leaked[@]} > 0 )); then
  echo "refusing to build: credential material in payload:" >&2
  printf '  %s\n' "${leaked[@]}" >&2
  exit 1
fi

if [ -n "$STAGE_ONLY" ]; then
  echo "staged $VERSION at $STAGE_ONLY"
  exit 0
fi

mkdir -p "$DIST"

# ---- tarball: unpacks INTO a directory, no wrapping component ----------------
# `tar -C … .` so the upgrade path can unpack straight into /opt/octo-pod-<v> without
# guessing at a top-level directory name.
TAR="$DIST/octo-pod-$VERSION.tar.gz"
tar -czf "$TAR" -C "$PKGROOT" .
( cd "$DIST" && shasum -a 256 "$(basename "$TAR")" > "$(basename "$TAR").sha256" )
echo "built $TAR"

# ---- deb -------------------------------------------------------------------
# THE HOST CONTRACT, DECLARED. Installing the package with apt on a stock host brings
# everything a pod needs from the distribution: the container runtime (podman 4.7 or
# later, for Quadlet), skopeo, nftables for the pod firewall, openssl for the pen CA, and
# what the CLI and the local converge run on. A distribution with an older podman
# refuses the package at install time, which is the right moment to learn it. A golden
# image carries all of this already, so dpkg finds every dependency on a box that will
# never reach a network. The NetBird agent is not a dependency: a pod that joins nothing
# needs none, and bootstrap installs it from NetBird's own repository.
mkdir -p "$STAGE/DEBIAN"
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: octo-pod
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Depends: bash-completion, ansible-core, python3-yaml, python3-jinja2, podman (>= 4.7), skopeo, nftables, openssl, ca-certificates
Maintainer: octo-pod maintainers <noreply@example.invalid>
Description: Edge appliance that gives sensor feeds identity and an audit trail
 Takes sensor feeds from UDP or TCP sockets, or from Zenoh peers, gives each source
 its own certificate and audit trail, and forwards only what is explicitly released
 onto the shared fabric.
 .
 This package is the platform layer only: identical on every pod, and replaced whole
 on upgrade. A pod's own identity lives in /etc/octo-pod and is never touched here.
CONTROL

# The CLI on PATH, owned by dpkg rather than created by a bootstrap that may not have run.
mkdir -p "$STAGE/usr/bin"
ln -sf /opt/octo-pod/bin/octo-pod "$STAGE/usr/bin/octo-pod"
ln -sf octo-pod "$STAGE/usr/bin/otp"

# OUTSIDE /opt, because this is the one file that only works from where the shell looks
# for it. bash-completion loads /usr/share/bash-completion/completions/<command> lazily,
# by command name, so the filename is the interface and dropping it in /opt/octo-pod
# would ship a completion nobody ever gets.
if [ -f "$REPO/completions/octo-pod.bash" ]; then
  mkdir -p "$STAGE/usr/share/bash-completion/completions"
  cp -a "$REPO/completions/octo-pod.bash" \
        "$STAGE/usr/share/bash-completion/completions/octo-pod"
  ln -sf octo-pod "$STAGE/usr/share/bash-completion/completions/otp"
fi
if [ -f "$REPO/completions/_octo-pod" ]; then
  mkdir -p "$STAGE/usr/share/zsh/vendor-completions"
  cp -a "$REPO/completions/_octo-pod" "$STAGE/usr/share/zsh/vendor-completions/_octo-pod"
fi

if command -v dpkg-deb >/dev/null 2>&1; then
  DEB="$DIST/octo-pod_${VERSION}_all.deb"
  dpkg-deb --root-owner-group --build "$STAGE" "$DEB" >/dev/null
  ( cd "$DIST" && shasum -a 256 "$(basename "$DEB")" > "$(basename "$DEB").sha256" )
  echo "built $DEB"
else
  echo "note: dpkg-deb not present — tarball only, .deb staged at $STAGE"
fi

# ---- the manifest a pod actually trusts -------------------------------------
# One file listing every artifact's hash. THIS is what gets cosign-signed in CI,
# which is what makes one signature cover the whole release: verify SHA256SUMS,
# then a plain hash check establishes any artifact listed in it.
# Only what was actually built: off Linux there is no .deb, and hashing a file that is
# not there would fail the build rather than produce a shorter manifest.
# THE IMAGE BUNDLE, built here rather than as a separate CI step, so it cannot be
# produced after SHA256SUMS and end up unlisted — an artifact the signature does not
# cover is one a pod must refuse, and discovering that at a site is expensive.
#
# It exists so an EDGE POD NEVER HOLDS A REGISTRY CREDENTIAL. Building it needs a
# credential; running from it does not. A captured pod yields the bytes it was already
# running and no access to anything else, which a PAT in ~/.docker/config.json does not
# give you. Airgap was the second reason, not the first.
if [ "$WITH_IMAGES" = 1 ]; then
  echo "building the image bundle (this pulls every pinned image)"
  IMGDIR="$DIST/images-build"
  rm -rf "$IMGDIR"
  "$REPO/tools/build-airgap-bundle.sh" --out "$IMGDIR" \
    || { echo "image bundle failed — a release without one is still valid, but pods" >&2
         echo "that cannot hold a credential will have nothing to install from." >&2
         exit 1; }
  ( cd "$IMGDIR" && tar -czf "$DIST/octo-pod-images-$VERSION.tar.gz" images )
  ( cd "$DIST" && shasum -a 256 "octo-pod-images-$VERSION.tar.gz" > "octo-pod-images-$VERSION.tar.gz.sha256" )
  rm -rf "$IMGDIR"
  echo "built $DIST/octo-pod-images-$VERSION.tar.gz"
fi

# THE DOCUMENTATION SITE, packaged so each release carries the documentation that
# matches it. It is listed in SHA256SUMS only when --docs was given to THIS run, never
# because a file of that name is left in dist/ from an earlier build: an artifact the
# signature covers must be one this build produced.
if [ -n "$DOCS_DIR" ]; then
  DOCSTAGE="$(mktemp -d)"
  cp -R "$DOCS_DIR" "$DOCSTAGE/octo-pod-docs-$VERSION"
  tar -czf "$DIST/octo-pod-docs-$VERSION.tar.gz" -C "$DOCSTAGE" "octo-pod-docs-$VERSION"
  rm -rf "$DOCSTAGE"
  ( cd "$DIST" && shasum -a 256 "octo-pod-docs-$VERSION.tar.gz" > "octo-pod-docs-$VERSION.tar.gz.sha256" )
  echo "built $DIST/octo-pod-docs-$VERSION.tar.gz"
fi

ARTIFACTS=()
if [ -f "$DIST/octo-pod-$VERSION.tar.gz" ];    then ARTIFACTS+=("octo-pod-$VERSION.tar.gz"); fi
if [ -f "$DIST/octo-pod_${VERSION}_all.deb" ]; then ARTIFACTS+=("octo-pod_${VERSION}_all.deb"); fi
if [ -f "$DIST/octo-pod-images-$VERSION.tar.gz" ]; then ARTIFACTS+=("octo-pod-images-$VERSION.tar.gz"); fi
if [ -n "$DOCS_DIR" ]; then ARTIFACTS+=("octo-pod-docs-$VERSION.tar.gz"); fi
( cd "$DIST" && shasum -a 256 "${ARTIFACTS[@]}" > SHA256SUMS )
printf '%s\n' "$VERSION" > "$DIST/LATEST"

# The landing page, mirrored to the bucket beside the artifacts. Verify steps go
# ON the page because an operator who has just downloaded is the one who needs them.
#
# THE SIGNER IS THE RELEASE WORKFLOW ON A RELEASE TAG, the same pattern octo-pod upgrade
# and install-to-pod.sh accept. A pattern that stops at the repository is met by any
# workflow in it on any ref, and this is the check a new operator runs before anything
# else can. The repository is quoted because its '.' would match any character.
if [ -n "$RELEASE_REPO" ]; then
  SIGNER_REPO="$(printf '%s' "$RELEASE_REPO" | sed 's/[][\\.*^$+?(){}|]/\\&/g')"
else
  SIGNER_REPO="<owner>/<repo>"
fi
cat > "$DIST/index.html" <<HTML
<!doctype html><meta charset=utf-8>
<title>octo-pod $VERSION</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:46rem;margin:3rem auto;padding:0 1rem}
code,pre{background:#f4f4f5;border-radius:4px}pre{padding:1rem;overflow-x:auto}code{padding:.1em .3em}</style>
<h1>octo-pod $VERSION</h1>
<p>An edge appliance that gives sensor feeds identity and an audit trail. A pod
installs one of these once; every version after that comes from
<code>sudo octo-pod upgrade</code>.</p>
<ul>
  <li><a href="octo-pod-$VERSION.tar.gz">octo-pod-$VERSION.tar.gz</a> — any Linux</li>
  <li><a href="octo-pod_${VERSION}_all.deb">octo-pod_${VERSION}_all.deb</a> — Debian/Ubuntu</li>
  <li><a href="octo-pod-images-${VERSION}.tar.gz">octo-pod-images-${VERSION}.tar.gz</a> — the
      container images, so a pod never holds a registry credential (optional, large)</li>
  <li><a href="SHA256SUMS">SHA256SUMS</a> + <a href="SHA256SUMS.cosign-bundle">signature</a></li>
</ul>
<h2>Verify before you install</h2>
<pre>cosign verify-blob \\
  --bundle SHA256SUMS.cosign-bundle \\
  --certificate-identity-regexp '^https://github\\.com/(?i:${SIGNER_REPO})/\\.github/workflows/release\\.yml@refs/tags/octo-pod-v' \\
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \\
  SHA256SUMS

sha256sum -c --ignore-missing SHA256SUMS</pre>
<p>Integrity here is the signature, not this page: the artifacts are public by
design, and the identity above, the release workflow running for a release tag, is
what proves they came from the release pipeline.</p>
HTML

echo "built $DIST/SHA256SUMS, LATEST, index.html"
