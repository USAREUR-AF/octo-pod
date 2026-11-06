<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Design: octo-pod joins the environment health surface

**Status:** partly implemented; broader fleet design remains proposed.
**Design date:** 2026-08-18. **Implementation note:** 2026-10-15.

The candidate has a host timer writing `octo-pod status --json`, a file-reading Rust
publisher and a separate Podman Quadlet unit. See
[the configure playbook](../platform/configure-octo-pod.yml),
[publisher](../arms/health-pub/src/main.rs) and
[unit](../platform/templates/pen/octo-health-pub.container.j2).
The local writer produces a current snapshot; that is not proof of the append-only
history proposed below. Fleet aggregation, reader integration and the other design goals
are not established merely by the publisher existing. Historical Compose failures below
explain the isolation decision; current operation uses systemd/Podman.

**Depends on:** goat ADR 1036 (`EnvHealthV1`, proposed) · ADR 1034 (fabric health, built, the pattern source)

---

## Context and scope

octo-pod repairs itself. A watchdog restarts a failed container, a timer renews an expiring
certificate, `netbird-selfheal` re-enrols a dropped agent. These recovery actions are not
visible remotely.

**A pod that repaired itself four hundred times looks identical, from anywhere else, to
one that never had a problem.** The only way to learn a pod's state is to reach it and run
`octo-pod status`, and a pod that cannot be reached is precisely the one worth knowing
about.

An earlier draft of this document designed a health message, a topic convention, a state
vocabulary and a reader. **All four already exist**, in goat's environment health surface:
a single honesty-graded envelope (`EnvHealthV1`) on the canonical
`ops/<domain>/health/<scope>` topic, reveal-only, with one renderer per level of detail:
`goat health`, a panoscope band, a stethoscope target. New domains are designed to use the existing
interfaces.

So this document is no longer "how to build pod health". It is **how octo-pod becomes a
domain in a surface that already exists**, and what is octo-pod-specific about doing so.

### Three kinds of pod, not two

Mesh membership and holding an upstream slot are independent, `netbird_setup_key` and
`fabric_slot_anchor` are set at different moments by different people. That produces three
configurations; a design supporting only the third excludes the others:

| | on the mesh | holds a slot | outward path |
|---|---|---|---|
| **A** | no | no | none. LAN-only, isolated |
| **B** | yes | no | routable, but no fabric identity to publish under |
| **C** | yes | yes | publishes onto the fabric |

A and B are supported configurations, not degraded ones: a pod that captures and retains locally
and releases nothing is complete by design.

## Goals

- **A pod's state is knowable without reaching it**, wherever a path exists.
- **A pod knows its own history whether or not anyone is listening.** Producing health is
  unconditional; transmitting it is not.
- **Absence is an explicitly represented state**, distinct from healthy and from unhealthy.
- **Self-repair is visible.** A pod healthy *because it keeps fixing itself* is
  distinguishable from one that has operated without faults.
- **Adds no new credential, port or inbound path.**
- **Adds no new surface.** It uses the fleet's envelope, topic convention, grading
  vocabulary and renderers.

## Non-goals

Each could reasonably be a goal. Each is explicitly refused.

- **Not a new envelope, topic convention, state vocabulary or renderer.** Those exist. The
  previous draft of this document defined all four, which is the mistake this revision
  corrects.
- **Not metrics.** No time series, no scrape endpoint. If someone wants Prometheus later,
  this is a source it could read, not a replacement.
- **Not alerting.** This produces a truthful state; deciding when to notify
  an operator is a different system with different owners.
- **Not remote control.** Health flows outward only. The moment it accepts an instruction
  it is an inbound control path on a border appliance and the threat model changes.
- **Not a fleet inventory.** It describes only pods that report their state. Knowing which
  pods *should* exist is the registry's job, and conflating the two produces a system that
  cannot tell "gone" from "never existed".
