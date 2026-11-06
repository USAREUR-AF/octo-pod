<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# A worked pod identity

**You do not copy this to make a pod.** `octo-pod join` or `init` writes the identity at
`/etc/octo-pod`; declaration commands such as `set-lan`, `add-arm`, `add-peer` and `egress`
fill it in.

This directory contains six worked example files, with values and reasoning in the
comments. A generated identity also has `host_vars/<pod>/40-egress.yml` for destinations
and downlink release gates; declare those with `octo-pod egress` as described in the
[egress guide](../docs/operations.md#sending-fabric-data-to-a-system-that-cannot-join).

| File | Contents |
|---|---|
| `inventory.ini` | the pod's name and how Ansible reaches it (itself) |
| `host_vars/<pod>/00-site.yml` | interfaces and addressing for the ingest LAN |
| `host_vars/<pod>/05-fabric.yml` | the mesh, upstream and update hosts this pod joins |
| `host_vars/<pod>/10-pod.yml` | pen name, upstream slot anchor, the release gate |
| `host_vars/<pod>/20-peers.yml` | publishers that already implement Zenoh |
| `host_vars/<pod>/30-arms.yml` | publishers that send bytes at a socket |

The six examples plus the generated `40-egress.yml` make up the seven identity files.
The shared playbooks and templates are in `../platform/`. `05-fabric.yml` belongs to the
identity because its values name one deployment's hosts.

The directory name under `host_vars/` must equal the inventory hostname. Ansible binds
identity **by directory name**, so a mismatch gives you no identity, no publishers, and no
error message.

CI also uses `inventory.ini` here as the fixture for `--syntax-check`, so it has to stay
valid.
