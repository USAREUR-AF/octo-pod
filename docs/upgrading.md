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

## Remove or rebuild a pod

Run removal from your workstation, using the release kit or a source checkout:

```bash
octo-pod remote deprovision <user>@<pod-ip>
# The same helper from a checkout:
tools/deprovision-pod.sh <user>@<pod-ip>
```

Removal stops the pod's workloads and removes its package, Quadlet definitions,
background timers, self-heal cron entry, and pod firewall rules and boot services.
It interrupts feeds. Arrange an alternate path first if they must remain available.
It preserves identity, private keys, CA, retained data, audit records, and the running
NetBird agent and peer. Reinstall the software to reuse the retained configuration.
This is a change from earlier releases, whose default deprovision command erased data.

For an approved rebuild or permanent retirement, preserve required records and prove
recovery before explicitly requesting permanent deletion:

```bash
tools/deprovision-pod.sh <user>@<lan-ip> --purge-data
```

`--purge-data` also removes pod identity, CA and leaf keys, retained pod/registry/egress
data, and NetBird's package and local state. Publishers need new certificates after a
rebuild. The workstation attempts mesh peer retirement using `goat` and its operator
credentials. Missing credentials, an unknown peer address, or retirement failure return
an error even when local cleanup succeeded. Resolve retirement and retry; if verified
manually, use `--keep-peer` to skip the automatic step. Successful automatic retirement
is recorded for retries. `--keep-netbird` preserves the agent, its state and peer even
with `--purge-data`; it does not merely preserve the package.

Use an independent LAN connection when removing NetBird. The helper refuses a detected
mesh SSH route before making changes, including with `--yes`. Maintain console access:
a route check cannot guarantee connectivity through every custom network arrangement.
The firewall removal deletes only the pod's `inet octo_pod` and `inet net_isolation`
tables. Other firewall tables are preserved; the pod's traffic restrictions no longer apply.

The carried bundle at `/opt/octo-stage` stays unless `--wipe-stage` is specified.
Podman, shared container images, OS users, SSH, network configuration, host tuning,
chrony/journald configuration and system logs stay. There is no `apt autoremove` or
shared image pruning. `--keep-images` and `--keep-docker` remain accepted as no-ops.
This is application removal, not a factory reset or secure disk erasure.

The helper supports the standard installation paths. Inventory overrides for storage,
TLS, firewall or registry paths require a separate operator cleanup plan; do not assume
custom paths have been erased. A root-only removal receipt and the mesh selector remain
under `/var/lib/octo-uninstall` so interrupted cleanup can resume after identity deletion.
Remove that directory manually only after local cleanup and peer retirement are verified.
Errors stop cleanup with a nonzero exit; rerun the same command after resolving them.
`--yes` skips only the confirmation. Reboot and verify that no pod workloads or firewall
units return before treating a host as retired.
