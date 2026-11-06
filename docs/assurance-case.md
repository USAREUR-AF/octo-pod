<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Assurance case

This page argues that octo-pod meets its security requirements. It states the threat
model, names the trust boundaries, shows how the design applies secure design principles,
and shows how the code counters common implementation weaknesses. Each claim points at
the code or the test that backs it, so a reviewer can check the claim instead of trusting
it.

Last reviewed: 2026-11-06, against `main` after release 0.5.0.

## Security Requirements

[SECURITY.md](../SECURITY.md#scope) defines a vulnerability as any way to break one of the
guarantees that a pod makes. Those guarantees are the security requirements:

| ID | Requirement |
|---|---|
| R1 | Data leaves the pod only after a declaration and an `apply`, and only to the destination or peer that it was released to. |
| R2 | An arm, peer or destination publishes and reads only the keys that its certificate is granted. |
| R3 | Data that a receiver sends to an egress destination never reaches the fabric. |
| R4 | The installer and `octo-pod upgrade` install only software that the release signature covers. |
| R5 | A private key, slot credential or setup key is readable only by the process that needs it. |
| R6 | The pod firewall admits only traffic that a declaration names. |
| R7 | Input from a sensor or a network peer can't crash an arm or run code in it. |

What the pod doesn't promise is part of the requirements too. Sensors hold no
credential, so a source address is provenance, not authentication. Egress to a receiver
is plain UDP or TCP. [SECURITY.md](../SECURITY.md#traffic-that-octo-pod-does-not-encrypt)
lists what octo-pod doesn't encrypt.

## Threat Model

### Assets

- Sensor data on the pod, and fabric data released to the pod.
- The pod's certificate authority (CA) key, and the private key of each identity that the
  pod issues.
- The fabric slot credential and the mesh setup key.
- The integrity of the software that a pod installs and runs.

### Threat Actors

| Actor | What they can do | Requirements at risk |
|---|---|---|
| A device on the sensor network | Send any bytes, from any address, to an ingest port | R6, R7 |
| A receiver or a partner router | Send data back on an egress connection; present any certificate | R3, R1 |
| A peer on the site network | Connect to the pen with its issued certificate, and try keys outside its grant | R2 |
| A network attacker between the pod and a download server, or a hostile mirror | Replace release files in transit or at rest | R4 |
| A person who takes the pod hardware | Read the disk | R5 |
| A malicious contributor or a compromised dependency | Propose code, or ship a changed package | R1 to R7 |

### Out of Scope

- Authenticating sensors. Sensors send plain bytes and hold no credential.
- The mesh's cryptography, which NetBird (WireGuard) provides.
- Pods and fabrics that others operate.

## Trust Boundaries

Data crosses these boundaries. At each one, a named mechanism decides what may cross.

| Boundary | What crosses it | What decides | Evidence |
|---|---|---|---|
| Sensor network to an ingest arm | Untrusted bytes | The firewall admits only declared ports and sources; the arm's source allowlist; a frame size cap | `platform/templates/host/octo-firewall.nft.j2`, `arms/common/src/source.rs`, `arms/common/src/framing.rs` |
| Arm to the pen router | One publisher's samples | Mutual TLS with a certificate that the pod CA signed, and an access control list (ACL) that denies by default and grants each certificate one key prefix | `platform/templates/pen/router-acl.json5.j2`, `arms/common/src/session.rs` |
| Pen to the fabric, through the uplink bridge | Released namespaces only | `bridge_allow` names what may leave; each key is confined to the pod's slot prefix; the fabric connection uses the slot credential and checks the certificate name | `arms/uplink-bridge/src/main.rs`, `platform/templates/bridge/octo-uplink-bridge.container.j2` |
| Fabric to the pen, through the downlink bridge | Fabric keys released to this pod | The downlink gate admits only named publishers and lanes, and moves each key under `downlink/` | `arms/egress/src/gate.rs` |
| Pen to a receiver, through an egress arm | Declared keys, to declared destinations | The egress arm's grant reads `downlink/` keys and writes nothing; each send is audited | `arms/egress/src/`, `arms/egress/src/audit.rs` |
| A peer to the pen, over the site network | A peer's own publishes and released reads | The peer's certificate and ACL grant; the firewall's peer source list | [ADR 0002](adr/0002-peers-reach-the-lan-and-read-released-keys.md), `platform/templates/pen/router-acl.json5.j2` |
| Release pipeline to the pod | Software | A keyless Sigstore signature on `SHA256SUMS` from this repository's release workflow, and a hash check of every file | `tools/install-to-pod.sh`, `bin/octo-pod-upgrade`, `.github/workflows/release.yml` |
| Contributor to `main` | Code | A pull request, review by a code owner, required checks and signed commits | [CONTRIBUTING.md](../CONTRIBUTING.md#code-review), [Release pattern](release-pattern.md) |

## Secure Design Principles

The design follows the principles of Saltzer and Schroeder.

| Principle | How octo-pod applies it | Evidence |
|---|---|---|
| Fail-safe defaults | The ACL denies by default. The firewall drops by default in every direction. A new pod forwards nothing until an operator releases it. A release that fails any check is refused. | `default_permission is deny` and `nft -c accepts the ruleset` in `platform/tests/test_templates.py`; `policy drop` in `octo-firewall.nft.j2` |
| Least privilege | Each publisher has its own certificate, granted one key prefix. An egress arm reads and never writes. The health publisher may `put` one key. Containers run as an unprivileged user with `NoNewPrivileges`, and mount certificates read-only. CI tokens are read-only unless a job needs more. | `every ACL subject is CN-scoped` and `downlink bridge writes only the downlink namespace` in `test_templates.py`; `USER 65532:65532` in each arm `Dockerfile`; `.github/workflows/*.yml` `permissions` |
| Complete mediation | The downlink gate checks each key when the bridge starts and again on every sample. The installer checks the hash again just before `dpkg` reads the package. | `arms/egress/src/gate.rs`; `tools/install-to-pod.sh` |
| Separation of privilege | Data leaves only after two acts: a declaration and an `apply`. A release needs a signed tag and an approval from a second person. | `bin/octo-pod-declare`; `.github/workflows/release.yml` |
| Economy of mechanism | Byte arms forward bytes without decoding them, so they have no payload parser to attack. Trust logic lives in one shared crate. | [Engineering principles](engineering-principles.md#capture-first-raw-bytes-no-decoding); `arms/common/` |
| Least common mechanism | One arm process and one certificate per publisher, so one compromised arm can't publish as another. | [Engineering principles](engineering-principles.md#one-arm-process-per-publisher) |
| Open design | Security rests on keys and signatures, not on a secret design. Releases use keyless signing, so no long-lived signing key exists. | [SECURITY.md](../SECURITY.md#verify-a-release) |
| Psychological acceptability | The CLI validates declarations and names the fix when it refuses. Operators pick from lists instead of editing files. | `platform/tests/test_cli.py` |

## Common Weaknesses

These weaknesses, from the CWE list and the OWASP Top 10, apply to a network appliance
like this one. The table shows how each one is countered.

| Weakness | Countermeasure | Evidence |
|---|---|---|
| Memory corruption (CWE-787, CWE-125, CWE-416) | The arms are Rust, and the workspace forbids `unsafe` code. Fuzzing runs on every change to `arms/`. | `unsafe_code = "forbid"` in `arms/Cargo.toml`; `arms/fuzz/fuzz_targets/`; `.github/workflows/cflite_pr.yml` |
| Uncontrolled resource use (CWE-400, CWE-770) | Frames are capped at 16 MiB. The queue between socket and session is bounded by age, count and bytes, and counts what it drops. | `MAX_FRAME` in `arms/common/src/framing.rs` and its tests; `arms/common/src/outbox.rs` |
| Improper input validation (CWE-20) | Each framing mode refuses a frame it can't parse. The SAPIENT arm checks each message against the schema rules before it relays it. | `arms/common/src/framing.rs`; `arms/sapient/src/framing.rs`; `arms/sapient-receiver/src/validate.rs`; fuzz targets `tcp_frame`, `sapient_frame`, `sapient_message`, `sapient_json`, `downlink_gate` |
| Missing authorization (CWE-862) | Every ACL subject is scoped to one certificate; an unscoped subject would be a wildcard, and a test refuses it. | `every ACL subject is CN-scoped (unscoped == wildcard)` in `test_templates.py` |
| Insecure defaults (CWE-1188) | Deny by default in the ACL and the firewall; nothing is released by default. | `nothing released to read and no egress: no downlink grant at all` in `test_templates.py` |
| OS command injection (CWE-78) | Workflows pass untrusted values to the shell as environment variables, never pasted into a script. Every shell script passes ShellCheck. Semgrep's command injection rules run on every change. | Comments in `.github/workflows/release.yml`; the `offline` and `security` jobs in `.github/workflows/ci.yml` |
| Path traversal (CWE-22) | Versions and identifiers are checked against a strict pattern before they become paths. | `valid_version` in `bin/octo-pod-upgrade`; `status refuses certificate path traversal` in `platform/tests/test_cli.py`; `valid_version refuses anything else, including path and URL syntax` in `platform/tests/test_upgrade.py` |
| Download of code without an integrity check (CWE-494, CWE-347) | The installer and the upgrade always verify the signature, then every hash. No option skips either check. | `a bad signature is refused even with the right --expect-sha256` and the other refusals in `platform/tests/test_upgrade.py`; `with no signer, the upgrade refuses rather than skips verification` in `platform/tests/test_release.py` |
| Time-of-check to time-of-use (CWE-367) | The installer checks the package hash again on the pod, just before `dpkg` reads it. | `tools/install-to-pod.sh` |
| Hard-coded or leaked credentials (CWE-798) | The pod holds no registry or repository credential. Gitleaks scans the full history on every change. | The `security` job in `.github/workflows/ci.yml`; [Engineering principles](engineering-principles.md#security) |
| Incorrect permissions on critical files (CWE-732) | The CA directory is readable only by root. Private keys are mode 0600. A peer bundle is mode 0600. A key that `remove-arm` should delete is always deleted. | the pen directory and TLS mode tasks in `platform/configure-octo-pod.yml`; `bundle packages exactly cert, key, CA and instructions, mode 0600` and `every private key the playbook writes is one the CLI deletes` in `test_templates.py`; `a private key is never listed` in `platform/tests/test_inspect.py` |
| Improper certificate validation (CWE-295) | The router requires a client certificate that the pod CA signed. Connections that leave the pod also check the certificate name. | [SECURITY.md](../SECURITY.md#tls-on-zenoh-connections) |
| Weak cryptography (CWE-327) | TLS 1.3 only, with AEAD cipher suites; no SHA-1 signatures and no CBC mode. Keys are ECDSA P-256. Certificate renewal refuses to sign when the clock reads before 2025. | [SECURITY.md](../SECURITY.md#cryptography); `platform/templates/pen/cert-renew.sh.j2` |
| Vulnerable or unmaintained components (CWE-1104) | `cargo-deny`, `cargo-audit`, `osv-scanner` and `pip-audit` run in CI; Dependabot proposes updates. Each accepted advisory records its reason and an expiry date. | `arms/deny.toml`; `arms/osv-scanner.toml`; `.github/dependabot.yml` |
| Untrusted build steps (CWE-829) | Every GitHub Action is pinned by commit. Releases carry SLSA provenance, and images carry signed build provenance that `signed-pins` checks before a pin merges. | `.github/workflows/`; `tools/verify-pins.sh`; `.github/workflows/pins.yml` |

## Residual Risks

These risks remain by design or are not yet addressed. The [roadmap](../ROADMAP.md) tracks
the work.

- Sensor feeds and egress to receivers are not encrypted or authenticated. Protect those
  links on the site network.
- The pen router is a single process. Restarting it interrupts every arm until they
  reconnect.
- A certificate that the pod CA signed stays valid until it expires. Access ends when its
  private key is deleted and the ACL no longer grants it, not through a revocation list.
- The Zenoh session settings listed in
  [the egress design's open questions](design-egress-gateway.md#open-questions) are not
  yet hardened.
- No hardware watchdog restarts a pod whose kernel hangs.
- An administrator can merge a change without an approval. The
  [review standards](../CONTRIBUTING.md#code-review) limit this to restoring service.

## Keep This Case Current

Update this page in the same pull request as a change that moves a trust boundary, adds
an input path, or changes a mechanism that a row above names. [Security review](security-review.md)
records each review of the whole case.