- **Health is never replayed.** A reconnecting pod publishes current state and nothing
  else. This is safe rather than lossy: counters are cumulative since boot, so one fresh
  message conveys everything that happened during an outage. `repairs: 412` after a week
  of silence says what 412 buffered messages would have said. Nothing to replay means no
  queue to bound and no stale state to mistake for current.
- **Not a replacement for `octo-pod status`.** That stays the detailed local view, now reading
  the retained record rather than only the present moment.

## Degree of constraint

Inherited and not negotiable:

- **`EnvHealthV1` is the envelope.** `domain`, `scope`, `ts_unix_ms`, `overall_color`,
  `sample_count`, `window_secs`, and a `oneof detail` for domain structure.
- **`ops/<domain>/health/<scope>` is the topic convention**, domain-first. `ops/` is the
  canonical operational namespace; `system/` is deprecated.
- **The existing five-state grading is reused**: `green | amber | red | idle |
  unmetered`. Unknown state must be reported rather than an unsupported healthy state.
  Each reading includes its sample count (`n`), and measured, assumed and inferred values
  remain distinct. Rules that prevent misleading alerts also apply.
- **The fabric is brokerless.** An unobserved publish is discarded. There is no queue to
  retain it.
- **A pod may be offline for days**, so what is published must be meaningful when it
  arrives late and must not imply currency it does not have.

---

## The design

### A new domain, not a new surface

octo-pod becomes the `pod` domain:

```
ops/pod/health/<pod-id>
```

One key per pod. The scope is the pod's handle, the same string that is its registry
handle, its mesh peer name and its slot alias, because those must already be identical.

A `PodDetailV1` arm is added to the existing `oneof detail`. Everything else, the header,
the grading, the renderers, is inherited and requires no new code on the reading side.

### What `PodDetailV1` carries

| Field | Why |
|---|---|
| `version`, `boot_id`, `uptime_s` | what it runs, and whether it restarted since last time |
| `publishers[]` | per arm and peer: declared, running, sink live, cert not-after |
| `orphans[]` | running but undeclared, the security-relevant one |
| `repairs` | cumulative counters since boot, per repair kind |
| `clock_anchored`, `clock_offset_ms` | whether wall-clock has ever been externally anchored |
| `releases_upstream` | whether this pod forwards anything at all |

**`repairs` exposes repeated recovery actions.** A rising repair count on a pod
reporting green distinguishes a system with recurring faults from one operating
without faults.

### Mapping pod state onto the inherited five

The grading vocabulary is not ours to extend, and it maps cleanly:

| Pod condition | `overall_color` | Why |
|---|---|---|
| everything declared is running, nothing undeclared is | **green** | measured |
| a named deficiency: a sink down, a certificate inside 30 days | **amber** | measured, degraded |
| a declared publisher is not running, or an orphan is | **red** | measured, failing |
| the pod cannot determine something it needs to | **idle** | honest-unknown, never false-green |
| **no publishers declared at all** | **unmetered** | there is no target to be healthy against. A legitimate state for a freshly built pod |

`sample_count` records the measurement basis: how many publishers the reading is derived from. A green
with `n=0` is `unmetered`, not health.

**Absence is asserted by the reader**, from staleness, the same rule the time domain uses,
where an attestation older than two heartbeat intervals stops counting as fresh. A pod
cannot publish its own absence, which is exactly why absence is the state that matters.

### Produced always; transmitted when there is somewhere to send it

To support configurations A and B: **health is a
local fact that may optionally be transmitted, not a message that may fail to send.**

Every pod maintains a bounded append-only record of its own state transitions and repair
counts, in `/var/lib/octo-pod` where local state is already stored. Publishing `EnvHealthV1` is
a second, additive consumer of that same record.

| Shape | What it gets |
|---|---|
| **A**: isolated | the local record. Available for local inspection: three months alone, and the first person to reach it learns the router restarted 400 times in week two and has been stable since. |
| **B**: mesh, no slot | the same record, readable by a human who connects. See the open question on why that is a diagnostic and not a fleet mechanism. |
| **C**: mesh + slot | the record, plus continuous publication into `ops/pod/health/`. |

