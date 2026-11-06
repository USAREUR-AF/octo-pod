<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Design: the pod delivers fabric data to systems that cannot join

**Implementation:** UDP/TCP and SAPIENT egress are in 0.4.0 and later;
[Zenoh partner egress](guide-zenoh-partners.md#publish-into-a-partners-own-zenoh) is in 0.5.0 and later.
See [Release status](release-status.md#release-status) for package and image requirements.
**Design date:** 2026-10-12
**Depends on:** goat ADR 1122 (the pod aggregation tier, accepted) · the rule that the shim
in front of a system may hold its credential · goat's release lanes
(`release/<lane>/`, router-confined lane identities)

---

## Context and scope

A pod was built for one direction. Sensors that implement no security protocol send bytes to a
socket; the pod gives each one an identity and forwards what someone releases onto the
fabric. goat ADR 1122 makes that the fleet's aggregation tier: one mesh peer, a local
router and per-protocol arms, "fronting site systems that never enroll in the mesh
themselves."

The same sites have systems that need the fabric's data back out: a track correlator, a C2
system, a display. Most of them have the same constraints as the sensors. They do not implement
Zenoh. Their owners will not, or may not, put a mesh client, a credential or new code on
them. Some must not hold a fabric credential at all, because their data is handled under a
stricter policy than the fabric's.

The receiver uses an existing protocol input. The pod owns the fabric identity, mesh
connection, release controls, audit records, firewall rules and software updates.

ADR 1122 describes data coming in. Local proposed
[ADR 0001](adr/0001-edge-appliance-and-translation-boundary.md) records why the endpoint
appliance owns connections and sessions while payload translation stays upstream. It does not create or supersede
GOAT release policy; this document remains the detailed egress design.

## Goals

- **The receiver changes nothing.** The pod presents each feed the way the receiver already
  takes it from a sensor: as datagrams, as a stream it is dialed with, or as a server it
  dials.
- **One way by construction.** Nothing a receiver sends reaches the fabric, whatever the
  receiver does and whatever is misconfigured on it.
- **Nothing leaves without two decisions.** Data reaches a receiver only when its own
  release flag and the pod's downlink allow list both name it, as on the way up.
- **Every send is attributable.** Each delivery is audited with its fabric key, size and
  SHA-256, per destination.

## Non-goals

- **Format conversion.** The producer supplies the receiver's format before publishing it.
  Byte destinations relay that payload unchanged. SAPIENT destinations accept already
  encoded SAPIENT messages; they parse and re-encode the envelope for their session,
  without mapping detection or alert contents.
- **Arbitrary session protocols.** SAPIENT session handling is implemented. A different
  registration or handshake protocol needs its own session arm, not a translator inside
  the byte relay.
- **Durability.** The same bounded, drop-oldest buffering as the arms. Stale tracks are not
  replayed; only the newest status messages are, on connect.
- **Failover between pods.** One pod is one path, as for ingest.

## The design

### A destination mirrors an arm

Ingest has three socket connection types, set by how the sensor behaves. Egress has the same three,
set by how the receiver behaves, because the receiver already expects to communicate with a sensor.
SAPIENT adds a session-aware destination:

| Arm (in) | Destination (out) | Who connects |
|---|---|---|
| `--udp`: the sensor sends datagrams | `udp://`: the pod sends datagrams | The pod pushes |
| `--tcp`: the sensor dials the pod | `tcp://`: the pod dials the receiver | The pod connects |
| `--dial`: the pod dials the sensor's server | `tcp-server://`: the receiver dials the pod | The receiver connects, as to the radar |
| SAPIENT session arm | `sapient://`: the pod registers as a sensor node | The pod connects, registers and heartbeats |

The third row is the one a fabric-first design misses. A correlator's radar-one input is a TCP
client: it dials the radar's server. A destination that only pushed could never feed it.
`tcp-server://` listens on the pod's own LAN address, serves only exact `--from`
addresses, holds `--max-clients` connections (default 1; a new connection from a permitted
address replaces the oldest), and can replay the newest `--replay` status messages (keys
ending `/status`) on connect, so a receiver that takes a radar's position from its status
has it at once.

Each destination is its own container, `octo-egress-<id>`, with its own pen certificate,
one literal address and its own NDJSON audit file.

### The downward path

```
fabric → downlink bridge (pod slot, downlink_allow) → pen as downlink/K → egress arm → receiver
```

- The **downlink bridge** holds the pod's slot and subscribes on the fabric only to the
  keys in `downlink_allow`. It republishes fabric key `K` on the pen as `downlink/K`, and
  the pen ACL lets it write `downlink/**` and nothing else.
- Each **egress arm** may only declare a subscriber on, and receive, its own
  `downlink/<key>` entries. It holds no fabric credential and has no write grant anywhere on
  the pen.
- A key must name its publisher: the first chunk is a literal anchor, or `release` followed
  by a literal lane. Wildcards in the first chunk, and the `ops`, `attest`, `system`,
  `downlink` and `@` roots, are refused. The CLI, the playbook and the arm hold the same
  rule, and a parity test keeps them aligned.

### One way, and what that means

Three things keep a receiver's data off the fabric:

1. **The receiver holds no identity.** There is nothing for it to publish with.
2. **The arm never forwards what it reads.** `udp://` and `tcp://` destinations never read
   their socket. A `tcp-server://` destination reads and discards whatever a receiver
   sends, and counts the bytes in its local audit summary. A `sapient://` destination
   consumes registration replies locally, rejects tasks with a `TaskAck`, and publishes
   none of the receiver's frames to the pen.
3. **The pen refuses it anyway.** An egress identity has no write grant, so even a
   compromised arm cannot publish into the pen, and nothing it receives can reach the
   uplink.

That is one way at the application layer, not on the wire. TCP destinations permit
return traffic at the transport layer; SAPIENT also exchanges session control messages
locally. No egress destination publishes receiver traffic to the pen. A boundary that must
be physically one way needs `udp://` through a data diode, or a diode that proxies TCP on
both sides.

With the pod firewall on, a `udp://`, `tcp://` or `sapient://` destination opens outbound
to its one address and port, and a `tcp-server://` destination opens inbound from its `--from`
addresses to its one port. Nothing is routed between the LAN and the mesh.

### Confinement

The fabric router authorizes reads for the pod's slot. Its permissions depend on the
fabric's policy and grants; local declarations do not create upstream authorization.
Confine that slot to the approved keys or receiver's release lane, and verify both an
allowed subscription and a denied one. The downlink allow list and destination release
flag then restrict which authorized data the pod actually delivers.

Every destination shares the pod's one slot, so a lane confines the pod as a whole, and
a shared credential is an unattributed feed. Until the pod can hold a
fabric identity per destination, each receiver gets its own pod.

Data leaving for a system outside the organization is a release in goat's terms: either it
uses the release controls or it is an egress path without release visibility. The pod's
own audit records every send, but only a lane makes the release visible to the data's
owners, so a pod that feeds an outside system reads from that system's lane.

## Validation

Tested against a track correlator's ASTERIX UDP input and its TCP-client radar-one
input, with the pod's firewall on. Both inputs received data from the pod alone. A
receiver that queues its retries opens several connections in its first minute; the
destination keeps the newest, and one connection then stays open.

## Alternatives considered

| Rejected | Why |
|---|---|
| **A socket adapter on the receiver's host** | Works, and is the right answer for a receiver that may join the mesh. It needs a mesh client and a fabric credential on the receiver, and its identity can write its own topics, so only its configuration keeps the receiver's output off the fabric. Misconfigured, it publishes the receiver's own output onto the fabric. |
| **The receiver's own Zenoh client** | The most native, and the most work for the owner, who builds, tests and maintains it. Same write exposure as the adapter, held by their code. |
| **A socket adapter on a separate gateway host** | The same function without the pod's enrollment, firewall, golden image, audit and release gates. Building those again for a second appliance doubles what has to be hardened and kept current. |
| **Delivery from the fabric's core** | The receiver is off the mesh, so the core cannot reach it without another network path, and there is no local point to enforce what reaches it. |
| **Push only** | Cannot feed a receiver whose input dials its sensor. See *A destination mirrors an arm*. |
| **Release lanes only** | The first version accepted only `release/<lane>/` keys. A goatnet without lanes, a lab, or a receiver inside the operator's own boundary fed from the operator's own sensors then had no path at all. Raw keys are allowed under the publisher-anchor rule; a receiver outside the organization still reads from a lane, which is the release (see *Confinement*). |

## Before You Deploy Egress

Delivery to a system outside the fabric is a decision for the fabric's operators as
much as the pod's. Settle these with them first:

- **The interconnection.** A receiver outside the fabric's boundary usually needs the
  operators' approval and an agreement for the connection. A pod in front of one
  receiver is a per-receiver boundary point; say so in the fabric's boundary records.
- **One receiver per pod.** The pod holds the receiver's fabric credential under the
  pod's custodian. A credential shared between receivers is an unattributed feed.
- **What a release is.** Data that leaves the fabric for an outside system is a
  release. Where the fabric has release lanes, feed an outside receiver from one.
- **The node class.** Enroll the pod with the class the operators assign to a sensor
  gateway. A fabric may define a dedicated class for pod appliances that its enrollment
  doesn't accept yet.
- **The LAN leg.** The link from the pod to the receiver is plaintext. Decide per site
  whether it needs MACsec or IPsec.
- **Records.** The pod keeps each destination's records locally and doesn't publish
  them: a receiver controls when it connects, so publishing its session records would
  let it signal onto the fabric.

## Cross-cutting concerns

**Security.** The downlink bridge trusts whatever router the fabric's CA roots authenticate,
so a substituted root would let an impostor feed every destination. `release --slot`
therefore trusts no file in the bundle until the key that signs it matches the
fingerprint the goatnet's operators gave out of band, the signature verifies, and the files
are the ones it signed; it pins the CA roots' SHA-256, and `apply` refuses any other
roots. A UDP receiver's allow list is an address, and an address can be forged from
the same link, so a dedicated link or VLAN, with port security, carries the destination.
The pod's health record, published to `ops/pod/health/<pod>` by default, lists each
destination's address and keys; anyone who can read that topic sees where the pod
delivers.

**Observability.** Each arm writes a summary line to its audit every period: received,
sent, errors, drops and, for a serve destination, consumers connected, refused and
replaced. `octo-pod status` shows every destination's state and release; `egress check`
shows whether the two gates agree.

## Open questions

- **A fabric identity per destination.** Needed for one pod to feed receivers cleared for
  different data. The downlink bridge would hold one session per identity.
- **Failover.** A second pod needs a floating address for serve destinations, and push
  destinations active on one pod only, or the receiver gets every datagram twice.
- **Replacement policy.** Replacing the oldest connection let the correlator’s queued
  retries replace connections repeatedly for a minute. Replacing only an idle or failed connection would avoid it.
- **Zenoh session hardening.** The pod's sessions leave multicast scouting at Zenoh's
  default, and sessions to the pen skip the certificate name check.
- **A pod class.** A pod with destinations fits a dedicated pod-appliance class better
  than the sensor-gateway class, where the fabric's enrollment accepts one.
