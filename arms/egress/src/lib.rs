//! octo-egress: the pod's outbound direction.
//!
//! An inbound octo-pod arm gives each source its own identity, a namespace, an audit trail
//! and a release step before anything leaves the pod. This crate gives each outbound
//! destination the same four things:
//!
//! ```text
//!  fabric router        downlink-bridge                pen (pod-local)      egress-arm /
//!  K (any key the  -->  gate: K in downlink_allow -->  downlink/K      -->  zenoh-egress-arm
//!  slot can read)       (subscribes nothing else)      (pod CA, mTLS)       ACL: only its
//!                                                                           keys, delivers
//!                                                                           to ONE declared
//!                                                                           destination +
//!                                                                           local audit
//! ```
//!
//! A destination is reached the way the consumer expects: a datagram (`udp://`), a
//! connection the arm dials (`tcp://`), a connection the consumer dials (`tcp-server://`,
//! the pod standing in for the radar) — those three are `egress-arm`. A partner that
//! already runs their own Zenoh mesh needs none of that: `zenoh-egress-arm` dials their
//! router in client mode instead, under a certificate they issued, confined to a prefix
//! they granted.
//!
//! What lives where:
//! - [`gate`]: the downlink gate, the mirror of the uplink bridge's `bridge_allow`.
//! - [`dest`]: the destination an `egress-arm` was declared with, its exact allow list, and
//!   the peers a `tcp-server://` destination accepts.
//! - [`zenoh_dest`]: the partner router and granted prefix a `zenoh-egress-arm` was
//!   declared with, and its allow list.
//! - [`serve`]: the listener for consumers that dial the pod.
//! - [`audit`]: an egress arm's local NDJSON record of what was sent where.
//! - [`session`]: the bridge's fabric leg and `zenoh-egress-arm`'s partner leg, both of
//!   which verify the router's name and retry forever rather than opening once. The pen leg
//!   uses the shared `octo_arm_common::SessionConfig`, which does not verify the name.
//!
//! The pen session, the bounded hand-off queue and its counters come from
//! `octo-arm-common`, so an egress arm ages out, drops and counts exactly as the inbound
//! arms do: stale data is dropped and counted, never sent late.
//!
//! What the deployment provides: a pod-CA certificate for each egress arm and one for the
//! bridge, the pod's slot certificate for the bridge's fabric leg, and pen ACL grants that
//! let each egress arm read only its own `downlink/...` keys. Each binary enforces its own
//! gate whether or not those grants are right; the pen ACL is the outer gate, the fabric
//! router's read grant for the slot the outermost.

pub mod audit;
pub mod dest;
pub mod gate;
pub mod serve;
pub mod session;
pub mod zenoh_dest;
