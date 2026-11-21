<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Guide: convert captured data to another format

**Status: work in progress, off by default.** The translation gateway is proposed in
[ADR 0004](https://github.com/usareur-af/octo-pod/blob/main/docs/adr/0004-host-the-translation-gateway-beside-the-arms.md). No image is
published for it, and no deployment has run it. Use it on a test pod, from a clone of
this repository.

A pod captures what a sensor sends and does not change it. When a receiver needs the same
data in another format, declare a translation gateway. The gateway reads the captured
samples on the pod's local router (the pen), converts each one, and publishes the
converted copy on its own keys. The captured sample stays where the arm published it.

A gateway is not an arm. It opens no connection to a sensor or a receiver, and it has its
own certificate, namespace and audit record.

## Before you start

- The arm that captures the data is declared and running, and you know its key. An arm
  publishes on `raw/<arm id>/<feed>/<source address>`. `octo-pod status` shows the topic.
- The source format and each target format have a pack in the gateway's bundle. The
  bundle holds `sapient` (BSI Flex 335 v2.0), `cot-xml` and `geojson`.
- Read [what the pinned packs carry](https://github.com/usareur-af/octo-pod/blob/main/gateway/translate/README.md#what-the-pinned-packs-carry).
  A conversion carries only the values both formats have a field for.

## Declare a gateway

This example converts the frames that the SAPIENT arm `radar1` captures to Cursor on
Target XML and to GeoJSON.

1. Declare the gateway:

   ```zsh
   sudo octo-pod translate add xl1 --source 'raw/radar1/sapient/*' --from sapient --to cot-xml,geojson --set how=m-g --stamp-time --stale-after 30
   ```

   - The gateway converts only a detection that reports latitude and longitude in
     degrees. It skips and counts any other (radians, UTM metres, or no stated
     coordinate system) without being told to, because the SAPIENT pack reads the two
     numbers as degrees for every coordinate system.
   - The results have no `uid`: the SAPIENT pack does not map the detection's object id
     to one yet. A CoT consumer that requires a `uid` refuses them until it does.
   - `--set` supplies a value that SAPIENT does not carry.
   - `--stamp-time` and `--stale-after` give each result the time the sample was
     received and a stale time 30 seconds later, because the SAPIENT pack maps neither.

2. Read what would change:

   ```zsh
   sudo octo-pod check
   ```

3. Apply it:

   ```zsh
   sudo octo-pod apply
   ```

   `apply` mints the certificate `octo-translate-xl1`, grants it a read on
   `raw/radar1/sapient/*` and a write on `raw/xl1/**`, starts an audit sink on
   `raw/xl1/**`, builds the image from `gateway/translate/` and starts
   `octo-translate-xl1.service`. The pen router restarts, as it does when any publisher
   is added.

The command writes one entry to `/etc/octo-pod/host_vars/<pod>/50-translate.yml`:

```yaml
translators:
  - id: xl1
    kind: translate
    description: "sapient to cot-xml, geojson"
    cert_cn: octo-translate-xl1
    namespace: "raw/xl1/**"
    enabled: true
    sources:
      - "raw/radar1/sapient/*"
    from: sapient
    to: [cot-xml, geojson]
    context:
      how: "m-g"
    stale_after_s: 30
    stamp_time: true
    max_distribution: public
```

## Check that it converts

Read the gateway's log:

```zsh
sudo journalctl -u octo-translate-xl1.service -n 20
```

The `stats` line shows `received` and `forwarded` rising together. `dropped_invalid` or
`skipped` rising means samples are not converting; the `translate stats` line and the
audit file name the error. On a SAPIENT feed `skipped` rises with every message that is
not a detection with a position (`no-shared-value`) and every detection that is not in
latitude and longitude (`condition-not-met`).

Read the audit file:

```zsh
sudo tail -n 5 /var/lib/octo-translate/xl1/xl1-translate.ndjson
```

Each `translated` line names the captured key, the SHA-256 of the captured bytes, the
published key and the SHA-256 of the result. Each `failed` or `skipped` line names the
captured key and the error.

The results are on `raw/xl1/cot-xml/raw/radar1/sapient/<source address>` and
`raw/xl1/geojson/raw/radar1/sapient/<source address>`.

## Release the converted data

A converted sample stays on the pod until its namespace is released, as for any
publisher. Add `raw/xl1/**` to `bridge_allow` in
`/etc/octo-pod/host_vars/<pod>/10-pod.yml`, then run `sudo octo-pod apply`. The uplink
bridge then publishes it to the fabric under the pod's slot. A receiver that cannot join
the fabric gets it through an egress destination, as for any fabric key
([operations](operations.md)).

`octo-pod release` does not open this gate for a gateway.

## Remove a gateway

```zsh
sudo octo-pod translate remove xl1
sudo octo-pod apply
```

`remove` stops the unit, deletes both copies of the gateway's private key and drops the
declaration. It keeps the certificate and the audit file. `apply` withdraws the pen
grants and the audit sink. If `bridge_allow` names `raw/xl1/**`, remove it by hand.

## What a gateway may read

A source is a pen key expression. It must begin with a literal chunk and name what to
read beneath it. A gateway cannot read:

- `ops/`, `attest/`, `system/`, `@/`: these are not sensor data.
- `downlink/`: data that came down from the fabric is not converted and sent back up.
- `raw/*/…` or `raw/**`, or any gateway's namespace: a gateway does not convert converted
  output.

The command, the playbook and the gateway binary each check these rules.

## What is not built

- A published, signed image and its digest pin. A pod installed from a package needs
  `translate_image` set to a digest-pinned reference it carries.
- A build on an air-gapped pod. Without `translate_image`, a clone of octo-pod builds
  `localhost/octo-translate:dev` on the pod, and that build fetches crates from
  crates.io, the goat-transformers crate from GitHub and its base images from cgr.dev.
  An air-gapped clone sets `translate_image` to an image it carries instead. When a
  build or pull produces a new image, the playbook restarts every gateway, even if no
  unit file changed.
- The gateway in `octo-pod status`, `top`, `get`, `describe`, `logs`, `verify` and
  shell completion. `octo-pod certs` lists its certificate.
- `octo-pod release` for a gateway's namespace.
- Formats other than the three in the bundle. To add one, see
  [the pack bundle](https://github.com/usareur-af/octo-pod/blob/main/gateway/translate/README.md#the-pack-bundle).
