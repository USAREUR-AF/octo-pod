<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# ADR 0004: Host the translation gateway beside the arms, as a separate unit

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-11-16 |
| Repo scope | octo-pod (`gateway/translate/`, `platform/`); interface to goat-transformers |
| Supersedes | none |
| Superseded by | none |
| Related | [ADR 0001](0001-edge-appliance-and-translation-boundary.md) (Proposed), which this record applies and does not change; [ADR 0002](0002-peers-reach-the-lan-and-read-released-keys.md) (Proposed), whose deferred "local reader" this record decides for one component |

## TL;DR (for a layperson)

A pod captures what a sensor sends and does not interpret it. Some receivers need the
same data in another format. ADR 0001 says a separate translation gateway does that
conversion, and that no arm does. This record proposes where one such gateway lives: in
this repository, in its own directory, built and versioned apart from the arms, off
unless an operator declares it. It reads captured samples from the pod's local router,
publishes a converted copy under its own identity, and never changes the captured
sample. If the maintainers prefer the gateway in a repository of its own, the directory
moves as it is.

## Context

ADR 0001 decides: "Keep semantic translation in the translation gateway. The gateway
owns sensor decoding, semantic validation, schema and field mappings, coordinate or unit
conversion, and output encoding. Octo-pod does not add a second mapping engine." It
rejects "Put translation into the pod's arms" because that "duplicates the gateway's
mappings and releases, expands payload parsing at the attachment boundary, and couples
semantic changes to session maintenance". Its first revisit trigger is "A proposed arm
begins mapping sensor fields, units, coordinates or message formats."

Three facts are not decided by ADR 0001:

