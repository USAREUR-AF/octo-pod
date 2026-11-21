<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# octo-translate: the translation gateway

**Status: work in progress, off by default.** [ADR 0004](../../docs/adr/0004-host-the-translation-gateway-beside-the-arms.md)
is Proposed. No image is published and no deployment has run this component. The gate
that lifts this status is acceptance of ADR 0004 and a run on a pod.

`octo-translate` reads captured samples from a pod's pen, converts each one to other
formats, and publishes the results under its own identity. It is not an arm: an arm
captures bytes and does not interpret them
([ADR 0001](../../docs/adr/0001-edge-appliance-and-translation-boundary.md)). The captured
sample is never changed; it stays the record.

The conversion is done by [goat-transformers](https://github.com/usareur-af/goat-transformers).
This crate holds no field name of any format. The field mappings are that library's
packs, copied into [`packs/`](packs/) at the commit the crate pins.

To declare one on a pod, see [the guide](../../docs/guide-translation-gateway.md).

## What it does with one sample

1. A subscriber receives a sample on a declared source key, for example
   `raw/radar1/sapient/192-0-2-50`. It checks the key and the size and puts the sample
   in a bounded queue. It converts nothing on the receive path.
2. One task takes each sample from the queue. A sample that waited longer than
   `max_age` is dropped and counted.
3. For each target format, the engine decodes the sample to the shared model. The
   gateway applies its position guard and the declared `only_when` rules, the engine adds
   the declared context for values the sample does not carry, and encodes the target
   format.
4. The result is published on `raw/<id>/<format>/<source key>` with the target format's
   content type and an attachment that names the source.
5. One line is written to the local audit file.

## Keys, content types and provenance

| Target wire type | Content type | Example pack |
|---|---|---|
| XML | `application/xml` | `cot-xml` |
| GeoJSON | `application/geo+json` | `geojson` |
| JSON | `application/json` | |
| protobuf | `application/protobuf;<pack name>` | `sapient` |
| bit fields | `application/octet-stream;<pack name>` | |

The default publish key, `{namespace}/{to}/{source}`, keeps the whole source key, so the
arm id, the feed and the source address chunk stay readable in every result's key. A
bridge that carries the result upstream carries the key and does not carry the
attachment.

The attachment is one JSON object:

```json
{"source_key": "raw/radar1/sapient/192-0-2-50", "source_ts": "2026-11-16T10:00:00.000Z",
 "source_hash": "sha256:…", "from": "sapient", "to": "cot-xml", "gateway": "xl1",
 "packs_commit": "9e951e9df8a212077cf7dcbc532aeadcdcbf88e5"}
```

`source_ts` is the Zenoh timestamp of the captured sample, or `null` when the pen did not
stamp one. `source_hash` is the SHA-256 of the captured bytes, in the form the pen's
audit sinks record, so a result can be joined to the audit event of the sample it came
from.

## When a sample does not convert

Nothing is published for it. It is counted, written to the audit file with the engine's
error, and logged the first time each error occurs and every hundredth time after.

| Case | Counter | Audit `kind` | `error` |
|---|---|---|---|
| The engine refuses the bytes or cannot write the target | `dropped_invalid` | `failed` | the engine's error name (`no-kind`, `malformed-wire`, …), or `unnamed` when the engine's message has no name |
| The bytes are not UTF-8 and the source format is text | `dropped_invalid` | `failed` | `not-text` |
| The engine panics on the sample | `dropped_invalid` | `failed` | `engine-panic` |
| The sample parses, and the target has no field for anything it carries (a SAPIENT status report on its way to CoT) | `skipped` | `skipped` | `no-shared-value` |
| The sample parses, and its position is in a coordinate system the packs do not express (below) | `skipped` | `skipped` | `coordinate-system-not-expressed` |
| The sample parses, and a declared `only_when` key is absent or has another value | `skipped` | `skipped` | `condition-not-met` |
| The sample is larger than `max_bytes` | `dropped_oversize` | none | |
| The queue is full | `dropped_overflow` | none | |
| The sample waited longer than `max_age` | `dropped_stale` | none | |
| The key is outside the declared sources | `dropped_key` | none | |

The gateway logs the arms' `stats` line with the shared counters (`received`,
`forwarded`, `forward_errors`, `dropped_invalid`, `skipped`, `dropped_overflow`,
`dropped_stale`) and a `translate stats` line with its own counters and the count of
each error name. The audit file gets the same summary.

