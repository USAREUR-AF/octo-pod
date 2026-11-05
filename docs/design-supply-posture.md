# Supply posture: what a pod is allowed to reach for

**Status:** the unified `supply:` declaration below remains proposed. Existing image
bundles, local-registry seeding, signed software upgrades and `set-mesh --mirror` are
implemented separately; they do not implement this unified model. Current operational
steps are in the [deployment guide](deployment.md#install-or-update). Status reviewed 2026-10-15.
**Prompted by:** two defects in one afternoon, both from the same modelling error.

## Context and scope

A pod needs artifacts: container images, its own software, a mesh setup key, an upstream
slot certificate. Where those come from is currently described by two separate settings
that have to agree:

```
octo-pod bootstrap -e bootstrap_mode=airgap        transient, bootstrap-time
image_mirror: localhost:5000                       durable, in the identity, set later
```

Those are not two decisions. They are one fact stated twice, in two places, at two
different times, by two different commands. Anything you can only keep consistent by
remembering to, you will eventually fail to keep consistent, and both halves of that
sentence happened on 2026-08-19: the same operator missed `set-mesh --mirror` twice in one
afternoon, and both times it surfaced as `apply` refusing at the first private image with
a message about registry credentials.

This note proposes replacing both with one declaration, made once, that everything else
derives from.

### The error message was describing the wrong fault

When it failed, the pod said:

> The registry refused the request as unauthorized. The arm images are private and this
> pod has no registry credential, that is deliberate, not a misconfiguration.

Every word of that is true and it is still the wrong diagnosis. The fault was not that
ghcr refused. The fault was that **a pod built to need nothing outside itself reached for
the internet at all**, while a complete, digest-identical copy of what it wanted sat in a
registry on its own disk.

A pod cannot say that, because nothing on it knows it is supposed to be airgapped.
`bootstrap_mode=airgap` was an argument to a command that finished hours earlier.

## Goals

- One declaration of where artifacts come from, made once, stored in the identity.
- An airgapped pod that **refuses** to reach outward, rather than one that prefers not to.
- Failure messages that name the posture, so the remedy is about the bundle or about the
  network, never both at once.
- No new surface for an operator to learn beyond the posture itself.

## Non-goals

- **Deciding this per artifact kind.** A pod that carries its images but fetches its own
  software is a shape nobody has asked for and every extra combination is a path nobody
  will run. One posture governs everything.
- **Making the connected path work offline, or the reverse.** They are different
  deployments, not modes of one deployment.
- **Changing how anything is carried.** `install-to-pod.sh --with-images` and the on-pod
  registry are unchanged; this is about what the pod believes, not about the mechanism.

## Two paths, and they are not symmetric

The instinct that they are two very distinct paths is right, and the asymmetry matters
more than the distinction.

| | **airgap** | **connected** |
|---|---|---|
| Images | carried, into the pod's own registry | pulled from the registry each one names |
| Its own software | carried, by re-running the installer | fetched by `octo-pod upgrade` |
| Setup key | carried, staged before the build | could be fetched, still carried today |
| Slot certificate | carried, out of band | carried, out of band |
| What must be true | **the bundle is complete before you start** | the network works and credentials exist |
| A missing artifact means | the bundle is wrong. Fix it elsewhere and re-carry | a reachability or credential problem, fixable here |
| Recovery | needs a person with physical or ssh access | often none, retry later |

**Airgap is the harder one and should be built first.** Not because it is more common, but
because it admits no escape hatch. Connected is a relaxation of it: every artifact an
airgapped pod needs at build time is one a connected pod could defer, so a design that
satisfies airgap satisfies connected with fewer constraints. The reverse is not true, and
a system built connected-first grows lazy fetches that airgap then has to find and remove.
That is exactly the shape of the bug that prompted this.

## Degree of constraint

Fixed, and worth stating because it is what makes airgap hard rather than tedious:

- **A pod holds no registry credential, ever.** A box that can be stolen should not carry
  one. This is not a gap to close.
- **Every image is pinned by digest.** This is what makes a local mirror safe:
  `localhost:5000/x@sha256:abc` and `ghcr.io/x@sha256:abc` are the same bytes by
  construction, because the digest is the identity. None of this would be defensible for
  tags.
- **The bundle is built elsewhere, by CI, and carried.** A pod never assembles one.
- **The site may have no route outward at all**, not merely a slow one. Anything that
  degrades from fast to slow is fine; anything that degrades from working to hanging is
  not.

## The design

### The posture is part of the identity

One field, written when the pod gets its identity, changed only by a command:

```yaml
# 05-fabric.yml
supply: airgap        # or: connected
```

`init` and `join` both set it. `octo-pod set-supply airgap|connected` changes it, for the
same reason every other declaration has a verb: a value that must be edited by hand is one
that gets edited wrongly, and the failure surfaces three steps later.

**There is no default.** A pod that does not say is a pod nobody decided about, and
guessing produces exactly the current behaviour: reach outward, fail on credentials, blame
the registry. `apply` should refuse and name the two commands.

### What each posture derives

Everything currently spread across flags becomes a consequence:

| Derived | airgap | connected |
|---|---|---|
| `image_mirror` | `localhost:5000` | empty, meaning each image's own registry |
| on-pod registry | started, always. It is part of the pod | not started |
| image pull | from the mirror only. **Reaching elsewhere is an error** | from upstream |
| `octo-pod upgrade` | refuses, and says to re-run the installer | works |
| a missing image | "not in this pod's registry. Rebuild the bundle" | "unreachable, or unauthorized" |

The third row is the whole point. Today an airgapped pod with no declared mirror will
happily try ghcr, and the only thing stopping it is that ghcr says no. **A declared
posture makes that attempt itself the fault**, caught before any network call, with a
message about the bundle rather than about credentials.

### Airgap means refuse, not prefer

Concretely, under `supply: airgap`:

- Image references resolve through `localhost:5000` and nowhere else. A reference that
  does not is a bug in the derivation, not a fallback.
- Before starting anything, assert every needed digest is in the local registry. That
  assert exists today and is good; under a declared posture its message can stop guessing
  which of three situations the operator is in.
- Any task that would fetch over the network is skipped with a reason, not attempted and
  recovered. A skipped task is legible; a caught exception is not.
- `octo-pod upgrade` refuses rather than trying and failing, and names the carried path.

That last one is a real behaviour change. It is correct: an airgapped pod that can upgrade
itself over the network was not airgapped.

### What this deletes

- `bootstrap_mode`, which described the same thing transiently.
- The `set-mesh --mirror` step from the runbook, which exists only because the posture had
  nowhere to live.
- The registry probe added on 2026-08-19, which infers at converge time what the pod
  should simply have been told. It was the right fix for a system with no posture and is
  redundant in one that has it.
- Two of the three branches in the "cannot get" message, because the posture already says
  which situation this is.

## Alternatives considered

| Alternative | Why not |
|---|---|
| **Keep `bootstrap_mode` and persist it** | Persisting the flag makes it durable without making it authoritative: `image_mirror` could still disagree, and then which wins? The problem is two fields, not where one of them lives. |
| **Infer from whether a local registry is running** | What was built on 2026-08-19, and it works, but it converges on a symptom. A registry running for some other reason is now the supply policy, and a registry that failed to start silently changes the pod's posture. Inference is the right answer only while there is nothing to ask. |
| **Per-artifact settings** | Images carried, software fetched, keys carried. Every combination is a path, most paths never run, and today's ten defects were all paths that never ran. |
| **Default to airgap** | Safer than defaulting to connected, and still a guess. A pod that never stated its posture is one nobody thought about, and the failure of a wrong guess arrives later and further away than a refusal to start. |

## Open questions

**Is `connected` one posture or two?** A pod pulling from ghcr and a pod pulling from a
site registry it can reach differ in credentials and in blast radius, but not in shape.
`mirrored` may be a third posture or may just be `connected` with a mirror set. Deciding
needs a site with more than one pod, which does not exist yet.

**What does a posture change mean for a running pod?** Moving connected to airgap needs a
bundle to arrive first, and the reverse needs credentials. Probably `set-supply` should
refuse when the new posture's preconditions are not met, which makes it the first
declaration that validates against the world rather than only against itself.

**Does the mesh setup key belong here?** It is carried today under both postures, so the
posture does not govern it. It may still belong in the same sentence in the docs, since an
operator preparing an airgapped build wants one list of everything to carry.

## What this makes possible

An operator preparing a site can be handed one list: **these artifacts, this box, no
network.** Nothing in the build then asks for anything that is not on the list, and
anything missing is named as missing from the bundle rather than as a failure to reach a
registry the site was never going to reach.

That is the deployment octo-pod is actually for. Today it is the one the system describes
worst.
