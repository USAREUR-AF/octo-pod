# Releases and support

## Release status

These guides describe `main`. The published
[0.5.0 release](https://github.com/usareur-af/octo-pod/releases/tag/octo-pod-v0.5.0)
includes the package, container bundle, checksums and signature bundle.

| Capability | 0.4.0 | 0.5.0 |
|---|---|---|
| Podman/Quadlet, UDP/TCP ingress, peer publishing and local audit | Included | Included |
| Gated uplink/downlink and UDP, TCP, TCP-server and SAPIENT destinations | Included | Included |
| Peer LAN source controls, released peer reads and `peer bundle` | Not included | Included |
| Publishing into a partner's router with `zenoh://` | Not included | Included |
| Strict SAPIENT registration/status fixes and reference test receiver | Not included | Included |
| Join checks the complete management origin before image fetch and convergence | Not included | Included |
| Uplink bridge published and pinned with the arm images | Not included | Included |

The image pins in `platform/group_vars/all/10-infrastructure.yml` are the images built for
the current release. A change to arm code reaches a package only after the images are
rebuilt and their digests reviewed and committed; see
[Rebuild the project images](https://github.com/usareur-af/octo-pod/blob/main/CONTRIBUTING.md#rebuild-the-project-images).
Published artifacts, deployed versions and end-to-end acceptance are separate states.

## What it does not do

| | |
|---|---|
| **Translate sensor data** | Producers supply the receiver's payload format. Byte arms relay payloads unchanged. The SAPIENT arm parses the protocol envelope, filters message types and manages the session without translating detection or alert contents |
| **Send receiver traffic back through egress** | Byte destinations discard receiver data or do not read it. SAPIENT egress consumes session replies locally and rejects tasks. Its pen grant is read-only; no receiver frame is published through this path |
| **Store and forward** | The buffer discards rather than grows. Stale tracks are not replayed |
| **Survive its own restart invisibly** | One router per pod. Restarting it is a pod-wide event that arms ride out |
| **Apply changes on its own** | Timers repair runtime faults. They never apply a configuration change |
| **Manage other pods** | A pod converges itself. Nothing reaches into it |