The gateway does not start (exit code 2, one `refused` audit line) when: a format is not
in the pack bundle; a target is the source format; the two formats share no model key;
a context key is not declared by a loaded model module; an `only_when` key is not a key
the source pack decodes; a source key reaches the
gateway's own namespace or a reserved root; or the pack bundle does not match its
manifest.

## Configuration

Each option is a flag and an environment variable. The unit template
`platform/templates/translate/octo-translate.container.j2` sets the variables from the
declaration.

| Variable | Default | Meaning |
|---|---|---|
| `TRANSLATE_ID` | required | Names the audit file and appears in every record |
| `TRANSLATE_SOURCES` | required | Pen key expressions to read, comma-separated |
| `TRANSLATE_FROM` | required | The pack name of the source format |
| `TRANSLATE_TO` | required | The pack names of the target formats, comma-separated |
| `TRANSLATE_NAMESPACE` | required | The literal key every result is published beneath |
| `TRANSLATE_PUBLISH_KEY` | `{namespace}/{to}/{source}` | Placeholders: `{namespace}` `{from}` `{to}` `{source}` `{tail}` |
| `TRANSLATE_CONTEXT` | none | Model keys the source does not carry, `key=value;key=value` |
| `TRANSLATE_ONLY_WHEN` | none | Convert a sample only when each decoded model key has this value, `key=value;key=value`; skip any other |
| `TRANSLATE_STALE_AFTER_S` | `0` (none) | Adds a stale time this many seconds after receipt, when the source has none |
| `TRANSLATE_STAMP_TIME` | `false` | Adds the receive time, when the source has no time |
| `TRANSLATE_PACKS_DIR` | `/usr/share/octo-translate/packs` | The pack bundle |
| `TRANSLATE_MAX_DISTRIBUTION` | `public` | `public` or `controlled`: the most restricted pack the engine may load |
| `TRANSLATE_ENDPOINT`, `_CERT`, `_KEY`, `_ROOT_CA` | `tls/127.0.0.1:7447` | The pen, and this gateway's pod-CA identity |
| `TRANSLATE_AUDIT_DIR` | `/var/lib/octo-translate` | Where `<id>-translate.ndjson` is kept |
| `TRANSLATE_QUEUE` | `512` | Queue length, in samples |
| `TRANSLATE_MAX_BYTES` | `1048576` | Largest sample accepted |
| `TRANSLATE_MAX_AGE_MS` | `1500` | Longest wait in the queue |
| `TRANSLATE_STATS_SECS` | `60` | Seconds between stats lines |

A context value never replaces a value the source message carries. `only_when` names
model keys; the keys of a format are in its model module under `packs/model/` and its
pack under `packs/packs/`. A declaration maps no field of any format (ADR 0001): a value
one format holds under a key another format does not read is a change to the packs, in
goat-transformers.

## The pack bundle

`packs/` holds the model modules and packs for `cot-xml`, `geojson` and `sapient`
(BSI Flex 335 v2.0), copied unmodified from goat-transformers, and `MANIFEST.json`, which
records the commit and the SHA-256 of each file. `Cargo.toml` pins the engine to the same
commit. The gateway checks every file against the manifest when it starts.

To move to a new goat-transformers commit, change `rev` in `Cargo.toml`, then run from
the repository root:

```zsh
python3 gateway/translate/tools/refresh-packs.py <goat-transformers checkout>
```

