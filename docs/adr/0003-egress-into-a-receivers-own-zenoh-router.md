# ADR 0003: Egress Into a Receiver's Own Zenoh Router

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-10-21 |
| Supersedes | none |
| Superseded by | none |
| Related | ADR 0002; [design-egress-gateway.md](../design-egress-gateway.md) |

## Summary

An egress destination could only be a socket: `udp://`, `tcp://` or `tcp-server://`. A
receiver that already runs its own Zenoh mesh would need a socket shim just to accept data
it can publish natively. A destination may now be `zenoh://IP:PORT`: the pod's egress arm
reads its released `downlink/` keys as usual and publishes them into the receiver's own
router, in client mode, under a certificate the receiver issued us, inside the prefix the
receiver granted.

## Context

- ADR 0002 covers a partner that dials the pod. This covers the reverse: the receiver keeps
  its router, its CA and its policy, and the pod is the client.
- The trust model reverses with it. On the way in, our CA mints the partner's certificate and
  our ACL confines it. Here the receiver's CA mints ours and the receiver's ACL confines us.
  The pod must never hold more than the receiver granted, and must never present our own
  pen identity to someone else's router.

## Decision

1. `octo-pod egress add <id> --dest zenoh://IP:PORT --prefix <p> --partner-bundle <dir>`
   declares a `kind: zenoh-egress` destination. The literal-address rule for every other
   destination applies to the receiver's router.
2. The receiver's certificate, key and CA are copied into `/etc/octo-pod/partner-bundles/<id>/`
   and mounted only into that destination's container. They are never minted here.
   `egress remove` deletes them.
3. The arm (`zenoh-egress-arm`) holds two sessions: the pod-CA identity that reads its
   released `downlink/` keys from the pen, and the receiver-issued identity that writes to
   the receiver. Fabric key `K` is published as `<prefix>/K`. The prefix must be a literal,
   declared twice (`prefix`, `allow_prefix`), and the arm refuses to start if they differ.
4. Release, revoke and check work exactly as for other destinations: two gates, both opened
   by `egress release`.
5. With the firewall on, the destination opens outbound TCP to exactly its address and port.

## Alternatives Considered

- **Have the receiver dial the pod and subscribe instead.** That is ADR 0002, and it is the
  right shape when the receiver can reach the pod. This exists for receivers that will only
  accept data into their own router.
- **Bridge the two routers.** Rejected, as for peers: router federation is a transitive
  trust bridge.

## Consequences

- A Zenoh-native receiver gets released data with no shim, and keeps full control of what it
  accepts.
- The pod holds a credential issued by an outside party. Its custody is the pod's, recorded
  by the destination's declaration, and it is removed with the destination.
- The egress image must carry `zenoh-egress-arm`. On a pod whose image predates it, the
  destination's unit fails to start; `octo-pod verify` reports the arm as not running.
