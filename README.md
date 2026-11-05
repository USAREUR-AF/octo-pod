# octo-pod

An edge gateway for UDP/TCP sensor feeds and Zenoh peers. Capture and audit locally,
then release selected feeds to a fabric or deliver fabric data to receivers.
`otp` is a short alias for `octo-pod`.

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

## Development

See [Contributing](https://github.com/usareur-af/octo-pod/blob/main/CONTRIBUTING.md)
for source builds, tests and image publishing. The [changelog](CHANGELOG.md) records
released and unreleased changes.