The script refuses a checkout that is not at the pinned commit, has uncommitted changes
under `model/` or `packs/`, or holds a pack that is not `distribution public`. To add a
format, add its pack name to `FORMATS` in the script and run it again. Then update the
lock file with `cargo update -p goat-transformers`, and update `NOTICE` if the new pack
names a new source.

A pod can use another bundle, for example one assembled from a private overlay, with
`packs_dir` in the declaration. That bundle needs its own `MANIFEST.json`. A pack marked
`distribution controlled` loads only when the declaration also sets
`max_distribution: controlled`.

## What the pinned packs carry

The engine reports, for each pair of formats, which values carry and which the target
cannot hold: run `goat-xf matrix --detail` in a goat-transformers checkout at the pinned
commit. These are the points that matter for the three shipped formats.

**SAPIENT to CoT**

| Value | Carried by | Without it |
|---|---|---|
| Position and its vertical error | the packs | |
| `uid` | nothing yet: the SAPIENT pack leaves `object_id` raw | the event has no `uid`, and a CoT consumer that requires one refuses it |
| `time`, `start` | `stamp_time: true` (the time the pod received the sample) | the event has neither |
| `stale` | `stale_after_s` | the event has none |
| `how` | `context: {how: ...}` | the event has none |
| `state` (free text) | the packs: `detail/remarks` | |

The SAPIENT pack reads `Location.x` as longitude and `Location.y` as latitude for every
coordinate system. The gateway therefore skips, by default and with no rule declared, a
detection whose location is in radians, in UTM metres or in no stated coordinate system,
and counts it as `coordinate-system-not-expressed`: a wrong position on a track feed is
worse than none. Only latitude and longitude in degrees (`Location.coordinate_system`
1) is converted. This position guard (`POSITION_GUARDS` in `src/convert.rs`) is the one
place the crate names a key of a format, and it stays until goat-transformers maps the
SAPIENT location by coordinate system. Every BSI Flex 335 reference detection with an
x-y-z location declares UTM; `tests/reference_vectors.rs` holds that they are skipped,
and that the same detection in radians or with no coordinate system is skipped too.

Every message that is not a detection has no value the targets hold and is skipped
(`no-shared-value`). A detection located by range and bearing only is skipped for
GeoJSON. For CoT it is converted when it carries a `state`, which CoT writes as
`detail/remarks`: the event then has no position. A declaration that needs a position
on every event declares `only_when: {sapient.location_coordinate_system: 1}`, which such
a detection does not meet: the position guard leaves it through, because it carries no
position to misread.

**CoT to SAPIENT**

The result is a `DetectionReport` with the position. `context: {sapient.node_id: ...}`
gives it a node id. It has no object id: the SAPIENT schema requires a ULID there, and a
CoT `uid` is not one. The message timestamp is the event time, split
into seconds and nanoseconds by the pack. It has no `report_id`, so a SAPIENT controller
that validates detections refuses it.

Each remaining point is a limit of the packs or the model at the pinned commit, and is
changed in goat-transformers.

## Build and test

Run these from `gateway/translate/`:

```zsh
cargo fmt --all --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
cargo deny check licenses bans sources
```

`tests/pen.rs` runs the whole gateway against a Zenoh router in the test process: it
publishes an unmodified BSI Flex 335 reference `DetectionReport` (from
`arms/sapient-receiver/tests/vectors`) and a CoT event into the pen as an arm would, and
checks the published results, their provenance, the audit file and the failure path.
`tests/reference_vectors.rs` runs every accepted BSI Flex 335 reference message through
the shipped packs with no rule declared (the two detections with an x-y-z location
convert, every other message is skipped) and with the latitude and longitude rule.

From `platform/`, `python3 tests/test_translate.py` checks the generated grants and unit,
the declaration gate, the CLI, and that this crate stays separate from the arms.

Build the image from the repository root:

```zsh
docker build -f gateway/translate/Dockerfile .
```
