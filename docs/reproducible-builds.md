<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Reproducible Builds

Building the same commit twice produces the same bytes. You can rebuild a release from its
tag on your own machine and compare the result with the signed `SHA256SUMS`, so you do not
have to trust the release workflow to have built what the source says.

CI proves it on every change to the build: `.github/workflows/reproducible.yml` builds each
artifact twice, in different directories, at different times, under a different umask, time
zone and locale, and requires identical bytes. It then plants a one-second difference in the
commit time and requires the comparison to fail, so a comparison that cannot fail does not
pass.

## What Is Reproducible

| Artifact | Reproducible | Proven in CI |
|---|---|---|
| `octo-pod-<version>.tar.gz` | Yes | Yes, `tools/check-reproducible.sh` |
| `octo-pod_<version>_all.deb` | Yes | Yes, `tools/check-reproducible.sh` |
| `octo-pod-docs-<version>.tar.gz`, when a release includes it | The packaging is | Yes, with a fixed site |
| Arm binaries | Yes, on Linux | Yes, `udp-ingest`, built twice on the host |
| Arm container images, the image manifest for each platform | Yes | Yes, `udp-ingest` for `linux/amd64`, built twice without cache and once over cached dependencies |
| The multi-platform index that `group_vars` pins | No | Not applicable |
| `octo-pod-images-<version>.tar.gz` | Expected, not proven | No |

## How It Works

- **One timestamp.** Every timestamp in every artifact is the commit time, read from git as
  `SOURCE_DATE_EPOCH` (the [reproducible-builds.org](https://reproducible-builds.org/docs/source-date-epoch/)
  convention). A caller can set it; without a git checkout and without the variable,
  `tools/build-release.sh` refuses to build.
- **Archives.** Tarballs use GNU tar with entries sorted by name, owner and group 0, modes
  normalized to 755 for directories and executables and 644 for everything else, and gzip
  without a file name or time in its header.
- **The package.** `dpkg-deb` takes its timestamps from `SOURCE_DATE_EPOCH`. The package is
  compressed with xz on one thread: Debian and Ubuntu default to different compressors, and
  threaded xz splits its output differently from single-threaded xz.
- **Arm binaries.** The builder image is pinned by digest, so every build uses the same
  compiler. `cargo build --locked` fixes every dependency. Images build at `/build`. A host
  build remaps the checkout and the cargo home with `--remap-path-prefix`, so neither path
  reaches a panic message.
- **Arm images.** `images.yml` pins BuildKit by digest, sets `SOURCE_DATE_EPOCH` to the
  commit time, and rewrites every file timestamp in every layer to it
  (`rewrite-timestamp=true`). The image config and history take the same time.

## Rebuild a Release

Do this on Ubuntu 24.04, the release runner, with git, GNU tar, gzip, dpkg and Python 3
installed. Releases built before reproducible builds were introduced do not reproduce.

Get the repository name exactly as the release workflow saw it. The release records it, and
letter case matters:

```sh
repo=$(gh repo view usareur-af/octo-pod --json nameWithOwner --jq .nameWithOwner)
```

Rebuild at the release tag:

```sh
git clone "https://github.com/$repo.git" octo-pod-rebuild
cd octo-pod-rebuild
git checkout octo-pod-v<version>
tools/build-release.sh --repo "$repo" <version>
```

Download the published hash list and its signature, and check the signature as
[SECURITY.md](../SECURITY.md#verify-a-release) describes:

```sh
gh release download octo-pod-v<version> --repo "$repo" -D published \
  -p SHA256SUMS -p SHA256SUMS.cosign-bundle
```

Compare your build with the published hashes. Every file you built must report `OK`:

```sh
(cd dist && sha256sum -c --ignore-missing ../published/SHA256SUMS)
```

The image bundle and the documentation site are not rebuilt by these steps, so
`--ignore-missing` skips them.

## Rebuild an Arm Image

Rebuild the image for one platform and compare its manifest digest with the published one.
Use the commit that built the image; `tools/pin-images.sh` writes it next to each pin as
`main@<commit>`.

Find the published manifest digest for the platform, starting from the pinned index:

```sh
crane manifest ghcr.io/usareur-af/octo-udp-ingest@sha256:<pinned index digest> \
  | jq -r '.manifests[] | select(.platform.architecture == "amd64") | .digest'
```

Rebuild with the same BuildKit and the same settings as `images.yml`:

```sh
git checkout <commit>
docker buildx create --use --driver docker-container \
  --driver-opt image=moby/buildkit:v0.34.1@sha256:99368f3e46177c6405b9740e4461d91cee004128daeecca78d4ceac50ec1da5c
SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) docker buildx build --no-cache \
  --platform linux/amd64 --provenance=false --sbom=false -f arms/udp-ingest/Dockerfile \
  --output type=oci,dest=udp-ingest.tar,rewrite-timestamp=true arms
tar -xOf udp-ingest.tar index.json | jq -r '.manifests[0].digest'
```

The two digests match for images that `images.yml` built after reproducible builds were
introduced. `images.yml` builds over a cache of compiled dependencies and the rebuild
above uses none; CI checks that the two produce the same image. CI proves that two builds match each other; a match with the published image
is expected and not yet proven in CI.

## Not Yet Reproducible

- **The pinned index.** Each platform's image carries BuildKit's SBOM and provenance
  attestations, which record when the build ran. The index that lists the platforms and
  their attestations therefore differs per build. Compare the platform manifests instead.
- **The image bundle.** Its pinned images are content-addressed, and the registry and
  skopeo tool images it carries are pinned by digest. It also carries those two tool images
  as `docker save` archives, whose bytes depend on the Docker version that wrote them.
  Building the bundle needs registry access and several hundred megabytes, so CI does not
  build it twice.
- **The documentation site's content.** CI proves the packaging with a fixed site. Whether
  mdBook renders the same site twice is not tested.
- **Host builds on macOS.** The macOS linker writes object file paths into the binary's
  UUID, so the same arm built in two directories differs. Linux builds and image builds do
  not have this problem.

## Check It Yourself

Build HEAD twice and compare. Commit first: the check builds HEAD, not the working tree.

```sh
tools/check-reproducible.sh --arm udp-ingest
```

Prove the comparison can fail:

```sh
tools/check-reproducible.sh --plant
```
