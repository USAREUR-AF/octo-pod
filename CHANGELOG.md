# Changelog

Notable changes to octo-pod. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Each tagged release carries the documentation site, `octo-pod-docs-<version>.tar.gz`,
  where this repository carries its builder. `SHA256SUMS` lists it, so it is signed and
  covered by the provenance like every other artifact. `tools/build-release.sh --docs`
  packages a built site, and a manual release run can build it with `build_docs`.
- Every release carries SLSA Build Level 3 provenance (`octo-pod-<version>.intoto.jsonl`)
  for each artifact the signed `SHA256SUMS` lists, and the release workflow verifies it
  from the published release with `slsa-verifier`.
- `SHA256SUMS.sigstore.json`: the manifest signature in the standard Sigstore bundle
  format. Pods and the installer keep using `SHA256SUMS.cosign-bundle`.
- `docs/images.md` lists every container image a pod runs, with its role, source and
  digest. `tools/gen-images-doc.py` generates it from the pins, and CI fails if it is
  stale.
- An OpenSSF Scorecard workflow, and status badges in the README.

### Fixed

- The watchdog no longer reports a running arm as declaration drift at random, and can no
  longer mistake a listening pen router for a silent one. It read `podman ps` and `ss`
  through `grep -q` under `pipefail`, where grep exits on its first match and the writer
  dies of SIGPIPE, so the pipeline failed because the name was found. It now reads each
  list once and matches against it. The installer's `rsync --help` check had the same
  fault.

### Security

- The uplink bridge pins Zenoh transport compression off, as every other arm already
  does, so the lz4_flex decompress path (RUSTSEC-2026-0041) stays unreachable on the leg
  that faces the external fabric. Upgrading Zenoh does not fix this advisory: no release
  moves to the fixed lz4_flex yet.
- The test dependencies require Jinja2 3.1.6 or later (PYSEC-2026-1471 through 1475).
  octo-pod uses neither the sandbox nor the `xmlattr` filter those advisories affect.
- CI checks the Python dependencies with `pip-audit`. `arms/osv-scanner.toml` records the
  five triaged Rust advisories, each with its reason and an expiry date, for osv-scanner
  and OpenSSF Scorecard, and a test keeps it in step with `arms/deny.toml`.

## [0.5.0] - 2026-11-05

Every image is rebuilt from this release's source and pinned by digest, the uplink bridge
among them.

### Added

- The uplink bridge is part of this repository (`arms/uplink-bridge`), published and
  pinned with the arm images and carried in the airgap bundle and the VM image. Pods no
  longer build it on the box, and no second repository is needed.

- Per-peer LAN source controls, released downlink reads, read revocation and bundle
  packaging through `octo-pod peer`.
- `zenoh://` egress into a partner's router using partner-issued credentials and a
  declared output prefix.
- A standalone SAPIENT reference test receiver, with lenient and strict validation.
  It is a development tool, not an installed pod service.

### Fixed

- Require a successful structured NetBird status response matching the requested
  management scheme, complete host and effective port before join fetches mesh images
  or converges. Convergence or verification failure cannot report `Joined`.
- Correct SAPIENT registration and status messages for strict BSI Flex 335 v2.0 receivers.
- Support peer command completion in macOS Bash 3 and zsh.
- Patch certificate/root dependencies and Tokio in the Rust lockfile; regenerate notices.

### Changed

- The uplink bridge reads `BRIDGE_PEN_*` and `BRIDGE_FABRIC_*`; the Quadlet unit sets them.
  `build-airgap-bundle.sh` and `build-vm-image.sh` no longer take `--bridge-src`.
- Require Rust 1.89 for source builds and check that minimum in CI. Keep the Zenoh
  dependency pinned to the router's 1.9.0 contract.

## [0.4.0] - 2026-10-20

Podman/Quadlet release. Existing Docker installations require a fresh install.

A 0.3.x pod's own `octo-pod upgrade` treats 0.4.0 as a routine upgrade and installs it
without the migration. Don't point a Docker-era pod's `octo_repo_url` at a mirror whose
`LATEST` is 0.4.0 or later; give it a fresh install instead.

### Fixed

- Bound newline frames, SAPIENT peer intervals and registration waits, and stalled
  TCP egress writes. Invalid declarations and missing CLI arguments fail clearly.
- Pin installation and download-page verification to the release workflow on release
  tags. Preserve staged images on failed extraction and replace release links atomically.
- Renew expired local leaves and include egress and downlink consumers. Status reports
  the uplink by its exact name and reads the generated egress certificate filename.
- Enable router readiness notification on Podman 4.x with an explicit final argument.
  Podman 4.7 or newer is required. A successful listener probe gates startup; the
  health start period is a failure grace period, not a fixed startup delay.

### Removed

- CAT-62 traffic simulator and the `simulate` CLI verb. Validate capture using a
  known test datagram from an allowed source on the sensor LAN.

### Added

- `otp` as the packaged short alias, with Bash and zsh completion. `op` remains available
  for 1Password. Typed inspection accepts resource kind and name; `get arms` includes
  ingress and egress, while `get ingress` filters to capture arms.
- Protected ingress endpoint editing with a dry run and explicit acknowledgement for
  released-source changes. Identity fields remain immutable; apply restarts changed arms.
