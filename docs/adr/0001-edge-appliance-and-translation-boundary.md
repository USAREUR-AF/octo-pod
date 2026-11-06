# ADR 0001: Keep the edge appliance and session boundary in octo-pod

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-10-15 |
| Repo scope | octo-pod; interfaces to GOAT and a translation gateway |
| Supersedes | none |
| Superseded by | none |
| Related | GOAT ADRs 1092, 1116, 1122 and 1144, linked below |

## TL;DR (for a layperson)

Octo-pod connects equipment that cannot join the fabric itself. It captures or delivers
feeds, keeps the endpoint connection alive, and records what was allowed through. A translation
gateway translates what the data means and how it is encoded. Keeping these jobs separate lets a
site repair a connection or update its appliance without changing the translation rules
or installing the fleet control plane on the box.

## Context

GOAT's fleet CLI is an operator control surface for enrollment, identities and release
decisions. A pod is a local appliance, often behind a network boundary with no inbound
management route. It must capture and retain locally before joining a mesh and converge
its own declared configuration without a workstation's portal or registry credentials.

[GOAT ADR 1122](https://github.com/usareur-af/goatcore/blob/main/docs/adr/1122-scale-envelope-systems-vs-peers-pod-tier.md)
is Accepted and defines a pod aggregation tier: one mesh peer, a local router and arms
for attached systems. It explicitly preserves per-system identity and source provenance;
fewer mesh peers do not make a shared credential sufficient attribution.
GOAT requires a system credential, or a shim holding it with custody recorded.
Its classification and admission decisions are made outside this appliance.

The implemented candidate already has byte capture, gated egress and a SAPIENT session
arm. Earlier prose said the pod never parsed a message and did not support sessions.
That was inaccurate once SAPIENT was added. This proposed record makes the existing
mechanism and the boundary with the operator's translation gateway explicit; it does not claim a
signed release or field acceptance.

## Decision

1. **Keep octo-pod as a separate local appliance and release unit.** Its CLI wraps Ansible
   with a local connection. Shared platform files are replaced by software upgrades;
   `/etc/octo-pod` holds the site's declarations and remains separate. The host runs
   systemd/Podman with digest-pinned application images. GOAT remains responsible for
   fleet enrollment, fabric identities and release policy; a pod does not manage other
   pods or make ownership and classification decisions. This repository owns package
   dependencies and bootstrap setup, including NetBird. The package manager supplies its
   declared distribution dependencies; the CLI prepares the remaining services. Device
   OS images, boot firmware, kernels and drivers belong to device/base-image pipelines.
   Existing LXD image helpers are lab test tooling, not a field-image delivery contract.

2. **Keep semantic translation in the translation gateway.** The gateway owns sensor decoding, semantic
   validation, schema and field mappings, coordinate or unit conversion, and output
   encoding. Octo-pod does not add a second mapping engine. Byte arms capture without
   interpreting sensor payloads; byte egress sends already prepared payloads. Translation
   is needed only when producer and receiver formats differ, not for every feed.

3. **Keep endpoint session mechanics in octo-pod.** For `sapient://` egress, the translation gateway or an
   already native publisher supplies one encoded `SapientMessage` per sample containing
   a `DetectionReport` or `Alert`. The session arm parses the envelope, refuses other
   relay types, registers as a sensor node, waits for acceptance, sends heartbeats and
   reconnects. It re-encodes the message with its own node id, send timestamp and TCP
   framing. Detection and alert domain fields are not translated. The published bytes
   and SAPIENT wire bytes therefore need not be identical. The reusable standalone arm
   also supports controller/client roles; those roles manage sessions rather than convert formats.

4. **Preserve the egress trust boundary.** A destination receives only explicitly released
   downlink keys and has a read-only pen grant. SAPIENT egress runs in the sensor role;
   receiver control frames terminate locally, tasks are rejected, and no receiver frame
   is republished into the pen. Registration and heartbeats are gated along with data:
   an unreleased destination must remain stopped. This is application-level confinement,
   not a physical diode or cross-domain accreditation claim.

5. **Keep attribution and custody visible.** Local per-arm certificates, source
   allowlists and audit records distinguish attachments inside the pen; a source address
   is not cryptographic proof of the sensor's identity. The current downlink uses one
   upstream slot per pod. It does not implement per-destination upstream credentials or
   a full custody-assessment record. Use separate pods and distinct upstream credentials
   where receivers have distinct custody or release boundaries until that is implemented.
   This ADR does not declare those remaining obligations satisfied.

The responsibility split is:

| Component | Owns |
|---|---|
| GOAT | Fleet enrollment, identity and release governance |
| Translation gateway | Sensor interpretation and translation into the required output format |
| octo-pod | Local capture, endpoint connections and sessions, gated delivery and audit |

Related Accepted GOAT decisions are
[ADR 1092](https://github.com/usareur-af/goatcore/blob/main/docs/adr/1092-moon-pod-peer-classes.md)
for peer roles, and classifies systems by function rather than packaging. This local ADR has its own
numbering and supersedes none of those decisions.

## Alternatives considered

| Option | Reason not chosen |
|---|---|
| Put local convergence into the GOAT fleet CLI | Couples appliance releases to the fleet control surface and installs unnecessary portal and authentication code on an edge device. GOAT can still prepare enrollment from the operator's workstation |
| Put translation into the pod's arms | Duplicates the gateway's mappings and releases, expands payload parsing at the attachment boundary, and couples semantic changes to session maintenance |
| Make the translation gateway own the receiver session too | Couples translation availability and release cadence to registration, heartbeats and reconnects. The pod already owns the declared endpoint, release gate and delivery audit |
| Use only byte relays | Works for ordinary sockets but cannot satisfy a receiver that requires SAPIENT registration and heartbeats |
| Require every attached system to install a mesh client | Appropriate for capable systems, but unavailable where the equipment or its owner cannot accept new software or credentials |

## Consequences

**Positive.** A translation change stays in the translation gateway. A protocol-session repair stays here.
Capture works without a translator or upstream connection, and an existing receiver can
consume already translated messages without joining the fabric itself.

**Negative.** The boundary must be maintained across two independently released
components. Session parsing remains attack surface and needs bounded framing and type
checks. A singleton router and drop-oldest queues do not guarantee continuous service
or lossless replay. Per-destination upstream identity and field acceptance are open work.

## Security assessment view

The assessable boundary is the declared source/destination, certificate and pen grant,
two release gates, host firewall and local audit evidence. SAPIENT parsing and envelope
rewriting must be included in that assessment; calling the arm a byte relay would omit
reachable code. Source-address attribution and a shared upstream slot do not prove
per-system credential custody. No waiver, national classification decision or product
accreditation is granted by this record. Live negative egress, lockdown defaults,
credential-free field installation and revocation acceptance are open work.

## Trigger conditions for revisit

- A proposed arm begins mapping sensor fields, units, coordinates or message formats.
- A receiver requires tasking or another return path into the fabric.
- Per-destination upstream credentials or a recorded custody assessment become available.
- Continuous delivery or lossless replay becomes a requirement for this deployment.
- GOAT changes the pod tier, admission or identity requirements cited here.

Implementation references: [engineering principles](../engineering-principles.md),
[egress design](../design-egress-gateway.md), [SAPIENT egress](../../arms/sapient/src/egress.rs),
[session handling](../../arms/sapient/src/session.rs) and
[relay envelope rules](../../arms/sapient/src/wire.rs).
