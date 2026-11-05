# ADR 0002: A Peer May Dial the Pen over the LAN and Read Fabric Keys Released to It

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-10-21 |
| Supersedes | none |
| Superseded by | none |
| Related | [design-egress-gateway.md](../design-egress-gateway.md); GOAT release lanes |

## Summary

A zenoh-native peer, such as a partner's middleware, could only write its own namespace and
only reach the pen over the mesh. Two partner integrations needed more: the partner sits on
the site LAN, not the mesh, and the partner also wants to subscribe to sensor data that has
been released to it. A peer may now declare `allow_sources` (the pen opens on the LAN to
exactly those addresses) and `read_keys` (fabric keys that come down through the same gate
egress destinations use). Both are off by default, both are one command each
(`octo-pod peer allow-from`, `octo-pod peer allow-read`), and neither widens any other
identity's access.

## Context

- With `pod_firewall_enabled: true`, the firewall opened the pen's port only on the mesh
  interface and to the pod's own containers. A partner on the site LAN that dialed the pen
  was dropped before TLS, which presents as a timeout on the partner's side. Arms and
  `tcp-server://` destinations already had a LAN path, scoped to declared `allow_sources`;
  peers had none.
- The pen ACL granted a peer write on `raw/<id>/**` and nothing else. The only way to get
  fabric data to an outside system was an egress destination, which delivers to a socket.
  A partner that already speaks Zenoh would need a socket shim on its side just to receive
  what it could subscribe to directly.
- The downward path already exists: the downlink bridge reads the fabric as the pod's slot,
  only for keys in `downlink_allow`, and republishes fabric key `K` on the pen as
  `downlink/K`. Two gates must both be open before a destination reads anything.

## Decision

1. **LAN reach.** A peer may declare `allow_sources`, literal IPv4 addresses only. The pod
   firewall renders one rule per such peer: the pen's port, on the LAN interface, from
   those addresses. A peer without it keeps the mesh-only opening. The pen ACL and the
   peer's certificate still decide what it may do once connected.
2. **Reads through the existing downward gate.** A peer may declare `read_keys`, fabric
   keys held to the same scope rule as egress keys (literal publisher anchor or
   `release/<lane>/`; never `**`, `ops`, `attest`, `system`, `downlink` or `@`). The pen
   grants that peer's own certificate, and no other identity, a read on `downlink/K` for
   each key, and only while both gates are open: the key is in `downlink_allow` and the
   peer's own `release.from_fabric` is true. An arm cannot declare `read_keys`; its source
   holds no credential to read with.
3. **One reader list.** Every decision about the downward path counts egress destinations
   and peer readers together (`downlink_users`): whether the downlink bridge runs, whether
   its image is needed, which keys `verify` treats as orphaned, and which keys `egress
   revoke` or `peer revoke-read` may drop. Revoking one reader never drops a key another
   released reader still needs.
4. **Commands, not hand edits.** `octo-pod add-peer --from`, `octo-pod peer allow-from`,
   `peer allow-read`, `peer revoke-read`, `peer bundle` and `peer check`. Each edits the
   declaration only; `octo-pod apply` renders the grant, the firewall rule and the peer's
   bundle. The playbook's declaration gate enforces the same rules when files are edited
   by hand.
5. **The bundle describes the grant.** The peer's instructions name the endpoint it can
   actually reach (the pod's `lan_ip` for a LAN peer, the mesh address otherwise), the
   `downlink/` keys it may subscribe to, and nothing it may not. `peer bundle` refuses to
   package instructions rendered before the last change to the declaration.

## Alternatives Considered

- **Grant a peer read on a raw pen namespace directly.** Simpler, and it would let a peer
  read another local publisher on the same pod. Rejected: it bypasses `downlink_allow`, so
  a release of fabric data would have no gate the egress path does not also have, and the
  pod's two release paths would disagree about what "released" means. A local reader can be
  added later as its own decision if a real case needs it.
- **Peers federate as routers.** Rejected for the reason 20-peers.yml already records:
  router federation makes the partner's router a transitive trust bridge, and every
  sensor's identity is lost at the hop.
- **Open the pen on the whole LAN.** Rejected: on a shared exercise LAN that is every other
  participant. The opening is per peer and per address, like an arm's.
- **Keep egress as the only outbound path.** Rejected for Zenoh-native receivers: it forces
  the receiver to stand up a socket listener for data it can subscribe to.

## Consequences

- A partner on the site LAN can publish to the pod and subscribe to what is released to it,
  over one session and one certificate, with no software of ours on its side.
- The confinement limit in design-egress-gateway.md applies unchanged: every read comes from
  the fabric through the pod's one slot. Until that slot is a release-lane member, the pod's
  declarations are the only limit on what reaches a peer. Data for an organization outside
  ours should be released into that organization's lane and granted by its lane key
  (`release/<lane>/...`), which makes the release visible to the data's owners. The slot is shared by every
  reader on the pod, and a shared credential is an unattributed feed; a receiver that
  needs its own attribution on the fabric still gets its own pod.
- `pod_firewall_enabled: true` now has a supported path for LAN partners. A partner that
  dials from an address not in `allow_sources` sees a connection timeout, not a TLS error;
  the bundle says so.
- A pod running a release without this change ignores `allow_sources` and `read_keys`
  silently: the declaration parses, and nothing is opened or granted. Upgrade before
  declaring either.
