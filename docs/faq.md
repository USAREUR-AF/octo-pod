<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Frequently Asked Questions

Short answers for three groups of readers: operators who run a pod at a site, partner
organizations whose systems exchange data with a pod, and contributors to this project.
Each answer links to the guide that gives the full procedure.

## Terms

| Term | Meaning |
|---|---|
| Pod | One octo-pod installation: one computer at one site. |
| Arm | A process that connects one external feed or one destination to the pod. |
| Fabric | The shared Zenoh network that many sites and systems use. |
| Pen | The pod's own Zenoh router. Everything that connects to the pod connects to the pen. |
| Mesh | The private network (NetBird) that connects a pod to other sites and to the fabric. |
| Slot | The credential that the fabric operator issues to a pod so that it can publish to the fabric and read from it. |
| Release | An operator action that allows data to cross from the pod to the fabric, or from the fabric to a receiver. |

## For Operators at a Site

### What Is octo-pod, and Where Does It Fit?

octo-pod is software for one small computer at one site. The pod receives sensor feeds,
records and audits them on the pod, and sends to the fabric only the feeds that you
release. It can also deliver released fabric data to a local receiver.

The pod is the boundary between a sensor network and the fabric. Sensors do not join the
fabric, and the fabric has no access to the sensor network. One pod serves one site. A
larger deployment uses many pods, and each pod publishes into the same fabric under its
own operator's control. See the [README](../README.md).

### What Does a Pod Not Do?

- It does not translate or decode sensor data. Byte arms forward payloads unchanged. The
  SAPIENT arm manages the session and the message envelope, but does not change detection
  or alert content.
- It does not store data to send later. When the link is down, the buffer discards old
  data instead of growing, and the pod does not replay old tracks after the link returns.
- It does not apply configuration changes by itself. Timers repair runtime faults only.
- It does not manage other pods, and no system outside the pod manages it.
- It does not provide high availability within one pod. The pod has one router, and a
  restart of the router affects every arm.

