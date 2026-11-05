# Working With Zenoh-Native Partners

How to connect a partner system that already speaks Zenoh to an octo-pod: let it publish
to you, let it subscribe to data you release to it, and publish into its own router.
Every step is a command. None of them changes the running pod until you run
`octo-pod apply`.

## Version Requirements

These peer controls and `zenoh://` destinations require 0.5.0 or later, whose egress
image contains `zenoh-egress-arm`. See [Release status](release-status.md#release-status).

Run every command on the pod, with `sudo`.

## Terms

| Term | Meaning |
|---|---|
| Pen | The pod's own Zenoh router. Everything that connects to the pod connects here. |
| Peer | A partner system that dials the pen with a certificate this pod issued it. It may write one namespace, `raw/<id>/**`. |
| Bundle | The files a peer needs: its certificate, its private key, the pod's CA, and instructions written for that peer. |
| Fabric | The goatnet the pod belongs to, reached over the mesh. |
| Release | Opening a path for data to cross from one side to the other. Every release here has two gates, and both must be open. |
| Downlink | The path from the fabric down to the pen. Fabric key `K` arrives on the pen as `downlink/K`. |
| Lane | A release lane on the fabric (`release/<lane>/...`): data its owners released for a particular organization. |
| Egress destination | A system the pod delivers released fabric data to. |

## Before You Start

- The pod is joined (`octo-pod join`) or initialized (`octo-pod init`).
- For fabric reads, the pod holds a slot authorized for the selected keys or release lane.
  The first `allow-read` or `egress release` can take its bundle with `--slot`. These
  commands configure local gates; they do not grant access on the fabric router.
- If partners reach you over the site LAN, the pod knows its LAN address. To check it,
  look for `lan_ip` in `/etc/octo-pod/host_vars/<pod>/00-site.yml`. To set it:

  ```zsh
  sudo octo-pod set-lan --iface <interface> --addr <ip>/<prefix>
  ```

## Let a Partner Publish to You

Declare the partner as a peer. Use `--from` with the addresses it dials from if it reaches
you over the site LAN; leave it out if it reaches you over the mesh:

```zsh
sudo octo-pod add-peer c2 --from 10.1.2.30
```

Read what `apply` would change:

```zsh
sudo octo-pod check
```

Apply it. This mints the peer's certificate, grants it write on `raw/c2/**`, opens the
firewall to its addresses, and writes its bundle:

```zsh
sudo octo-pod apply
```

Package the bundle for hand-off:

```zsh
sudo octo-pod peer bundle c2
```

The archive holds the peer's private key. Hand it over out of band, send the `sha256` it
prints by a separate channel so the partner can check the archive, and delete your copy
once they confirm receipt.

Confirm the peer is declared, and how it reaches you:

```zsh
sudo octo-pod status
```

A connected session does not prove anything arrived: a put the pen refuses looks like a
successful put on the partner's side. Confirm the key, size and rate you actually receive
in the pen's audit before telling the partner it works.

## Let a Partner Subscribe to Released Data

A peer reads nothing until you release fabric keys to it. For a partner outside your
organization, release from its lane rather than from a publisher's raw keys, so the data's
owners see the release.

Release one or more fabric keys to the peer. This adds them to `downlink_allow` and opens
the peer's own read gate:

```zsh
sudo octo-pod peer allow-read c2 --key 'release/REL-C2/<anchor>/sensor2/**'
```

If the pod holds no slot yet, add `--slot <dir> --ca-fingerprint <fp>`, as for
`egress release`. The command refuses a bundle whose signature does not check out.

Apply it:

```zsh
sudo octo-pod apply
```

Repackage the bundle. Its instructions now list the keys the peer may subscribe to:

```zsh
sudo octo-pod peer bundle c2
```

**The peer subscribes to `downlink/<key>`, not to the fabric key.** The bundle's
instructions name the exact keys. A subscription anywhere else receives nothing, and
receives it silently.

## Publish Into a Partner's Own Zenoh

Use this when the partner will only accept data into its own router, rather than dialing
yours. The partner issues a certificate for the pod and grants it a prefix on its router.
Put its files in one directory as `cert.pem`, `key.pem` and `ca.pem`.

Declare the destination:

```zsh
sudo octo-pod egress add c2-out --dest zenoh://10.1.2.40:7447 --prefix raw/goat --partner-bundle ~/c2-issued --key 'release/REL-C2/<anchor>/sensor2/**'
```

If the partner's router certificate is issued for an identity rather than the address you
dial, add `--no-verify-name`. The pod keeps the partner's files in
`/etc/octo-pod/partner-bundles/<id>/`, so you can delete your download.

Open both downward gates for it:

```zsh
sudo octo-pod egress release c2-out
```

Apply it:

```zsh
sudo octo-pod apply
```

Fabric key `K` arrives on the partner's router as `raw/goat/K`. Ask the partner to confirm
what they receive.

## Release Your Own Data Up to the Fabric

A peer's data stays in the pen until you release it upward. This uses the slot bundle the
goatnet's operators issued for this pod:

```zsh
sudo octo-pod release c2 --slot <dir> --ca-fingerprint <fp>
```

Apply it:

```zsh
sudo octo-pod apply
```

## The Firewall

Turn the pod firewall on:

```zsh
sudo octo-pod set-firewall on
```

You never open a port by hand. Every opening comes from a declaration, and `apply` renders
them all:

| Declaration | Opening |
|---|---|
| A peer | The pen's port over the mesh |
| A peer with `--from` | The pen's port on the LAN, from those addresses only |
| An arm | Its port, from its sources, on the LAN |
| A `tcp-server://` destination | Its port, from its `--from` addresses |
| A `udp://`, `tcp://` or `zenoh://` destination, once released | Outbound to its one address and port |

Nothing is routed between the LAN and the mesh in either direction.

## Check, Change and Take Back

See every egress destination's and peer's downward gates, and whether they agree:

```zsh
sudo octo-pod peer check
```

Change where a peer may dial from:

```zsh
sudo octo-pod peer allow-from c2 --from 10.1.2.30,10.1.2.31
```

Make a peer mesh-only again:

```zsh
sudo octo-pod peer allow-from c2 --clear
```

Stop releasing one key to a peer:

```zsh
sudo octo-pod peer revoke-read c2 --key 'release/REL-C2/<anchor>/sensor2/**'
```

Stop releasing everything to a peer:

```zsh
sudo octo-pod peer revoke-read c2
```

Revoking never drops a key that another released destination or peer still reads; the
command lists any it kept.

Remove a peer completely. This stops it, revokes its key and drops its declaration:

```zsh
sudo octo-pod remove-peer c2
```

Run `sudo octo-pod apply` after each change.

## Prove the Access Rules

`tools/prove-peer-access.py` renders the pen's access rules and runs them in a real Zenoh
router on localhost. It connects as each kind of identity and checks that every allowed
put and subscription arrives and every refused one does not. It needs Python with the
pen's pinned `eclipse-zenoh`, plus `pyyaml`, `jinja2` and `openssl`.

Create an environment with those packages:

```zsh
python3 -m venv /tmp/zv && /tmp/zv/bin/pip install eclipse-zenoh==1.9.0 pyyaml jinja2
```

Run the proof:

```zsh
/tmp/zv/bin/python tools/prove-peer-access.py
```

Run it again with the access rules turned off. Every check that a refused write or read
does not arrive must now fail; a check that still passes was passing on timing:

```zsh
PROOF_DISABLE_ACL=1 /tmp/zv/bin/python tools/prove-peer-access.py
```

## If It Does Not Work

| Symptom | Likely cause |
|---|---|
| The partner's connection times out | It dials from an address not in `--from`, or over the LAN without `--from`. Check `octo-pod status`. |
| Connected, but nothing arrives at the pod | It publishes outside `raw/<id>/`, for example with an org prefix. |
| Subscribed, but the partner receives nothing | It subscribes to the fabric key instead of `downlink/<key>`, the key is not released, or the source is quiet. Check `octo-pod peer check`. |
| `peer bundle` refuses | `apply` has not run since the last change. Run `sudo octo-pod apply`. |
| `peer check` says `DISAGREE` | A peer's read is open but its key is missing from `downlink_allow`. Run `peer allow-read` again with that key. |
| Nothing comes down at all | The pod holds no slot. Give `allow-read` or `egress release` the `--slot` bundle. |