This also repairs something a message-only design gets wrong in shape C. The fabric is
brokerless: if the bridge is down when a transition happens, **that transition is gone.**
With a local record the pod still knows, and the next reader sees counters that already
account for it.

The record is not a queue. It is never drained, never re-sent, and is bounded by age and
size like everything else on the pod.

---

## Fleet-scale ramifications

This section exists because the naive design, one health key per pod, one reader
subscribing across the fleet, is **measurably dangerous** on this fabric, and the
measurements already exist.

### The binding constraint is keyexpr cardinality, and it is not linear

From the fabric's own cardinality measurements:

| Keys in the router | scoped GET | tree walk |
|---|---|---|
| baseline + health + tenants + audit | 2 ms | 17–19 ms |
| +5k | 2 ms | 38 ms |
| +15k | 3 ms | **227 ms** |
| 50k **with active writes colliding with a crawl** | **0.65–7.3 s** | **6–29 s** |

The last row measures concurrent reads and writes, which a fleet health system produces:
**continuous small writes from every pod, plus a broad read across all of them.** Health
publishing *is* the active-write side. A fleet-wide subscription or crawl *is* the read
side. A design that does both at 50k keys stalls the router for seconds.

octo-pod carries live radar tracks. A six-second stall in a track display is not a
degraded service; it is a wrong one.

**Consequence:** the per-pod key count must be **exactly one**, and it must be treated as
part of the pod's cardinality budget rather than as free. One key per metric, a
`/health/cpu`, a `/health/certs`, multiplies the fleet's key count by the number of
fields and increases the concurrent read/write load described above. Varying data goes in the payload. This is the
fabric's existing stable-key contract, and health is not exempt from it.

### Never subscribe fleet-wide; aggregate in tiers

A reader that subscribes across every pod creates fleet-wide read load and is also a
single process receiving N messages per interval. At 10⁵ pods on a 60-second cadence that
is ~1,700 messages per second into one subscriber, and at 10⁸ the load increases to approximately 1.7 million messages per second
for one subscriber.

Health must be summarized through the same aggregation tiers that let managed systems exceed
network participants: **pod → site → region → fleet**, each tier subscribing only to its
own scope and republishing a summary on a single key. A fleet-level reader subscribes to
summaries, never to pods.

This structure also provides the expected pod count: each tier knows how many
children it expects, so *absent* is computed where the expected count is maintained.

### QoS: health must never share a band with mission data

Priority on this fabric binds to **authenticated identity and namespace**, not to a
priority byte a publisher can set for itself. That is what makes the guarantee
unspoofable, and it means health needs to be *declared* into a low band rather than
only marked as low in a publisher-controlled field.

Two requirements follow:

- **Health is never in the reserved mission-critical slice.** Whatever envelope protects
  track delivery, health is excluded from it and receives lower scheduling priority when the link is
  congested.
- **Degradation is enforced by the band, not by intent.** The design already says health
  must never be a reason a pod stops capturing. Intent is not a mechanism; the priority
  band is.

### mTLS session economics

Every session costs a handshake, a certificate-chain validation, and router-side state
that persists for the session's life. At fleet scale these are real quantities:

- **Health reuses the pod's existing session.** Opening a second connection to publish a
  few hundred bytes a minute doubles session count, handshake CPU and router state for the
  whole fleet. If QoS binding requires a distinct identity for the low band, that trade,
  a second session per pod against band separation, must be measured before it is chosen,
  not assumed either way.
- **Correlated certificate expiry is a fleet-wide event.** Leaves are issued at
  provisioning, so pods built in the same week expire in the same week. The pod renews its
  own certificates, which prevents simultaneous expiry, but health traffic is the thing that would
  fail *simultaneously across a whole provisioning cohort*, and the health system must not
  be what discovers that. Expiry should be staggered at issuance.

