# Podman/Quadlet migration: one container runtime across the estate

**Status:** done; Podman and Quadlet shipped in 0.4.0.
**Prompted by:** the device base image's package contract: one golden image carries ONE
container runtime — podman — and any appliance built on it installs on top from
carried artifacts. octo-pod is not fielded anywhere, so the choice was migrate
octo-pod rather than admit docker into every box forever.

**Repository boundary:** octo-pod delivers a package, application containers and dependency
setup. Device OS images are supplied externally. LXD helpers in this repository are lab
test tooling only; they are not a requirement for Raspberry Pi, Thor or other devices.

## Goals

- octo-pod runs entirely on podman + Quadlet on a contract base image; `docker`
  appears nowhere in the platform, the CLI, or the docs.
- Every lesson encoded in the compose files survives the move — each is named
  below with where it lands.
- The offline test suite and CI stay green at every stage.

## Non-goals

- No behaviour changes to arms, ACL, certs, bridge, or supply chain beyond the
  runtime swap. One migration, one variable.
- No image changes: same digests, same registries, same bundle.

## The mapping (compose idiom → Quadlet idiom)

| Compose | Quadlet | Lesson preserved |
|---|---|---|
| `container_name: octo-*` | `ContainerName=` in `octo-*.container` | naming convention tools rely on |
| `restart: unless-stopped` | `[Service] Restart=on-failure` | systemd owns liveness; `systemctl --failed` becomes the signal |
| json-file `max-size/max-file` | journald drop-in installed by the playbook | bounded logs (the 2.2 GB syslog disk loss) — now at the journal layer podman logs into |
| `depends_on: condition: service_healthy` | router unit `Notify=healthy` + `HealthCmd`; sinks `After=`+`Wants=` the router unit | Sinks that start before the router never subscribe: explicit `PodmanArgs=--sdnotify=healthy` supplies the startup gate on Podman 4.7+. Podman 5+ also honors `Notify=healthy`. The watchdog remains necessary for later failures |
| `extra_hosts: penhost:host-gateway` | `PodmanArgs=--add-host penhost:host-gateway` | Sinks off host-net so their listener can't collide with the router |
| `.env` + `${VAR:?}` interpolation | values inlined at Jinja render; required-var refusals move to playbook asserts (fail at render, not at start) | refuse-to-fall-back-to-a-tag stays; it just fails earlier and names the variable |
| per-publisher sink loop in ONE file | one rendered `octo-pen-audit-sink-<id>.container` per declaration, plus a reap task that removes unit files no declaration names | a namespace that can be written is watched — AND converge-on-delete becomes structural instead of hoped-for |
| health as its own compose project | inherent: every unit is its own failure domain; health image pull cannot block the router by construction | health must never be a reason a pod stops capturing |
| `docker compose up -d` | render units → `systemctl daemon-reload` → `systemctl start` | apply stays idempotent and previewable |
| `docker pull` gate before up | `podman pull` gate, unchanged wording | pull-verifies-and-warms; the failure reason is the real one |
| watchdog `docker ps`/`restart` | `systemctl is-active`/`restart` on unit names | debounced repair; restart-not-repair |

## Costs accepted

- Container lifecycle moves under systemd: `docker logs octo-…` becomes
  `journalctl -u octo-…`; README and troubleshooting tables need the sweep.
- Quadlet units are generated (cannot `systemctl enable`); boot wiring comes
  from `[Install]` in the .container files.
- Quadlet supports `Notify=healthy` only on Podman 5+. Podman 4.x, including
  Ubuntu 24.04's 4.9.x, ignores this value and notifies via conmon.
  The router therefore adds `PodmanArgs=--sdnotify=healthy`, supported from 4.7.
  The supported runtime and package dependency floor is now 4.7.
  `HealthStartPeriod=10s` is a grace period for failed probes, not a minimum
  startup delay. A successful probe can make the router ready earlier. Verify
  that the service stays activating while the probe fails and becomes active
  after a successful probe. Measure activation from `InactiveExitTimestampMonotonic`
  to `ActiveEnterTimestampMonotonic`; the notified main PID can change during startup.
  Timer-driven oneshot services may be inactive between runs; check their timers
  and last result separately from the long-running container services.

## Stages

- **O1a — pen**: DONE. Router + sink quadlets (+ PartOf, + reap); playbook §pen swap.
- **O1b — arms**: DONE. One arm template absorbing env.j2's contract; per-arm
  units + reap; podman-build dev path; compose/ deleted from the repo and the
  release payload.
- **O1c — everything else**: DONE. Health unit; watchdog/cert-renew/verify/
  bootstrap ported; bootstrap now ASSERTS the base contract instead of
  installing a runtime; journald caps replace daemon.json; deprovision keeps
  the runtime (it is the base image's) and gains --keep-images; remove-arm
  deletes quadlets (a leftover unit resurrects its arm at boot). Test suites
  retargeted to the quadlet artifacts — 85 checks green.
- **O2 — supply posture**: implement docs/design-supply-posture.md (`supply:`
  in identity, refuse-not-prefer).
  ALSO EARNED HERE: the on-pod registry existed because `docker load` loses
  RepoDigests; podman's containers-storage preserves them (proven on the Thor),
  so O2 should retire octo-registry and seed containers-storage directly with
  native skopeo — the airgap path loses a moving part.
- **O3: parallel deploy** on a Jetson Thor test machine (contract base):
  Posture B standalone, sensor-LAN test datagram proof, before any fleet story.

## Known remainders (deliberate)

- **sync-leg bridge**: done. The bridge lives in this repository as
  `arms/uplink-bridge`, is published and pinned with the arm images, and runs as the
  `octo-uplink-bridge` Quadlet unit.
- **README/docs sweep:** current operating instructions use Podman/systemd. Docker and
  Compose references in migration comparisons and incident history describe the earlier
  implementation; they are not current deployment commands.
- No in-place docker→podman upgrade path for a RUNNING docker-era pod: none is
  fielded, so the migration is fresh-install-only, on purpose.
