<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# octo-pod

<!-- badges: the public repository's live status. The documentation book leaves this block out. -->
[![CI](https://github.com/USAREUR-AF/octo-pod/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/USAREUR-AF/octo-pod/actions/workflows/ci.yml)
[![CodeQL](https://github.com/USAREUR-AF/octo-pod/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/USAREUR-AF/octo-pod/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/USAREUR-AF/octo-pod/badge)](https://scorecard.dev/viewer/?uri=github.com/USAREUR-AF/octo-pod)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/USAREUR-AF/octo-pod?sort=semver)](https://github.com/USAREUR-AF/octo-pod/releases/latest)
[![MSRV 1.89](https://img.shields.io/badge/MSRV-1.89-orange.svg)](arms/Cargo.toml)
<!-- /badges -->

octo-pod is an edge gateway appliance. It runs on one small computer where sensors
live, takes in their feeds, records and audits them locally, and releases only what
you choose onto a shared Zenoh network, the **fabric**, that other sites and systems
share. It works the other way too: it can deliver data from the fabric to a local
receiver.

**Where it fits.** A sensor speaks its own wire format on its own network and has no
place on a shared fabric; a shared fabric should not reach back into a sensor's
network. octo-pod is the boundary between the two. Each pod sits at one site, owns the
identity and access rules for every feed that crosses it, and keeps a local record of
what it saw even when the link to the fabric is down. The fabric carries only the feeds
a pod releases, under the names and to the audiences its operator declared. One pod
serves one site; a larger deployment is many pods, each releasing into the same fabric
and each governed on its own.

**What crosses, and what does not.** Data enters through an **ingress arm**: a UDP or
TCP sensor feed, or a Zenoh-native publisher that connects directly. It leaves through
an **egress arm** to a receiver or to a partner's own Zenoh router, carrying only the
keys you released. Nothing crosses that you did not declare: every arm and peer gets
its own certificate and a narrow set of keys it may publish or read, and an optional
pod firewall limits traffic to the connections you named. Capture stays local until you
release it.

`otp` is a short alias for `octo-pod`.

For short answers for operators, partners and contributors, read the
[frequently asked questions](docs/faq.md).

## Functions

An **arm** connects one external feed or destination to the pod. **Ingress** brings
data into the pod; **egress** sends released fabric data to a receiver. The **fabric**
is the shared Zenoh network the pod joins. These are the functions exposed by the
pod's configuration and CLI:

### Ingress: bring data into the pod

- **UDP ingress arm (`--udp`):** receives datagrams sent by a sensor to a UDP port on the pod.
- **TCP listener ingress arm (`--tcp`):** accepts a sensor's TCP connection and receives its feed.
- **TCP client ingress arm (`--dial`):** connects to a sensor's TCP server and receives its feed.
- **Zenoh peer ingress (`add-peer`):** lets a system that already uses Zenoh connect directly and publish into its own permitted namespace.

See [Adding a publisher](docs/operations.md#adding-a-publisher) for setup and TCP framing.

### Egress: deliver released data to a receiver

- **UDP egress arm (`udp://`):** sends each released message as a UDP datagram to the receiver.
- **TCP client egress arm (`tcp://`):** connects to the receiver's TCP server and sends the released feed.
- **TCP server egress arm (`tcp-server://`):** listens on the pod so an allowed receiver can connect and receive the released feed.
- **SAPIENT egress arm (`sapient://`):** connects to a SAPIENT controller, registers as a sensor, maintains heartbeats, and relays released SAPIENT detections and alerts.
- **Zenoh egress arm (`zenoh://`):** connects to a partner's own Zenoh router using credentials the partner issued, then publishes released data under the prefix they allowed.
- **Zenoh peer reads (`peer allow-read`):** lets a partner connected to the pod's router subscribe to the fabric keys explicitly released to that partner.

See [Egress setup](docs/operations.md#sending-fabric-data-to-a-system-that-cannot-join)
and the [Zenoh partner guide](docs/guide-zenoh-partners.md). Zenoh partner egress and
released peer reads require 0.5.0 or later; see [Release status](docs/release-status.md).

### Local services and controls

- **Local capture and audit:** records received feeds locally for inspection, subject to configured storage limits.
- **Uplink bridge:** publishes only the local feeds explicitly released for the shared fabric.
- **Downlink bridge:** brings only approved fabric keys into the pod for released egress destinations and peer reads.
- **Identity and access:** gives each arm or peer its own certificate and restricts which Zenoh keys it may publish or read.
- **Pod firewall:** when enabled, limits network traffic to the declared management, sensor, peer, and destination connections.
- **Health reporting:** publishes the pod's own health to the fabric for monitoring.
- **Operator inspection:** shows flow counters, service status, logs, and certificate checks through the CLI.
- **Automatic maintenance:** renews certificates and repairs supported service faults without applying configuration changes automatically.
- **Installation and upgrades:** installs verified software and carried container images, including at disconnected sites, while preserving the pod's identity and declarations.

Byte ingress and byte egress preserve the payload format. SAPIENT egress manages the
session and protocol envelope; its input must already contain SAPIENT messages.
Local capture does not provide durable store-and-forward delivery.

## Operate from the CLI

Every change edits the pod's declaration; nothing on the running pod changes until you
`apply`. `otp` is the short alias for every command below.

- **Set up:** `bootstrap` prepares the host and loads carried images; `init` stands up
  a pod for local capture; `join` enrolls it in a mesh; `set-mesh`, `set-lan` and
  `set-firewall` declare how it reaches the network.
- **Declare what crosses:** `add-arm` / `remove-arm` for ingress feeds, `add-peer` /
  `remove-peer` for Zenoh peers, `release` to publish a captured feed to the fabric,
  `egress add` / `egress remove` for receivers, and `peer allow-read` / `revoke-read` /
  `allow-from` / `bundle` for what a peer may read or send. `edit` opens the
  declaration directly.
- **Apply and verify:** `check` shows what `apply` would change and changes nothing;
  `apply` makes the running pod match the declaration; `verify` checks configuration
  and services; `verify-flowing` confirms data is moving; `verify-clean` confirms no
  residue.
- **Watch:** `status` for service health, `top` for live flow counters, and `get` /
  `describe` / `logs` / `certs` for declarations, detail, logs and certificate checks.
- **Maintain:** `upgrade` moves to a new version, `fetch-images` pulls carried images,
  and `identity` renews or inspects certificates. Between applies the pod renews its
  own certificates and repairs supported service faults on its own, without changing
  your declaration.

## Requirements

- **Pod:** Ubuntu 24.04, amd64 or arm64, with SSH and sudo access.
- **Laptop:** Git, authenticated [GitHub CLI](https://cli.github.com/) and
  [cosign](https://docs.sigstore.dev/cosign/system_config/installation/).
- **During setup:** access to the OS and bootstrap package repositories. For a
  disconnected site, see [Air-gapped installation](docs/deployment.md#air-gapped-installation).

## Deploy a pod

This quickstart uses the published **0.5.0** release. Examples use `192.0.2.10` for
the pod and `192.0.2.30` for a sensor; replace them and the SSH user with your values.
For an existing pod, use the [upgrade guide](docs/upgrading.md). Stop on any failed command.

### 1. Install from your laptop

```zsh
gh repo clone usareur-af/octo-pod
cd octo-pod
tools/install-to-pod.sh operator@192.0.2.10 --version 0.5.0 --with-images
ssh operator@192.0.2.10
```

The installer verifies the release and transfers its container bundle.
Run the remaining commands on the pod.

### 2. Prepare the pod

```sh
sudo octo-pod bootstrap --tags base,verification,clock,netbird && \
  sudo octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000 --tags images
```

The first command prepares dependencies; the second loads the carried images.
Stop if either command fails.

### 3. Initialize or join

For local capture without a mesh:

```sh
sudo octo-pod init --pod pod1 && \
  sudo octo-pod set-mesh --mirror localhost:5000
```

Or, to join a mesh, use this **instead of `init`**. Replace the key path and URL
with the values from your mesh operator:

```sh
sudo octo-pod join --setup-key-file /path/to/setup-key --mgmt https://mesh.example.test --pod pod1 && \
  sudo octo-pod set-mesh --mirror localhost:5000
```

See [Mesh enrollment](docs/deployment.md#join-a-mesh) if you need to obtain a key.

### 4. Add a sensor and start capture

```sh
sudo octo-pod add-arm sensor1 --udp 48021 --from 192.0.2.30 --bind 192.0.2.10
sudo octo-pod check
sudo octo-pod apply
```

Configure the sensor to send UDP to the pod at `192.0.2.10:48021`.
For TCP feeds, see [Adding a publisher](docs/operations.md#adding-a-publisher).

### 5. Verify

```sh
sudo octo-pod verify
sudo octo-pod status
sudo octo-pod verify-flowing
```

`verify` checks configuration and services. After the sensor sends data,
`verify-flowing` must pass and the received/forwarded counters must increase.
Allow about a minute for the first statistics line. Captured data stays local
until you explicitly release it.

## Use the pod

```sh
sudo octo-pod get arms
sudo octo-pod logs arms sensor1 -f
sudo octo-pod top
```

| Task | Guide |
|---|---|
| Deploy without Internet access | [Air-gapped installation](docs/deployment.md#air-gapped-installation) |
| Join a mesh or install manually | [Deployment](docs/deployment.md) |
| Publish captured feeds to the fabric | [Publishing upstream](docs/operations.md#publishing-upstream) |
| Deliver fabric data to a receiver | [Egress](docs/operations.md#sending-fabric-data-to-a-system-that-cannot-join) |
| Connect a Zenoh-native partner | [Partner guide](docs/guide-zenoh-partners.md) |
| Enforce source-address rules | [Firewall](docs/operations.md#the-pod-firewall) |
| Upgrade or rebuild an existing pod | [Upgrades](docs/upgrading.md) |
| Diagnose a failed step | [Troubleshooting](docs/operations.md#troubleshooting) |
| Check released versus `main` features | [Release status](docs/release-status.md) |
| List the container images a pod runs | [Container images](docs/images.md) |

## Development

See [Contributing](https://github.com/usareur-af/octo-pod/blob/main/CONTRIBUTING.md)
for source builds, tests and image publishing. The [changelog](CHANGELOG.md) records
released and unreleased changes.