### Synchronisation is the failure nobody plans for

A fixed 60-second cadence means every pod publishes on the same boundary. That is a
periodic spike rather than a smooth load, and it gets far worse in the case that matters:
**after connectivity is restored, every pod reconnects and publishes at once.**

The cadence must be jittered by an offset derived from a hash of the pod's identity: stable per pod, uniformly spread across the interval, and requiring no coordination.
Reconnect must additionally be backed off, because restored connectivity is precisely when
the fabric is least able to absorb a synchronised burst.

### Failure of the health system must not look like failure of the fleet

If a collector stops, every pod beneath it goes *absent* simultaneously. This can be mistaken for
a fleet-wide outage when the failure is in monitoring.

Each tier therefore publishes its own liveness alongside its summary, and a reader that
has lost a collector reports **"health data unavailable for 4,000 pods"** rather than **"4,000 pods are
down"**. These conditions require different operator responses.

### What this means for the fleet-scale design

| At one site | At fleet scale |
|---|---|
| one key per pod is free | one key per pod is a budgeted cost; one key per *metric* increases router load |
| a reader subscribes to all pods | readers subscribe to tier summaries; nothing crawls the fleet |
| cadence is a constant | cadence is jittered per pod, and is a variable to be re-derived |
| health shares the data path | health is a separate, lower QoS band, deprioritized first |
| absence means a pod is gone | absence means a pod is gone *or* the tier above cannot collect health data, and those are reported differently |

---

## What happened when it first ran

Built 2026-08-18, first started on a real pod 2026-08-19. The design isolated capture from a health publisher failure.
The failure was caused by the file permissions described below.

**The publisher could not read its own key.**

```
Invalid TLS private key file: Permission denied (os error 13)
connect_private_key_file=/etc/zenoh/tls/health-key.pem
```

It runs as the nonroot container uid and mounts the pen's TLS directory directly, the way
the router does rather than the way an arm does. The router's key is `0640 root:<runtime>`.
The health key was `0600 root:root`, because the task that opens a key to the container gid
named exactly one file and this publisher did not exist when that task was written. It
crash-looped.

Two observations explain the impact.

**The failure had not been detected because the publisher had not run.** The image was not in the airgap
bundle, so on every pod built so far there was nothing present to be refused by a
`0600 root:root` key. The bug and the thing that hid it were the same omission.

**Separating it from the pen preserved capture.** Under the earlier design, where
health was a service in the pen's compose file, a publisher that cannot start is a
`docker compose up` that does not complete, and the router does not start either. The pod
would have lost capture because it could not report health, which is the exact inversion
this design was written to prevent. Instead the pen started, the pod captured, and the
failure was reported locally.

The fix opens both keys to the container gid and restarts the publisher when the ownership
changes. A bind-mounted file's permissions change under a running container without it
noticing, so the handler makes the post-apply state deterministic: a crash-looping container does recover on its
own eventually, but "recovers within the backoff window" and "is running when apply
finishes" are different promises and only the second is checkable.

## Alternatives considered

