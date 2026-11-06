<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Carry images from a Mac to an installed pod

This is an operator-initiated SSH delivery path for a field device that cannot pull
images itself. Any registry credential stays on the Mac. It is separate
from a device-initiated update: it neither downloads nor installs the octo-pod package.

Status: implementation under local review. Offline validation tests have passed;
a real transfer and registry load on a matching pod have not yet been accepted.

## Prerequisites

- A trusted octo-pod checkout whose eight upstream image pins exactly match the
  installed pod package. Use the reviewed release checkout, not an arbitrary branch.
- On the Mac: Python 3, Docker Desktop running, OpenSSH and enough disk space for the
  bundle, a temporary copy and its compressed archive. The shell entry point works
  with macOS's Bash 3.2; no GNU rsync flags are needed.
- Registry read access for every image in the pin list that is not public. The existing builder
  reads `REGISTRY_AUTH_FILE`, or `~/.docker/config.json`, with plaintext registry auth
  entries; Docker credential-helper entries alone are not available inside its skopeo
  container. Use a private temporary auth file, remove it afterward, and never put it
  in the bundle. A token needs access to the audit image as well as the octo arm images.
- On the pod: a compatible amd64 or arm64 Linux OS, the matching octo-pod package,
  Python 3, Podman, skopeo and ansible-playbook. SSH host keys must already be verified;
  the operator account needs noninteractive sudo for the operation. No GitHub credential
  is installed there. Configure an SSH alias/ProxyJump if direct access is unavailable.

This carries container images, not OS packages, NetBird enrollment, certificates or the
source-built uplink bridge. Fully disconnected first installation also needs the package
and architecture-matched OS dependencies. Use the existing signed release installer
(`tools/install-to-pod.sh --with-images`) for published package delivery; do not treat
this image-only helper as a replacement installer.

## Stage, then load

From the matching checkout on the Mac:

```zsh
tools/push-images.sh field-pod
```

The script checks the installed pins before downloading, calls the existing bundle
builder, checks the OCI indexes and every referenced manifest/config/layer against its
SHA-256 digest, then copies an archive over SSH. It includes both amd64 and arm64 plus
the matching registry tools. The destination rechecks the archive hash, contents and
installed pins before accepting a new root-only stage under
`/var/lib/octo-pod/image-delivery/<archive-sha256>`.

To build, transfer, load and verify in one explicit operation:

```zsh
tools/push-images.sh field-pod --load
```

Or reuse a trusted bundle already produced by `build-airgap-bundle.sh`:

```zsh
tools/push-images.sh field-pod --bundle /path/to/octo-stage --load
```

`--load` runs the installed CLI's `bootstrap --tags images` with airgap mode, the new
stage directory and `image_mirror=localhost:5000`. It can create/restart the loopback
registry and configure its Podman registry drop-in. It then checks that all eight
pinned index digests resolve from that registry. It does not run `apply`, join the
mesh, change `/etc/octo-pod`, or restart application units. Review effective image
mirror and image overrides separately before your normal on-pod `check` and `apply`.

A stage-only run prints the exact on-pod load command. If loading fails, the verified
stage remains for diagnosis and retry using that command. Re-delivering the identical
archive refuses to overwrite an existing stage. Interrupted SCP transfers restart from
zero; the current implementation does not promise resumable transfer.

## Trust and failure behavior

The checkout and installed pins are the trust anchor for application image content.
The delivery receipt proves transfer integrity; it is not a release-workflow signature.
The existing builder still obtains registry/skopeo tool images by tag, so this helper
does not establish signed provenance or reproducibility for those tool images. Use the
signed published image bundle when release-signature provenance is required.

Invalid/empty pins, different installed pins, missing platform tools, corrupt OCI
content, path traversal, links and extra non-image files are refused before loading.
The Mac transfers only `images/` and a generated delivery receipt. It never transfers
its registry auth file, SSH keys or 1Password session. SSH agent forwarding is disabled.
No operation changes the jumpbox, enrolls a new mesh peer or requires a lab mirror.

## Verification

```zsh
python3 platform/tests/test_push_images.py
python3 platform/tests/test_release.py
/bin/bash tools/push-images.sh --help
shellcheck tools/push-images.sh
```

Field acceptance additionally requires a real transfer and load on each supported CPU
architecture, checking the registry's digests and representative application traffic
through the separately reviewed apply step. A successful archive transfer alone does
not establish a successful deployment.
