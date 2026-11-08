<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Security Policy

octo-pod operates at the edge of a data fabric: it terminates sensor feeds, holds a fabric
identity, gates what leaves the site and runs a host firewall. Security reports come
before feature work.

## Report a Vulnerability

**Don't open a public issue, pull request or discussion about a vulnerability.** A public
report tells an attacker before a fix exists.

Report it through GitHub private vulnerability reporting:
<https://github.com/usareur-af/octo-pod/security/advisories/new>. You can also open the
repository's **Security** tab and select **Report a vulnerability**. The report creates a
draft advisory that only you and the maintainers can read.

Include:

- What the issue is and what an attacker gains.
- Steps or a proof of concept that reproduce it.
- The affected versions, commits or configurations.
- A suggested fix, if you have one.
- Whether you want credit in the advisory.

Don't include real addresses, keys, tokens or site details. Use documentation addresses
(`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) and synthetic values.

If you can't use GitHub private vulnerability reporting, open a public issue that asks
the maintainers for a private contact. Don't put any detail about the vulnerability in
that issue.

## What to Expect

| Stage | Target |
|---|---|
| Acknowledgement | Within 3 business days, and never later than 14 days after the report |
| Triage and severity | Within 7 business days |
| Status updates | At least every 14 days until the report is closed |
| Fix and advisory | Depends on severity; the maintainers agree on a disclosure date with you |
| Disclosure | Coordinated with you |

The maintainers develop the fix in private and release it first. They then publish a
GitHub Security Advisory for this repository. When the vulnerability affects a published
release, they request a CVE identifier for it through the advisory. The
[changelog](CHANGELOG.md) entry for the release that contains the fix names the advisory
and its CVE identifier. Reporters are credited by name unless they ask not to be.

If the maintainers decide that a report is not a vulnerability, they tell you why.

## Supported Versions

| Version | Security fixes |
|---|---|
| The latest minor release line (see [Releases](https://github.com/usareur-af/octo-pod/releases)) | Yes |
| Older release lines | No. Upgrade to the latest release |
| `main` | Yes. Fixes merge to `main` and ship in the next release |

A fix ships as a new release of the latest minor line. The project does not publish fixes
for older lines. While the version is below 1.0, a minor release may change behavior;
read the [upgrade guide](docs/upgrading.md) before you upgrade.

## Scope

In scope:

- The code, scripts and Ansible in this repository.
- Container images, packages and release artifacts built from this repository by its
  own CI.
- The default configuration a release installs, including the pod firewall and the
  release gates.

A vulnerability in a pod is any way to break one of the guarantees the pod makes. For
example:

- Data leaves the pod without a declaration and an `apply`, or reaches a destination or
  peer that it was not released to.
- An arm, peer or destination publishes or reads outside the keys its certificate grants.
- Data that a receiver sends to an egress destination reaches the fabric.
- `tools/install-to-pod.sh` or `octo-pod upgrade` installs software that the release
  signature does not cover.
- A private key, slot credential or setup key becomes readable by a process or person
  that should not read it, for example through file modes, logs or a bundle.
- The pod firewall admits traffic that no declaration names.
- Input from a sensor or network peer crashes an arm or runs code in it.
- A weakness in the configuration that [Cryptography](#cryptography) describes.

Out of scope:

- Pods and fabrics operated by others. Report those to their operators.
- Vulnerabilities in third-party dependencies. Report them upstream, and tell us if
  octo-pod is affected.
- The absence of authentication on a sensor feed. Sensors send plain bytes and hold no
  credential; the pod records the source address as provenance, not as authentication.
  See [engineering principles](docs/engineering-principles.md#context-and-scope).

## Verify a Release

Every release is signed. The release workflow writes `SHA256SUMS`, the SHA-256 hash of
every release file, and signs that list with [cosign](https://docs.sigstore.dev/)
keyless signing. The signing certificate names this repository's release workflow
(`.github/workflows/release.yml`) and the release tag (`octo-pod-v<version>`). The
signature is recorded in the public Sigstore transparency log. No long-lived signing key
exists, so there is no signing key to steal or rotate.

`tools/install-to-pod.sh` and `octo-pod upgrade` check the signature and the hashes
before they install anything, and refuse a release that fails either check. No option
skips the signature check.

To verify a release yourself, download its files:

```sh
gh release download octo-pod-v<version> --repo usareur-af/octo-pod \
  -p SHA256SUMS -p SHA256SUMS.cosign-bundle -p 'octo-pod_<version>_all.deb'
```

Check the signature on `SHA256SUMS`:

```sh
cosign verify-blob --bundle SHA256SUMS.cosign-bundle \
  --certificate-identity-regexp '^https://github\.com/(?i:usareur-af/octo-pod)/\.github/workflows/release\.yml@refs/tags/octo-pod-v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
```

The `(?i:...)` group matches the owner and repository names in any letter case, because
links and clones may spell them differently from the signing certificate.

That check downloads Sigstore's public keys first. To check offline instead, use the
standard Sigstore bundle that releases from 0.6.0 carry, and the keys in a checkout of
this repository:

```sh
gh release download octo-pod-v<version> --repo usareur-af/octo-pod -p SHA256SUMS.sigstore.json
cosign verify-blob --new-bundle-format --bundle SHA256SUMS.sigstore.json \
  --trusted-root platform/sigstore/trusted_root.json \
  --certificate-identity-regexp '^https://github\.com/(?i:usareur-af/octo-pod)/\.github/workflows/release\.yml@refs/tags/octo-pod-v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
```

Check that the files match the signed hashes:

```sh
sha256sum -c --ignore-missing SHA256SUMS
```

Check the signature first. The signature makes the hash list trustworthy; a hostile file
can carry its own hash list.

You can also rebuild a release from its tag and compare your files with `SHA256SUMS`. See
[Reproducible Builds](docs/reproducible-builds.md).

From the next release, each release also includes SLSA build provenance,
`octo-pod-<version>.intoto.jsonl`. It records the source repository, the tag and the
workflow that built each file that `SHA256SUMS` lists. Verify a file with
[slsa-verifier](https://github.com/slsa-framework/slsa-verifier):

`slsa-verifier` compares the source repository exactly, letter case included. Use the
repository name as the release notes print it.

```sh
slsa-verifier verify-artifact octo-pod-<version>.tar.gz \
  --provenance-path octo-pod-<version>.intoto.jsonl \
  --source-uri github.com/USAREUR-AF/octo-pod \
  --source-tag octo-pod-v<version>
```

The package pins the router, audit and arm container images by digest in
`platform/group_vars/all/10-infrastructure.yml`. When a release includes the image bundle
for disconnected sites, `SHA256SUMS` lists it. The image workflow attaches an SPDX SBOM
and build provenance to each image that it builds; see
[Rebuild the project images](CONTRIBUTING.md#rebuild-the-project-images).

## Cryptography

octo-pod uses published, standard algorithms through maintained libraries: OpenSSL,
rustls with the *ring* provider (through Zenoh), and Sigstore cosign. It implements no
cryptographic algorithm of its own. Every key that the pod generates gives 128 bits of
security, above the 112-bit minimum in NIST SP 800-57 Part 1.

### Keys and Certificates That the Pod Generates

The pod runs its own certificate authority (CA) and issues one certificate to each
identity: the router, each arm, peer and egress destination, the health publisher and
the bridges. OpenSSL generates every key on the pod. No private key that the pod
generates leaves the pod, except a peer's key in the `octo-pod peer bundle` archive that
the operator transfers to that peer.

| Item | Algorithm | Validity | Source |
|---|---|---|---|
| CA key | ECDSA, curve P-256 (`prime256v1`) | Not applicable | `platform/configure-octo-pod.yml` |
| CA certificate | Self-signed, ECDSA with SHA-256 | 3650 days | `platform/configure-octo-pod.yml` |
| Leaf keys | ECDSA, curve P-256 | Not applicable | `platform/configure-octo-pod.yml` |
| Leaf certificates | ECDSA with SHA-256, `CA:FALSE`, an extended key usage for each purpose | 825 days | `platform/configure-octo-pod.yml` |
| Renewed leaf certificates | The same key, ECDSA with SHA-256 | 825 days, renewed 60 days before they expire | `platform/templates/pen/cert-renew.sh.j2` |

The pod generates no RSA keys. The CA key is in `/var/lib/octo-pen/ca/`, a directory that
only root can read. Renewal refuses to sign when the system clock reads a year before
2025, so a clock reset to 1970 can't produce certificates dated to that time.

### TLS on Zenoh Connections

Every connection to the pod's Zenoh router (the pen) uses mutual TLS. The router listens
only on a `tls/` endpoint, requires a client certificate that the pod CA signed, and
denies every action that the access control list does not grant
(`platform/templates/pen/router-acl.json5.j2`). The arms, bridges and health publisher
use the same mutual TLS settings (`arms/common/src/session.rs`).

The TLS implementation is the Zenoh 1.9.0 TLS link: rustls 0.23 with the *ring*
cryptography provider. The arms pin Zenoh 1.9.0 in `arms/Cargo.toml`, and the router
image (`eclipse/zenoh` 1.9.0) is pinned by digest in
`platform/group_vars/all/10-infrastructure.yml`. octo-pod does not change the rustls
defaults.

| Property | Value |
|---|---|
| Protocol version | TLS 1.3 only, on both sides, whenever mutual TLS is on |
| Cipher suites | `TLS_AES_256_GCM_SHA384`, `TLS_AES_128_GCM_SHA256`, `TLS_CHACHA20_POLY1305_SHA256` |
| Key exchange | Ephemeral X25519, P-256 or P-384, which gives forward secrecy |
| Certificate signatures accepted | ECDSA (P-256, P-384), Ed25519, RSA PKCS#1 v1.5 and RSA-PSS with keys of 2048 to 8192 bits |
| Hash functions | SHA-256, SHA-384, SHA-512 |

The configuration offers no CBC-mode cipher, no RC4, no SHA-1 signature and no static
RSA key exchange. A certificate signed with SHA-1 or with an RSA key shorter than 2048
bits fails verification.

**Name checks.** A client inside the pod checks that the router's certificate chains to
the pod CA and permits server authentication. It doesn't check the host name, because the
router certificate names the pod's addresses, not the names that containers connect to
(`arms/common/src/session.rs`). Connections that leave the pod do check the host name:
the uplink and downlink bridges' fabric connections
(`platform/templates/bridge/octo-uplink-bridge.container.j2`,
`arms/egress/src/bin/downlink-bridge.rs`), and a `zenoh://` destination's connection to a
partner router (`platform/templates/egress/octo-zenoh-egress.container.j2`). An operator
can disable the check for one `zenoh://` destination with `--no-verify-name`.

### Credentials Issued Outside the Pod

| Credential | Who chooses the key | What the pod checks | Source |
|---|---|---|---|
| Fabric slot bundle | The fabric operator | An ECDSA P-256 signature over SHA-256 on the signed profile, from a signing key whose SHA-256 fingerprint the operator receives through a separate channel. The pod then pins the SHA-256 digest of the fabric CA roots | `bin/octo_pod_slot.py` |
| Partner credentials for `zenoh://` | The partner | The partner router's certificate, with the TLS rules above | `arms/egress/src/session.rs` |

### Release Signing and Integrity

| Item | Algorithm | Source |
|---|---|---|
| Release file hashes | SHA-256, in `SHA256SUMS` | `.github/workflows/release.yml` |
| Release signature | cosign keyless: a short-lived certificate from the Sigstore certificate authority for a new ECDSA P-256 key, valid for 10 minutes | `.github/workflows/release.yml` |
| Signature check on install and upgrade | cosign, with the release workflow and tag as the required signer | `tools/install-to-pod.sh`, `bin/octo-pod-upgrade` |
| Egress audit records | SHA-256 hash of each payload | `arms/egress/src/audit.rs` |

### Traffic That octo-pod Does Not Encrypt

- **Sensor feeds.** UDP and TCP ingress arms receive what the sensor sends, without
  encryption. The `--from` address list is provenance, not authentication.
- **Egress to receivers.** `udp://`, `tcp://`, `tcp-server://` and `sapient://`
  destinations send plain UDP or TCP, because the receiver gets data the same way it gets
  data from a sensor.

Protect these links on the site network, for example with a separate physical network
or VLAN, or link encryption. Between sites, the pod's traffic crosses the mesh (NetBird,
which uses WireGuard). The mesh's cryptography belongs to NetBird and is outside this
repository.
