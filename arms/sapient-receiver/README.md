<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# SAPIENT registration test receiver

A portable TCP test receiver using the same protobuf definitions as the pod's
SAPIENT arm. Frames use a little-endian 32-bit length prefix.

From `arms/`, start a local receiver:

```sh
cargo run -p sapient-receiver -- --bind 127.0.0.1 --port 5020
```

To reject registrations that fail the implemented checks:

```sh
cargo run -p sapient-receiver -- --bind 127.0.0.1 --port 5020 --strict
```

Lenient mode logs violations and accepts registrations. Strict mode returns a
negative RegistrationAck containing the reasons, then closes the connection.
After acceptance, the receiver sends addressed StatusReports every second.
Use `--status-interval-ms` (50 to 60000) to change that cadence; it must be
shorter than the peer's idle timeout. No statuses are sent before acceptance.

## Reference convergence

Validation is ported from the Crown-owned Apache-2.0 BSI Flex 335 v2.0 Test
Harness, pinned at `6a2999d70d761d224914ddd8fa3f663af5005be0`. It covers the
reference's envelope, registration, status, detection, alert, task, acknowledgement
and error validators, including nested rules. See `NOTICE` for attribution.

All **231 unmodified reference vectors** match: **38 accepted, 193 rejected**.
Of the negative vectors, **35 fail protobuf-JSON decoding** and **158 fail
semantic validation**. The corpus test reports each vector's stage and reasons;
manifest hashes and an exact file-set check prevent modified or omitted fixtures.

JSON decoding uses `prost-reflect` with the descriptor generated from the same
vendored protobuf schema as the arm. This preserves the existing `prost-types`
timestamps and generated wire types for every other consumer. It accepts protobuf
JSON names and enum syntax, rejects unknown fields and conflicting oneofs, and
then transcodes into the normal typed `SapientMessage`. The TCP path and corpus
both call the same `violations()` implementation.

Corpus agreement means reference-vector parity, not proof of all possible
inputs, full BSI conformance, or a production SAPIENT controller's behavior. Use the independent corpus
rerun below to verify the verdicts. Real-controller testing remains necessary
once routing is available.

### Reference behaviors to preserve

- Presence is separate from value: explicit `acceptance: false`, zero location
  coordinates and zero concurrent tasks are present, not missing.
- The reference's `Duration.Value.NotEmpty` rejects zero, including settle time;
  it is not a generic positive-number rule. The arm declares a one-millisecond
  settle time and a latitude/longitude WGS84 region_area.
- RegistrationAck validation requires explicit acceptance. Unlike the earlier
  partial validator, the reference does not require a destination or rejection
  reason here. The receiver still emits an addressed acknowledgement and reasons.
- `LocationType` has context-specific rules. The status location-definition path
  requires location units/datum; other paths enforce location/range-bearing XOR
  and a UTM zone. The port preserves this distinction.
- The reference does not require every optional subtree or implement every rule
  suggested by schema comments. Extra local restrictions are not silently added.
- Some negative detection vectors contain an obsolete `detectionLocation` field
  and fail JSON decoding before their named rule. Separate typed-message tests
  exercise report IDs, object IDs, confidence and missing location so these
  decode failures cannot conceal missing semantic checks.

The receiver is test tooling, not a field controller or a lab deployment:

- No TLS or authentication. Bind to loopback or an explicitly controlled test
  network. The CLI defaults to all interfaces; specify `--bind` deliberately.
- No tasking, alert generation, controller registration exchange or peer-idle
  eviction. Heartbeats keep the tested arm connection alive; they do not emulate
  every controller's session policy.
- Logs record message kinds, violations and acknowledgements, not full payloads.
- No packaged ARM64/AMD64 artifacts, static-link proof or cross-build is included.

## Tests

From `arms/`:

```sh
cargo test -p sapient-receiver -p sapient --locked
cargo clippy -p sapient-receiver -p sapient --all-targets --locked -- -D warnings
```

The arm's `receiver_` tests run its production session driver against the
receiver's production handler over loopback TCP. Both lenient and strict modes
accept the arm and keep it registered beyond several idle windows, with
heartbeats in both directions. Separate builder tests require zero reference
violations for the arm's registration and heartbeat. Refusal handling remains
covered by the session tests.

Receiver tests cover acceptance in strict mode, correctly addressed statuses with
unique ULID report IDs, no status before registration, and a fragmented frame
that remains decodable across heartbeat ticks. The tests do not start Zenoh,
deploy an appliance or connect to a production SAPIENT controller.

## Independent corpus rerun

From `arms/`:

```sh
SAPIENT_CORPUS_REPORT=/tmp/sapient-corpus-results.json \
  cargo test -p sapient-receiver --locked --test corpus -- --nocapture
```

The JSON report lists all 231 expected/actual verdicts with decode/validation
stages and diagnostics. No upstream checkout, network, fixture conversion tool
or running lab is needed once Cargo dependencies are available. The test returns
nonzero for any mismatch, fixture hash change, omission, duplication or extra file.