See [What it does not do](release-status.md#what-it-does-not-do) and the
[non-goals](https://github.com/usareur-af/octo-pod/blob/main/docs/engineering-principles.md#non-goals).

### What Hardware and Operating System Do I Need?

- An amd64 or arm64 computer.
- Debian-family or Ubuntu-family Linux with systemd, Podman 4.7 or later and skopeo.
  Ubuntu 24.04 is the documented installation path. On Ubuntu 24.04, the package installs
  Podman and skopeo.
- SSH and sudo access to the pod.
- A laptop with Git, an authenticated GitHub CLI (`gh`) and cosign.

Ubuntu 22.04, Debian 12, Raspberry Pi OS and JetPack 6 include an older Podman without
Quadlet, and apt refuses the package on them. Such a device needs a base image with a newer
Podman. This project does not build device operating system images, and it does not
publish minimum processor, memory or disk sizes. Test the pod with your expected feed
rates before you deploy it. See [Before you start](deployment.md#before-you-start).

### How Do I Install a Pod?

Run the installer from a clone of this repository on your laptop. This example uses
`192.0.2.10` for the pod:

```zsh
tools/install-to-pod.sh operator@192.0.2.10 --version <version> --with-images
```

The installer verifies the release signature before it copies anything to the pod. Then,
on the pod, you prepare the host with `bootstrap`, create the pod's identity with `init`
or `join`, declare a sensor with `add-arm`, and run `check`, `apply` and `verify`. See
[Deploy a pod](../README.md#deploy-a-pod) and [Deployment](deployment.md).

### Can I Install a Pod Without Internet Access?

Yes, if you prepare the pod while it is still connected. `--with-images` carries the
container images, but not the operating system packages. Before you disconnect the pod:

- Install the octo-pod package and its dependencies, NetBird, cosign and the clock
  packages.
- Run one cosign signature check as root on the pod, so that cosign stores the Sigstore
  trust data in `/root/.sigstore`. Without that data, a disconnected pod can't verify an
  upgrade.
- Prepare the Python environment that `verify` uses.

At the site, load the carried images:

```sh
sudo octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000 --tags images
```

A pod that only captures locally (`init`) needs no network. A pod that joins a mesh (`join`)
needs access to the mesh management server. See
[Air-gapped installation](deployment.md#air-gapped-installation). To deliver new images
to an installed pod from a Mac, see [Image delivery from a Mac](mac-airgap-images.md).

### How Do Upgrades Work?

You upgrade each pod yourself, with `sudo octo-pod upgrade`. There is no automatic
upgrade and no fleet rollout: a pod never upgrades itself, and no other system upgrades
it. The timers on the pod repair runtime faults only.

An upgrade follows these steps:

1. Record `octo-pod version`, `octo-pod status` and `octo-pod verify-flowing`, and keep
   a copy of `/etc/octo-pod`, the keys and the retained data.
2. Run `upgrade --check`.
3. If the pod uses its local image registry, stage the new images with
   `upgrade --images` and load them with `bootstrap`.
4. Run `upgrade --to <version>`, then `check`, `apply`, `verify` and `verify-flowing`.

`upgrade` verifies the release signature and refuses a release that it can't verify.
It never installs an older version unless you name one with `--allow-downgrade`.
`upgrade --rollback` returns to the previous software, but not to previous data or
configuration. Upgrades never change `/etc/octo-pod`. `apply` can restart the router,
so plan for a short interruption.

`bootstrap` also disables Ubuntu's automatic package updates, so that a background update
can't change packages while the pod runs. Plan operating system security updates as a
separate task. A pod that runs the older Docker layout (0.3.x) needs a new installation,
not an upgrade. See [Upgrades and rebuilds](upgrading.md#upgrading).

### How Do I Verify a Release?

`tools/install-to-pod.sh` and `octo-pod upgrade` verify every release automatically. To
verify a release yourself, download its files:

```sh
gh release download octo-pod-v<version> --repo usareur-af/octo-pod \
  -p SHA256SUMS -p SHA256SUMS.cosign-bundle -p 'octo-pod_<version>_all.deb'
```

Check the cosign keyless signature on `SHA256SUMS`:

```sh
cosign verify-blob --bundle SHA256SUMS.cosign-bundle \
  --certificate-identity-regexp '^https://github\.com/(?i:usareur-af/octo-pod)/\.github/workflows/release\.yml@refs/tags/octo-pod-v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
```

Then check the files against the signed hashes:

```sh
sha256sum -c --ignore-missing SHA256SUMS
```

Check the signature first: a hostile file can carry its own hash list. From the next
release, each release also includes SLSA build provenance in
`octo-pod-<version>.intoto.jsonl`, which you verify with `slsa-verifier`. See
[Verify a release](https://github.com/usareur-af/octo-pod/blob/main/SECURITY.md#verify-a-release).

### What Leaves the Pod, and What Never Leaves It?

Sensor and fabric data leave the pod only after you declare a release and run `apply`:

- Feeds that you release to the fabric with `octo-pod release`, under the pod's slot.
- Fabric data that you release to an egress destination or to a peer.

The pod also sends the network traffic that it needs to operate: mesh control traffic,
DNS, time synchronization, and release downloads when you upgrade.

Every 60 seconds, the pod writes a health summary (the output of
`octo-pod status --json`) to its own router, under the key `ops/pod/health/<pod>`. The
summary names every arm, peer and destination, with their ports and site addresses. It
reaches the fabric only if you add that key to `bridge_allow` in the pod's `10-pod.yml`;
the default `bridge_allow` does not include it. To stop the health summary, set
`health_pub_enabled: false` in the pod's `05-fabric.yml`.

These never leave the pod:

- Captured data that you have not released. Capture and audit records stay on the pod.
- The pod's certificate authority key and the arms' private keys. A peer's private key
  leaves only in the archive that `octo-pod peer bundle` creates and you transfer.
- Data that a receiver sends to an egress destination. The pod discards it, or answers
  it locally for a SAPIENT session.
- Registry or repository credentials. The pod holds none.

When the pod firewall is on, the pod routes nothing between the site network and the
mesh. See [The pod firewall](operations.md#the-pod-firewall) and
[What runs without you](operations.md#what-runs-without-you).

### What Happens When the Link to the Fabric Is Down?

Capture and audit continue on the pod. The pod does not queue data for the fabric: it
records data that arrives during the outage on the pod, but does not send that data to
the fabric later. When the link returns, the pod sends new data only. Each arm's buffer
keeps only data from the last few seconds and counts every message that it discards;
`octo-pod top` shows those counts. See
[Bounded buffering](https://github.com/usareur-af/octo-pod/blob/main/docs/engineering-principles.md#bounded-buffering-and-deliberate-data-loss).

### What Do I Do When a Step Fails?

Stop at the first failed command. Read its message, then use `octo-pod status`,
`octo-pod describe`, `octo-pod logs` and `octo-pod certs --check` to find the cause. See
[Troubleshooting](operations.md#troubleshooting). Report a defect in a GitHub issue,
without addresses, keys or other site details. Report a security problem privately, as
[the security policy](https://github.com/usareur-af/octo-pod/blob/main/SECURITY.md#report-a-vulnerability)
describes.

## For Partner Organizations

### How Does My System Receive Data From a Pod?

Choose the method that matches what your system already does:

| Your system | Method | What you need |
|---|---|---|
| Uses Zenoh, and can connect to the pod | Subscribe to the pod's pen as a peer, on `downlink/<key>` | A bundle from the pod operator: a certificate, a private key, the pod's CA certificate and instructions |
| Uses Zenoh, and accepts data only into its own router | A `zenoh://` destination: the pod publishes into your router under a prefix that you allow | You issue a certificate to the pod and grant it a prefix |
| Does not use Zenoh | A `udp://`, `tcp://`, `tcp-server://` or `sapient://` destination | Nothing new. Your system receives the data through an input that it already has |

The pod operator releases each key to your system separately. See the
[partner guide](guide-zenoh-partners.md) and
[Egress](operations.md#sending-fabric-data-to-a-system-that-cannot-join).

### Does My System Need New Software or a Credential?

For `udp://`, `tcp://`, `tcp-server://` and `sapient://` destinations, no. Your system
receives data the same way that it receives data from a sensor. For a peer, your system
holds the certificate that the pod issued; it can publish only under `raw/<id>/**` and
can read only the keys released to it. For a `zenoh://` destination, you issue the
credential, and the pod keeps it.

### What Format Is the Data In?

The format that the producer sent. The pod does not translate data. A `udp://`, `tcp://`
or `tcp-server://` destination relays the bytes unchanged, so your input must accept that
format. A `sapient://` destination needs keys that already carry SAPIENT (BSI Flex 335
v2.0) messages. The pod changes only the node identifier, the send time and the framing.
See [Before it carries real feeds](operations.md#before-it-carries-real-feeds).

### Can My System Send Data Back Through the Pod?

Not through an egress destination. Nothing that a receiver sends reaches the fabric. A
`udp://`, `tcp://` or `tcp-server://` destination discards what the receiver sends. A
`sapient://` destination answers registration and task messages locally, and refuses
every task. If your boundary must be physically one way,
use a `udp://` destination through a data diode.

As a peer, your system can publish under its own namespace, `raw/<id>/**`. That data
stays on the pod until the pod operator releases it. See
[Let a partner publish to you](guide-zenoh-partners.md#let-a-partner-publish-to-you).

### How Is the Connection Protected?

Peer connections use mutual TLS 1.3, with one certificate and one access rule for each
peer. A `zenoh://` destination connects to your router with mutual TLS 1.3 and the
credential that you issued. `udp://`, `tcp://`, `tcp-server://` and `sapient://`
destinations use plain UDP or TCP, so protect that link on your network. See
[Cryptography](https://github.com/usareur-af/octo-pod/blob/main/SECURITY.md#cryptography).

### How Do We Confirm That Data Arrives?

A connected session does not prove delivery. The pen can refuse a publication that looks
successful to the sender. The pod operator checks the pen's audit record, and you check
what your system receives. See [If it does not work](guide-zenoh-partners.md#if-it-does-not-work).

## For Contributors

### How Do I Contribute?

Open an issue before a large change, so that the approach is agreed first. A small fix
can go directly to a pull request. Branch from `main`, give the pull request a
Conventional Commits title, sign every commit, add a DCO sign-off, run the checks and
complete the pull request template. See
[Contributing](https://github.com/usareur-af/octo-pod/blob/main/CONTRIBUTING.md).

### What Is the DCO, and How Do I Sign Off a Commit?

The Developer Certificate of Origin (DCO) is a statement that you wrote the change, or
have the right to submit it under the project's license. Add it to each commit with
`git commit -s`, which adds a `Signed-off-by:` line. A CI check refuses a pull request
with a commit that has no sign-off from its author. Also sign every commit with an SSH or
GPG key that is registered with GitHub. The project uses no contributor license
agreement. See
[First-time setup](https://github.com/usareur-af/octo-pod/blob/main/CONTRIBUTING.md#first-time-setup).

### What Tests Must a Change Include?

New functionality and bug fixes come with automated tests in the same pull request. Put
tests on the security boundary, and list every command that you ran in the pull request's
Test Plan. See
[Test policy](https://github.com/usareur-af/octo-pod/blob/main/CONTRIBUTING.md#test-policy).

### How Do I Build and Test From Source?

Run the Rust checks from `arms/`: `cargo fmt --all --check`,
`cargo clippy --workspace --all-targets -- -D warnings` and
`cargo test --workspace --locked`. Source builds need Rust 1.89 or later. Run the
platform tests from `platform/`, for example `python3 tests/test_templates.py`. A changed
shell script must also pass `shellcheck`. See
[Run the checks](https://github.com/usareur-af/octo-pod/blob/main/CONTRIBUTING.md#run-the-checks).

### How Are Decisions Made?

The person who does the work makes day-to-day technical decisions, through pull request
review. Architectural decisions are recorded as ADRs in `docs/adr/`. Maintainers decide
project questions (governance, license, scope and releases) by rough consensus. See
[Governance](https://github.com/usareur-af/octo-pod/blob/main/GOVERNANCE.md).

## For Everyone

### How Do I Report a Security Vulnerability?

Report it privately through GitHub private vulnerability reporting:
<https://github.com/usareur-af/octo-pod/security/advisories/new>. Don't report it in a
public issue. The maintainers acknowledge a report within 3 business days, and never
later than 14 days. See the
[security policy](https://github.com/usareur-af/octo-pod/blob/main/SECURITY.md).

### What License Does octo-pod Use?

The Apache License, Version 2.0. Portions written by U.S. Federal Government employees
within the scope of their employment are not subject to copyright protection in the
United States (17 U.S.C. 105) and are in the public domain there. In other countries,
those portions are released under the Apache License 2.0. Contributions from others are
released under the same license. Third-party components keep their own licenses. See
[LICENSE](../LICENSE), [NOTICE](../NOTICE) and
[INTENT.md](https://github.com/usareur-af/octo-pod/blob/main/INTENT.md).
