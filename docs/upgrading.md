<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Upgrades and rebuilds

Use this guide for an existing pod. A new installation uses the
[README quickstart](../README.md#deploy-a-pod).

## Upgrading

This path is for an existing Podman pod running the fixed 0.4.0 upgrader or later. The
older Docker layout requires a fresh install, not this in-place sequence.

Before changing software, record `sudo octo-pod version`, `sudo octo-pod status` and
`sudo octo-pod verify-flowing` when a continuous feed is expected. Preserve the complete
recovery unit: `/etc/octo-pod`, keys and credentials, retained data and audit records,
current software and image digests. Prove recovery separately. `apply` can restart
singleton services, and the live buffer deliberately drops data rather than replaying
it. Continuous service and zero data loss require a verified alternate path and a
retention plan before the change.

The pod needs a reachable signed release surface, cosign, and the new images at their
pinned digests. Record the release surface once, using the URL supplied by its operator:

```bash
sudo octo-pod set-mesh --repo <release-base-url> &&
sudo octo-pod upgrade --check
```

The URL serves `LATEST` and version directories containing the manifest, signature,
package and image bundle. A GitHub repository web page is not that surface.

If this pod uses its local registry, stage and seed the signed image bundle before
convergence. `--images` follows `LATEST`; check that it still names your reviewed version:

```bash
sudo octo-pod upgrade --images &&
sudo octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000 --tags images &&
sudo octo-pod set-mesh --mirror localhost:5000
```

With the images available and recovery prepared, select the exact reviewed version:

```bash
sudo octo-pod upgrade --to <version> &&
sudo octo-pod check &&
sudo octo-pod apply &&
sudo octo-pod version &&
sudo octo-pod verify &&
sudo octo-pod verify-flowing       # for a declared continuous feed
```

Stop on a failed step. Verify fresh data at the receiver as well; local counters alone do
not prove delivery. The new software verifies the signed manifest and downloaded artifact
before installation. The installer and generated download instructions now use the
release workflow/tag signer restriction too. The release workflow's own post-publication
self-check still uses a broader repository pattern; tightening that check remains open.

**Software rollback:** a package install records the previous version and must download
its signed package again; keep that version and its images available. A release-directory
install retains the previous software tree. With compatible configuration and images:

```bash
sudo octo-pod upgrade --rollback &&
sudo octo-pod check &&
sudo octo-pod apply &&
sudo octo-pod verify
```

This rolls back software, not retained data, credentials or configuration. Use the tested
recovery procedure when those changed.

**Lab prerelease migration:** some `0.4.0~lab*` pods still run the old upgrader. That code
does not gain these guarantees by reading new documentation. For a separately approved
migration, set `OCTO_POD_CERT_IDENTITY` on the root upgrade process to the exact reviewed
release-workflow/tag identity, do not use `--expect-sha256` (the old code skips signature
verification when it is supplied), and use a tested host snapshot for recovery rather
than relying on the old `--rollback`. Record the actual installed upgrader and signer in
the migration plan before running it.

`upgrade` never installs an older version automatically. Versions are ordered the way dpkg orders them,
so a prerelease such as `0.4.0~lab2` sorts below `0.4.0`, and a `LATEST` at or below the
running version requires no update. To go to an older version on purpose, name it:
`sudo octo-pod upgrade --to <version> --allow-downgrade`.

Installation and upgrades never modify `/etc/octo-pod`. Where releases come from is
`octo_repo_url` in the pod's `05-fabric.yml`.

To pin an exact artifact, add `--expect-sha256 <64-hex>` with a digest you got from
somewhere other than the mirror. It is an extra check, not a replacement: the signature
and the signed manifest are still verified, and the tarball, `.deb` or (with `--images`)
image bundle must match your digest as well. Without cosign, `upgrade` refuses.

cosign checks the signature against sigstore's public keys. Every release from 0.6.1
carries those keys in `platform/sigstore/trusted_root.json`, and the release workflow
refuses to publish keys that can't verify that same release. A pod running 0.6.1 or later
therefore verifies the next release offline, against the keys of the release it runs, and
needs no route to the internet. The upgrade output says `verifying signature offline`.

Three cases still go online, to `tuf-repo-cdn.sigstore.dev`:

- The pod runs a release older than 0.6.1, which carries no keys. Move it to 0.6.1 from a
  laptop with `tools/install-to-pod.sh`, which checks the signature on the laptop.
- The target release is older than 0.6.0 and publishes only `SHA256SUMS.cosign-bundle`,
  which cosign can't check against a local file.
- Sigstore changed its keys after the pod's release was built, so the keys the pod
  carries don't verify the new release. The upgrader then asks sigstore online. A pod
  with no route out refuses, with both of cosign's answers. Install that release from a
  laptop instead.

Online, cosign downloads the keys and caches them in `/root/.sigstore`. A pod with no route
and no cache refuses with cosign's TUF or connection error. `OCTO_POD_TRUSTED_ROOT=<file>`
makes the upgrader use another copy of the keys, and the output says so.

Who may sign a release follows who built the one the pod runs: every release records the
repository whose release workflow built it in `/opt/octo-pod/RELEASE`, and `upgrade`
accepts only a signature from that repository's `release.yml` workflow, run for an
`octo-pod-v*` tag. Other workflows and branches in the same repository are refused. A
fork's pods therefore trust the fork's releases. To name a different signer, set
`OCTO_POD_CERT_IDENTITY` to a certificate identity regexp; `upgrade` prints it on every
run that uses it. A release built outside a release workflow names no repository, so
`upgrade` refuses until you set `OCTO_POD_CERT_IDENTITY`. The release it then installs
records its repository, so the next upgrade needs no override.

## Rebuild an existing pod

Only for an approved rebuild of an existing pod. A new box uses the [README quickstart](../README.md#deploy-a-pod); an existing
Podman pod normally uses [Upgrading](#upgrading). Before a rebuild, preserve its identity,
keys, retained data and audit records, prove recovery separately, and establish a working
alternate path if feeds must remain available. This command is not an upgrade.

```bash
tools/deprovision-pod.sh <user>@<pod-ip>
```

Type the address when it asks. Keeps the carried image bundle for reinstallation.
Add `--wipe-stage` to delete that bundle too. Removes the pod’s mesh peer if you hold a NetBird admin
token; `--keep-peer` skips that.
