# Operations

Run these commands on the pod unless a section says otherwise. `otp` is an alias
for `octo-pod`. [Release status](release-status.md) lists what each release includes.
For one bullet per ingress, egress, and local service, start with the
[function overview](../README.md#functions).

## Everyday commands

```bash
sudo octo-pod status             # every arm and peer: state, transport, topic, release
sudo octo-pod top                # the same, live: rates, mesh, problems (q quits)
sudo octo-pod get arms           # all ingress and egress arms, with direction
sudo octo-pod get ingress        # only sensor-facing ingress arms
sudo octo-pod describe arms radar1 # one item in full, with the commands to dig further
sudo octo-pod logs arms radar1 -f # its container's logs
sudo octo-pod certs --check      # every certificate; fails if one ends within 30 days
sudo octo-pod check              # what apply would change; changes nothing
sudo octo-pod apply              # converge
sudo octo-pod verify             # prove it is BUILT right
sudo octo-pod verify-flowing     # ..and that data is actually arriving
sudo octo-pod add-peer acme      # a publisher that already speaks Zenoh
sudo octo-pod upgrade            # take a new version of the software
octo-pod version                 # what to quote in a support request
```

**`top` is the live view.** It shows one row per arm, bridge and egress destination, in
the order data moves through the pod, with the received and sent rate, drops, queue,
consumers and a trend. The rates come from the stats line each container already logs
every period, so `top` needs no new port or agent, and a rate is per second over the last
period. Keys: `1` flows, `2` declarations, `3` mesh peers, `4` problems, `enter` detail
with the commands to dig further, `l` logs, `/` filter, `?` help. It changes nothing.
`--once` and `--json` print one snapshot for scripts.

For example, start with a live overview, then inspect a named item. `otp` below is
the packaged alias for `octo-pod`; these are the same commands:

```bash
sudo otp top                         # select a row; Enter for detail, q to quit
sudo otp top --once                  # one text snapshot, then exit
sudo otp describe bridges uplink     # declaration, container, flow and certificates
sudo otp logs bridges uplink --tail 30
```

A running container can still report drops. `DROP` covers the last measured period;
`describe` also shows counters accumulated since container start and queue occupancy.
`LAST` is the age of the statistics line, not proof of a fresh sensor detection.
A SAPIENT output rate can be heartbeats alone. Investigate the named item and its
logs before treating a nonzero rate as successful delivery of sensor data.
A dashboard layout or declared subscription records intended connectivity. Validate
actual delivery with the pod counters and a subscriber or receiver at the destination.

**`get`, `describe`, `logs` and `certs` answer one question each**, in the shapes kubectl
uses. `get arms` includes ingress and egress with a direction column; `get ingress`
and `get egress` filter them. `get flows` also includes bridges and declared peers.
`describe arms NAME` and `logs arms NAME` accept either direction. The `edit arms`
command currently supports ingress endpoint corrections only.
`get KIND` prints one row per item, and `-o wide` or `-o json` gives more. LAST is
the age of the item's last stats line. `describe [TYPE] <id>` takes an arm, peer or egress id,
`uplink`, `downlink`, a pod container such as `pen-zenoh-router`, or any container name.
It prints the declaration with credentials redacted, the container, the flow counters,
the certificate, recent log lines other than stats lines, and the commands to run next.
`logs [TYPE] <id>` runs `podman logs` for that item. A peer runs on its own box, so it shows the
peer's audit sink; a stopped container shows its unit's journal, because podman removes
it when it stops. `certs` lists every certificate the pod holds, the slot certificate
included, when each ends and when the renewal timer next runs; `get certs -o wide` adds
the subject and the full path. `--check` exits 1 when one has expired, ends within
`--days` (30 by default) or cannot be read, so a monitoring check has to run it as root.
A removed publisher's certificate, kept for the audit trail without its key, is listed
as retired and never fails the check. Run without root, each verb says first that its
view is incomplete. All four read the same collector as `top` and change nothing.

**`verify` and `verify-flowing` answer different questions.** The first asks whether the
pod is built correctly. The second asks whether data is arriving right now, which only has
an answer once sensors exist. `verify` reports silence; only `verify-flowing` fails on it.

**`status` shows declared against actual**, which is how you see a publisher declared but
never started, a container nothing declares, or a release that does nothing because the pod
holds no slot.

## Adding a publisher

Three shapes, by how the sensor behaves:

```bash
# it sends UDP to a port you choose
sudo octo-pod add-arm radar1 --udp 48021 --from 10.1.2.30 --bind 10.1.2.10 --feed cat62-v1

# it connects to you over TCP
sudo octo-pod add-arm feed2 --tcp 48022 --from 10.1.2.31 --bind 10.1.2.10 --feed cat62-v1

# it listens, and you connect to it
sudo octo-pod add-arm radar-one --dial 192.0.2.104:9000 --feed cat62-v1 --framing len32be
```

`--framing` applies to TCP only: `stream`, `newline`, `len32be`, `len32le`. Getting it wrong
produces a running arm and subtly corrupt frames.

`--from` takes the sensor's IPv4 address. The arm compares addresses as text prefixes, so
`10.1.2.3` also admits 10.1.2.30 to 10.1.2.39. The pod firewall matches the address
exactly, so turn it on (see [The pod firewall](#the-pod-firewall)).

If the sensor is on a dedicated ingest NIC, give the pod its address there first, then
`apply`:

```bash
sudo octo-pod set-lan --iface eth1 --addr 10.1.2.10/24
```

### A partner that speaks Zenoh

The LAN/read controls, `peer` commands and the `zenoh://` destination below require
0.5.0 or later; see [Release status](release-status.md#release-status).

A partner system that already runs Zenoh is a peer, not an arm: it dials the pen itself
with a certificate this pod issues it. `--from` lets it dial over the site LAN from those
addresses; without it, it reaches the pen over the mesh only:

```bash
sudo octo-pod add-peer c2 --from 10.1.2.30
```

After `apply`, package its certificate and instructions for hand-off:

```bash
sudo octo-pod peer bundle c2
```

A peer may also subscribe to fabric data you release to it (`octo-pod peer allow-read`),
and the pod can publish into a partner's own router (`egress add --dest zenoh://`).
[`docs/guide-zenoh-partners.md`](guide-zenoh-partners.md) walks through every step.

## Retiring a publisher

```bash
sudo octo-pod remove-arm radar1
sudo octo-pod apply
```

| What | Happens |
|---|---|
| Containers | Stopped and deleted first |
| Private key, both copies | Deleted: the pen's and the arm's own |
| Certificate | **Kept**, so past audit records still resolve |
| Declaration | Removed last |
| ACL grant | Left until you converge. Inert once the key is gone |

`--keep-key` keeps the credential, for moving an arm rather than retiring it. If
`remove-arm` cannot find a key where it expects one, it says so and exits non-zero.

**If you edit a declaration by hand, `apply` refuses.** A running container that nothing
declares is a publisher still accepting data with a credential you believe you revoked, so
the play stops and names it. `-e reap_orphans=true` stops the containers and nothing else:
both private keys stay on disk. Follow it with `remove-arm` to remove the key.

## Sending fabric data to a system that cannot join

An egress destination is an arm in reverse. A system that cannot join the mesh receives
fabric data from the pod through its own egress arm: its own certificate, one literal
address, and an audit record of every send. The pod delivers it the way that system
expects to get it from the sensor itself, so the system's owner changes nothing: they point
an input they already have at the pod.

### When to use the pod, and when not to

A system can get fabric data three ways. Pick by what you can ask of the system's owner:

| The system | What runs on it | Use |
|---|---|---|
| Cannot or should not join the mesh, or its owner should change nothing | Nothing of yours | An egress destination on this pod |
| May join the mesh and run your software, but speaks no Zenoh | A socket adapter under the system's own identity | The adapter on the system's host. No pod |
| May join the mesh, and its owner will build Zenoh support | Its own Zenoh client, under its own identity | Nothing extra. It subscribes directly |

Only the pod is one way by construction: the system holds no identity, and a destination
may read only its own keys and may publish nothing. In the other two, the system's identity
can write its own topics, so only its configuration or its code keeps its output off the
fabric.

### Declare a destination

| The system | Declare the destination as | What happens |
|---|---|---|
| Listens for datagrams (an ASTERIX UDP input) | `--dest udp://<its-ip>:<port>` | The pod sends one datagram per message |
| Listens for a TCP connection | `--dest tcp://<its-ip>:<port>` | The pod dials it and streams |
| Dials the sensor (a radar-one input, a C2 client) | `--dest tcp-server://<pod-lan-ip>:<port> --from <its-ip>` | It dials the pod, as it would dial the radar |
| Runs a SAPIENT controller (a DMM, a fusion node) | `--dest sapient://<its-ip>:<port> --node-type <type>` | The pod registers with it as one sensor node and sends what is released |
| Accepts data through its own Zenoh router | `--dest zenoh://<its-ip>:<port> --prefix <prefix> --partner-bundle <dir>` | The pod authenticates with partner-issued credentials; requires 0.5.0 or later |

A `tcp-server://` destination answers only the `--from` addresses, serves one consumer by
default (`--max-clients`), and can hand a newly connected consumer the newest status
messages first (`--replay`). A consumer that reconnects replaces its own stale connection.
Anything a consumer sends is read and discarded: nothing reaches the mesh through it.

A `sapient://` destination runs the SAPIENT session arm (BSI Flex 335 v2.0) in the sensor
role. It dials the controller, registers under its own node id with the `--node-type` you
give (default `OTHER`), sends a `StatusReport` every `--status-interval` seconds (default
5), and relays each released `DetectionReport` or `Alert` under that node id. Its keys must
already carry SAPIENT: one `SapientMessage` per sample. The producer supplies those encoded
messages. The arm parses and re-encodes the envelope, changes the node id and send
timestamp, and adds TCP framing. It does not map detection or alert domain fields.
It rejects every `Task` the controller sends, with the `TaskAck` the standard requires, and nothing the controller sends reaches the
pen. Its node id is kept under its audit directory, so a restart registers as the same
node.

Because the arm registers and heartbeats whether or not data flows, a `sapient://`
destination runs only once it is released: until then `apply` keeps its arm stopped and
nothing starts it at boot, so the controller never sees the pod before its data is released.
Revoking it stops the arm and ends the session.

Keys are the publisher's own fabric keys, the ones you would subscribe to with `goat sub`.
Each must start with the publisher's anchor (or `release/<lane>/` on a goatnet that
releases data into lanes), never a wildcard, and never `ops`, `attest`, `system` or the
pod's own `downlink` namespace.

Two gates, as on the way up: the destination's own `release.from_fabric` in
`40-egress.yml`, and its keys in `downlink_allow` in `10-pod.yml`. `egress release` opens
both for one destination and `egress revoke` closes both, so no single edit starts an
export.

Declare a radar for a system's ASTERIX input, shut until released:

```bash
sudo octo-pod egress add c2-radar1 --dest udp://10.1.2.20:8610 \
  --key '<sensor-anchor>/sensors/radar1/asterix/data'
```

Declare a radar-one feed that a system dials, as it would dial the radar:

```bash
sudo octo-pod egress add c2-radar1 --dest tcp-server://10.1.2.10:9000 --from 10.1.2.20 \
  --replay 2 --key '<sensor-anchor>/sensors/radar1/radar-one/**'
```

Declare a radar's SAPIENT detections for a controller, registered as a radar node:

```bash
sudo octo-pod egress add c2-sapient1 --dest sapient://10.1.2.30:48000 --node-type RADAR \
  --key '<sensor-anchor>/sensors/radar1/sapient/**'
```

Open both downward gates for the first destination. The first release on a pod also takes
the bundle GOAT issued for the pod, because the downlink bridge reads the fabric as the
pod's slot, and the fingerprint that proves the bundle is genuine (see
[Publishing upstream](#publishing-upstream)); add `--router-ip <router-mesh-ip>` if the pod
has no pin for the router's name yet:

```bash
sudo octo-pod egress release c2-radar1 --slot ~/slot --ca-fingerprint SHA256:<fingerprint>
```

Open both gates for the next destination:

```bash
sudo octo-pod egress release c2-radar1
```

Converge. The first time, run the `apply` that `release --slot` printed, which carries the
slot's certificate paths; after that, plain `apply` is enough:

```bash
sudo octo-pod apply
```

Check that the two gates agree for every destination:

```bash
sudo octo-pod egress check
```

The downlink bridge holds the pod's slot and subscribes on the fabric only to the keys in
`downlink_allow`, so a pod without a slot reads nothing. On the pen, fabric key `K` comes
down as `downlink/K`; each egress arm may read only its own keys and may write nothing,
so nothing on the destination's side can publish into the fabric through it. With the pod
firewall on, a `udp://`, `tcp://` or `sapient://` destination opens outbound to that one
address, and a `tcp-server://` one opens inbound from its `--from` addresses only.

| What | Where |
|---|---|
| Every send, connect and refusal | `/var/lib/octo-egress/<id>/<id>-egress.ndjson`, with a SHA-256 per send; a `sapient://` destination also records its session events |
| The arm | `octo-egress-<id>.service`; `journalctl -u octo-egress-<id>` |
| The bridge | `octo-downlink-bridge.service`; `journalctl -u octo-downlink-bridge` |

Addresses must be literal, because a name could resolve somewhere else tomorrow, and the
arm refuses to start if its destination is missing from its own allow list. To stop one
destination, `egress revoke` it and `apply`: the arm keeps running and receives nothing,
except a `sapient://` arm, which stops.
To retire it, `egress remove` it and `apply`: its unit and private key are deleted, and its
certificate and audit records are kept.

### Before it carries real feeds

- **Confine what the pod can read.** The fabric router authorizes the pod's slot.
  Local release commands do not grant upstream read access. Have the fabric operator
  grant only the approved keys or receiver's release lane, and declare the same keys
  locally. Verify allowed and denied subscriptions at the fabric boundary. Data leaving
  for a system outside your organization belongs on an approved release lane.
- **One pod per receiver.** Every destination shares the pod's one slot, and a shared
  credential is an unattributed feed. The pod has no fabric identity per
  destination yet.
- **Settle the interconnection.** A receiver outside your authorization boundary makes the
  pod a boundary point. Record it, with the agreements your accreditation requires, before
  real data crosses.
- **Plan for the pod failing.** The pod is the receiver's only path, and there is no
  failover between pods yet. A second pod needs a floating address for `tcp-server://`
  destinations, and push destinations enabled on the active pod only, or the receiver gets
  every datagram twice.
- **Check the format.** A `udp://`, `tcp://` or `tcp-server://` destination relays bytes
  unchanged, so the receiver's input must accept the format its keys carry. A `sapient://`
  destination changes only the node id, the timestamp and the framing, so its keys must
  carry SAPIENT messages.
- **Know what one way means here.** Nothing a receiver sends reaches the fabric. On the
  wire, a `tcp-server://` or `tcp://` destination is still a TCP connection, so a
  receiver's bytes do reach the pod before it discards them. A `sapient://` destination
  goes further: SAPIENT is a session, so the arm reads and answers the controller's
  registration acknowledgements and tasks. Where a boundary must be physically one way,
  use `udp://` through a data diode.

### Known limits

- A reconnecting receiver replaces its own connection, and the newest connection wins. A
  receiver that queues several attempts can churn for a minute before one connection holds.
- The pod's Zenoh sessions leave multicast scouting at Zenoh's default instead of turning it
  off, and sessions to the pen don't check the pen's certificate name. The downlink
  bridge's fabric session does check the router's name.
- Re-adding a removed destination under the same id is untested.

[`docs/design-egress-gateway.md`](https://github.com/usareur-af/octo-pod/blob/main/docs/design-egress-gateway.md) records why egress works
this way and what was rejected.

## The pod firewall

Off until you turn it on. Once on, inbound, outbound and routed traffic all start at drop,
and the openings come from the same declarations as everything else:

| Opening | Comes from |
|---|---|
| SSH | the mesh, plus the LAN sources in `set-lan --ssh-from` |
| The mesh itself | fixed: WireGuard, NetBird's control plane, DNS, NTP |
| Each arm's port | `add-arm`, from its `--from` addresses only |
| The pen's port, per peer | `add-peer`: over the mesh; with `--from`, also on the LAN from those addresses only |
| Each released destination | `egress add` and `egress release`: its one address, port and protocol |
| The fabric router | the pinned router address, over the mesh only |

Nothing is routed between the LAN and the mesh. If you manage the pod over its LAN, declare
that path before turning the firewall on, or SSH closes to everything but the mesh:

```bash
sudo octo-pod set-lan --iface eth1 --addr 10.1.2.10/24 --ssh-from 10.1.2.5/32
```

Turn it on:

```bash
sudo octo-pod set-firewall on
```

Apply it:

```bash
sudo octo-pod apply
```

`apply` loads the new ruleset and keeps it only if the mesh's management service and the
fabric router still accept new connections. If either does not, the previous ruleset comes
back and `apply` fails. The verified ruleset loads at boot before the network comes up.
`set-firewall off` and `apply` remove the table and its boot unit.

## What runs without you

Three timers, whether or not anyone is logged in. All three repair runtime faults only.
None applies a configuration change.

| | |
|---|---|
| `octo-watchdog.timer` | every minute. Restarts an audit sink that is up but subscribing nothing, or a router listening on nothing. Reports declaration drift, never acts on it. Writes `/var/lib/octo-pen/watchdog.status` |
| `pen-cert-renew.timer` | daily. Renews any certificate the pod's own CA signed inside its 60-day window, then restarts what presents it. Refuses if the clock is implausible |
| `pen-cert-expiry.timer` | daily. Fails loudly in `systemctl --failed` when something is expiring and renewal did not fix it |

The pod also publishes its own health onto the fabric every 60 seconds, jittered by its
identity. Turn it off with `health_pub_enabled: false` in the pod's `05-fabric.yml`.

## Publishing upstream

**This takes two people and two tools.** A pod cannot turn it on by itself.

**The operator**, on a workstation with portal access:

```bash
goat machine enroll <pod-name> --node-class POD --autonomy remote \
  --custodian <custodian-handle> --json > <pod-name>.json
```

It prints the **slot anchor** and a **bundle URL**. The URL is one-time and expires in about
24 hours. Fetch it, unpack it, and send the whole directory by the same out-of-band channel
as a setup key: the certificate, the private key, the CA roots, NOTES.md, and the signed
profile (`<anchor>-goat-cli.cbor` with `<anchor>-goat-cli-ca.pem`, the key that signs it).
Then, through a channel the bundle did not travel on, give the fingerprint of that signing
key. `goat ca ls` on your workstation prints it; it is the value `goat ca add` prints.

> Current GOAT main accepts standalone `POD` under ADR 1092.
> This requires a GOAT release containing that change. Confirm the deployed mint
> supports `POD` before enrolling; an older mint may reject it. `POD` is a trust
> boundary, not a `PLATFORM --workloads` function. An operator enrolling for someone
> else passes `--custodian`. See the [GOAT enrollment guide](https://github.com/usareur-af/goatcore/blob/main/docs/operations/gear-enrollment-sop.md#pod-and-moon-appliances).

**You**, on the pod:

```bash
mkdir -p ~/slot && chmod 700 ~/slot      # copy the whole bundle here
chmod 600 ~/slot/*-key.pem
sudo octo-pod release radar1 --slot ~/slot --ca-fingerprint SHA256:<fingerprint>
```

`release` checks the bundle before it opens anything. The key that signs it must have the
fingerprint you were given, the signature must hold, and the certificate, private key and
CA roots must be the files it signed. It then pins the fingerprint and the CA roots' SHA-256
in `10-pod.yml`: a later release with the same bundle needs no fingerprint, and `apply`
refuses any other CA roots. At a terminal you can leave out `--ca-fingerprint` and confirm
the fingerprint when `release` shows it. An older bundle with no signed profile can only be
confirmed at a terminal, by the SHA-256 of its CA roots.

That opens both gates for that one publisher and prints the exact `apply` to run, with the
anchor filled in. `release` names one publisher at a time.

Verify it is sending, from the pod:

```bash
sudo octo-pod status                     # "N of M publisher(s) released"
sudo journalctl -u octo-uplink-bridge -n 5 --no-pager | grep stats
```

Verify it arrived, from a workstation on the mesh:

```bash
goat sub '<slot-anchor>/**' --follow
```

A slot with no upstream grant is worse than no slot: the pod reports success while
everything it sends is discarded at the far end.

## Correct an existing arm

Use `edit arms` to correct the endpoint of the **same onboarded sensor**. It changes
the declaration, preserving the arm ID, certificate identity, namespace, keys and
release settings. `--from` is a trust boundary: it changes which source addresses
are accepted and the source provenance. A replacement sensor should be onboarded
as a new identity, not silently inherit an existing arm's grants.

```bash
sudo otp edit arms radar1 --from 192.0.2.20 --udp 8601 --dry-run
sudo otp edit arms radar1 --from 192.0.2.20 --udp 8601
sudo otp check
sudo otp apply
sudo otp describe arms radar1
```

For an already released arm, changing `--from` or `--dial` requires
`--acknowledge-trust-change`. Preview first and use that flag only after confirming
this is the same sensor. It does not grant permission to onboard a replacement under
an old identity. `--dry-run` never writes.

`edit` validates and writes only the declaration; it prints the before/after fields.
`check` previews the changes. `apply` restarts a changed arm to pick up its endpoint
and converges the firewall, so expect a brief interruption for that feed. Unchanged
arms are kept running. The edit command does not mint or revoke credentials.

Use `--tcp PORT` for an existing TCP listener, `--dial HOST:PORT` for an existing
TCP dial-out arm, and `--bind IP` for the local listening address. A changed numeric
dial target supplies its new source allowlist unless `--from` is explicit; a hostname
target requires `--from`. IPv4 addresses are required to match the current firewall
contract. This command rejects changes of transport kind, identity, namespace, feed
label or release gates. It also refuses invalid addresses and declared listener-port
collisions. It does not discover ports used by unrelated host services; review
`check` and the host's listeners before applying.

## Tab completion

Installed with the package. It completes the things that must match something else exactly:

```
octo-pod set-lan --iface <TAB>       enP2p1s0  mgbe0_0  wlP1p1s0  can0 …
octo-pod add-arm r1 --framing <TAB>  stream  newline  len32be  len32le
octo-pod remove-arm <TAB>            the ids this pod actually declares
```

The package installs the Bash completion loader dependency and a Zsh completion entry.
Completion is available in a new Bash login shell, or a Zsh shell with its standard
completion system enabled. Existing shells must be reopened after installation.
Custom shell configurations that disable completion also disable these suggestions.

```bash
octo-pod get <TAB>                   resource types
octo-pod describe bridges <TAB>     uplink, downlink
octo-pod logs egress <TAB>          declared destinations
octo-pod get flows -o <TAB>         table, wide, json
```

`describe flows sapient-receiver` and `logs egress sapient-receiver` accept a type
followed by a name. The short forms, such as `describe sapient-receiver`, still work.
A supplied type restricts lookup to that type. After a name is complete, Tab offers
only the remaining flags. `status` shows the whole pod; use `get TYPE` for a list.

Addresses, ports and setup keys are deliberately not completed.

## Troubleshooting

**This system fails quietly.** A rejected publish looks identical to a successful one at
the sender. `sudo octo-pod verify` is the only thing that tells working from silent. Run it
after every change.

One exception: a certificate whose CN is in no ACL subject makes the router log this once at
session open, naming the peer by Zenoh id rather than CN:

```
… did not match any configured ACL subject. Default permission `Deny` will be applied
```

| Symptom | Usually |
|---|---|
| Sensor data never appears | it is sending from an address other than `--from`. Check with `sudo tcpdump -ni any port 48021` |
| …still nothing | wrong destination, or the arm is bound to the wrong leg. Compare `ss -ulnp \| grep 48021` against the address the sensor uses |
| Peer connects, nothing arrives | their key prefix. They must publish to `raw/<their-id>/…` with no org or anchor prefix |
| Arm says `forwarded=N`, sink sees nothing | the router is denying the writes. Check the CN in the ACL matches the cert the arm mounts |
| Dial-out feed never connects | a listening arm pointed at a sensor that also listens. It needs `--dial` |
| `verify` fails on `drift` | a declared publisher is not sending. For an occasional feed set `expect: { continuous: false }` |
| Sink looks healthy, no data | a dead audit sink keeps serving metrics. Check `goat_audit_subscriber_session_state` |
| Everything stops after a router restart | sinks do not reconnect on their own. Run `sudo octo-pod apply` |
| `octo-pod: command not found` | package installation did not finish |
| `check` says "not a pod yet" | `join` or `init` has not run |
| Router stops accepting arms | its certificate may have expired. `systemctl status pen-cert-renew`, or force it with `sudo /usr/local/sbin/pen-cert-renew`. It refuses on an implausible clock, so check `date` first |
| A publisher you removed is still running | `apply` refuses and names it. `remove-arm <id>` is the clean removal |
| `join` hangs at enrolment | a spent setup key. It is one-off; mint a fresh one |
| A traceback instead of an error | you are missing `sudo` |

```bash
ss -ulnp | grep 48021                        # is the arm listening, and on what
netbird status | grep Management             # are we on the mesh
sudo podman logs -f octo-pen-zenoh-router    # router; logs no accepted sessions at info
```