- **No gateway is named.** ADR 0001 describes the gateway's duties and not an
  implementation. [goat-transformers](https://github.com/usareur-af/goat-transformers) is
  a public library that converts between formats from data files ("packs"), with a Rust
  engine that uses only the standard library. Its design document names octo-pod as a
  consumer through "a separate translator process that subscribes to captured keys and
  republishes converted ones".
- **A gateway outside the pod can read captured data only after release.** A peer
  ([ADR 0002](0002-peers-reach-the-lan-and-read-released-keys.md)) reads `downlink/K`,
  which exists only after `K` was released to the fabric and released back down. A pod
  with no upstream connection, which is a supported state, cannot be served that way.
- **Nothing on the pen may read another publisher's namespace** except the pod's own
  first-party identities. ADR 0002 rejected a direct read grant for peers and left "a
  local reader" to "its own decision if a real case needs it".

Implemented with this record: the gateway crate, its tests, its pinned pack bundle, the
declaration, and the generated certificate, grant, audit sink and unit. Not implemented:
a published image and its pin, and the gateway in `status`, `top`, `get`, `describe`,
`logs`, `verify`, the firewall rendering and the release bundle. `certs` lists its
certificate. No deployment has run it.

## Decision

1. **The gateway is a separate unit in this repository, not an arm.** It lives in
   `gateway/translate/` as its own Cargo workspace with its own lock file, dependency
   policy, image and version. It is not a member of the `arms` workspace, and no file
   under `arms/` changes. ADR 0001 decision 2 stands as written: no arm maps a field.
   The gateway shares one thing with the arms, by path: the pen session and counter code
   in `arms/common`.

2. **octo-pod holds no mappings.** The mapping engine is goat-transformers, a dependency
   pinned by commit. The mappings are that library's packs, copied unmodified at the same
   commit into `gateway/translate/packs/` with a manifest of the commit and each file's
   SHA-256. The gateway refuses to start when a file differs from the manifest. A mapping
   change is a change in goat-transformers, followed here by a pin change; it is never an
   edit in this repository. By default the engine loads only packs marked
   `distribution public`.

   A declaration may hold one rule that names model keys: `only_when` (convert a sample
   only when a decoded key has a given value), an operator's configuration of one
   gateway, applied by key name on the decoded model. A declaration holds no field
   mapping (ADR 0001): an earlier draft had `carry`, which took a result value from
   another key of the same sample, and it was removed in review. A value one format
   holds under a key another does not read is a pack change in goat-transformers.

   The crate names a key of a format in one place: a position guard, on by default,
   that skips a sample whose position the pinned packs would misread. The pinned
   SAPIENT pack reads `Location.x` and `Location.y` as longitude and latitude for every
   coordinate system, so a detection in radians, UTM metres or no stated system is
   skipped (`coordinate-system-not-expressed`) rather than published with those
   numbers as degrees. It is not left to a declared rule, because a wrong position on a
   track feed is worse than none, and it goes when goat-transformers maps that position
   by coordinate system.

3. **The gateway is a pen client with one certificate and two grants.** It is declared
   in `50-translate.yml`, and one declaration generates its certificate, its grants, its
   audit sink and its unit, as for an arm. The pen grants its certificate a read on
   exactly the declared source key expressions, and a write on `raw/<id>/**`. A source
   must begin with a literal chunk, must not intersect the gateway's own namespace, and
   must not name `downlink/`: data that came down is not converted and sent back up.
   This is the local reader ADR 0002 deferred, decided for this component only. A peer
   still has no read on a raw namespace.

4. **The captured sample is the record.** The gateway never changes, deletes or
   republishes it. A converted sample is a second publication, on
   `raw/<id>/<format>/<source key>`, with the target format's content type and an
   attachment that names the source key, the source timestamp, the SHA-256 of the
   captured bytes, both formats and the pack commit. A local audit file records the same
   for every sample, converted or not.

5. **A sample that does not convert is not published.** It is counted, recorded with the
   engine's error and dropped. The gateway never publishes the source bytes or a partly
   converted message in place of a result.

6. **A converted sample is released like any other.** The gateway's namespace is a
   publisher namespace: it has an audit sink, and it leaves the pod only when
   `bridge_allow` names it. No new path out of the pod exists.

7. **Off by default.** A pod with no `50-translate.yml`, or an empty one, renders no
   certificate, no grant and no unit, and its generated ACL is unchanged.

## Alternatives considered

| Option | Reason not chosen |
|---|---|
| A `translate` member of the `arms` workspace | It is the arm that ADR 0001's first revisit trigger describes. Translation would share the arms' lock file, image workflow and release, which is the coupling ADR 0001 rejects |
| Link the library into the ingest or session arms | ADR 0001 rejects it by name: it "expands payload parsing at the attachment boundary" and couples a mapping change to session maintenance. Capture would also depend on a pack existing for the format |
| A repository of its own, now | It is the strictest reading of "two independently released components". It needs `arms/common` reachable from outside this repository, and a second place for the unit template and the grant, which must be generated from the same declaration as the certificate. The directory is laid out so that this move stays open |
| An external gateway attached as a peer | Works today with no change, but reads only keys that went up to the fabric and came back down. It cannot serve a pod with no upstream connection, and it converts under the pod's shared slot instead of a local identity |
| Convert at the receiver | Correct when the receiver can. This is for receivers that accept one format and cannot be changed |

## Consequences

**Positive.** A site can feed a receiver that needs another format without an upstream
connection and without changing an arm. A mapping change, a capture change and a session
change stay in three places. Every converted sample names the captured sample it came
from, and the captured sample is still on the pen for a consumer that wants the bytes.

**Negative.** The pod image set and the platform layer grow by one optional component.
The gateway parses payloads, which the byte arms deliberately do not: that code now runs
on the pod, in its own container. The dependency is a git commit, not a registry
release, because goat-transformers is not published to crates.io; `gateway/translate/deny.toml`
therefore names one git remote, which `arms/deny.toml` does not. A conversion is only as
complete as the packs: values the target format cannot hold are dropped, and the
library's capability report, not this repository, states which. The gateway does not
validate a message beyond what a pack states.

## Security assessment view

The assessable boundary is the gateway's certificate, its two pen grants (generated; the
ACL golden files show them), its namespace's audit sink, the existing upstream release
gate, and its local audit file. The gateway reads attacker-influenced bytes that an arm
captured: sample size and queue length are bounded, the engine uses no `unsafe` code and
no third-party crate, and the gateway holds no credential except its own leaf. A
compromised gateway can read the declared source keys and write its own namespace; it
cannot write a capturing arm's namespace, read any other key, or reach the fabric.
Running with `max_distribution: controlled` and an overlay pack bundle puts mappings
written from a non-public specification on the pod; that is a decision for the owner of
that specification and is not made here. No waiver, classification decision or
accreditation is granted by this record. Field acceptance, fuzzing of the conversion
path, and a signed image are open work.

## Trigger conditions for revisit

- An arm, or code under `arms/`, begins to depend on a pack or on goat-transformers.
- The gateway needs a session with a receiver, a return path, or a write outside its
  own namespace.
- A second gateway implementation is proposed, or mappings are proposed for this
  repository.
- goat-transformers publishes releases to a registry, or changes its pack language in a
  way the pinned commit cannot follow.
- The maintainers decide that ADR 0001's "independently released" requires a separate
  repository.
