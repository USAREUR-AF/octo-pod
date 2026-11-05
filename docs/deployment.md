# Deployment

Install and configure a new pod. Commands say whether they run on the laptop or pod.
For an existing installation, use [Upgrading](upgrading.md).

## Before you start

| | |
|---|---|
| On your laptop | `ssh <user>@<pod-ip>` working, `cosign`, and `gh` (logged in) to download releases. Run everything from a clone of this repo |
| To join a goatnet | the `goat` CLI with a configured context (step 1's `mint-key.sh` wraps `goat machine mesh-key`). A pod that joins nothing doesn't need it |
| A pod to build | a second box you can SSH into - physical or VM. The steps below run against it, never against your laptop |
| From whoever runs the mesh | a **setup key** and the management server's **public** URL. Skip both for a pod that joins nothing |
| Decided in advance | the pod's name: lowercase letters, digits and hyphens |

The pod name is also its mesh peer name and its slot alias. All three must match, so pick
it before you start.

Supported: amd64 or arm64 Debian/Ubuntu-family Linux with systemd, Podman 4.7 or later
and skopeo, such as
Ubuntu 24.04. The [LXD image helper](#lab-only-lxd-image-helper) is for lab testing.
The package declares that contract, so on Ubuntu 24.04 installing it with apt brings
podman and skopeo. Ubuntu 22.04, Debian 12, Raspberry Pi OS and JetPack 6 ship an older
podman, without Quadlet: apt refuses the package there, and such a device needs a base
image that brings a newer podman.

Dependency installation is split between the package installer and the CLI:

| Component | How it is installed or prepared |
|---|---|
| Device OS, bootloader, kernel and drivers | Device/base-image pipeline, outside this package |
| Ansible, Python YAML/Jinja, Podman 4.7+, skopeo, nftables, OpenSSL and CA certificates | Package dependencies; `install-to-pod.sh` reads them from the `.deb` and installs them with apt |
| NetBird agent | `octo-pod bootstrap`, from its apt repository or staged `.deb` files |
| cosign and clock packages | Connected bootstrap attempts their installation; disconnected hosts need them prepared beforehand |
| Application containers and local registry | Carried image bundle, seeded by bootstrap, then configured by `apply` |
| Mesh enrollment and per-pod identity | `join` or `init`, after bootstrap |

Bootstrap checks Podman and skopeo; it does not replace the package manager or repair an
unsupported OS. A tarball install must satisfy the package dependency list separately.

Verification is layered: disposable Linux/Podman tests cover sessions and release gates;
the published release provides signed artifacts. Neither establishes clean field-host
installation or uninterrupted service during an upgrade. Field acceptance is open
work.

`--with-images` carries containers, not every OS package. A fresh box still needs the
package dependencies and NetBird available through apt or separately staged `.deb` files.
For a disconnected site, prepare those before travel; see the staging instructions in
[the airgap bundle tool](../tools/build-airgap-bundle.sh). Prepare the verification venv too:
`verify` uses `eclipse-zenoh` and needs it installed before losing access to PyPI.

## Install or update

This repository delivers the octo-pod package, its application containers and dependency
setup. The device arrives with a supported OS already installed. Creating or flashing
Raspberry Pi, Jetson Thor or other device OS images belongs to the device/base-image
pipeline. LXD is lab-only; it is not a deployment requirement.

**New pod:** start with the supported OS contract [above](#before-you-start), working SSH
and sudo, and the laptop tools. The `.deb` is architecture-independent; the container
bundle carries amd64 and arm64 images. Install the published release from your laptop:

```bash
tools/install-to-pod.sh <user>@<pod-ip> --version 0.5.0 --with-images
```

For a mesh member, [mint its setup key](#1-mint-a-setup-key-and-stage-it) before joining.
After installation, follow [Prepare the box](#3-prepare-the-box) through declaring and
checking a feed. Use `init` for a standalone pod or `join` for a mesh member. Installing
the package alone does not configure a pod.

The installer verifies the release manifest and signature before transfer. The published
0.5.0 package provides the capabilities listed above. The older 0.3.4 Docker release requires a fresh Podman installation.

**Existing Podman pod with the fixed 0.4.0 upgrader:** follow [Upgrading](upgrading.md#upgrading) for
the release source, image staging, recovery preparation and verification commands. A
legacy Docker pod needs a planned fresh install. A lab prerelease with the old upgrader
needs the separate migration instructions there. After that preparation, the update is:

```bash
sudo octo-pod upgrade --check &&
sudo octo-pod upgrade --to <version> &&
sudo octo-pod check &&
sudo octo-pod apply &&
sudo octo-pod verify
```

Stop if any step fails; confirm fresh data at the receiver afterward.

## Air-gapped installation

`--with-images` carries containers, not a complete OS dependency bundle. Prepare the
package and its dependencies before disconnecting. Keep a copy of these guides with
the release.

### Prepare while connected

1. Install the package and carry its images using [README steps 1–2](../README.md#1-install-from-your-laptop). This verifies the
   release before transfer and seeds the pod's local registry.
2. Ensure the pod has its package dependencies, NetBird, cosign and clock packages.
   Check signature verification while connected so the required Sigstore trust material
   is available. Keep the verified manifest and signature bundle with the artifacts.
3. Prepare the local ACL verification environment on the pod:

   ```sh
   sudo apt-get install -y netcat-openbsd logrotate python3-venv python3-pip
   sudo python3 -m venv /opt/octo-verify/venv
   sudo /opt/octo-verify/venv/bin/pip install 'eclipse-zenoh==1.9.0'
   ```

If the target cannot connect during preparation, stage architecture-matched `.deb`
files and Python wheels separately. NetBird packages go in `/opt/octo-stage/deb/`.
The image bundle builder does not download those packages. Install the OS dependencies,
cosign and verification environment before running the pod commands; the image tarball
alone cannot provision a fresh offline OS.

### Load at the site

With the package installed and the verified images under `/opt/octo-stage`, load only
the images, without attempting connected package setup:

```sh
sudo octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000 --tags images
```

Continue with [README steps 3–5](../README.md#3-initialize-or-join). Use `init` for isolated capture; `join` still requires
access to the mesh's management server. Check local capture and the actual receiver
path before disconnecting any remaining setup access. SAPIENT and other optional
verification paths can require additional test images or Python dependencies; stage
and exercise the selected path in advance.

For image delivery to an already installed pod from a Mac, use the separate
[image-transfer guide](mac-airgap-images.md). It does not install OS dependencies or
replace the first-install procedure.

## Join a mesh

After installation and bootstrap, obtain an unused setup key and the management URL
from the mesh operator. Place the key in a private file on the pod, then run:

```sh
sudo octo-pod join --setup-key-file /path/to/setup-key --mgmt https://mesh.example.test --pod pod1
sudo octo-pod set-mesh --mirror localhost:5000
```

Replace the example path, URL and pod name. `join` creates the identity, enrolls the
pod and converges it. Do not run `init` first. The sequence below includes minting
and staging a key from an enrolled machine with the workstation's GOAT CLI.

## Build a pod

Use this sequence for a new mesh-connected pod. For local-only capture, use
[the README quickstart](../README.md#deploy-a-pod). Existing pods use the
[upgrade guide](upgrading.md).

### 1. Mint a setup key and stage it

On your laptop.

```bash
tools/mint-key.sh <user>@<pod-ip> --pod <pod-name>
```

Mints a one-off key and writes it to `/root/nb.key` on the pod without displaying it.
The helper stages the key under `/root`; keep it private and mint a fresh key for each rebuild.

**A setup key is one-off.** It is consumed the moment a box enrols. Mint a fresh one for
every rebuild. A second attempt with a spent key does not fail cleanly: `netbird up` never
returns, and the play gives up after 120 seconds.

`mint-key.sh` wraps `goat machine mesh-key <pod-name>`, which is quota-neutral and
re-derives the key's groups from the machine's class. For a machine with no anchor yet,
enrol it once first:

```bash
goat machine enroll <pod-name> --node-class POD --autonomy supervised --custodian <handle>
```

### 2. Install from a published release

Select the published 0.5.0 release explicitly. Use a later reviewed signed version when
one supersedes it; omitting `--version` selects the newest release available from the
chosen repository.

```bash
tools/install-to-pod.sh <user>@<pod-ip> --version 0.5.0 --with-images
```

Checks the signature and the fingerprint on your laptop before anything reaches the pod.
The line that matters:

```
[4/7] check the signature (who made this)
      Verified OK
```

`--with-images` carries the container images. A pod usually has no route to a registry,
so carrying them is the normal path. Drop the flag only if the pod can reach a
registry that already holds them.

### 3. Prepare the box

For a connected setup using the carried container bundle, prepare the packages first,
then seed the local registry:

```bash
ssh <user>@<pod-ip>
```

On the pod:

```bash
sudo octo-pod bootstrap --tags base,verification,clock,netbird &&
sudo octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000 --tags images
```

The first run installs NetBird and attempts cosign/clock setup without trying to pull the
application images. The second loads those images from the carried bundle.
Package installation has already supplied Podman and skopeo; bootstrap checks the runtime.
For a fully disconnected host, prepare all dependencies, cosign and the verification
venv beforehand, then run the second command. The container bundle alone is not an OS
dependency bundle.

The pod has no identity yet.

### 4. Give it an identity and a mesh

```bash
sudo octo-pod join --setup-key-file /root/nb.key --mgmt https://<netbird-host> --pod <pod-name>
```

Writes `/etc/octo-pod`, enrols on the mesh and starts the pen. On current `main`, join
requires a successful structured NetBird status response for the requested scheme,
host and effective port before fetching mesh images or converging. Failed convergence
or verification exits without reporting `Joined`.

Confirm with:

```bash
netbird status        # want: Management: Connected
```

For a pod that joins nothing, use [`init`](#a-pod-that-joins-nothing) instead.

### 5. Record where images come from

```bash
sudo octo-pod set-mesh --mirror localhost:5000
```

Bootstrap's mirror override applied to that run only. If you skip this, `apply` looks for a registry
running on the pod and uses it, saying so.

### 6. Confirm nothing is captured yet

```bash
sudo octo-pod status
```

Router up, no arms. Declaring a sensor is a separate decision and can be weeks later.

### 7. Declare a sensor and converge

```bash
sudo octo-pod add-arm radar1 --udp 48021 --from <sensor-ip> --bind <pod-lan-ip>
sudo octo-pod check
sudo octo-pod apply
```

`check` shows what would change and changes nothing.

- **`--from`** is an allowlist and the only access control an arm has. Traffic from any
  other address is rejected and counted in `dropped_source`; the arm also logs the
  refusal. The address must be static. The prefix-matching limitation is documented
  under [Adding a publisher](operations.md#adding-a-publisher).
- **`--bind`** is the address the arm listens on. Without it an arm binds the mesh address,
  which a LAN sensor cannot reach: the container looks healthy and receives nothing.

One declaration produces a certificate, an ACL grant, an audit sink and the running arm.

### 8. Send data

Send a known test datagram from the sensor LAN to the pod's declared UDP listener
(port 48021 in this example). Use a source address listed in `--from` and
coordinate the test with the sensor operator. This package ships no traffic simulator.

On the pod:

```bash
sudo octo-pod verify-flowing
sudo podman logs --tail 3 octo-udp-ingest-radar1 | grep stats
```

Expect the received and forwarded counters to increase, with
`forward_errors=0 dropped_source=0`. The stats line prints
once a minute, so wait rather than sending again.

## A pod that joins nothing

A pod that captures and retains locally needs no mesh, no setup key and no upstream slot.
After installation and bootstrap, use `init` in place of `join`:

```bash
sudo octo-pod init --pod <pod-name>
sudo octo-pod apply
```

Everything else is the same, and two things get simpler. The pod takes the address off its
default route, so `add-arm` needs no `--bind`. And with no mirror declared, `apply` finds
the registry on the pod and uses it.

To join a mesh later:

```bash
sudo octo-pod set-mesh --mgmt https://<netbird-host>
sudo octo-pod join --setup-key-file <PATH> --mgmt https://<netbird-host> --pod <pod-name>
```

## Installing without the script

Do the same three things by hand: verify, copy, install.

```bash
gh release download octo-pod-v<version> -R <owner>/<repo> \
  -p "octo-pod_<version>_all.deb" -p SHA256SUMS -p SHA256SUMS.cosign-bundle

cosign verify-blob --bundle SHA256SUMS.cosign-bundle \
  --certificate-identity-regexp '^https://github\.com/<owner>/<repo>/\.github/workflows/release\.yml@refs/tags/octo-pod-v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
sha256sum -c --ignore-missing SHA256SUMS

scp octo-pod_<version>_all.deb <pod>:/tmp/
ssh <pod> 'sudo dpkg -i /tmp/octo-pod_<version>_all.deb; sudo apt-get install -f -y'
```

Replace `<owner>/<repo>` with the repository whose releases you install. Verify the
signature before the fingerprints, in that order. The signature establishes the
fingerprint list; reversed, a hostile file carrying its own list would pass.

`tools/install-to-pod.sh` takes the repository with `--repo <owner>/<repo>`. Without it,
the script uses the repository it came from: the `RELEASE` file of the release it was
unpacked from, or its clone's GitHub remote.

To install a locally built package:

```bash
tools/build-release.sh
tools/install-to-pod.sh <user>@<pod-ip> --deb dist/octo-pod_<version>_all.deb
```

## Lab only: LXD image helper

`tools/build-vm-image.sh` builds a disposable-lab LXD image for exercising the package and
its dependencies. It does not build a Raspberry Pi, Jetson Thor or other field-device
boot image, and it is not part of the package installation path. The lab image holds the substrate
packages, NetBird, cosign, the octo-pod package, and every container image staged as the
airgap bundle. It holds no identity: no pod name, setup key, certificate or site address.

```bash
tools/build-vm-image.sh --host <lxd-host> --manifest <packages.manifest>
```

The LXD host does the building, so only the committed source crosses your link. Every
octo-pod image is compiled from source there, the image is published as
`octo-pod-gateway/<version>-<sha>-<arch>`, and its manifest, every package version and
image digest, lands in `dist/vm-image/`. An image with the same name is never
overwritten, and the build VM is deleted whether the build passes or fails (`--keep`
keeps a failed one).

The lab image takes the LXD host's architecture: an amd64 host builds an amd64 VM image,
and an arm64 host builds an arm64 VM image. Field devices use their own OS images and
install this package and its dependencies. Multi-architecture application containers do
not constitute a universal bootable device image.

A lab VM launched from it starts with [Prepare the box](#3-prepare-the-box); the packages are already present. Bootstrap
and local image seeding can run offline; `join` still needs a reachable mesh management
server and the separately staged setup key:

```bash
sudo octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000
sudo octo-pod join --setup-key-file /root/nb.key --mgmt https://<netbird-host> --pod <pod-name>
sudo octo-pod set-mesh --mirror localhost:5000
```

## Carry images from a Mac

For an installed pod that cannot pull private registry images, use the matching
reviewed checkout on your Mac:

```zsh
tools/push-images.sh field-pod          # build, verify and stage images
tools/push-images.sh field-pod --load   # also seed and verify its local registry
```

This image-only path keeps registry credentials on the Mac. It requires matching
installed image pins and does not install the package, enroll the device or run
`apply`. See [the field delivery instructions](mac-airgap-images.md) for access,
OS dependencies, trust limits and recovery. Live field acceptance is still pending.


### Image checks before merge

The `PR images` workflow builds affected arms on native AMD64 and ARM64 runners.
Changes to shared workspace inputs or the check workflow select every arm.
The final images are loaded only on their runners, with no registry login or
image push. Each check verifies architecture and the default nonroot UID/GID,
then starts every binary copied into `/usr/local/bin` with `--help`, networking
disabled, a read-only filesystem and no capabilities. This checks compiler/base
compatibility and the final loader/CLI path; it does not prove mTLS sessions,
message delivery, field deployment or production readiness.

Publication remains in `images.yml`. A successful PR build changes no deployment
pin and does not deliver an image to a pod.
