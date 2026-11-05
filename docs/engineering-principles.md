# octo-pod: engineering principles

**Scope:** the Podman-based appliance, 0.4.0 and later.
See [Release status](release-status.md#release-status) for package and image availability.

This document records the implementation boundaries, decisions and tradeoffs.
[ADR 0001](adr/0001-edge-appliance-and-translation-boundary.md) remains Proposed;
implementation or publication does not change that decision's status or establish
field acceptance.

---

## Context and scope

A pod is one box at one site. Sensors on the local network, mostly radars, emit bytes
at a socket. They speak no security protocol, hold no credential, and will never be
changed to.

The pod runs at a site with limited connectivity and remote operator access. The design
prioritizes local capture, bounded resource use and recovery without local intervention.

The pod does five things:

1. **Listens** where each sensor is programmed to send, or dials out to sensors that serve
   their own stream.
2. **Assigns scoped identities** to declared arms and peers, with their own credentials,
   namespaces and audit trails. A source address remains provenance, not sensor authentication.
3. **Records** what arrived, locally, whether or not anything upstream is reachable.
4. **Forwards nothing** until someone explicitly releases it.
5. **Delivers fabric data to systems that cannot join**, the way each one already connects
   to a sensor, once someone releases it. The receiver installs nothing and holds no
   credential. See `docs/design-egress-gateway.md`.

## Goals

- **Attribution.** Preserve the declared publisher identity and observed source
  provenance on forwarded data.
- **Local autonomy.** Capture and retention work with no upstream connectivity, no mesh
  membership and no operator present.
- **Unattended operation.** The pod survives reboots, outages, certificate expiry and
  partial failure without anyone reaching it.
- **Safe convergence.** Re-running convergence preserves the declared intent. Changes
  can be previewed before application; retiring or rebuilding a pod is a separate action.
- **Verifiable software supply.** A pod can prove what it is running came from this
  repository's release pipeline, without holding any credential.

## Non-goals

- **Translating the data.** Upstream producers own sensor decoding, semantic validation and format
  conversion. Byte arms capture without a schema prerequisite. SAPIENT parses envelopes
  and session messages, and filters relay types, but does not map detection or alert
  domain fields. See *Capture-first*.
- **Durability.** The pod is not a store-and-forward queue. Data older than a few seconds
  is discarded deliberately. See *Bounded buffering*.
- **High availability within one pod.** The router is a singleton and a restart is a
  pod-wide event. See *Singleton router*.
- **Automatic application of declaration changes.** The pod repairs runtime faults by
  itself and never applies a config change unattended. See *Repair, never apply*.
- **Being a fleet control plane.** The pod converges itself. It does not manage other
  pods, and nothing reaches into it.
- **Kubernetes.** Its patterns are used; its platform is not. See *Alternatives considered
  at the system level*.

---

## System context

Four components, with deliberately different lifecycles.

| Component | What it is | Changes when |
|---|---|---|
| **arms** | small Rust binaries, one per sensor, in containers | a new wire protocol is needed |
| **the pen** | a local Zenoh router, a CA, one audit sink per publisher | a publisher is added or removed |
| **platform** | Ansible playbooks, templates and the CLI | the software is upgraded |
| **identity** | seven files in `/etc/octo-pod`, including `40-egress.yml` | an operator declares something |

The data path:

```
sensor → socket → arm → pen router (mTLS, ACL) → audit sink
                                  └→ bridge → shared fabric   (only if released)
```

The downward path, for a system that cannot join:

```
shared fabric → downlink bridge → pen router as downlink/K → egress arm → receiver
                (pod slot, only released keys)               (reads only, writes nothing)
```

A pod that captures and retains, and publishes nothing, is a **complete and legitimate
state**, not a half-installed one. That shape governs the whole system.

See [ADR 0001](adr/0001-edge-appliance-and-translation-boundary.md) for the package,
dependency and session boundaries, and the [deployment guide](deployment.md#install-or-update) for
installation and updates.

## Degree of constraint

The fabric and host contract constrain the implementation:

- **The fabric is Zenoh.** The pod publishes into an existing fabric, so it speaks Zenoh.
  What was chosen is how to live with its properties, not whether to use it.
- **The host is amd64 or arm64 Debian/Ubuntu-family Linux with systemd, Podman 4.7 or later and skopeo.**
  Ubuntu 24.04 is the documented package path. Ubuntu-based Jetsons were the original
  target, not the only supported hardware. Their RTC problem still matters under *Time*.

Everything else in this document was a free choice.

---

## Design decisions

Each decision states what was chosen, why, what was rejected, and what the choice costs.

### A CLI wrapping Ansible, rather than extending the fleet CLI

**Decision.** octo-pod is its own thin bash CLI over Ansible with `connection=local`.

**Why.** `goat`, the fleet CLI, is a control surface for the fabric: it talks to the
portal, mints slots, enrols machines. It runs on an operator's workstation and reaches
outward. octo-pod does the opposite job: it converges the box it is running on. The
inventory is literally `pod ansible_connection=local`. There is no control node and
nothing reaching in, because a pod is frequently behind someone else's NAT with no route
back.

**Alternatives considered.**

| Option | Why it lost |
|---|---|
| Add pod verbs to `goat` | Couples two lifecycles. Pod convergence would ship on the fabric CLI's cadence, and the fabric CLI's portal and auth code would land on a border appliance with no business holding it. |
| Write convergence in Rust | Reimplements what Ansible already does, including `--check --diff`, the part that matters most, because it lets an operator read what a run *would* change. |

The two tools are also used by different people at different moments. The operator running
`goat machine enroll` has portal access and is at a desk. The person running
`octo-pod apply` is at a site, possibly on a phone tether, and should need credentials to
nothing.

**Cost accepted.** The CLI is deliberately thin bash rather than a program. It prints every
command it runs, so nothing it does is a black box you cannot reproduce by hand.

### Ansible, rather than Puppet, Salt, Nix or shell

**Decision.** Ansible with `connection=local`.

**Alternatives considered.**

| Option | Why it lost |
|---|---|
| **Puppet** | Needs an agent plus a master. A pod behind NAT cannot be pulled by a master, and installing an agent to converge a box that can converge itself is a dependency for nothing. |
| **Nix** | Genuinely better immutability. Loses on adoption cost: the hardware ships Ubuntu-based images, the fleet's tooling is apt-shaped, and the people who will maintain this do not write Nix. |
| **Plain shell** | No idempotence, no dry run, no diff, and idempotence is the property the entire operating model rests on. |

**Cost accepted, honestly.** Ansible is slow, its error messages are poor, and YAML is a
weak language for conditionals. Those were judged cheaper than the alternatives' costs.

### Two layers: platform and identity

**Decision.** The software is one layer, replaced wholesale on upgrade. The pod's own
declaration is another, which an upgrade cannot touch.

**Why.** They change for different reasons and must change independently. An upgrade
replaces every playbook and template and must not disturb the arms an operator declared.
Adding a sensor must not require a software release.

**The test is concrete.** Can you replace the whole platform layer while the pod is running
and lose nothing an operator configured? Yes, and that is what makes unattended upgrades
safe.

**The corollary took a while to see.** Nothing that names a deployment belongs in the
shared layer. Five values (mesh host, upstream endpoint, DNS pins, portal, package repo)
were sitting in `group_vars`, meaning every site that received the software also received
the addresses of a fabric it was not part of. They now live in the pod's own
`05-fabric.yml`, and a test named *"the platform layer names no deployment"* fails if one
comes back.

### One arm process per publisher

**Decision.** Each sensor gets its own process, container and certificate.

**Why.** An arm has one key prefix. Sharing a process between two sensors would put both
under one namespace, and the namespace is the unit of release, so releasing one sensor
would release the other. Separate processes also give separate certificates, separate blast
radius, and an audit trail that can say which sensor produced what.

### A certificate per publisher, minted locally

**Decision.** The pod runs its own CA and issues one leaf per publisher.

**Why.** The ACL scopes by certificate CN. One shared credential would make every publisher
indistinguishable to both the policy and the audit trail, which defeats the point of the
appliance, attribution.

The CA is pod-local rather than fleet-central because a pod must be able to onboard a
sensor with no route to anywhere.

**Cost accepted.** Pod-local certificates mean nothing outside the pod. This is correct:
what leaves the pod is authorised by the upstream slot, an entirely separate credential
minted off-box.

### One declaration generates four artifacts

**Decision.** The certificate, the ACL grant, the audit sink and the connection bundle are
all generated from one entry.

**Why.** They are four expressions of one fact: *this publisher may write here*. Generated
separately they drift: a hand-minted certificate with no policy, or a policy with no
certificate. Generated together, neither can exist alone.

This is also why the ACL is machine-generated and hand edits are discarded. It is the most
dangerous file in the repository: read once at startup, silent in every failure mode, and
an unscoped subject is a wildcard granting every CA-rooted certificate full rights.

### Capture-first: raw bytes, no decoding

**Decision.** Byte-ingest arms forward exactly what arrived, with no schema prerequisite.
An upstream application translates payloads when a destination requires another format. The
SAPIENT session arm additionally parses protocol envelopes and control messages; it
relays already encoded messages, rewrites the session node id and send timestamp, and
frames them for TCP. These are session operations, not sensor-format mappings.

**Why.**

1. **Onboarding is not blocked on a specification.** A vendor who cannot tell you their
   message format can still be captured today and decoded later.
2. **The trust boundary stays thin.** A parser is attack surface; not decoding means the
   arm has almost none.

**Cost accepted.** Capture does not establish semantic validity. SAPIENT envelope/type
checks do not validate the meaning of a detection. Typed decoding and mappings belong in
the producer or an external translator; adding them to an arm would change the boundary in ADR 0001.

### Rust for the arms

**Decision.** The arms are Rust; trust behaviour lives in one shared crate.

**Why.** An arm parses attacker-influenced bytes off a socket, on a box nobody can reach,
with a length-prefixed framing mode where a bad length is a plausible input. That is
precisely where memory-safety bugs become remote compromise. Rust also produces a single
static binary with no runtime, which suits a container that should be nothing but the
program.

Everything defining trust behaviour (session setup, the allowlist gate, key grammar,
counters) lives in `common/` so the arms cannot drift on the parts that must be
identical.

### Bounded buffering, and deliberate data loss

**Decision.** A small queue sits between the socket and the session. It discards rather
than grows.

**Why not durability.** This is a live stream. A track from six seconds ago is not
valuable, it is misleading: replaying a backlog into a live picture after a reconnect is
worse than having dropped it.

**Why it exists at all.** Two reasons. The socket loop never blocks on the network, so a
stall cannot push loss into the kernel buffer where nothing counts it. And an outage
becomes visible. Before this, `Session::put` returned success into a dead session, so the
"forwarded" counter was counting data that never left the box.

**Three bounds, each for a different failure:**

| Bound | Protects against |
|---|---|
| **age** | staleness. Freshness beats completeness |
| **count** | a burst growing the queue without limit |
| **bytes** | memory exhaustion. Item count is not a memory bound. A few hundred 16 MB frames is gigabytes, and being OOM-killed is worse than losing data. |

Everything dropped is counted, so the cost of an outage is measured rather than assumed.

### Repair, never apply

**Decision.** A timer repairs runtime faults. It never applies declaration changes.

**Why.** `apply` is level-triggered and correct, but only runs when a human runs it.
Everything between two runs was unwatched, including a known failure where an audit sink
is up, green in `podman ps`, and subscribing nothing.

The split is exact:

| Difference | Who acts |
|---|---|
| **runtime fault**: actual state degraded, desired state unchanged | automatically, immediately |
| **declaration drift**: the desired state itself moved | reported; a human applies it |

A watchdog that applied configuration would restart the router and disturb every arm on the
pod because somebody edited a file, which is a 3am outage waiting to happen. The kubelet restarts a
crashed pod; it does not roll your Deployment because a YAML changed.

Adding the watchdog let a fan-out be deleted: an Ansible handler used to restart *every*
audit sink whenever the ACL changed. A sink's liveness is now the sink's own business.

### Convergence in both directions

**Decision.** `apply` refuses when a running publisher is no longer declared.

**Why.** Creating from a declaration is the easy half. Deleting an arm from `30-arms.yml`
regenerated the ACL without its grant, regenerated the router config, reported `ok`, and
left the container running with a valid certificate, still bound to its port and still
publishing.

That is the worst shape a convergence bug takes. Every artifact agrees the publisher is
gone, the operator believes they revoked it, and it is still there.

**Measured.** Removing a declaration by hand and sending to the port that no longer had
one: the counter went from `received=60` to `received=75 forwarded=75`. The undeclared
publisher accepted fifteen more datagrams and forwarded every one. After `apply` refused
and `-e reap_orphans=true` removed it, the same send produced nothing: no container, no
listener on the port, and no grant for the CN in the router config the running process
had loaded.

**Why it reports rather than reaps.** Stopping a live publisher interrupts whatever it
carries, and this play runs unattended from a timer on some pods. Declaration drift is a
human decision, so the default names the exact remedy and `-e reap_orphans=true` is the
deliberate act that applies it.

**What reaping does not do.** It stops containers. Both copies of the private key stay on
disk, still signed by the pod's CA. That is survivable rather than safe: the regenerated
ACL no longer grants the CN, and the router reloads it before anything reconnects, so the
credential is inert. It is not a retirement, and the message says so. `remove-arm` is the
verb that removes the key.

### Revocation is a file on disk, not a policy

**Decision.** `remove-arm` deletes both copies of a publisher's private key, and fails
loudly if it cannot find one where it expects it.

**Why.** A brokerless fabric has no revocation list to publish and no broker to enforce
one. The ACL is regenerated from the declaration, but it is read once at router startup,
and a certificate signed by the pod's CA stays valid for 825 days regardless. So what
actually ends a publisher's access is the absence of its private key.

That makes the removal path security-critical in a way it does not look. It reads like
cleanup.

**How it failed.** `remove-arm` derived the key filename from the certificate CN,
`octo-arm-<id>-key.pem`. The playbook writes it from the id, `<id>-arm-key.pem`. The two
never matched. Both the plan line and the `unlink` were guarded by `if key.exists()`, so
the mismatch read as "nothing to do": the command printed a clean removal, exited 0, and
had never once deleted a private key. There was a second copy it did not know about at
all, `/etc/octo-arm-<id>/tls/arm-key.pem`, mode 0640 and readable by the container
runtime, which is the copy the arm actually mounts.

The header comment asserted the opposite, in italics, for months.

**The fix that matters is not the path.** Correcting the string takes a minute and would
drift again the next time either side is renamed. The two sides are in different
languages, in different directories, written weeks apart, and nothing read both. So a
test now does: it extracts every per-publisher path the playbook writes, including the
ones openssl produces inside a shell block off a `${out}` prefix, evaluates the CLI's own
path expressions, and asserts that every file holding a private key is one the CLI
deletes. It checks the reverse too, because a CLI path that matches nothing the playbook
writes is exactly the original bug and is invisible from the CLI side.

**A guard that names one file cannot express a two-file invariant.** The certificate mint
was `creates:`-guarded on the certificate. Removal deliberately keeps the certificate so
old audit records still resolve. Re-adding the same id therefore found the certificate,
skipped the mint, and failed placing a key that was never generated. Guarding on the key
instead only moves the hole: this script writes the key first, so an interruption between
the two openssl calls would leave a key whose guard blocks the certificate forever. The
guard now lives in the script and requires both files, non-empty, which is the only
condition that means the identity is complete.

### The pod publishes its own health

**Decision.** A pod publishes one message on `ops/pod/health/<pod>` every 60 seconds,
jittered by its own identity. The privileged half runs on the host; the publisher is a
container that reads one file.

**Why.** Everything the pod does to keep itself alive is invisible. A watchdog restarts a
dead sink, a timer renews an expiring certificate, selfheal re-enrols a dropped agent. From anywhere else, a pod that has repaired itself four hundred times looks exactly
like one that never had a problem. The only way to learn a pod's state was to reach it,
and a pod that cannot be reached is the one worth knowing about.

**Why the split by privilege.** Computing health requires host-side runtime visibility.
A privileged systemd timer uses Podman on the host and writes a file atomically; the
container reads it. The publisher holds no runtime socket, no Podman access and no host
network, and its entire grant on the fabric is `put` on one key.

**Alternatives considered.**

| Option | Why it lost |
|---|---|
| **Define a new envelope, topic and reader** | The first draft of the design did this, and independently reproduced a reveal-only shape, a five-state grading and a denominator argument that all already exist in the fleet's environment health surface. Arriving at the same answer separately is reassuring about the reasoning and a waste of a build. |
| **Central scraping** | A mesh-enrolled pod is reachable, so this is not impossible, but it cannot reach a pod that never joined, it adds a listener to a border appliance, and it inverts the load direction so one curious reader creates work on every pod at once. |
| **Reuse the audit sinks** | They already observe everything, but they observe *sensor data*. Mixing appliance telemetry into that namespace means a consumer of tracks receives health. |
| **Publish only on change** | Cheapest and wrong: silence would mean both "nothing changed" and "the pod is gone". A heartbeat is what makes absence detectable. |

**Cost accepted.** One key per pod is a real charge against the fabric's cardinality
budget, the tree walk is O(N) in keys, measured at 227 ms for 15k. That is why it is one
key and not one per metric, and why the design says never to subscribe fleet-wide. A pod
with no upstream slot still cannot publish at all; that shape is unserved and named as an
open question in [`design-pod-health.md`](design-pod-health.md).

### Releases and packages, not `git clone`

**Decision.** One artifact, a `.deb` or a tarball, into a versioned directory behind a
symlink, with one verb: `octo-pod upgrade`.

**Why.** The original install was "clone the repo onto the box". That fails for an operator
in another country on four counts: it needs repository credentials per site; `git pull` is
neither atomic nor reversible; a merge conflict is unresolvable by someone who does not use
git; and there is no version to name in a support request.

Rollback is flipping the symlink back, because the previous release is still on disk.

**No apt repository, deliberately.** A repo is a second distribution channel to build, sign
and host, with its own metadata layout, while a `.deb` installed by file already gives the
two things worth having: dependency installation, and dpkg owning `/opt/octo-pod`.

**A near miss worth recording.** One variable, `octo_repo_url`, was serving both the release
file surface and an apt `sources.list` line. Pointed at the release surface, apt looks for
`dists/<suite>/…`, 404s, and a broken apt source fails *every* apt operation on the box,
including the installs provisioning itself depends on. They are now separate variables, and
the apt one is empty because nothing publishes a repository.

The clone still exists, for developers. `upgrade` refuses to run in one and says why.

### Signatures, and a public download surface

**Decision.** The artifact surface is public. Trust is carried by a signature over
`SHA256SUMS`, verified on the pod.

**Why public.** Otherwise a pod cannot fetch the software until it has joined the mesh, and
it cannot join the mesh until it has the software. Making it public means *where it came
from proves nothing*, so the trust has to travel with the artifact.

**Why keyless.** OIDC to a short-lived certificate. There is no private key to store, steal
or rotate, and what an operator verifies is the workflow identity. A signing key sitting in
CI would be the highest-privilege credential in the system, because it installs as root on
every pod.

**A flaw found by review, recorded because it was subtle.** The first version had an
`--unverified` flag for bring-up, which left `LATEST`, `SHA256SUMS` and the artifact all
controlled by the same server, so the hash check passed vacuously and a hostile mirror got
root at the next `apply`. It was replaced by `--expect-sha256`, which takes a digest from
*outside* the channel being verified. That flag then skipped the signature in its turn,
and on `--images` compared the digest to nothing, so any 64 hex characters unpacked an
unsigned bundle. Now the signature is always verified and the digest is an extra check on
the artifact. There is deliberately no way to skip verification.

### The pod renews its own certificates

**Decision.** A timer renews anything the pod's CA signed that is inside the window.

**Why.** Every leaf is valid 825 days and nothing ever re-minted them. Roughly two years
after provisioning, every pod in the fleet would stop accepting arms on the same date, presenting as a broken router rather than an expired certificate. The documented fix was
"re-issue from the CA on the management host", which was not true: the CA that signed them
is on the pod.

**How it avoids drift.** It holds no list of certificates and does not read the declaration.
It uses the existing certificate as the specification for its replacement (same CN, SAN,
EKU, key type), so it cannot diverge from what `apply` would mint, and it picks up arms
added long after it was written. Anything the pod's CA did not sign is skipped, which is how
the upstream slot certificate, which genuinely needs an operator, is left alone.

### The singleton router

**Decision.** One pen router per pod. HA was considered and rejected.

**Why.** Two routers with arms connecting to either would remove the pod-wide restart (Zenoh
routers can peer), but it doubles the audit surface, requires the ACL to be identical across
both, and introduces split-brain questions on a box in a field.

**The judgement:** make the restart *invisible*, so arms ride it out and resume by
themselves, rather than make it impossible. Revisit only if the gap proves to cost something real.

---

## Cross-cutting concerns

### Time

TLS validity is wall-clock, which makes the clock infrastructure here rather than a
detail.

The hardware is RTC-less and cold-boots at 1970. Every mTLS handshake then fails
*"certificate is not yet valid"*. The pod cannot join its own fabric, and cannot reach the
time source that would fix it.

Two layers, the same pattern the fleet already uses:

1. **Persist the clock to disk** and restore it *before the network*, advancing only, so it
   is a no-op on hardware with a real RTC.
2. **Let chrony refine it**, with stepping bounded to the first few updates. An
   unauthenticated source is needed to bootstrap from 1970, but must not be able to walk a
   running pod's clock, because that clock governs certificate validity.

Certificate renewal refuses to run when the clock is implausible. Signing then would date
every replacement to the epoch, turning a wrong clock into permanently broken trust across
every certificate at once.

### Security

The pod holds no registry credential and no repository credential, deliberately: it is the
thing that gets stolen, and it must yield only what it was already running.

Authorisation is per-publisher and scoped by certificate CN. The ACL is generated, never
hand-edited, because it is read once at startup and silent in every failure mode.

### Observability

Zenoh is brokerless: an unobserved publish is gone, not queued. That is why every namespace
that can be written has an audit sink watching it: a namespace with no subscriber is a
namespace where data silently evaporates.

Faults surface without anyone connecting to the box: unrepaired faults exit non-zero so a
failed systemd unit is visible in `systemctl --failed`, which is the only always-present
signal on a host with no metrics scraper.

---

## Alternatives considered at the system level

| Rejected | Why |
|---|---|
| **Kubernetes** | The patterns are right: declarative desired state, level-triggered reconciliation, liveness probes, immutable pinned artifacts. They are used. The platform is not: a control plane on a border appliance with no operator is the opposite of what the constraint asks for. |
| **Auto-apply on drift** | See *Repair, never apply*. |
| **Decoding or schema validation at the arm** | See *Capture-first*. |
| **Durable spooling to disk** | Turns a six-second blip into a disk-full failure on a box nobody can reach, for data that is not valuable by the time it would be replayed. |
| **Mesh-only distribution of the software** | Elegant, since revoking the peer revokes updates, but it cannot bootstrap a box that has not joined yet. |

---

## Open questions

- **A pod with no upstream slot still cannot report.** Health now leaves a pod that holds
  a slot. A pod without one has no outward path at all, which is a supported shape and
  invisible by construction. The design calls for a locally retained record so such a pod
  is legible on first visit; that half is not built.
- **No hardware watchdog.** A hung kernel never runs the software self-heal.
- **No ingress shaping.** A flood from a permitted source is absorbed by dropping, but not
  shaped.
- **No default download mirror.** `octo_repo_url` is empty by default; set it to a mirror
  the release workflow publishes to before `octo-pod upgrade` can fetch.

---

## Where to look

| Path | What it holds |
|---|---|
| `README.md` | how to operate a pod |
| `docs/adr/INDEX.md` | architecture decisions, their status and implementation boundaries |
| `docs/design-pod-health.md` | why a pod reports its own health, and what it costs at fleet scale |
| `docs/design-egress-gateway.md` | why the pod delivers fabric data to systems that cannot join, and why the receiver changes nothing |
| `docs/design-supply-posture.md` | proposed: declaring airgap or connected once, instead of inferring it |
| `example/` | a worked identity, to read rather than copy |
| `platform/tests/` | the offline suites. Read the ACL grant golden file first |