- A Mac image delivery helper that validates pinned OCI content, transfers a verified
  stage and optionally seeds the on-pod registry without applying application changes.
  Live field acceptance remains pending.

- `octo-pod top`, a live read-only view: rates, drops, queue and consumers per arm, bridge
  and egress destination, read from the stats lines the containers already log, plus
  declarations, mesh peers and a problems list. `--once` and `--json` print a snapshot.
- `octo-pod get`, `describe`, `logs` and `certs`, read-only views using kubectl’s command structure: a
  row per arm, peer, destination, bridge, mesh peer, problem or certificate (`-o wide`,
  `-o json`); one item in full with its declaration redacted; one item's logs, from the
  unit's journal once it has stopped; and every certificate's expiry, the slot
  certificate included, with the renewal timer. `certs --check` fails when one ends
  within 30 days or cannot be read.
- SAPIENT session egress for released fabric detections and alerts, with per-destination
  identity, read-only pen access and a local audit trail. Unreleased sessions stay stopped
  at boot and apply; verify refuses a running unreleased session.

- Podman and Quadlet replace Docker: every pen, arm and bridge is a systemd-managed
  container rendered from the declarations.
- Egress: a pod delivers released fabric data to systems that can't join the fabric, as
  UDP or TCP pushes or as a TCP server the system dials, through two release gates.
- A deny-by-default host firewall rendered from the declarations, with an automatic
  restore if a change disconnects the pod.
- A golden VM image built from committed source, for amd64 and arm64.
- `octo-pod remote` for workstation-side install, key minting and deprovisioning.

### Changed

- Release trust follows the repository that built the release. Each release records that
  repository in `/opt/octo-pod/RELEASE`, `octo-pod upgrade` accepts only that repository's
  release workflow, run for an `octo-pod-v*` tag, as the signer, and
  `tools/install-to-pod.sh` takes `--repo`. Release trust derives from the selected repository. A
  pod moving to releases from another repository sets `OCTO_POD_CERT_IDENTITY` once, or
  installs that repository's first release with `install-to-pod.sh --repo`.

### Security

- A slot bundle is trusted only after its signed profile verifies against the fingerprint
  the goatnet's operators give out of band; the pod pins the fabric CA roots, and apply
  refuses any others.
- Egress keys must name a publisher; wildcards and reserved roots are refused in the CLI,
  the playbook and the arm.
- `octo-pod upgrade --expect-sha256` no longer skips the signature. The signed manifest is
  always verified, and the supplied digest is an extra check on the tarball, the `.deb`
  or the image bundle. `--images` with any 64-hex digest used to unpack an unsigned
  bundle.
- The upgrade signer is pinned to `.github/workflows/release.yml` on `octo-pod-v*` tags of
  the release's repository, not any workflow in it. `OCTO_POD_CERT_IDENTITY` still
  overrides it, and the override is printed whenever it is used.

### Fixed

- `pen-cert-renew` renews an expired leaf the pen signed. It decided which certificates
  were its own with a time-checked `openssl verify`, which fails on an expired one, so a
  pod switched on after its leaves expired skipped them all and reported no failures.
- `octo-pod status` reports a stopped uplink bridge on a pod that also runs the downlink
  bridge; any running container named `*bridge*` used to stand in for it. `top` shows a
  bridge the pod needs as NOT RUNNING instead of leaving its row out.
- `octo-pod top` continues processing after a container log line that is not UTF-8 or carries a malformed
  number or date, and `--once` shows escape and control characters instead of passing
  them to the terminal.
- `octo-pod status` no longer opens a file outside the pen's TLS directory when a
  hand-edited `cert_cn` contains a path.
- Native amd64/arm64 image builds and exact platform verification, including SAPIENT's
  upstream pin mapping. Commit builds carry all six first-party arm images by digest;
  offline releases include both new egress images and verify all eight bundle pins.
- Release CI dependencies, notices generation and two exact historical recipe-label
  secret-scanner false positives. Other secret detection remains enabled.
- Patched Quinn, Rustls and WebPKI dependencies, including Rustls 0.23.46 for the four
  advisories published on 2026-10-14. Advisories are handled under SECURITY.md.
- `octo-pod upgrade` accepts prerelease versions (`X.Y.Z~<word><N>`, such as `0.4.0~lab2`)
  and orders versions the way dpkg does. A `LATEST` at or below the running version is
  nothing to do; an older version takes `--rollback` or `--to <version> --allow-downgrade`.
  `--rollback` returns a release-directory install to a kept prerelease. A package install
  downloads the previous `.deb` again, so it needs that prerelease's `.deb` on the mirror
  and a signer that covers it through `OCTO_POD_CERT_IDENTITY`: a prerelease has no
  `octo-pod-v*` tag, because git refuses `~` in one, so `release.yml` never signs it.
- `octo-pod upgrade --images` refuses a `LATEST` it cannot order against the running
  version, as the software path does, instead of staging that bundle.
- A failed signature check prints cosign's own error, so a pod that can't reach
  sigstore's trust root reads as that rather than as a forged release.

- Many bootstrap, firewall and CLI fixes since 0.3.4; see the commit history.

## [0.3.4]

The last Docker-based release.
