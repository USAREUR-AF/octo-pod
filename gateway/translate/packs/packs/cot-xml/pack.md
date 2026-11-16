# Pack: cot-xml

Cursor on Target event XML, the `<event>` document with a `<point>` and
`<detail>`. Public. Fields map to the shared model (`spec/shared-model.md`).

| Wire | Model key | Note |
|---|---|---|
| `@version` | none | constant `2.0`, written on every encode |
| `@uid`, `@type`, `@how` | `uid`, `type`, `how` | |
| `@time`, `@start` | `time_ms` | ISO 8601; both are written from the one key. Decode reads `time`, and `start` only when `time` is absent |
| `@stale` | `stale_ms` | supplied by the caller: `convert` context, CLI `--set stale_ms=<epoch ms>` |
| `point/@lat`, `@lon`, `@hae`, `@ce`, `@le` | `lat`, `lon`, `hae`, `ce`, `le` | |
| `detail/contact/@callsign` | `callsign` | |
| `detail/track/@course`, `@speed` | `course`, `speed_ms` | degrees true, metres per second |

Written when the entity has no value: `type` `a-u-X`, and `hae`, `ce`, `le`
`9999999`. `how` and `stale` are written only when the entity has them.

A `start` that differs from `time` does not survive a round trip: both leave
with the value of `time`. Unmapped attributes of `<event>` round-trip through
residue; unmapped child elements are not carried.