| Option | Reason rejected |
|---|---|
| **Define our own envelope, topic and reader** | What the previous draft of this document did. It independently reproduced the reveal-only shape, the five-state grading and the denominator argument. Arriving at the same answer separately is reassuring about the reasoning and duplicated implementation work. It would also have produced a second surface for operators to learn and a second renderer to maintain, which is precisely what the cross-domain contract exists to prevent. |
| **Prometheus exporter scraped centrally** | The earlier reasoning here was too broad and is corrected: a **mesh-enrolled pod is reachable**, so scraping is not impossible for shapes B and C. It is refused for three narrower reasons: it cannot reach shape A at all; it adds a listener and a port to a border appliance; and it inverts the load direction, so one reader’s query creates work on every pod at once. Publishing costs the pod the same whether one reader exists or a hundred. |
| **Push to a central HTTP collector** | Works through NAT, but adds a second outward dependency with its own endpoint, credential and availability, parallel to a fabric the pod is already authorised on. Requires maintaining two network paths instead of one. |
| **Reuse the existing audit sink stream** | The sinks already observe everything, but they observe *sensor data*. Mixing appliance telemetry into that namespace means a consumer of tracks receives health, and the release gate would have to reason about both. Different data, different lifetime, different audience. |
| **Log shipping** | Unstructured, unbounded and expensive on a constrained link. The question here is small and structured; a log pipeline is the wrong size of answer. |
| **`octo-pod status --json` over SSH, collected by a fleet tool** | Today's implicit answer. Requires reachability, a credential per pod, and N connections per query. The per-system manual step that does not scale to large fleets. |
| **Publish only on change** | Uses fewer messages but cannot distinguish failure: silence would then mean both "nothing changed" and "the pod is gone". A heartbeat is what makes absence detectable. |

---

## Cross-cutting concerns

**Security.** Health publishes under the pod's existing slot with its existing certificate,
into `ops/`, subject to the same ACL and release rules as anything else it writes. It
reveals what the pod runs and how healthy it is: operationally sensitive, not secret, and
no keys, sensor data or addresses beyond what the fabric already knows. It flows one way
and accepts nothing.

**Time.** `ts_unix_ms` is the pod’s local timestamp and may be wrong, a pod at 1970 is
precisely a pod worth hearing from. The reader records its own receipt time and reports
both. Freshness is judged on receipt, never on the sender's clock. `clock_anchored` says
whether the pod’s clock has been externally anchored.

**Failure.** If the bridge is down the publish is discarded and the pod continues. Health
must never block, buffer unboundedly, or become a reason a pod stops capturing.

---

## Open questions

1. **For shape B, is reading a pod over the mesh acceptable?** A mesh-enrolled pod without
   a slot is reachable, so a human can connect and read its record. That is fine as a
   *diagnostic*, one operator, one pod, a question they already have. It is not a *fleet
   mechanism*: a loop over ten thousand pods is the per-system manual step that does not
   survive scale.
   If that holds, shape B supports local inspection and no fleet-level visibility, which may be
   the argument for **issuing every mesh-enrolled pod a slot purely so it can publish
   health**, independent of whether it ever releases sensor data. That is a policy decision
   about what a slot means, and it belongs to whoever owns the fabric.
2. **Cadence versus staleness.** The right interval is a function of how quickly an operator
   must learn a pod is gone, which nobody has stated. It also sets the reader's staleness
   threshold, since absence is defined in heartbeat intervals.
3. **Does the low QoS band justify a second identity, and therefore a second session?**
   Priority binds to authenticated identity **and namespace**. Publishing into `ops/` may
   give sufficient separation on the namespace alone. The measurement that would settle it
   compares two mechanisms that are both currently *proposed*, so the recommendation is to
   take the reversible path, publish on the existing session, and mint a second identity
   only if contention is ever observed.
4. **Does `repairs` need to name what was repaired?** Counting is enough to detect a pod
   repeatedly repairing faults; naming is what tells you why. Naming raises the payload and the
   cardinality budget.
5. **Who owns `PodDetailV1`?** The envelope is goat's. A new `oneof` arm is a change to a
   shared contract, and needs the same review as any other fabric-wide schema change.

---

## What this makes possible

Stated plainly so it can be argued with:

- A pod repeatedly failing and recovering for a month becomes visible before it exhausts
  whatever it is compensating for.
- A pod that stops publishing is noticed, rather than discovered when someone needs it.
- An undeclared publisher, the security case convergence catches at `apply` time,
  becomes visible on a pod nobody has run `apply` on.
- Pods appear in `goat health`, the panoscope band and stethoscope **without additional
  rendering code**, because they are a domain in a surface that already renders.
- Fleet health includes the expected pod count, so readers can detect missing pods.
