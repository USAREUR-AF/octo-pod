#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
Offline tests for the generated pen config. No pod, no network, no docker.

WHY THIS EXISTS
    The pen ACL is the most dangerous file in this repo: it is machine-generated, it is
    read once at router startup, and every way it can be wrong is silent. A denied put
    logs nothing on either side; an unscoped ACL subject is a WILDCARD that hands every
    CA-rooted cert first-party write. Neither shows up in --check --diff, which only
    tells you the file changed, not what the change means.

    So these tests assert on the SEMANTICS of the rendered output — the expanded set of
    (cert-CN, message, flow, key-expr, permission) grants — not on its text. That is what
    lets someone reformat, re-comment or restructure the template and know they changed
    nothing that matters.

    Each assertion below maps to a fault that actually happened on this pod.

RUN
    python3 tests/test_templates.py          # from platform/
"""
import json
import os
import pathlib
import re
import subprocess
import sys

try:
    import yaml
    from jinja2 import Environment, FileSystemLoader, Template
except ImportError:                                          # pragma: no cover
    sys.exit("needs pyyaml + jinja2:  pip install pyyaml jinja2")

HERE = pathlib.Path(__file__).resolve().parent
PLATFORM = HERE.parent
sys.path.insert(0, str(PLATFORM.parent / "bin"))
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))
        FAILURES.append(label)


# ----------------------------------------------------------------- JSON5 → dict
def json5_to_obj(text):
    """
    The router config is JSON5: // comments, unquoted keys, trailing commas. Converted
    here rather than taking a dependency, so CI needs nothing but pyyaml + jinja2.
    String-aware so a // inside a value is never mistaken for a comment.
    """
    out, i, in_str, esc = [], 0, False, False
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word, k = text[i:j], j
            while k < len(text) and text[k] in " \t":
                k += 1
            # A bare identifier followed by ':' is a key. Anything else (true/false/null)
            # is a literal and must be left alone. Keys occur inline, not just at line start.
            out.append(f'"{word}"' if k < len(text) and text[k] == ":" else word)
            i = j
            continue
        out.append(c)
        i += 1
    s = re.sub(r",(\s*[}\]])", r"\1", "".join(out))           # trailing commas
    return json.loads(s)


def render(name, ctx):
    env = Environment(loader=FileSystemLoader(str(PLATFORM / "templates")),
                      trim_blocks=True, keep_trailing_newline=True)   # trim_blocks: ansible default
    env.filters["regex_replace"] = lambda s, p, r="": re.sub(p, r, str(s))
    # Ansible's tests: `match` anchors at the start, `search` anywhere.
    env.tests["match"] = lambda s, p: re.match(p, str(s)) is not None
    env.tests["search"] = lambda s, p: re.search(p, str(s)) is not None
    return env.get_template(name).render(**ctx)


def load_ctx():
    ctx = yaml.safe_load((HERE / "fixtures.yml").read_text())

    def resolve(v):
        if isinstance(v, str) and "{{" in v:
            return Template(v).render(**ctx)
        if isinstance(v, list):
            return [resolve(x) for x in v]
        if isinstance(v, dict):
            return {k: resolve(x) for k, x in v.items()}
        return v

    for _ in range(5):
        ctx = {k: resolve(v) for k, v in ctx.items()}
    # configure-octo-pod.yml provisions only enabled publishers; mirror that here.
    ctx["active_peers"] = [p for p in ctx["peers"] if p["enabled"]]
    ctx["active_arms"] = [a for a in ctx["arms"] if a["enabled"]]
    ctx["active_egress"] = [e for e in ctx.get("egress", []) if e["enabled"]]
    return derive_downlink(ctx)


def derive_downlink(ctx):
    """active_peer_readers and downlink_users, as group_vars derives them.
    check_downlink_users_parity renders the real group_vars expressions against the same
    inputs and compares, so this copy cannot drift from what a pod computes."""
    ctx["active_peer_readers"] = [p for p in ctx["active_peers"]
                                  if p.get("read_keys") and (p.get("release") or {}).get("from_fabric")]
    ctx["downlink_users"] = ctx["active_egress"] + ctx["active_peer_readers"]
    return ctx


def load_shipped_ctx(**overrides):
    """The ACL as a REAL pod gets it: platform/group_vars, not fixtures.

    fixtures.yml deliberately carries different values so a test cannot accidentally
    pass against production data — which is right, and which also meant the ACL suite
    never once looked at the list of first-party identities the product actually ships.
    A dormant component sat in that list with rights to read every publisher's
    namespace, on every pod, and every check here passed.

    So: same assertions, run twice. Once on synthetic inputs to prove the mechanism,
    once on the shipped defaults to prove the VALUES.
    """
    gv = yaml.safe_load((HERE.parent / "group_vars" / "all" / "10-infrastructure.yml").read_text())
    ctx = dict(gv)
    # What a brand-new pod has: an address, a name, and nothing declared. This is the
    # state every pod passes through, so it is the one worth pinning.
    # inventory_hostname, because identities derived from it render malformed without
    # one. The health publisher's CN is octo-health-<pod>, and a context with no pod name
    # produced "octo-health-" — which this suite then dutifully compared against a golden
    # file, proving a malformed identity was stable rather than that it was right.
    ctx.update({"mesh_ip": "127.0.0.1", "pen_id": "a-new-pod",
                "inventory_hostname": "a-new-pod",
                "peers": [], "arms": [], "egress": []})
    ctx.update(overrides)
    # Lists group_vars derives from other lists. Plain Jinja renders each to a string, so
    # `a + b` would concatenate text; derive_downlink() computes them as lists instead, and
    # check_downlink_users_parity proves that computation matches ansible's own.
    for derived in ("active_peer_readers", "downlink_users", "active_zenoh_egress"):
        ctx.pop(derived, None)

    def resolve(v):
        if isinstance(v, str) and "{{" in v:
            return yaml.safe_load(Template(v).render(**ctx)) if v.strip().startswith("{{") \
                   and v.strip().endswith("}}") and "[" in v else Template(v).render(**ctx)
        if isinstance(v, list):
            return [resolve(x) for x in v]
        if isinstance(v, dict):
            return {k: resolve(x) for k, x in v.items()}
        return v

    for _ in range(5):
        ctx = {k: resolve(v) for k, v in ctx.items()}
    ctx["active_peers"] = []
    ctx["active_arms"] = []
    ctx["active_egress"] = []
    return derive_downlink(ctx)


def grants(acl):
    """Expand policies into concrete (cn, message, flow, key, permission) tuples.

    Flow matters and is easy to get wrong by eye: put/delete on INGRESS means the subject
    may publish into that namespace; on EGRESS it is only the router delivering matched
    samples out to a subscriber. Conflating them reads as 'first party can forge into a
    partner's namespace' when it cannot.
    """
    out = set()
    for pol in acl["policies"]:
        for sn in pol["subjects"]:
            subj = next((s for s in acl["subjects"] if s["id"] == sn), None)
            if subj is None:
                out.add(f"DANGLING-SUBJECT:{sn}")
                continue
            cns = subj.get("cert_common_names") or ["*WILDCARD*"]
            for rn in pol["rules"]:
                rule = next((r for r in acl["rules"] if r["id"] == rn), None)
                if rule is None:
                    out.add(f"DANGLING-RULE:{rn}")
                    continue
                for cn in cns:
                    for m in rule["messages"]:
                        for f in rule["flows"]:
                            for k in rule["key_exprs"]:
                                out.add(f"{cn} | {m} | {f} | {k} | {rule['permission']}")
    return out


def main():
    ctx = load_ctx()
    print("\nACL — templates/pen/router-acl.json5.j2")
    raw = render("pen/router-acl.json5.j2", ctx)

    try:
        cfg = json5_to_obj(raw)
        check("renders valid JSON5 (a malformed ACL stops the router dead)", True)
    except Exception as e:                                   # noqa: BLE001
        check("renders valid JSON5 (a malformed ACL stops the router dead)", False, str(e))
        print("\n".join(f"  {i+1:3d}| {l}" for i, l in enumerate(raw.splitlines())))
        return finish()

    acl = cfg["access_control"]
    check("default_permission is deny", acl.get("default_permission") == "deny",
          f"got {acl.get('default_permission')!r}")
    check("access control is enabled", acl.get("enabled") is True)

    # 2026-08-10: a subject with cert_common_names omitted is a WILDCARD in Zenoh —
    # every CA-rooted cert got first-party write. Config was valid and logged nothing.
    unscoped = [s["id"] for s in acl["subjects"] if not s.get("cert_common_names")]
    check("every ACL subject is CN-scoped (unscoped == wildcard)", not unscoped,
          f"unscoped: {unscoped}")

    g = grants(acl)
    check("no dangling rule/subject references",
          not [x for x in g if x.startswith("DANGLING")],
          str([x for x in g if x.startswith("DANGLING")]))

    # A publisher must never inherit first-party rights.
    fp = next(s for s in acl["subjects"] if s["id"] == "mtls-firstparty")
    leaked = [p["cert_cn"] for p in ctx["active_peers"] + ctx["active_arms"]
              if p["cert_cn"] in fp["cert_common_names"]]
    check("no publisher CN sits in mtls-firstparty", not leaked, f"leaked: {leaked}")

    # Each publisher writes its own namespace and nothing else.
    for p in ctx["active_peers"] + ctx["active_arms"]:
        ns = p["namespace"]
        writes = {x.split(" | ")[3] for x in g
                  if x.startswith(p["cert_cn"] + " | ")
                  and x.split(" | ")[1] in ("put", "delete")
                  and x.split(" | ")[2] == "ingress"}
        check(f"{p['id']}: ingress-write scoped to {ns}", writes == {ns}, f"got {writes}")
        reads = {x.split(" | ")[3] for x in g
                 if x.startswith(p["cert_cn"] + " | ")
                 and x.split(" | ")[1] in ("declare_subscriber", "query")}
        want = ({f"downlink/{k}" for k in p.get("read_keys") or []}
                if (p.get("release") or {}).get("from_fabric") else set())
        if want:
            check(f"{p['id']}: reads exactly downlink/<its released read_keys>", reads == want,
                  f"got {reads}, want {want}")
        elif p.get("read_keys"):
            check(f"{p['id']}: declared reads but not released: no read grant", not reads, f"got {reads}")
        else:
            check(f"{p['id']}: no read grant on anything", not reads, f"got {reads}")

    # A peer's read grant binds to ITS OWN policy entry only — never to the subject
    # whose namespace it reads (bravo must not gain anything from india's grant),
    # and nowhere else.
    read_rule_policies = [pol for pol in acl["policies"] if "india-read-interest" in pol["rules"]]
    check("india's read rules bind only to india's own subject",
          len(read_rule_policies) == 1 and read_rule_policies[0]["subjects"] == ["mtls-india"],
          str(read_rule_policies))
    bravo_policies = [pol for pol in acl["policies"] if "mtls-bravo-arm" in pol["subjects"]]
    check("bravo's own policy gains nothing from india's read grant",
          all("india" not in r for pol in bravo_policies for r in pol["rules"]),
          str(bravo_policies))

    # Disabled publishers must be invisible everywhere.
    disabled = [p for p in ctx["peers"] + ctx["arms"] if not p["enabled"]]
    for p in disabled:
        check(f"{p['id']} (enabled:false) absent from the ACL",
              p["id"] not in raw and p["cert_cn"] not in raw)

    golden = HERE / "golden" / "acl-grants.txt"
    if golden.exists():
        want = set(golden.read_text().split("\n")) - {""}
        added, removed = sorted(g - want), sorted(want - g)
        check("expanded grant set matches golden", not added and not removed,
              "added:\n    " + "\n    ".join(added) + "\n  removed:\n    " + "\n    ".join(removed))
    else:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text("\n".join(sorted(g)) + "\n")
        print(f"  ----  wrote golden file ({len(g)} grants) — review it, then commit")

    check_egress(ctx, raw, acl, g)

    print("\npen quadlets — one unit per declaration")
    router = render("pen/octo-pen-zenoh-router.container.j2", ctx)
    # Sinks do not retry a failed session. Under compose the gate was
    # depends_on: service_healthy; under Quadlet it is Notify=healthy on the
    # router (started == listener answers) + After/PartOf on every sink.
    check("router gates on its listener (Notify=healthy + HealthCmd)",
          "Notify=healthy" in router and "HealthCmd=" in router)
    check("router image comes from the pinned var", ctx["zenoh_image"] in router)

    def render_sink(suffix, key, sub_id):
        return render("pen/octo-pen-audit-sink.container.j2",
                      dict(ctx, sink_suffix=suffix, sink_key=key, sink_sub_id=sub_id))

    # Zenoh is brokerless: an unobserved put is gone, not queued. Every namespace
    # that can be written must have a sink watching it — one unit per declaration.
    for p_ in ctx["active_peers"] + ctx["active_arms"]:
        sink = render_sink("-" + p_["id"], p_["namespace"], "testpod-" + p_["id"])
        check(f"{p_['id']}: sink subscribes {p_['namespace']}",
              f"Environment=AUDIT_SUBSCRIBER_KEY={p_['namespace']}\n" in sink)
        check(f"{p_['id']}: sink restarts WITH the router (PartOf — the orphan-sink repair, structural)",
              "PartOf=octo-pen-zenoh-router.service" in sink)
        # On host-net its peer listener collides with the router on :7447.
        check(f"{p_['id']}: sink not on host networking", "Network=host" not in sink)

    # Per-declaration files make absence structural: the play renders sinks from
    # the ACTIVE set only, and a reap task removes units no declaration names.
    play = (PLATFORM / "configure-octo-pod.yml").read_text()
    check("play renders sinks from the active set only",
          "loop: \"{{ active_peers + active_arms }}\"" in play)
    check("play reaps sink units no declaration names",
          "reap sink units no declaration names" in play)
    for p_ in disabled:
        check(f"{p_['id']} (enabled:false) gets no unit by construction", True)

    print("\narm quadlets — templates/arm/octo-arm.container.j2 (one template, every arm)")
    # The env contract lives in ONE template (it was env.j2; the compose-era
    # whole-file interpolation hazard died with compose, so an arm now carries
    # only its own kind's variables — asserted per kind below).
    _envp = {"udp-ingest": "UDP_INGEST", "tcp-ingest": "TCP_INGEST", "tcp-connect": "TCP_CONNECT"}
    for a in ctx["active_arms"]:
        unit = render("arm/octo-arm.container.j2", dict(ctx, a=a))
        kind = a.get("kind") or ("udp-ingest" if a.get("transport") == "udp" else "tcp-ingest")
        ep = _envp[kind]
        env_vals = dict(l[len("Environment="):].split("=", 1)
                        for l in unit.splitlines() if l.startswith("Environment="))
        prefix = a["namespace"].removesuffix("/**")
        check(f"{a['id']}: key prefix matches the ACL grant",
              env_vals.get(f"{ep}_KEY_PREFIX") == prefix, f"got {env_vals.get(ep + '_KEY_PREFIX')!r}")
        check(f"{a['id']}: allow_sources present (an arm's only access control)",
              env_vals.get(f"{ep}_ALLOW_SOURCES") == a["allow_sources"])
        check(f"{a['id']}: no unrendered Jinja left", "{{" not in unit)
        imgline = next((l for l in unit.splitlines() if l.startswith("Image=")), "")
        check(f"{a['id']}: image is a digest-pinned reference",
              "@sha256:" in imgline, f"got {imgline!r}")
        # Everything the arm binary requires, set and non-empty, for ITS kind.
        required = [f"{ep}_FEEDS", f"{ep}_KEY_PREFIX", f"{ep}_ENDPOINT",
                    f"{ep}_CERT", f"{ep}_KEY", f"{ep}_ROOT_CA"]
        if kind != "tcp-connect":
            required.append(f"{ep}_BIND_HOST")
        missing = [v for v in required if not env_vals.get(v, "").strip()]
        check(f"{a['id']}: every variable its kind demands is set",
              not missing, f"missing/empty: {missing}")
        # A dial-out arm must dial. Rendering it as a listener is not a degraded
        # mode: two servers and no connection presents as a dead feed.
        if a.get("kind") == "tcp-connect":
            check(f"{a['id']}: dial-out feed names the sensor's target",
                  env_vals.get("TCP_CONNECT_FEEDS", "").startswith(f"{a['feed']}={a['target']}"),
                  f"got {env_vals.get('TCP_CONNECT_FEEDS')!r}")
            check(f"{a['id']}: dial-out arm declares no listener at all",
                  "TCP_INGEST_FEEDS" not in env_vals and f"{ep}_BIND_HOST" not in env_vals)

    # Arms are pulled, never built on the pod. A pod installed from the package
    # has no arms/ source, so nothing deployable may carry a build reference.
    print("\narms are pulled, not built on the pod")
    repo = HERE.parent.parent
    armt = (PLATFORM / "templates" / "arm" / "octo-arm.container.j2").read_text()
    check("arm quadlet takes its image from the pinned vars, never a build",
          "Image={{ _image }}" in armt and "build" not in armt.lower())
    check("a source-build path still exists for arm development (podman build, gated)",
          "podman build" in play)
    # Every image reference must be digest-pinned. A tag is a mutable pointer, so whoever
    # controls it controls what every pod runs at its next apply. This check used to live
    # in the package build; the package is gone, so it lives here, where CI runs it.
    # Fixtures are excluded: they carry readable fake tags on purpose.
    print("\nimage references are digest-pinned")
    refs = set()
    for f in list((repo / "arms").rglob("Dockerfile")) \
             + list((repo / "platform" / "group_vars").rglob("*.yml")):
        for line in f.read_text().splitlines():
            line = line.split("#")[0].replace('"', "")
            line = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-", " ", line).replace("}", "")
            if "FROM" in line or "image:" in line:
                refs |= set(re.findall(r"[a-zA-Z0-9._-]+(?:\.[a-zA-Z]{2,})?/[a-zA-Z0-9._/-]+"
                                       r"(?:@sha256:[a-f0-9]+|:[a-zA-Z0-9._-]+)", line))
    # Named, self-clearing exception. The arm images cannot be pinned until images.yml
    # has run once and produced a digest, and there is no point failing every push until
    # then. Naming them means a THIRD floating reference still fails, and pinning these
    # two makes the exception dissolve on its own.
    # Empty, and it stays empty. The three arm images were published multi-arch by
    # images.yml and pinned by digest; an entry here is a licence to ship a mutable tag
    # to a pod, so it should exist only between a first build and the commit that pins it.
    # Empty, and it stays empty. An entry here is a licence to ship a mutable tag to a
    # pod, so it should exist only between a first build and the commit that pins it.
    PENDING_FIRST_PUBLISH = set()
    floating = sorted(r for r in refs if "@sha256:" not in r)
    unexpected = [r for r in floating if r not in PENDING_FIRST_PUBLISH]
    check(f"all {len(refs)} image references pinned by digest (or a named exception)",
          not unexpected, "floating: " + ", ".join(unexpected))
    still_pending = [r for r in floating if r in PENDING_FIRST_PUBLISH]
    if still_pending:
        print(f"  NOTE  {len(still_pending)} arm image(s) await the first images.yml run:")
        for r in still_pending:
            print(f"          {r}")
        print("        Paste the digests into group_vars and delete them from")
        print("        PENDING_FIRST_PUBLISH in this file.")

    # The platform layer ships UNCHANGED to every deployment. An address written into it
    # is therefore handed to every site, including sites that are not part of that
    # fabric — which is how one deployment's mesh, upstream and package-repo addresses
    # came to sit in group_vars. Where the fabric is belongs to the identity layer
    # (host_vars/<pod>/05-fabric.yml), which reaches one pod.
    #
    # Only platform/ is scanned: it is the layer that carries configuration. CLI help
    # text needs illustrative addresses and is prose, not config.
    print("\nthe platform layer names no deployment")
    # 100.64.0.0 is NetBird's default overlay range (RFC 6598 shared address space), the
    # same on every mesh unless a deployment changes it through mesh_cidr: a default, not
    # one deployment's address.
    ALLOWED_IPS = {"0.0.0.0", "127.0.0.1", "255.255.255.255", "100.64.0.0"}
    stray = []
    for f in sorted(PLATFORM.rglob("*.yml")) + sorted(PLATFORM.rglob("*.j2")):
        rel = f.relative_to(PLATFORM)
        if str(rel).startswith("tests/"):
            continue                       # fixtures carry synthetic addresses on purpose
        for n, line in enumerate(f.read_text().splitlines(), 1):
            for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", line):
                if ip not in ALLOWED_IPS:
                    stray.append(f"{rel}:{n}  {ip}")
    # THE README SHIPS TOO. It is in the package payload, so it reaches every site — and
    # an operator's username and their pod's address got into it exactly that way, in an
    # example command. The address guard above only scanned platform/, on the reasoning
    # that it is "the layer that carries configuration"; the README carries instructions,
    # which people copy, and a real host in one is a real host handed to every reader.
    readme = PLATFORM.parent / "README.md"
    leaked = []
    if readme.exists():
        for n, line in enumerate(readme.read_text().splitlines(), 1):
            for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", line):
                if not (ip.startswith(("127.", "10.", "192.0.2.", "198.51.100.", "203.0.113."))
                        or ip in ("0.0.0.0", "255.255.255.255")):
                    leaked.append(f"README.md:{n} {ip}")
            # user@host in a copyable command is the same leak wearing a different hat
            for m in re.findall(r"\b([a-z][a-z0-9_-]{2,})@(?:<|\d)", line):
                if m not in ("user", "operator", "root"):
                    leaked.append(f"README.md:{n} {m}@… (a real account)")
    check("no real hosts or usernames in the README (it ships to every site)",
          not leaked, "; ".join(leaked[:5]))

    check("no site addresses under platform/ (loopback and 0.0.0.0 only)", not stray,
          "\n          ".join(stray))

    # The same rule for the four values that name hosts rather than addresses. Empty
    # here, supplied by the token, guarded at each point of use.
    gv = yaml.safe_load((PLATFORM / "group_vars/all/10-infrastructure.yml").read_text())
    for var, empty in (("fabric_endpoint", ""), ("fabric_host_pins", []),
                       ("fabric_portal_url", ""), ("netbird_mgmt_host", ""),
                       ("octo_repo_url", "")):
        check(f"{var} is empty in the platform layer (it belongs to the identity)",
              gv.get(var) == empty, f"got {gv.get(var)!r}")

    # Several of the things a pod runs unattended are SHELL SCRIPTS GENERATED FROM
    # TEMPLATES — cert renewal, the mesh self-heal, the deferred-enrolment hook. CI
    # shellchecks bin/, but nothing looked at these, so a Jinja edit could ship a script
    # that does not parse to every pod in the fleet and only fail on a timer at 3am.
    print("\ngenerated shell scripts parse")
    shell_templates = {
        "pen/cert-renew.sh.j2": dict(cert_renew_days=60, cert_leaf_days=825),
        "pen/cert-expiry-check.sh.j2": dict(cert_expiry_warn_days=90),
        "host/netbird-selfheal.sh.j2": dict(selfheal_reboot_after_fails=10),
        "host/octo-watchdog.sh.j2": dict(),
        "host/netbird-enrol-pending.sh.j2": dict(octo_repo_url="https://pkgs.example.invalid"),
    }
    for name, extra in shell_templates.items():
        try:
            rendered = render(name, dict(ctx, **extra))
        except Exception as e:                                   # noqa: BLE001
            check(f"{name} renders", False, str(e))
            continue
        r = subprocess.run(["bash", "-n"], input=rendered, text=True, capture_output=True)
        check(f"{name} renders to valid bash", r.returncode == 0, r.stderr.strip())

    play = (repo / "platform" / "configure-octo-pod.yml").read_text()
    # A clone builds (it has arms/); a package pulls (it does not). The playbook must
    # decide from the filesystem, not from a flag an operator has to know about.
    check("playbook detects clone vs package", "_arm_src" in play and "Cargo.toml" in play)
    check("build only when the arm source is present AND the image is not pinned",
          "podman build" in play
          and "when: _arm_from_src" in play
          and "_arm_src.stat.exists and not _arm_pinned" in play,
          "the source build must depend on both: a packaged pod has no arms/ to build "
          "from, and a digest-pinned image cannot be built at all — a digest names a "
          "specific set of bytes, and building is how you would make different ones")

    # Source-detection alone is a guess about how the software arrived, and it guessed
    # wrong on a real pod: a box that had once been an rsync install still had
    # /opt/octo-pod/arms beside the playbooks, so a packaged pod tried to build a digest.
    # The image reference is a fact about what was asked for, so it decides.
    # A mirror that rewrites four of five references is worse than none: the pod starts,
    # four images come from the mirror, and the fifth silently reaches the internet — on
    # the box that has no internet. Assert every one goes through the same rule.
    import re as _re
    from jinja2 import Environment as _Env
    _e = _Env(); _e.filters["regex_replace"] = lambda s, p, r="": _re.sub(p, r, str(s))
    gv = yaml.safe_load((repo / "platform/group_vars/all/10-infrastructure.yml").read_text())
    _refs = ["zenoh_image", "audit_image", "udp_ingest_image", "tcp_ingest_image", "tcp_connect_image"]
    for mirror in ("", "localhost:5000"):
        rendered = {}
        for v in _refs:
            up = gv[f"{v}_upstream"]
            rendered[v] = _e.from_string(str(gv[v])).render(
                image_mirror=mirror, **{f"{v}_upstream": up}).strip()
        if mirror:
            wrong = [v for v, r in rendered.items() if not r.startswith(mirror + "/")]
            check("every image goes through image_mirror when one is set",
                  not wrong, f"not mirrored: {wrong}")
        else:
            wrong = [v for v, r in rendered.items() if r != gv[f"{v}_upstream"]]
            check("no mirror set means each image keeps its own registry",
                  not wrong, f"changed with no mirror: {wrong}")
        undigested = [v for v, r in rendered.items() if "@sha256:" not in r]
        check(f"the digest survives the rewrite (mirror={mirror or 'none'})",
              not undigested, f"lost the digest: {undigested}")

    check("a digest pin forces the pull path",
          "is search('@sha256:')" in play,
          "nothing checks whether the image is digest-pinned before choosing to build")

    # ---------------------------------------------------------------- shipped defaults
    print("\nacl — the SHIPPED group_vars, on a pod with nothing declared")
    for arm_enabled, label in ((False, "legacy arm off (the default)"),
                               (True, "legacy arm on")):
        sctx = load_shipped_ctx(arm_enabled=arm_enabled)
        sacl = json5_to_obj(render("pen/router-acl.json5.j2", sctx))["access_control"]
        fp = next(x for x in sacl["subjects"] if x["id"] == "mtls-firstparty")
        cns = set(fp["cert_common_names"])

        # First-party means: write our namespaces AND read every publisher's. So the
        # question this answers is "who can read everything on a pod nobody configured",
        # and the answer must be the pod's own infrastructure and nothing else.
        want = {"octo-pen-router", "octo-uplink-bridge"}
        if arm_enabled:
            want.add("octo-arm-sapient")
        # The pod's own health publisher is present on every pod by default. It is NOT
        # first-party: it writes one key and reads nothing, which is why it appears in the
        # actor set below and not in the first-party CN set above.
        if sctx.get("health_pub_enabled"):
            want_actors = want | {sctx["health_pub_cn"]}
        else:
            want_actors = want
        check(f"first-party CNs, {label}: {sorted(want)}", cns == want,
              f"got {sorted(cns)} — an identity here reads EVERY publisher's namespace")

        # And the leaf certs minted must match: a key on disk for something with no
        # grant is a liability, and a grant with no key is a hole waiting for one.
        leaf_cns = {c["cn"] for c in sctx["pen_leaf_certs"]}
        check(f"leaf certs minted, {label}: no key without a reason",
              leaf_cns <= cns | {"octo-pen-router"},
              f"minted {sorted(leaf_cns)} but first-party is {sorted(cns)}")

        sg = grants(sacl)
        actors = {x.split(" | ")[0] for x in sg}
        check(f"no unexpected actor holds any grant, {label}", actors <= want_actors,
              f"unexpected: {sorted(actors - want_actors)}")

    sgolden = HERE / "golden" / "acl-grants-default-pod.txt"
    sctx = load_shipped_ctx(arm_enabled=False)
    sg = grants(json5_to_obj(render("pen/router-acl.json5.j2", sctx))["access_control"])
    if sgolden.exists():
        want = set(sgolden.read_text().split("\n")) - {""}
        added, removed = sorted(sg - want), sorted(want - sg)
        check("default-pod grant set matches golden", not added and not removed,
              "added:\n    " + "\n    ".join(added) + "\n  removed:\n    " + "\n    ".join(removed))
    else:
        sgolden.parent.mkdir(parents=True, exist_ok=True)
        sgolden.write_text("\n".join(sorted(sg)) + "\n")
        print(f"  ----  wrote {sgolden.name} ({len(sg)} grants) — review it, then commit")

    check_every_task_is_tagged()
    check_dirs_created_before_written()
    check_every_notify_has_a_handler()
    check_playbook_jinja_parses()
    check_removal_deletes_every_key_the_playbook_writes()
    check_egress_removal_deletes_every_key_the_playbook_writes()
    check_slot_trust()
    check_egress_cli()
    check_egress_key_rule_parity()
    check_session_destinations_run_only_when_released()
    check_sapient_declaration_rules()
    check_declare_set_firewall()
    check_bootstrap_runtime_floor()
    check_watchdog_drift(ctx)
    check_arm_release_slot()
    check_ca_roots_pin_in_playbook()
    check_image_carries_what_the_playbooks_install()
    check_firewall(ctx)
    check_firewall_mesh_port_discovery()
    check_firewall_mesh_mgmt_addresses()
    check_downlink_users_parity()
    check_peer_reads(ctx)
    check_peer_cli()
    check_peer_declaration_gate()
    check_the_cli_never_prints_a_setup_key()
    return finish()


def playbook_tasks(path, tasks=None, inherited=None):
    """Every leaf task in a playbook, with the tags in force on it.

    Blocks pass their tags down, which is how the file is written, so a task inside a
    tagged block counts as tagged even with no tags: of its own."""
    if tasks is None:
        tasks = (yaml.safe_load(path.read_text()) or [{}])[0].get("tasks") or []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tags = t.get("tags", inherited)
        nested = [k for k in ("block", "rescue", "always") if k in t]
        if nested:
            for k in nested:
                yield from playbook_tasks(path, t[k], tags)
            continue
        yield t.get("name", "<unnamed>"), tags


def leaf_tasks(tasks):
    """Every leaf task in file order, blocks flattened."""
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        nested = [k for k in ("block", "rescue", "always") if k in t]
        if nested:
            for k in nested:
                yield from leaf_tasks(t[k])
            continue
        yield t


def check_dirs_created_before_written():
    """A directory a playbook creates is created before anything writes into it.

    copy, template and get_url create the file, never the directory above it. When the
    box already has the directory the order never shows, which is how the journald
    drop-in shipped written-before-created: stock Ubuntu 24.04 has no
    /etc/systemd/journald.conf.d, and bootstrap died on the first box to run it after the
    Podman migration. A task that creates a directory says the play cannot count on it
    existing, so every write into it has to come after that task."""
    print("\nplaybooks — directories created before they are written")
    for name in ("bootstrap-octo-pod.yml", "configure-octo-pod.yml"):
        tasks = list(leaf_tasks((yaml.safe_load((HERE.parent / name).read_text()) or [{}])[0]
                                .get("tasks")))
        created = {}
        for i, t in enumerate(tasks):
            a = t.get("ansible.builtin.file") or t.get("file")
            if isinstance(a, dict) and a.get("state") == "directory":
                path = str(a.get("path") or a.get("dest") or "").rstrip("/")
                if path:
                    created.setdefault(path, i)
        # podman, unlike docker, refuses a bind-mount source that does not exist, so a
        # host path given to `podman run -v` must be created by an earlier task.
        unmade = []
        for i, t in enumerate(tasks):
            cmd = (t.get("ansible.builtin.command") or t.get("command")
                   or t.get("ansible.builtin.shell") or t.get("shell"))
            cmd = cmd.get("cmd", "") if isinstance(cmd, dict) else cmd
            if not isinstance(cmd, str) or "podman run" not in cmd:
                continue
            for host in re.findall(r"(?:-v|--volume)[ =]+\"?(/[^\s\":]+|\{\{[^}]+\}\}):", cmd):
                if created.get(host.rstrip("/"), len(tasks)) > i:
                    unmade.append(f"'{t.get('name')}' mounts {host}, which no earlier task creates")
        check(f"{name}: every podman run bind mount is created before the run", not unmade,
              "\n    ".join(unmade))
        late = []
        for i, t in enumerate(tasks):
            for mod in ("copy", "template", "get_url", "lineinfile", "blockinfile"):
                a = t.get(f"ansible.builtin.{mod}") or t.get(mod)
                if not isinstance(a, dict):
                    continue
                dest = str(a.get("dest") or a.get("path") or "").rstrip("/")
                parent = dest.rsplit("/", 1)[0]
                if "{{" not in dest and created.get(parent, -1) > i:
                    late.append(f"'{t.get('name')}' writes {dest} before "
                                f"'{tasks[created[parent]].get('name')}' creates {parent}")
        check(f"{name}: each directory it creates exists before its first write", not late,
              "\n    ".join(late))


def check_playbook_jinja_parses():
    """Every Jinja expression in the playbooks and group_vars parses.

    --syntax-check reads the YAML and never templates a task's arguments, so a broken
    expression waits for the first run that reaches it. "the base image's job" did: an
    apostrophe inside a single-quoted Jinja string ended the string early, and bootstrap
    died at a status message. Parsing needs no variables, so every expression is checked,
    including conditions, which Ansible wraps in {{ }} itself."""
    import jinja2
    env = jinja2.Environment(extensions=["jinja2.ext.do", "jinja2.ext.loopcontrols"])
    conditions = {"when", "failed_when", "changed_when", "until"}
    bad = []

    def visit(node, where, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                visit(v, where, k)
        elif isinstance(node, list):
            for v in node:
                visit(v, where, key)
        elif isinstance(node, str):
            templated = "{{" in node or "{%" in node or "{#" in node
            src = node if templated else ("{{ " + node + " }}" if key in conditions else None)
            if src is not None:
                try:
                    env.parse(src)
                except jinja2.TemplateSyntaxError as e:
                    bad.append(f"{where} ({key}): {e.message}: {node.strip()[:100]!r}")

    files = sorted(HERE.parent.glob("*.yml")) + sorted((HERE.parent / "group_vars").rglob("*.yml"))
    print(f"\nplaybooks — every Jinja expression parses ({len(files)} files)")
    for f in files:
        visit(yaml.safe_load(f.read_text()), f.relative_to(HERE.parent))
    check("every expression in the playbooks and group_vars parses", not bad, "\n    ".join(bad))


def check_every_notify_has_a_handler():
    """Every `notify:` names a handler, or a handler's `listen:`, in the same play.

    Ansible stops the whole play at a notify with no handler, and only when the notifying
    task CHANGES something, so a missing handler hides until the first box where that task
    has work to do. Bootstrap's journald caps notified 'restart journald', which only
    configure defined; the first run that wrote the caps died on it."""
    print("\nplaybooks — every notify has a handler")
    for pb in sorted((HERE.parent).glob("*.yml")):
        plays = yaml.safe_load(pb.read_text()) or []
        if not isinstance(plays, list):
            continue
        for p in (p for p in plays if isinstance(p, dict) and "tasks" in p):
            handlers = list(leaf_tasks(p.get("handlers")))
            known = {h.get("name") for h in handlers}
            for h in handlers:
                lst = h.get("listen")
                known |= {lst} if isinstance(lst, str) else set(lst or [])
            missing = []
            for t in leaf_tasks(p.get("tasks")):
                n = t.get("notify")
                for name in [n] if isinstance(n, str) else (n or []):
                    if name not in known:
                        missing.append(f"'{t.get('name')}' notifies '{name}'")
            check(f"{pb.name}: every notify has a handler", not missing, "\n    ".join(missing))


def check_every_task_is_tagged():
    """No task may be untagged. This is not tidiness — it is the bug of 2026-08-17.

    The CLI never runs these playbooks whole: `octo-pod join` runs bootstrap with
    --tags netbird and then --tags images, and each verb drives its own subset. A task
    with no tag is therefore invisible to every one of those paths, and Ansible filters
    it out SILENTLY — unlike a `when:` miss, a tag miss prints no "skipping" line and
    leaves no trace in the output at all.

    'netbird — enrol now' lost its tag that way. join probed the management server,
    found it reachable, enrolled nothing, printed "on the mesh", and the direct
    enrolment path had never run on any pod. Nothing failed, so nothing was noticed.

    Tagging every task makes an omission a test failure instead of a silent no-op.

    WHICH playbooks this applies to is read out of bin/ rather than listed here, so the
    rule follows the code: the day someone drives write-identity.yml with --tags, that
    playbook starts being checked without anyone remembering to add it. A playbook only
    ever run whole is not held to this — an untagged task there is not a trap."""
    print("\nplaybooks — every task reachable by tag")
    driven = tag_driven_playbooks()
    check("the tag-driven playbook set was found", bool(driven),
          "no 'ansible-playbook … --tags' found in bin/ — if the CLI stopped using tags "
          "this check is dead and should go; if bin/ moved, this test is looking in the "
          "wrong place and is silently passing")
    for name in sorted(driven):
        pb = HERE.parent / name
        if not pb.exists():
            continue
        untagged = [n for n, tags in playbook_tasks(pb) if not tags]
        check(f"no untagged task in {name}", not untagged,
              f"{name} is run with --tags by the CLI, so these are silently filtered "
              "out and never execute:\n    " + "\n    ".join(untagged))


def check_egress(ctx, raw, acl, g):
    """The downward path: what may come down, who may read it, and the units that run it.

    The same two-gate discipline as the upward path. A destination reads ONLY its own
    downlink keys and only once released; the downlink bridge writes ONLY the downlink
    namespace; a shut or disabled destination is invisible. Asserted on the expanded
    grant set, not on template text, so a refactor that keeps the words and changes the
    meaning still fails here.
    """
    print("\negress — templates/pen/router-acl.json5.j2, downward grants")
    ns = "downlink/**"

    def pen_key(k):
        return "downlink/" + k

    for e in ctx["active_egress"]:
        cn = e["cert_cn"]
        mine = {x for x in g if x.startswith(cn + " | ")}
        if (e.get("release") or {}).get("from_fabric"):
            want = set()
            for k in e["keys"]:
                want |= {f"{cn} | declare_subscriber | ingress | {pen_key(k)} | allow",
                         f"{cn} | put | egress | {pen_key(k)} | allow",
                         f"{cn} | delete | egress | {pen_key(k)} | allow"}
            check(f"{e['id']} (released): reads exactly its own downlink keys",
                  mine == want, f"got {sorted(mine)}")
        else:
            check(f"{e['id']} (shut): no grant and no subject",
                  not mine and cn not in raw, f"got {sorted(mine)}")
    for e in [e for e in ctx.get("egress", []) if not e["enabled"]]:
        check(f"{e['id']} (enabled:false, even if released) absent from the ACL",
              e["id"] not in raw and e["cert_cn"] not in raw)

    bridge = {x for x in g if x.startswith("octo-downlink-bridge | ")}
    check("downlink bridge writes only the downlink namespace, and reads nothing",
          bridge == {f"octo-downlink-bridge | put | ingress | {ns} | allow",
                     f"octo-downlink-bridge | delete | ingress | {ns} | allow"},
          f"got {sorted(bridge)}")
    fp = next(s for s in acl["subjects"] if s["id"] == "mtls-firstparty")
    leaked = [e["cert_cn"] for e in ctx.get("egress", [])
              if e["cert_cn"] in fp["cert_common_names"]] + \
             (["octo-downlink-bridge"] if "octo-downlink-bridge" in fp["cert_common_names"] else [])
    check("no egress or downlink CN sits in mtls-firstparty", not leaked, f"leaked: {leaked}")
    check("the audit sink (CN octo-pen-router) watches the downlink namespace",
          f"octo-pen-router | declare_subscriber | ingress | {ns} | allow" in g)
    up_reads = {x for x in g if x.startswith("octo-uplink-bridge | ") and "downlink/" in x}
    check("the uplink bridge cannot read what came down (no re-export path)",
          not up_reads, f"got {sorted(up_reads)}")

    print("\negress quadlets — one unit per destination, one downlink bridge")
    for e in ctx["active_egress"]:
        unit = render("egress/octo-egress.container.j2", dict(ctx, d=e))
        env = dict(l[len("Environment="):].split("=", 1)
                   for l in unit.splitlines() if l.startswith("Environment="))
        # Both binaries exit 2 only for a refused declaration. Restarting it looped forever
        # and wrote a refusal into the audit record every RestartSec.
        check(f"{e['id']}: a refused declaration (exit 2) stays failed instead of looping",
              "Restart=on-failure" in unit and "RestartPreventExitStatus=2" in unit)
        if e["dest"].startswith("sapient://"):
            sap = e.get("sapient") or {}
            check(f"{e['id']} (SAPIENT session): runs the sapient arm from its pinned image",
                  "Exec=/usr/local/bin/sapient" in unit and f"Image={ctx['sapient_image']}" in unit
                  and "egress-arm" not in unit)
            check(f"{e['id']}: declared keys, one allowed target, as the sensor side",
                  env.get("SAPIENT_EGRESS_KEYS") == ",".join(e["keys"])
                  and env.get("SAPIENT_TARGET") == e["dest"].removeprefix("sapient://")
                  and env.get("SAPIENT_ALLOW_DEST") == e["allow_dest"]
                  and env.get("SAPIENT_ROLE") == "asm"
                  and "SAPIENT_LISTEN" not in env and "SAPIENT_RELAY_KEY" not in env,
                  f"got {env}")
            check(f"{e['id']}: registers as its declared node type, at its declared interval",
                  env.get("SAPIENT_NODE_TYPE") == str(sap.get("node_type", "OTHER")).upper()
                  and env.get("SAPIENT_STATUS_INTERVAL_S") == str(sap.get("status_interval_s", 5)),
                  f"got {env}")
            check(f"{e['id']}: no egress-arm settings leak into the session arm",
                  not [k for k in env if k.startswith("EGRESS_")], f"got {env}")
            check(f"{e['id']}: keeps its node id beside its audit record",
                  env.get("SAPIENT_STATE_DIR") == "/var/lib/octo-egress/state"
                  and env.get("SAPIENT_AUDIT_DIR") == "/var/lib/octo-egress")
            check(f"{e['id']}: stops on SIGINT, so it closes its session and audit record",
                  "--stop-signal=SIGINT" in unit)
            check(f"{e['id']}: mounts only its own TLS and audit directories",
                  f"Volume=/etc/octo-egress-{e['id']}/tls:/etc/octo/tls:ro" in unit
                  and f"Volume={ctx['egress_audit_dir']}/{e['id']}:/var/lib/octo-egress" in unit)
            empty = [k for k, v in env.items() if v == ""]
            check(f"{e['id']}: no environment value renders empty", not empty, f"empty: {empty}")
            continue
        check(f"{e['id']}: runs the egress arm from the pinned image",
              "Exec=/usr/local/bin/egress-arm" in unit and f"Image={ctx['egress_image']}" in unit)
        check(f"{e['id']}: carries its own keys, destination and exact allow list",
              env.get("EGRESS_KEYS") == ",".join(e["keys"])
              and env.get("EGRESS_DEST") == e["dest"]
              and env.get("EGRESS_ALLOW_DEST") == e["allow_dest"]
              and "EGRESS_LANE" not in env, f"got {env}")
        if e["dest"].startswith("tcp-server://"):
            check(f"{e['id']} (consumers dial it): carries its peers, max_clients and replay",
                  env.get("EGRESS_PEERS") == e["allow_sources"].replace(" ", "")
                  and env.get("EGRESS_MAX_CLIENTS") == str(e.get("max_clients", 1))
                  and env.get("EGRESS_REPLAY") == str(e.get("replay", 0)), f"got {env}")
        else:
            check(f"{e['id']} (the pod connects out): no listener settings",
                  not {"EGRESS_PEERS", "EGRESS_MAX_CLIENTS", "EGRESS_REPLAY"} & env.keys(),
                  f"got {env}")
        check(f"{e['id']}: stops on SIGINT, so it closes its session and audit record",
              "--stop-signal=SIGINT" in unit)
        check(f"{e['id']}: mounts only its own TLS and audit directories",
              f"Volume=/etc/octo-egress-{e['id']}/tls:/etc/octo/tls:ro" in unit
              and f"Volume={ctx['egress_audit_dir']}/{e['id']}:/var/lib/octo-egress" in unit)
        empty = [k for k, v in env.items() if v == ""]
        check(f"{e['id']}: no environment value renders empty", not empty, f"empty: {empty}")

    dl = render("egress/octo-downlink-bridge.container.j2", ctx)
    denv = dict(l[len("Environment="):].split("=", 1)
                for l in dl.splitlines() if l.startswith("Environment="))
    check("downlink bridge runs from the same image, as its own binary",
          "Exec=/usr/local/bin/downlink-bridge" in dl and f"Image={ctx['egress_image']}" in dl)
    check("downlink bridge carries downlink_allow, and no lane",
          denv.get("DOWNLINK_ALLOW") == ctx["downlink_allow"] and "DOWNLINK_LANE" not in denv)
    check("downlink bridge's fabric identity is the slot, by anchor",
          denv.get("DOWNLINK_FABRIC_CERT", "").endswith(f"/{ctx['fabric_slot_anchor']}-cert.pem"))

    up = render("bridge/octo-uplink-bridge.container.j2", ctx)
    uenv = dict(l[len("Environment="):].split("=", 1)
                for l in up.splitlines() if l.startswith("Environment="))
    check("uplink bridge is a Quadlet unit carrying bridge_allow",
          uenv.get("BRIDGE_ALLOW") == ctx["bridge_allow"]
          and f"Image={ctx['uplink_bridge_image']}" in up)

    play = (PLATFORM / "configure-octo-pod.yml").read_text()
    code = "\n".join(l for l in play.splitlines() if not l.lstrip().startswith("#"))
    check("no docker command is left in the play (comments may still name history)",
          not re.search(r"\bdocker (compose|run|ps|pull|build|load|rm|stop)\b", code))
    check("the play validates egress declarations as a gate (tags: always)",
          "egress — validate declarations" in play)
    check("the play refuses bridge_allow that would re-export downlink data",
          "downlink/" in play and "bridge_allow" in play
          and "re-export" in play)
    check("the play reaps egress units no declaration names",
          "reap egress units no declaration names" in play)


def check_firewall(ctx):
    """The pod firewall: deny by default in, out and routed; openings only as declared.

    Asserted on the rendered rules, then syntax-checked with `nft -c` where nft exists
    (CI's Linux runner), because a ruleset that does not parse is a pod with no firewall.
    """
    import shutil
    import tempfile

    print("\nfirewall — templates/host/octo-firewall.nft.j2")
    fctx = dict(ctx, lan_iface="eth1", firewall_wan_iface="eth0",
                mgmt_ssh_sources=["192.0.2.14/32"],
                fabric_host_pins=[{"ip": "100.127.0.8", "names": "zenoh.example.invalid"}])
    rules = render("host/octo-firewall.nft.j2", fctx)
    lines = [l.strip() for l in rules.splitlines() if l.strip() and not l.strip().startswith("#")]

    for chain in ("input", "output", "forward"):
        check(f"{chain}: policy drop", re.search(rf"chain {chain} \{{\s*type filter hook {chain} "
                                                  rf"priority filter; policy drop;", rules) is not None)
    check("no chain accepts everything", "accept" not in lines and "policy accept;" not in rules)
    ssh = [l for l in lines if "dport 22" in l]
    check("SSH only from the management set", ssh == ["tcp dport 22 ip saddr @mgmt_ssh accept"], str(ssh))
    check("the management set is the mesh range plus the declared sources",
          "elements = { 100.64.0.0/10, 192.0.2.14/32 }" in rules)

    for a in ctx["active_arms"]:
        if a.get("kind") == "tcp-connect":
            port = a["target"].rsplit(":", 1)[1]
            want = f'oifname "eth1" ip daddr {{ {a["allow_sources"]} }} tcp dport {port} accept'
            check(f"{a['id']} (dial-out): only its sensor's declared address, on the LAN only",
                  want in lines, "\n".join(l for l in lines if f"dport {port}" in l))
        else:
            want = (f'iifname "eth1" ip saddr {{ {a["allow_sources"]} }} '
                    f'{a.get("transport", "udp")} dport {a["port"]} accept')
            check(f"{a['id']}: its port, from its declared source, on the LAN only", want in lines,
                  "\n".join(l for l in lines if str(a["port"]) in l))
    for a in [a for a in ctx["arms"] if not a["enabled"]]:
        check(f"{a['id']} (enabled:false) opens nothing", str(a.get("port", "x")) not in rules)

    # Peers: the mesh opening stands for every peer; a LAN opening exists only for a peer
    # that declares allow_sources, and only from those addresses — never wider.
    port = ctx["pen_listen_port"]
    check("peers: the pen is open over the mesh", f'iifname "wt0" tcp dport {port} accept' in lines)
    want_lan = {f'iifname "eth1" ip saddr {{ {p["allow_sources"].replace(" ", "").replace(",", ", ")} }} '
                f'tcp dport {port} accept'
                for p in ctx["active_peers"] if p.get("allow_sources")}
    got_lan = {l for l in lines if l.startswith('iifname "eth1"') and l.endswith(f"tcp dport {port} accept")}
    check("peers: the pen opens on the LAN for exactly the peers with allow_sources, from them only",
          got_lan == want_lan and want_lan, f"got {got_lan}, want {want_lan}")

    # zenoh:// rides TCP. Rendering the scheme as the protocol gave "zenoh dport", which
    # nft refuses, and a refused ruleset is a pod with no firewall update at all.
    zen = {"id": "kilo", "kind": "zenoh-egress", "enabled": True, "cert_cn": "octo-egress-kilo",
           "dest": "zenoh://192.0.2.50:7447", "allow_dest": "192.0.2.50:7447", "prefix": "raw/goat",
           "allow_prefix": "raw/goat", "keys": ["1d18de66e8a09a5e68170b3b3de6563b/sensors/radar1/asterix/data"],
           "release": {"from_fabric": True}}
    zrules = render("host/octo-firewall.nft.j2", dict(fctx, active_egress=[zen]))
    check("a released zenoh:// destination opens TCP to exactly its address",
          'oifname "eth1" ip daddr 192.0.2.50 tcp dport 7447 accept' in zrules, zrules[-800:])
    check("no rule ever names the scheme as a protocol", "zenoh dport" not in zrules)

    # A dial target may be a hostname. nft resolves a hostname when it loads the ruleset,
    # and the boot unit loads it before the network is up, so one hostname in a rule
    # would fail the boot load and start the pod with no firewall at all.
    named = dict(ctx["active_arms"][0], id="hotel", kind="tcp-connect",
                 target="sensor.example.invalid:7009", allow_sources="192.0.2.40")
    named.pop("port", None)
    hrules = render("host/octo-firewall.nft.j2", dict(fctx, active_arms=[named]))
    hlines = [l.strip() for l in hrules.splitlines() if l.strip() and not l.strip().startswith("#")]
    check("a hostname dial target never reaches a rule (nft would resolve it at boot)",
          not any("sensor.example.invalid" in l for l in hlines))
    check("a hostname dial target opens its declared address instead",
          'oifname "eth1" ip daddr { 192.0.2.40 } tcp dport 7009 accept' in hlines)

    for e in ctx.get("egress", []):
        scheme, hostport = e["dest"].split("://")
        host, port = hostport.rsplit(":", 1)
        if scheme == "tcp-server":
            peers = e["allow_sources"].replace(" ", "").replace(",", ", ")
            rule = f'iifname "eth1" ip saddr {{ {peers} }} ip daddr {host} tcp dport {port} accept'
        else:
            # A SAPIENT session is a TCP connection the pod dials.
            proto = "tcp" if scheme == "sapient" else scheme
            rule = f'oifname "eth1" ip daddr {host} {proto} dport {port} accept'
        if e["enabled"] and (e.get("release") or {}).get("from_fabric"):
            check(f"{e['id']} (released): exactly its destination", rule in lines,
                  "\n".join(l for l in lines if port in l))
            if scheme == "tcp-server":
                inbound = rules.split("chain input", 1)[1].split("chain output", 1)[0]
                check(f"{e['id']} (consumers dial the pod): opened inbound only, from its peers",
                      rule in inbound and f"daddr {host} " not in rules.split("chain output", 1)[1])
        else:
            check(f"{e['id']} ({'disabled' if not e['enabled'] else 'shut'}): no opening",
                  host not in rules)

    check("the fabric router, at its pin, over the mesh only",
          'oifname "wt0" ip daddr 100.127.0.8 tcp dport 7447 accept' in lines)
    disc = render("host/octo-firewall.nft.j2", dict(fctx, firewall_mesh_tcp_ports=[443, 10000, 33073],
                                                     firewall_mesh_udp_ports=[443, 3478, 33073]))
    check("the mesh's control plane and relays: the ports the agent was assigned, on the WAN leg",
          'oifname "eth0" tcp dport { 443, 10000, 33073 } accept' in disc
          and 'oifname "eth0" udp dport { 443, 3478, 33073 } accept' in disc)
    check("before the agent knows its relays: 443 TCP, 443 and 3478 UDP",
          'oifname "eth0" tcp dport { 443 } accept' in rules
          and 'oifname "eth0" udp dport { 443, 3478 } accept' in rules)
    # Found on a lab pod: NetBird sent the mesh's domains to its nameserver on
    # 5353, the firewall allowed DNS on 53 only, and NetBird marked the nameserver down.
    dns = [l for l in lines if "5353" in l]
    check("mesh DNS (53 and NetBird's 5353), over the mesh to mesh addresses only",
          dns == ['oifname "wt0" ip daddr 100.64.0.0/10 udp dport { 53, 5353 } accept',
                  'oifname "wt0" ip daddr 100.64.0.0/10 tcp dport { 53, 5353 } accept'], str(dns))
    # Found on the lab gateway: NetBird's DNS answered the management name with a mesh
    # address first, so with the control plane open on the WAN leg only, a new
    # connection to management was dropped and every ruleset change was refused.
    mgmt = [l for l in lines if l.startswith('oifname "wt0"') and "tcp dport { 443" in l]
    check("no management opening over the mesh unless its name resolved to a mesh address",
          mgmt == [], str(mgmt))
    mrules = render("host/octo-firewall.nft.j2",
                    dict(fctx, firewall_mesh_mgmt_ips=["100.127.0.16", "100.127.0.17"]))
    check("management's mesh addresses: its control ports, to those addresses only, over the mesh",
          'oifname "wt0" ip daddr { 100.127.0.16, 100.127.0.17 } tcp dport { 443 } accept' in mrules)
    mdisc = render("host/octo-firewall.nft.j2",
                   dict(fctx, firewall_mesh_mgmt_ips=["100.127.0.16"],
                        firewall_mesh_tcp_ports=[443, 10000, 33073]))
    check("management's mesh addresses take the control ports the agent was assigned",
          'oifname "wt0" ip daddr { 100.127.0.16 } tcp dport { 443, 10000, 33073 } accept' in mdisc)
    mname = render("host/octo-firewall.nft.j2",
                   dict(fctx, firewall_mesh_mgmt_ips=["mgmt.example.invalid"]))
    check("a management name never reaches a rule (nft would resolve it at boot)",
          "mgmt.example.invalid" not in mname
          and not any(l.strip().startswith('oifname "wt0" ip daddr {') and "tcp dport { 443" in l
                      for l in mname.splitlines()))
    fwd = rules.split("chain forward", 1)[1]
    fwd_accepts = [l.strip() for l in fwd.splitlines() if l.strip().endswith("accept")]
    check("nothing is routed between legs: only container bridges and replies",
          fwd_accepts == ["ct state established,related accept",
                          'iifname "podman*" oifname "podman*" accept'], str(fwd_accepts))

    nft = shutil.which("nft")
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "fw.nft"
        f.write_text(rules)
        apply = pathlib.Path(tmp) / "apply.sh"
        apply.write_text(render("host/octo-firewall-apply.sh.j2", fctx))
        r = subprocess.run(["bash", "-n", str(apply)], capture_output=True, text=True)
        check("the apply-and-verify script parses", r.returncode == 0, r.stderr)
        if shutil.which("shellcheck"):
            r = subprocess.run(["shellcheck", str(apply)], capture_output=True, text=True)
            check("the apply-and-verify script passes shellcheck", r.returncode == 0, r.stdout)
        if nft:
            # nft needs CAP_NET_ADMIN even in check-only mode. Keep the rest of
            # the suite unprivileged; sudo must not prompt on an unattended runner.
            command = [nft, "-c", "-f", str(f)]
            if os.geteuid() != 0 and shutil.which("sudo"):
                command = ["sudo", "-n", *command]
            r = subprocess.run(command, capture_output=True, text=True)
            check("nft -c accepts the ruleset", r.returncode == 0, r.stderr)
        else:
            print("  SKIP  nft -c (no nft here; CI's Linux runner checks it)")

    check_firewall_apply(fctx)


def check_peer_reads(ctx):
    """A peer's released reads, end to end in the rendered config: the downlink comes up for
    peer readers alone, stays down when nothing reads, and each bundle's instructions say
    exactly what the pen grants and where to dial — no more, no less."""
    print("\npeer reads and bundles — router-acl.json5.j2 and peer/bundle-README.md.j2")

    def acl_for(**over):
        c = derive_downlink(dict(ctx, **over))
        return json5_to_obj(render("pen/router-acl.json5.j2", c))["access_control"]

    only_peers = acl_for(egress=[], active_egress=[])
    rules = {r["id"] for r in only_peers["rules"]}
    check("peer readers alone bring the downlink up (bridge may write downlink/**)",
          "downlink-bridge-write" in rules and "india-read-interest" in rules, str(sorted(rules)))
    shut = [dict(x, release=dict(x.get("release") or {}, from_fabric=False)) for x in ctx["active_peers"]]
    nothing = acl_for(egress=[], active_egress=[], active_peers=shut)
    nrules = {r["id"] for r in nothing["rules"]}
    check("nothing released to read and no egress: no downlink grant at all",
          not any(r.startswith("downlink-") or r.endswith("-read-interest") for r in nrules),
          str(sorted(nrules)))

    base = dict(ctx, lan_ip="10.0.0.5", mesh_ip="100.127.0.1")
    by_id = {x["id"]: x for x in ctx["active_peers"]}
    india = render("peer/bundle-README.md.j2", dict(base, item=by_id["india"]))
    check("india's bundle dials the pod's LAN address, not the mesh",
          '"tls/10.0.0.5:7447"' in india and "100.127.0.1" not in india, india[:400])
    check("india's bundle names its released read as the downlink/ key it subscribes to",
          "downlink/1d18de66e8a09a5e68170b3b3de6563b/sensors/sensor2/**" in india
          and "You have no read access" not in india)
    check("india's bundle says which addresses it must dial from", "100.127.0.23, 100.127.0.24" in india)
    alpha = render("peer/bundle-README.md.j2", dict(base, item=by_id["alpha"]))
    check("alpha's bundle (no allow_sources, no reads) dials the mesh and claims no read access",
          '"tls/100.127.0.1:7447"' in alpha and "You have no read access" in alpha)
    juliet = render("peer/bundle-README.md.j2", dict(base, item=by_id["juliet"]))
    check("juliet's bundle (reads declared, not released) claims no read access and names no key",
          "You have no read access" in juliet and "tpq50" not in juliet)
    lanless = render("peer/bundle-README.md.j2", dict(base, lan_ip="", item=by_id["india"]))
    check("a LAN peer on a pod with no lan_ip is told to ask, not handed a wrong address",
          "Ask us for the endpoint" in lanless and "100.127.0.1" not in lanless)


def check_peer_cli():
    """`octo-pod add-peer --from` and `octo-pod peer` end to end on a scratch identity: what a
    peer may reach and read, the shared downward gate with egress, and the hand-off bundle.

    The cross-checks matter most: revoking an egress destination must never pull a key a
    released peer still reads, and revoking a peer's read must never pull one an egress
    destination still needs. Both share downlink_allow."""
    import contextlib
    import io
    import os
    import runpy
    import tarfile
    import tempfile
    import time

    print("\npeer CLI — bin/octo-pod-declare add-peer and bin/octo-pod-peer on a scratch identity")
    bindir = HERE.parent.parent / "bin"
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        pod = root / "etc" / "host_vars" / "pod-x"
        pod.mkdir(parents=True)
        (pod / "00-site.yml").write_text('lan_iface: "eth1"\nlan_ip: "10.1.2.5"\n')
        (pod / "10-pod.yml").write_text('pen_id: "pod-x"\nfabric_slot_anchor: ""\nbridge_allow: "raw/octo/**"\n')
        (pod / "20-peers.yml").write_text("peers: []\n")
        (pod / "30-arms.yml").write_text("arms: []\n")
        (pod / "40-egress.yml").write_text("egress: []\n")
        (pod / "05-fabric.yml").write_text("fabric_endpoint: ''\nfabric_host_pins: []\n")
        scratch = {"OCTO_POD_ETC": root / "etc", "OCTO_PEN_STATE": root / "pen",
                   "OCTO_QUADLET_DIR": root / "quadlet", "OCTO_EGRESS_ETC": root / "egress-etc",
                   "OCTO_PARTNER_BUNDLES": root / "partner"}

        def run(cli, *argv):
            out = io.StringIO()
            saved_env = {k: os.environ.get(k) for k in scratch}
            saved = (os.geteuid, sys.argv, os.getcwd())
            os.environ.update({k: str(v) for k, v in scratch.items()})
            os.geteuid = lambda: 0
            sys.argv = [cli, *argv]
            os.chdir(tmp)
            code = 0
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    runpy.run_path(str(bindir / cli), run_name="__main__")
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                out.write(str(e.code or ""))
            finally:
                for k, v in saved_env.items():
                    os.environ.pop(k, None) if v is None else os.environ.update({k: v})
                os.geteuid, sys.argv = saved[0], saved[1]
                os.chdir(saved[2])
            return code, out.getvalue()

        def peer():
            return {x["id"]: x for x in yaml.safe_load((pod / "20-peers.yml").read_text())["peers"]}["c2"]

        def allow():
            v = yaml.safe_load((pod / "10-pod.yml").read_text()).get("downlink_allow") or ""
            return [k for k in v.split(",") if k]

        k1 = "1d18de66e8a09a5e68170b3b3de6563b/sensors/sensor2/**"
        k2 = "1d18de66e8a09a5e68170b3b3de6563b/sensors/tpq50/**"

        for bad in ("c2.example.invalid", "0.0.0.0", "10.1.2.300"):
            code, out = run("octo-pod-declare", "add-peer", "c2", "--from", bad)
            check(f"add-peer refuses --from {bad}", code != 0
                  and "c2" not in (pod / "20-peers.yml").read_text(), out)
        code, out = run("octo-pod-declare", "add-peer", "c2", "--from", "10.1.2.30")
        check("add-peer --from declares a LAN peer: write-only, its own namespace, its sources",
              code == 0 and peer().get("allow_sources") == "10.1.2.30"
              and peer()["namespace"] == "raw/c2/**" and not peer().get("read_keys"), out)
        check("add-peer points at the next commands (bundle, allow-read)",
              "octo-pod peer bundle c2" in out and "octo-pod peer allow-read c2" in out, out)

        for bad in ("**", "*/sensors/x", "ops/audit/x", "downlink/x", "release/**"):
            code, out = run("octo-pod-peer", "allow-read", "c2", "--key", bad)
            check(f"allow-read refuses a key that reaches too far: {bad}",
                  code != 0 and "refusing these keys" in out and not peer().get("read_keys"), out)
        code, out = run("octo-pod-peer", "allow-read", "ghost", "--key", k1)
        check("allow-read refuses a peer that is not declared", code != 0 and "add-peer ghost" in out, out)
        code, out = run("octo-pod-peer", "allow-read", "c2", "--key", k1)
        check("allow-read opens both gates: read_keys + release.from_fabric, and downlink_allow",
              code == 0 and peer().get("read_keys") == [k1]
              and peer()["release"].get("from_fabric") is True and allow() == [k1], out)
        check("allow-read tells the operator the key the peer subscribes to", f"downlink/{k1}" in out, out)
        check("allow-read keeps the declaration's comments",
              "never peer/router" in (pod / "20-peers.yml").read_text())
        code, out = run("octo-pod-peer", "allow-read", "c2", "--key", k1, "--key", k2)
        check("allow-read again adds without duplicating", code == 0
              and peer()["read_keys"] == [k1, k2] and allow() == [k1, k2], out)

        # An egress destination reading k1 too: revoking IT must not cut the peer off.
        code, _ = run("octo-pod-egress", "add", "relay", "--dest", "udp://10.1.2.20:8600", "--key", k1)
        code2, _ = run("octo-pod-egress", "release", "relay")
        code3, out = run("octo-pod-egress", "revoke", "relay")
        check("egress revoke keeps a key a released peer still reads", code == code2 == code3 == 0
              and k1 in allow(), f"{out} allow={allow()}")
        code, out = run("octo-pod-peer", "check")
        check("peer check: every reader's gates agree (exit 0)", code == 0 and "c2" in out
              and "RELEASED" in out, out)

        # And the reverse: the egress destination released again, the peer revokes k1.
        run("octo-pod-egress", "release", "relay")
        code, out = run("octo-pod-peer", "revoke-read", "c2", "--key", k1)
        check("revoke-read keeps a key an egress destination still needs, and says so",
              code == 0 and peer()["read_keys"] == [k2] and k1 in allow() and "still read" in out,
              f"{out} allow={allow()}")
        code, out = run("octo-pod-peer", "revoke-read", "c2", "--key", "1d18de66e8a09a5e68170b3b3de6563b/never/read")
        check("revoke-read refuses a key the peer does not read", code != 0, out)
        code, out = run("octo-pod-peer", "revoke-read", "c2")
        check("revoke-read with no --key closes all: no read_keys, release.from_fabric false, k2 gone",
              code == 0 and not peer().get("read_keys")
              and peer()["release"].get("from_fabric") is False and k2 not in allow(), f"{out} allow={allow()}")

        # A gate opened by hand, without the declaration, is flagged.
        run("octo-pod-peer", "allow-read", "c2", "--key", k2)
        text = (pod / "10-pod.yml").read_text().replace(k2, "")
        (pod / "10-pod.yml").write_text(text)
        code, out = run("octo-pod-peer", "check")
        check("peer check flags a released read whose key is missing from downlink_allow (exit 1)",
              code == 1 and "DISAGREE" in out, out)

        code, out = run("octo-pod-peer", "allow-from", "c2", "--from", "10.1.2.30, 10.1.2.31")
        check("allow-from replaces the sources", code == 0 and peer()["allow_sources"] == "10.1.2.30, 10.1.2.31", out)
        code, out = run("octo-pod-peer", "allow-from", "c2", "--from", "lan.example.invalid")
        check("allow-from refuses a name", code != 0 and peer()["allow_sources"] == "10.1.2.30, 10.1.2.31", out)
        code, out = run("octo-pod-peer", "allow-from", "c2", "--clear")
        check("allow-from --clear makes the peer mesh-only", code == 0 and "allow_sources" not in peer(), out)

        # The bundle: refused until apply minted it, refused when the declaration is newer
        # than its instructions, then packaged 0600 with exactly the four files.
        code, out = run("octo-pod-peer", "bundle", "c2")
        check("bundle refuses before apply has minted it", code != 0 and "octo-pod apply" in out, out)
        b = root / "pen" / "bundles" / "c2"
        (b / "tls").mkdir(parents=True)
        for f in ("tls/octo-peer-c2-cert.pem", "tls/octo-peer-c2-key.pem", "tls/ca-roots.pem", "README.md"):
            (b / f).write_text(f"synthetic {f}\n")
        old = time.time() - 60
        os.utime(b / "README.md", (old, old))
        code, out = run("octo-pod-peer", "bundle", "c2")
        check("bundle refuses instructions older than the declaration (apply first)",
              code != 0 and "describe the old grant" in out, out)
        os.utime(b / "README.md", None)
        code, out = run("octo-pod-peer", "bundle", "c2", "--out", str(root / "c2.tar.gz"))
        arc = root / "c2.tar.gz"
        names = []
        if arc.exists():
            with tarfile.open(arc) as tar:
                names = sorted(tar.getnames())
        check("bundle packages exactly cert, key, CA and instructions, mode 0600, with a sha256",
              code == 0 and names == ["c2/README.md", "c2/tls/ca-roots.pem",
                                      "c2/tls/octo-peer-c2-cert.pem", "c2/tls/octo-peer-c2-key.pem"]
              and (arc.stat().st_mode & 0o777) == 0o600 and "sha256" in out, f"{out} {names}")


def check_peer_declaration_gate():
    """The playbook's declaration gate, run unmodified by ansible-playbook, on peers: the
    rule that holds when the CLI is bypassed and 20-peers.yml is written by hand."""
    import os
    import shutil
    import tempfile

    print("\npeer declarations — configure's gate, evaluated by ansible-playbook")
    if not shutil.which("ansible-playbook"):
        print("  SKIP  no ansible-playbook here; CI installs ansible-core")
        return
    play = yaml.safe_load((HERE.parent / "configure-octo-pod.yml").read_text())
    task = next(t for t in leaf_tasks(play[0]["tasks"]) if t.get("name") == "egress — validate declarations")
    good = {"id": "c2", "enabled": True, "cert_cn": "octo-peer-c2", "namespace": "raw/c2/**",
            "allow_sources": "10.1.2.30, 10.1.2.31",
            "read_keys": ["1d18de66e8a09a5e68170b3b3de6563b/sensors/sensor2/**"],
            "release": {"from_fabric": True}}

    def gate(peers, arms=()):
        with tempfile.TemporaryDirectory() as tmp:
            pb = pathlib.Path(tmp) / "p.yml"
            pb.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"peers": list(peers), "arms": list(arms), "egress": [], "bridge_allow": "raw/octo/**"},
                "tasks": [task]}], sort_keys=False))
            r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb)],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               env=dict(os.environ, ANSIBLE_STDOUT_CALLBACK="json",
                                        ANSIBLE_LOCALHOST_WARNING="false"))
            try:
                res = json.loads(r.stdout)["plays"][0]["tasks"][-1]["hosts"]["localhost"]
                return not res.get("failed", False)
            except (ValueError, KeyError, IndexError):
                return None

    check("a LAN peer with a released read passes the gate", gate([good]) is True)
    for label, bad in (("a hostname in allow_sources", {"allow_sources": "c2.example.invalid"}),
                       ("a key reaching the whole fabric", {"read_keys": ["**"]}),
                       ("a key in a reserved root", {"read_keys": ["downlink/x"]}),
                       ("read_keys written as a string", {"read_keys": "fixture-publisher/x"})):
        check(f"the gate refuses a peer with {label}", gate([dict(good, **bad)]) is False)
    arm = {"id": "radar9", "enabled": True, "cert_cn": "octo-arm-radar9", "namespace": "raw/radar9/**",
           "read_keys": ["1d18de66e8a09a5e68170b3b3de6563b/x"]}
    check("the gate refuses an arm with read_keys (no identity to read with)", gate([], [arm]) is False)


def check_downlink_users_parity():
    """active_peer_readers and downlink_users, evaluated by ansible-playbook from the real
    group_vars expressions, must match the harness's derive_downlink() — the copy every
    template test renders with. Without this, the harness could pass against a rule the
    pod does not actually compute."""
    import os
    import shutil
    import tempfile

    print("\ndownlink users — group_vars evaluated by ansible-playbook vs the harness")
    if not shutil.which("ansible-playbook"):
        print("  SKIP  no ansible-playbook here; CI installs ansible-core")
        return
    gv = yaml.safe_load((HERE.parent / "group_vars" / "all" / "10-infrastructure.yml").read_text())
    fx = yaml.safe_load((HERE / "fixtures.yml").read_text())
    cases = {
        "fixtures": (fx["peers"], fx.get("egress", [])),
        "peer readers only, no egress": (fx["peers"], []),
        "an empty read_keys list is no reader": (
            [{"id": "k", "enabled": True, "read_keys": [], "release": {"from_fabric": True}}], []),
        "nothing declared": ([], []),
    }
    for label, (peers, egress) in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            pb = pathlib.Path(tmp) / "p.yml"
            pb.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"peers": peers, "egress": egress,
                         **{k: gv[k] for k in ("active_peers", "active_egress",
                                               "active_peer_readers", "downlink_users")}},
                "tasks": [{"name": "out", "ansible.builtin.debug": {
                    "msg": "{{ {'readers': active_peer_readers | map(attribute='id') | list, "
                           "'users': downlink_users | map(attribute='id') | list} }}"}}]}],
                sort_keys=False))
            r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb)],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               env=dict(os.environ, ANSIBLE_STDOUT_CALLBACK="json",
                                        ANSIBLE_LOCALHOST_WARNING="false"))
            try:
                got = json.loads(r.stdout)["plays"][0]["tasks"][-1]["hosts"]["localhost"]["msg"]
            except (ValueError, KeyError, IndexError):
                got = {"error": (r.stdout + r.stderr)[-600:]}
        ctx = derive_downlink({"active_peers": [p for p in peers if p.get("enabled")],
                               "active_egress": [e for e in egress if e.get("enabled")]})
        want = {"readers": [p["id"] for p in ctx["active_peer_readers"]],
                "users": [x["id"] for x in ctx["downlink_users"]]}
        check(f"{label}: ansible and the harness agree on who reads the downlink",
              got == want, f"ansible {got}, harness {want}")


def check_firewall_mesh_port_discovery():
    """The ports configure reads from `netbird status --json`, evaluated by Ansible itself.

    Run through ansible-playbook rather than plain Jinja on purpose: the two disagree on
    escapes in string literals ('\\1' is a backreference to Ansible and \\x01 to plain
    jinja2), and these expressions are all regex_replace backreferences."""
    import os
    import shutil
    import tempfile

    print("\nfirewall — mesh ports read from the agent (evaluated by ansible-playbook)")
    if not shutil.which("ansible-playbook"):
        print("  SKIP  no ansible-playbook here; CI installs ansible-core")
        return
    play = yaml.safe_load((HERE.parent / "configure-octo-pod.yml").read_text())
    task = next(t for t in leaf_tasks(play[0]["tasks"])
                if t.get("name") == "firewall — the mesh's control-plane and relay ports, from the agent")
    status = {"management": {"url": "https://mgmt.example.invalid:443", "connected": True},
              "signal": {"url": "https://signal.example.invalid:10000", "connected": True},
              "relays": {"details": [{"uri": "stun:relay.example.invalid:3478"},
                                     {"uri": "turn:turn.example.invalid:3479?transport=udp"},
                                     {"uri": "rels://relay.example.invalid:33073"},
                                     {"uri": "rels://relay2.example.invalid"}]}}

    def evaluate(nb):
        with tempfile.TemporaryDirectory() as tmp:
            pb = pathlib.Path(tmp) / "p.yml"
            pb.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"pod_firewall_enabled": True, "netbird_relay_ports": [443], "_fw_nb": nb},
                "tasks": [task, {"name": "out", "ansible.builtin.debug": {
                    "msg": "{{ {'tcp': firewall_mesh_tcp_ports, 'udp': firewall_mesh_udp_ports} }}"}}]}],
                sort_keys=False))
            r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb)],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               env=dict(os.environ, ANSIBLE_STDOUT_CALLBACK="ansible.posix.json",
                                        ANSIBLE_LOCALHOST_WARNING="false"))
            try:
                tasks = json.loads(r.stdout)["plays"][0]["tasks"]
                return tasks[-1]["hosts"]["localhost"]["msg"]
            except (ValueError, KeyError, IndexError):
                return {"error": (r.stdout + r.stderr)[-600:]}

    got = evaluate({"rc": 0, "stdout": json.dumps(status)})
    check("joined: TCP = management, signal and relays; UDP = relays (QUIC), STUN, TURN",
          got == {"tcp": [443, 10000, 33073], "udp": [443, 3478, 3479, 33073]}, str(got))
    got = evaluate({"rc": 1, "stdout": ""})
    check("not joined yet: netbird_relay_ports and the fixed defaults only",
          got == {"tcp": [443], "udp": [443, 3478]}, str(got))


def check_firewall_mesh_mgmt_addresses():
    """The management's mesh addresses, as configure resolves them, evaluated by
    ansible-playbook. IP literals resolve to themselves, so no DNS is needed; the one
    name used ends in .invalid, which never resolves."""
    import os
    import shutil
    import tempfile

    print("\nfirewall — the management's mesh addresses (evaluated by ansible-playbook)")
    if not shutil.which("ansible-playbook"):
        print("  SKIP  no ansible-playbook here; CI installs ansible-core")
        return
    play = yaml.safe_load((HERE.parent / "configure-octo-pod.yml").read_text())
    names = ("firewall — the mesh addresses the management name resolves to",
             "firewall — open the management's mesh addresses, if it has any")
    tasks = [t for t in leaf_tasks(play[0]["tasks"]) if t.get("name") in names]
    check("configure resolves and keeps the management's mesh addresses", len(tasks) == 2,
          str([t.get("name") for t in tasks]))
    if len(tasks) != 2:
        return

    def evaluate(nb, mgmt_host=""):
        with tempfile.TemporaryDirectory() as tmp:
            pb = pathlib.Path(tmp) / "p.yml"
            pb.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"pod_firewall_enabled": True, "mesh_cidr": "100.64.0.0/10",
                         "netbird_mgmt_host": mgmt_host, "_fw_nb": nb},
                "tasks": tasks + [{"name": "out", "ansible.builtin.debug": {
                    "msg": "{{ firewall_mesh_mgmt_ips }}"}}]}], sort_keys=False))
            r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb)],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               env=dict(os.environ, ANSIBLE_STDOUT_CALLBACK="ansible.posix.json",
                                        ANSIBLE_LOCALHOST_WARNING="false"))
            try:
                tasks_out = json.loads(r.stdout)["plays"][0]["tasks"]
                return tasks_out[-1]["hosts"]["localhost"]["msg"]
            except (ValueError, KeyError, IndexError):
                return {"error": (r.stdout + r.stderr)[-600:]}

    def agent(url):
        return {"rc": 0, "stdout": json.dumps({"management": {"url": url, "connected": True}})}

    got = evaluate(agent("https://100.127.0.16:443"))
    check("the agent's management resolves inside the mesh range: that address", got == ["100.127.0.16"],
          str(got))
    got = evaluate(agent("https://192.0.2.7:443"))
    check("the agent's management resolves outside the mesh range: no opening", got == [], str(got))
    got = evaluate({"rc": 1, "stdout": ""}, mgmt_host="100.127.0.20")
    check("no agent status yet: netbird_mgmt_host is resolved instead", got == ["100.127.0.20"], str(got))
    got = evaluate(agent("https://mgmt.example.invalid:443"))
    check("a name that does not resolve: no opening, and the play carries on", got == [], str(got))
    got = evaluate({"rc": 1, "stdout": ""})
    check("nothing to resolve: no opening", got == [], str(got))


# Stand-ins for the commands octo-firewall-apply runs. `timeout` plays the network: it
# lets a new TCP connection succeed only to the host/port pairs in STUB_OPEN, which is
# what every check in the script depends on.
_FW_STUBS = {
    "nft": 'echo "$*" >> "$STUB_LOG"\ncase "$*" in "-f "*/new.nft) : > "$STUB_LOG.applied" ;; esac\n',
    "ip": '[ "$STUB_JOINED" = 1 ] && echo "5: wt0    inet 100.127.0.5/16 scope global wt0"\nexit 0\n',
    "netbird": r"""r='[{"uri":"stun:relay.example.invalid:3478","available":true},'
r="$r"'{"uri":"rels://relay.example.invalid:33073","available":true}]'
[ "$STUB_RELAYS" = none ] && r='[]'
printf '{"management":{"url":"https://mgmt.example.invalid:443","connected":%s},"relays":{"details":%s}}\n' \
  "${STUB_MGMT_CONNECTED:-true}" "$r"
""",
    "sleep": "exit 0\n",
    # The network may differ before and after the new ruleset loads (the nft stub marks
    # the load): STUB_OPEN_BEFORE / STUB_OPEN_AFTER, each defaulting to STUB_OPEN.
    "timeout": 'shift\nfor a in "$@"; do last=$a; done\n'
               'open="${STUB_OPEN_BEFORE-$STUB_OPEN}"\n'
               '[ -e "$STUB_LOG.applied" ] && open="${STUB_OPEN_AFTER-$STUB_OPEN}"\n'
               'case "$last" in\n'
               '  *"/dev/tcp/"*) case " $open " in *" ${last#*/dev/tcp/} "*) exit 0 ;; esac; exit 1 ;;\n'
               'esac\nexec "$@"\n',
}


def check_firewall_apply(fctx):
    """octo-firewall-apply keeps a ruleset only when every check opens a new connection,
    restores the previous one otherwise, and leaves the firewall untouched when a joined
    pod cannot be checked. Run against stubs, so it needs neither root nor a mesh.
    """
    import shutil
    import tempfile

    print("\nfirewall — octo-firewall-apply decisions (stubbed nft, ip, netbird and network)")
    bash = shutil.which("bash")
    if subprocess.run([bash, "-c", "(( BASH_VERSINFO[0] * 10 + BASH_VERSINFO[1] >= 44 ))"]).returncode:
        print("  SKIP  needs bash 4.7 or later (the pod has 5.2)")
        return
    jq = shutil.which("jq")
    mgmt, relay, router = "mgmt.example.invalid/443", "relay.example.invalid/33073", "100.127.0.8/7447"

    def run(joined, open_, connected="true", with_jq=True, previous="old ruleset\n", relays="present",
            before=None, after=None):
        tmp = pathlib.Path(tempfile.mkdtemp())
        stubs = tmp / "bin"
        stubs.mkdir()
        for name, body in _FW_STUBS.items():
            (stubs / name).write_text("#!/bin/sh\n" + body)
            (stubs / name).chmod(0o755)
        for name in ("bash", "grep", "install", "sed") + (("jq",) if with_jq else ()):
            (stubs / name).symlink_to(shutil.which(name))
        new, live, log = tmp / "new.nft", tmp / "live.nft", tmp / "nft.log"
        new.write_text("new ruleset\n")
        if previous is not None:
            live.write_text(previous)
        script = tmp / "apply.sh"
        script.write_text(render("host/octo-firewall-apply.sh.j2", dict(fctx, pod_firewall_file=str(live))))
        env = {"PATH": str(stubs), "STUB_LOG": str(log), "STUB_JOINED": "1" if joined else "0",
               "STUB_OPEN": " ".join(open_), "STUB_MGMT_CONNECTED": connected, "STUB_RELAYS": relays}
        if before is not None:
            env["STUB_OPEN_BEFORE"] = " ".join(before)
        if after is not None:
            env["STUB_OPEN_AFTER"] = " ".join(after)
        r = subprocess.run([bash, str(script), str(new)], env=env, capture_output=True, text=True)
        calls = log.read_text().splitlines() if log.exists() else []
        out = {"rc": r.returncode, "calls": calls, "loaded": f"-f {new}" in calls,
               "restored": "delete table inet octo_pod" in calls,
               "live": live.read_text() if live.exists() else None, "err": r.stderr}
        shutil.rmtree(tmp)
        return out

    if jq:
        o = run(True, [mgmt, relay, router])
        check("joined, management, a relay and the router take new connections: kept and recorded",
              o["rc"] == 0 and o["loaded"] and not o["restored"] and o["live"] == "new ruleset\n", str(o))
        o = run(True, [relay, router])
        check("joined, agent says connected but a NEW management connection is refused: restored",
              o["rc"] == 1 and o["restored"] and o["live"] == "old ruleset\n"
              and o["calls"][-1].startswith("-f ") and o["calls"][-1].endswith("/live.nft"), str(o))
        o = run(True, [mgmt, relay, router], connected="false")
        check("joined, agent says disconnected: restored",
              o["rc"] == 1 and o["restored"] and o["live"] == "old ruleset\n", str(o))
        # A lab pod's reboot: the relays sat on a port the ruleset did not open, and
        # everything else answered through tunnels that already existed.
        o = run(True, [mgmt, router])
        check("joined, management answers but every relay is refused: restored",
              o["rc"] == 1 and o["restored"] and o["live"] == "old ruleset\n", str(o))
        o = run(True, [mgmt, router], relays="none")
        check("joined, the agent lists no relays: kept",
              o["rc"] == 0 and o["loaded"] and o["live"] == "new ruleset\n", str(o))
        o = run(True, [], before=[mgmt, relay, router], after=[mgmt, relay])
        check("joined, the router answered before the change and not after: restored",
              o["rc"] == 1 and o["restored"] and o["live"] == "old ruleset\n", str(o))
        # A lab pod after its reboot: the router was unreachable BECAUSE of the
        # old ruleset; the fix must not be rolled back for not having fixed it instantly.
        o = run(True, [], before=[mgmt], after=[mgmt, relay])
        check("joined, the router was not answering before (outage): not required after",
              o["rc"] == 0 and o["loaded"] and o["live"] == "new ruleset\n", str(o))
    else:
        print("  SKIP  the joined-pod cases (no jq here; the pod has it)")
    o = run(True, [mgmt, router], with_jq=False)
    check("joined, but the mesh cannot be checked (no jq): the new ruleset is never loaded",
          o["rc"] == 1 and not o["loaded"] and not o["restored"] and o["live"] == "old ruleset\n", str(o))
    o = run(False, [router], with_jq=False)
    check("not joined, router answers: kept without a mesh check",
          o["rc"] == 0 and o["loaded"] and o["live"] == "new ruleset\n", str(o))
    o = run(False, [], before=[router], after=[])
    check("not joined, the router answered before and not after: restored",
          o["rc"] == 1 and o["restored"] and o["live"] == "old ruleset\n", str(o))
    o = run(False, [])
    check("not joined, the router never answered: not required, kept",
          o["rc"] == 0 and o["loaded"] and o["live"] == "new ruleset\n", str(o))
    o = run(False, [], previous=None, before=[router], after=[])
    check("first apply fails with no previous ruleset: the table is removed, nothing reloaded",
          o["rc"] == 1 and o["restored"] and o["live"] is None and o["calls"][-1] == "delete table inet octo_pod",
          str(o))



# ------------------------------------------------------------- slot bundles, signed
# Two synthetic CA roots (O=Example), so a CA roots file holds two certificates the way a
# real goatnet's does. Public material only; the keys that made them were not kept.
TEST_ROOT_PEM = """-----BEGIN CERTIFICATE-----
MIIBqDCCAU+gAwIBAgIUXdxyEXdOxovUG0epj6giHVcGM3wwCgYIKoZIzj0EAwIw
KTEQMA4GA1UECgwHRXhhbXBsZTEVMBMGA1UEAwwMVGVzdCBSb290IENBMCAXDTI2
MTAxMjE2NDkwOFoYDzIxMjYwOTE4MTY0OTA4WjApMRAwDgYDVQQKDAdFeGFtcGxl
MRUwEwYDVQQDDAxUZXN0IFJvb3QgQ0EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNC
AAQYGcEivyDtMXeKj9KIF6SQS5XVkQgCVC0wPu/L74lEWlp2fdsFLo+Dy0XSEgVr
5emXqPpJ4gXVW55ZGTA9we6Ao1MwUTAdBgNVHQ4EFgQUyy3eJosIbjhKNIFhAdnc
hrCilUUwHwYDVR0jBBgwFoAUyy3eJosIbjhKNIFhAdnchrCilUUwDwYDVR0TAQH/
BAUwAwEB/zAKBggqhkjOPQQDAgNHADBEAiAIF5J8t/qubGSEczDYKK6QQXS7B/wE
xSJTr4ZgrCEwhAIgGJOPCeaTBHu4COfGV/Q5mmpCpLcd09ram93NuicMJAw=
-----END CERTIFICATE-----
"""
OTHER_ROOT_PEM = """-----BEGIN CERTIFICATE-----
MIIBrDCCAVGgAwIBAgIUAbFggw8ZzEl51YaX1F38bvJ7nTkwCgYIKoZIzj0EAwIw
KjEQMA4GA1UECgwHRXhhbXBsZTEWMBQGA1UEAwwNT3RoZXIgUm9vdCBDQTAgFw0y
NjEwMTIxNjQ5MDhaGA8yMTI2MDkxODE2NDkwOFowKjEQMA4GA1UECgwHRXhhbXBs
ZTEWMBQGA1UEAwwNT3RoZXIgUm9vdCBDQTBZMBMGByqGSM49AgEGCCqGSM49AwEH
A0IABAXOOckUpDpK8ycN/djF/Ad/hCMk9rN2LIPZt46Q51c0K1BIKZiZEV7wAL3t
S3lxsFwUdiTNsVKBJ0D+5URlOwajUzBRMB0GA1UdDgQWBBRAEJPKVwtYangMly2y
LC/B7YHXxTAfBgNVHSMEGDAWgBRAEJPKVwtYangMly2yLC/B7YHXxTAPBgNVHRMB
Af8EBTADAQH/MAoGCCqGSM49BAMCA0kAMEYCIQDzflfMt57mC9MiKkWjf90g9uBO
QhlnPETc7dsTKIXNNQIhALNrYibbZ/pB4nPx5/My7yxnxihxssAjJKGzripp/04D
-----END CERTIFICATE-----
"""
# A P-256 public key, and the fingerprint `goat ca ls` (goat 0.5.13) printed for it after
# `goat ca add`. octo-pod must compute the same value, or an operator's fingerprint from
# the goatnet would never match.
GOAT_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEGBnBIr8g7TF3io/SiBekkEuV1ZEI
AlQtMD7vy++JRFpadn3bBS6Pg8tF0hIFa+Xpl6j6SeIF1VueWRkwPcHugA==
-----END PUBLIC KEY-----
"""
GOAT_KEY_FINGERPRINT = "SHA256:f5c8eea0615f1579b8a638fcc36a6eb548e50e4f90bd07183e413d0dffffedbf"
SLOT_ANCHOR = "0123456789abcdef0123456789abcdef"


def cbor(x):
    """Canonical CBOR for the synthetic signed profiles: shortest heads, and map keys sorted
    by their encoded bytes, which is how GOAT's issuer writes a bundle."""
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | ai]) + n.to_bytes(size, "big")
        raise ValueError(n)
    if isinstance(x, bool):
        return b"\xf5" if x else b"\xf4"
    if isinstance(x, int):
        return head(0, x) if x >= 0 else head(1, -1 - x)
    if isinstance(x, bytes):
        return head(2, len(x)) + x
    if isinstance(x, str):
        return head(3, len(x.encode())) + x.encode()
    if isinstance(x, list):
        return head(4, len(x)) + b"".join(cbor(i) for i in x)
    pairs = sorted((cbor(k), cbor(v)) for k, v in x.items())
    return head(5, len(pairs)) + b"".join(k + v for k, v in pairs)


class SignedSlots:
    """Synthetic GOAT bundles, signed the way GOAT signs them by a throwaway P-256 key that
    openssl makes for this run. The slot's own key file is placeholder text."""

    def __init__(self, where):
        import base64
        import hashlib
        self.dir = pathlib.Path(where)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.key = self.dir / "signer.pem"
        subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout",
                        "-out", str(self.key)], check=True, capture_output=True)
        self.pub = subprocess.run(["openssl", "ec", "-in", str(self.key), "-pubout"],
                                  check=True, capture_output=True, text=True).stdout
        der = base64.b64decode("".join(ln for ln in self.pub.splitlines() if "-----" not in ln))
        self.fingerprint = "SHA256:" + hashlib.sha256(der[-65:]).hexdigest()

    def make(self, name, roots=None, endpoint="tls/zenoh.example.invalid:7447",
             notes_endpoint=None, expires_at=None, signed=True, roots_name="fabric-ca-root.pem",
             anchor=SLOT_ANCHOR):
        import time
        slot = self.dir / name
        slot.mkdir()
        roots = TEST_ROOT_PEM + OTHER_ROOT_PEM if roots is None else roots
        cert, key = f"synthetic certificate for {anchor}\n".encode(), b"placeholder, not a key\n"
        (slot / f"{anchor}-cert.pem").write_bytes(cert)
        (slot / f"{anchor}-key.pem").write_bytes(key)
        (slot / f"{anchor}-key.pem").chmod(0o600)
        (slot / roots_name).write_text(roots)
        (slot / "NOTES.md").write_text(f"Router endpoint:     {notes_endpoint or endpoint}\n")
        if signed:
            now = int(time.time())
            profile = {
                "version": 1, "device_id": "pod-x", "ca_id": "test-ca", "issued_at": now - 60,
                "activation_deadline": now + 86400,
                "expires_at": now + 90 * 86400 if expires_at is None else expires_at,
                "goat_cli_extras": {
                    "portal_endpoint": "https://portal.example.invalid",
                    "portal_token": b"synthetic token", "router_endpoint": endpoint,
                    "mtls_cert": cert, "mtls_key": key, "ca_roots": roots.encode()}}
            sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.key)],
                                 input=cbor(profile), check=True, capture_output=True).stdout
            (slot / f"{anchor}-goat-cli.cbor").write_bytes(cbor({**profile, "signature": sig}))
            (slot / f"{anchor}-goat-cli-ca.pem").write_text(self.pub)
        return slot


def check_slot_trust():
    """bin/octo_pod_slot.py: nothing in a bundle is trusted until the key that signs it
    matches the operator's fingerprint, the signature holds and the files are the ones
    it covers. Synthetic bundles, signed by a key openssl makes for this run."""
    import hashlib
    import importlib.util
    import shutil
    import tempfile
    import time

    print("\nslot trust - bin/octo_pod_slot.py on synthetic signed bundles")
    if not shutil.which("openssl"):
        print("  SKIP  no openssl here; CI and every pod have it")
        return
    spec = importlib.util.spec_from_file_location(
        "octo_pod_slot", HERE.parent.parent / "bin" / "octo_pod_slot.py")
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)

    def refused(fn, *words):
        try:
            fn()
        except lib.SlotError as exc:
            return all(w in str(exc) for w in words), str(exc)
        return False, "accepted"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "goat.pem").write_text(GOAT_KEY_PEM)
        fp, _ = lib.signer_key(tmp / "goat.pem")
        check("the signing key's fingerprint is the one `goat ca ls` prints for it",
              lib.shown(fp) == GOAT_KEY_FINGERPRINT, lib.shown(fp))
        (tmp / "goat-hdr.pem").write_text(GOAT_KEY_PEM.replace(
            "-----BEGIN PUBLIC KEY-----\n", "-----BEGIN PUBLIC KEY-----\nCA-ID: test-ca\n\n"))
        check("a CA-ID header in the key file leaves the fingerprint unchanged",
              lib.signer_key(tmp / "goat-hdr.pem")[0] == fp)
        (tmp / "root.pem").write_text(TEST_ROOT_PEM)
        ok, why = refused(lambda: lib.signer_key(tmp / "root.pem"), "public key")
        check("a certificate is not taken for the signing key", ok, why)

        for given in (GOAT_KEY_FINGERPRINT, GOAT_KEY_FINGERPRINT.upper(),
                      fp, ":".join(fp[i:i + 2] for i in range(0, 64, 2)),
                      f"  sha256 Fingerprint={fp.upper()}  "):
            check(f"normalize accepts {given[:24]}...", lib.normalize(given) == fp)
        for bad in (fp[:-1], fp + "0", "SHA256:" + "g" * 64, ""):
            ok, why = refused(lambda b=bad: lib.normalize(b), "not a SHA-256 fingerprint")
            check(f"normalize refuses {bad[:20]!r}...", ok, why)

        s = SignedSlots(tmp / "a")
        other = SignedSlots(tmp / "b")
        roots = hashlib.sha256((TEST_ROOT_PEM + OTHER_ROOT_PEM).encode()).hexdigest()
        try:
            slot = lib.slot_material(s.make("good"))
        except lib.SlotError as exc:
            check("slot_material reads a whole signed bundle", False, str(exc))
            return
        check("slot_material finds the CA roots, the profile and its signer, and never takes "
              "the signer for the roots", slot.ca.name == "fabric-ca-root.pem"
              and slot.profile.name == f"{SLOT_ANCHOR}-goat-cli.cbor"
              and slot.signer.name == f"{SLOT_ANCHOR}-goat-cli-ca.pem", str(slot))
        t = lib.trust(slot, expected=s.fingerprint)
        check("the operator's fingerprint, a valid signature and matching files: trusted, "
              "two CA roots and all", t.ca_fingerprint == lib.normalize(s.fingerprint)
              and t.roots_sha256 == roots and t.endpoint == "tls/zenoh.example.invalid:7447"
              and not t.warnings, str(t))
        ok, why = refused(lambda: lib.trust(slot), "no fingerprint", s.fingerprint, "--ca-fingerprint")
        check("no fingerprint, no pin and no terminal: refused, naming the fingerprint to check",
              ok, why)
        ok, why = refused(lambda: lib.trust(slot, expected=other.fingerprint), "not the")
        check("a fingerprint that is not the signing key's: refused", ok, why)
        check("the pinned fingerprint answers for a later release",
              lib.trust(slot, pinned_ca=lib.shown(t.ca_fingerprint)).roots_sha256 == roots)
        ok, why = refused(lambda: lib.trust(slot, pinned_ca=other.fingerprint), "pinned")
        check("a signing key that differs from the pin: refused without --ca-fingerprint", ok, why)
        t2 = lib.trust(slot, expected=s.fingerprint, pinned_ca=other.fingerprint)
        check("a new signing key the operator names replaces the pin, with a warning",
              t2.ca_fingerprint == lib.normalize(s.fingerprint)
              and any("changes from" in w for w in t2.warnings), str(t2.warnings))
        check("at a terminal, yes trusts the key",
              lib.trust(slot, ask=lambda _prompt: "yes").ca_fingerprint == t.ca_fingerprint)
        ok, why = refused(lambda: lib.trust(slot, ask=lambda _prompt: "no"), "not confirmed")
        check("at a terminal, anything but yes refuses", ok, why)

        rotated = lib.slot_material(s.make("rotated", roots=OTHER_ROOT_PEM))
        t3 = lib.trust(rotated, pinned_ca=s.fingerprint, pinned_roots=roots)
        check("new CA roots under the pinned signing key are trusted, with a warning",
              t3.roots_sha256 != roots and any("roots change" in w for w in t3.warnings),
              str(t3.warnings))

        tampered = s.make("tampered")
        f = tampered / f"{SLOT_ANCHOR}-goat-cli.cbor"
        f.write_bytes(f.read_bytes().replace(b"zenoh.example", b"zenoX.example"))
        ok, why = refused(lambda: lib.trust(lib.slot_material(tampered), expected=s.fingerprint),
                          "valid signature")
        check("a profile changed after signing: refused", ok, why)
        swapped = s.make("swapped")
        (swapped / f"{SLOT_ANCHOR}-goat-cli-ca.pem").write_text(other.pub)
        ok, why = refused(lambda: lib.trust(lib.slot_material(swapped), expected=other.fingerprint),
                          "valid signature")
        check("a signing key swapped in beside a profile it did not sign: refused", ok, why)
        for label, name, content in (
                ("certificate", f"{SLOT_ANCHOR}-cert.pem", b"another certificate\n"),
                ("private key", f"{SLOT_ANCHOR}-key.pem", b"another key\n"),
                ("CA roots", "fabric-ca-root.pem", OTHER_ROOT_PEM.encode())):
            changed = s.make(f"changed-{name}")
            (changed / name).write_bytes(content)
            ok, why = refused(lambda c=changed: lib.trust(lib.slot_material(c), expected=s.fingerprint),
                              f"not the {label}")
            check(f"a {label} file that is not the one signed: refused", ok, why)
        expired = lib.slot_material(s.make("expired", expires_at=int(time.time()) - 86400))
        check("an expired profile is trusted with a warning to fetch a fresh one",
              any("expired" in w for w in lib.trust(expired, expected=s.fingerprint).warnings))
        broken = s.make("broken")
        f = broken / f"{SLOT_ANCHOR}-goat-cli.cbor"
        f.write_bytes(f.read_bytes()[:-9])
        ok, why = refused(lambda: lib.trust(lib.slot_material(broken), expected=s.fingerprint),
                          "not a valid signed profile")
        check("a truncated profile: refused with a reason, not a traceback", ok, why)

        for label, encoded in (
                ("list key", "a18000"),
                ("map key", "a1a000"),
                ("tagged list key", "a1c08000"),
                ("bytes key", "a14000"),
                ("nested list key", "a16178a18000"),
                ("duplicate integer key", "a201000100")):
            f.write_bytes(bytes.fromhex(encoded))
            ok, why = refused(lambda: lib.read_profile(f), "not a valid signed profile")
            check(f"a profile with a {label}: refused without a traceback", ok, why)
        half = s.make("half")
        (half / f"{SLOT_ANCHOR}-goat-cli-ca.pem").unlink()
        ok, why = refused(lambda: lib.slot_material(half), "whole bundle")
        check("a profile without the key that signs it: refused", ok, why)
        odd = s.make("odd-name", roots_name="fabric-ca.pem", anchor="ca" * 16)
        try:
            why = lib.slot_material(odd).ca.name
            ok = why == "fabric-ca.pem"
        except lib.SlotError as exc:
            ok, why = False, str(exc)
        check("an anchor with 'ca' in it does not make its key file a CA roots candidate", ok, why)

        bare = lib.slot_material(s.make("unsigned", signed=False))
        check("an older bundle without a signed profile is recognised",
              bare.profile is None and bare.signer is None)
        ok, why = refused(lambda: lib.trust(bare, expected=s.fingerprint), "no signed profile")
        check("unsigned: --ca-fingerprint has nothing to check, so it is refused", ok, why)
        ok, why = refused(lambda: lib.trust(bare), "no signed profile", roots)
        check("unsigned, no terminal: refused, naming the roots' digest", ok, why)
        tu = lib.trust(bare, ask=lambda _prompt: "yes")
        check("unsigned, confirmed at a terminal: trusted, with a warning",
              tu.ca_fingerprint == "" and tu.roots_sha256 == roots and tu.warnings, str(tu))
        check("unsigned roots that match the pin need no confirmation",
              lib.trust(bare, pinned_roots=roots).roots_sha256 == roots)
        moved = lib.slot_material(s.make("unsigned-other", signed=False, roots=OTHER_ROOT_PEM))
        ok, why = refused(lambda: lib.trust(moved, pinned_ca=s.fingerprint, pinned_roots=roots,
                                            ask=lambda _prompt: "yes"), "signing key")
        check("a pod that pinned a signing key refuses unsigned roots it has not seen, "
              "even at a terminal", ok, why)

        pod = tmp / "10-pod.yml"
        pod.write_text('pen_id: "pod-x"\ngoatnet_ca_fingerprint: ""\n'
                       'fabric_ca_roots_sha256: ""   # kept comment\n')
        said = lib.record(pod, t)
        text = pod.read_text()
        check("record pins both values in place, keeping the line's comment",
              f'goatnet_ca_fingerprint: "{lib.shown(t.ca_fingerprint)}"' in text
              and f'fabric_ca_roots_sha256: "{roots}"   # kept comment' in text
              and text.count("fabric_ca_roots_sha256") == 1 and len(said) == 2, text)
        check("record again with the same values changes nothing", lib.record(pod, t) == []
              and pod.read_text() == text)
        old = tmp / "old-10-pod.yml"
        old.write_text('pen_id: "pod-x"\n')
        lib.record(old, tu)
        check("record adds a missing pin, and an unsigned bundle pins only the roots",
              f'fabric_ca_roots_sha256: "{roots}"' in old.read_text()
              and "goatnet_ca_fingerprint" not in old.read_text(), old.read_text())


def check_egress_cli():
    """`octo-pod egress` end to end on a scratch identity: refusals, the two gates, the slot.

    The CLI insists on root because /etc/octo-pod is root-owned; here it runs as the test
    user against a temporary OCTO_POD_ETC, with the root check answered in-process.
    """
    import contextlib
    import hashlib
    import io
    import os
    import runpy
    import shutil
    import tempfile

    print("\negress CLI — bin/octo-pod-egress on a scratch identity")
    if not shutil.which("openssl"):
        print("  SKIP  no openssl here to sign the synthetic bundle; CI and every pod have it")
        return
    cli = str(HERE.parent.parent / "bin" / "octo-pod-egress")
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        pod = root / "etc" / "host_vars" / "pod-x"
        pod.mkdir(parents=True)
        (pod / "10-pod.yml").write_text('pen_id: "pod-x"\nfabric_slot_anchor: ""\nbridge_allow: "raw/octo/**"\n')
        (pod / "30-arms.yml").write_text("arms: []\n")
        (pod / "40-egress.yml").write_text("egress: []\n")
        (pod / "05-fabric.yml").write_text("fabric_endpoint: ''\nfabric_host_pins: []\n")
        # A signed bundle whose NOTES.md names another endpoint: NOTES.md is not signed, so
        # release must take the endpoint from the profile.
        signer = SignedSlots(root / "bundles")
        slot = signer.make("slot", notes_endpoint="tls/notes.example.invalid:7447")
        anchor = SLOT_ANCHOR
        roots = hashlib.sha256((TEST_ROOT_PEM + OTHER_ROOT_PEM).encode()).hexdigest()
        # remove deletes the pen key, the Quadlet unit and the destination's TLS directory;
        # here those paths are scratch, so the test can never touch a real pod's files.
        scratch = {"OCTO_PEN_STATE": root / "pen", "OCTO_QUADLET_DIR": root / "quadlet",
                   "OCTO_EGRESS_ETC": root / "egress-etc"}

        def run(*argv):
            out = io.StringIO()
            saved = (os.environ.get("OCTO_POD_ETC"), os.geteuid, sys.argv)
            os.environ["OCTO_POD_ETC"] = str(root / "etc")
            saved_scratch = {k: os.environ.get(k) for k in scratch}
            os.environ.update({k: str(v) for k, v in scratch.items()})
            os.geteuid = lambda: 0
            sys.argv = ["octo-pod-egress", *argv]
            code = 0
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    runpy.run_path(cli, run_name="__main__")
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                out.write(str(e.code or ""))
            finally:
                os.environ.pop("OCTO_POD_ETC") if saved[0] is None else os.environ.update(OCTO_POD_ETC=saved[0])
                for k, v in saved_scratch.items():
                    os.environ.pop(k, None) if v is None else os.environ.update({k: v})
                os.geteuid, sys.argv = saved[1], saved[2]
            return code, out.getvalue()

        key = "1d18de66e8a09a5e68170b3b3de6563b/sensors/radar1/asterix/data"
        code, out = run("add", "recv1", "--dest", "udp://receiver.example.invalid:8600", "--key", key)
        check("add refuses a name where a literal address belongs", code != 0 and "literal" in out, out)
        for bad in ("**", "*/sensors/x", "release/**", "ops/audit/x", "downlink/x"):
            code, out = run("add", "recv1", "--dest", "udp://10.1.2.20:8600", "--key", bad)
            check(f"add refuses a key that reaches too far or into a reserved root: {bad}",
                  code != 0 and "refusing these keys" in out, out)
        code, out = run("add", "recv1", "--dest", "udp://10.1.2.20:8600", "--key", key)
        podyml = (pod / "10-pod.yml").read_text()
        check("add declares a publisher's key, shut, and writes no lane", code == 0
              and "downlink_lane" not in podyml and "downlink_allow" in podyml
              and "from_fabric: false" in (pod / "40-egress.yml").read_text(), out)
        code, out = run("add", "acts2", "--dest", "udp://10.1.2.21:8600", "--key", "release/OTHER/b")
        check("another publisher or a release lane on the same pod is fine: no one-lane rule",
              code == 0, out)
        code, out = run("add", "radar1", "--dest", "tcp-server://10.1.2.10:9000",
                        "--key", "1d18de66e8a09a5e68170b3b3de6563b/sensors/radar1/radar-one/**")
        check("tcp-server:// without --from is refused: a listener answering no one",
              code != 0 and "--from" in out, out)
        code, out = run("add", "radar1", "--dest", "tcp-server://0.0.0.0:9000", "--from", "10.1.2.20",
                        "--key", "1d18de66e8a09a5e68170b3b3de6563b/sensors/radar1/radar-one/**")
        check("tcp-server:// on 0.0.0.0 is refused: it would answer on every leg",
              code != 0 and "specific" in out, out)
        code, out = run("add", "acts3", "--dest", "udp://10.1.2.22:8600", "--from", "10.1.2.20",
                        "--key", key)
        check("--from on a destination the pod connects out to is refused", code != 0, out)
        code, out = run("add", "radar1", "--dest", "tcp-server://10.1.2.10:9000", "--from", "10.1.2.20",
                        "--replay", "2", "--key", "1d18de66e8a09a5e68170b3b3de6563b/sensors/radar1/radar-one/**")
        radar1 = next(e for e in yaml.safe_load((pod / "40-egress.yml").read_text())["egress"]
                    if e["id"] == "radar1")
        check("tcp-server:// declares its consumers, max_clients and replay", code == 0
              and radar1.get("allow_sources") == "10.1.2.20" and radar1.get("max_clients") == 1
              and radar1.get("replay") == 2 and radar1.get("allow_dest") == "10.1.2.10:9000", str(radar1))
        code, out = run("check")
        check("check reports the new destinations shut", code == 0 and "shut" in out, out)
        before = {n: (pod / n).read_text() for n in ("10-pod.yml", "40-egress.yml", "05-fabric.yml")}

        def unchanged():
            return all((pod / n).read_text() == t for n, t in before.items())

        code, out = run("release", "recv1", "--slot", str(slot), "--router-ip", "100.127.0.8")
        check("release --slot with no fingerprint to check the bundle against is refused, "
              "and changes nothing", code != 0 and "--ca-fingerprint" in out and unchanged(), out)
        code, out = run("release", "recv1", "--slot", str(slot), "--router-ip", "100.127.0.8",
                        "--ca-fingerprint", GOAT_KEY_FINGERPRINT)
        check("release --slot with another goatnet's fingerprint is refused, and changes nothing",
              code != 0 and "not the" in out and unchanged(), out)
        code, out = run("release", "recv1", "--ca-fingerprint", signer.fingerprint)
        check("--ca-fingerprint without --slot is refused, and changes nothing",
              code != 0 and "--slot" in out and unchanged(), out)
        code, out = run("release", "recv1", "--slot", str(slot), "--router-ip", "100.127.0.8",
                        "--ca-fingerprint", signer.fingerprint)
        podyml = (pod / "10-pod.yml").read_text()
        fabric = yaml.safe_load((pod / "05-fabric.yml").read_text())
        check("release opens both gates", code == 0 and f'downlink_allow: "{key}"' in podyml
              and "from_fabric: true" in (pod / "40-egress.yml").read_text(), out)
        check("release --slot persists the anchor from the certificate's filename",
              f'fabric_slot_anchor: "{anchor}"' in podyml, podyml)
        check("release --slot pins the signing key's fingerprint and the CA roots' digest",
              f'goatnet_ca_fingerprint: "{signer.fingerprint}"' in podyml
              and f'fabric_ca_roots_sha256: "{roots}"' in podyml, podyml)
        check("release --slot fills the endpoint from the signed profile, not NOTES.md, and "
              "pins the router",
              fabric.get("fabric_endpoint") == "tls/zenoh.example.invalid:7447"
              and fabric.get("fabric_host_pins") == [{"ip": "100.127.0.8", "names": "zenoh.example.invalid"}],
              str(fabric))
        check("release --slot names the fabric CA root, not the portal's CA",
              "fabric-ca-root.pem" in out and "goat-cli-ca" not in out, out)
        code, out = run("release", "radar1")
        check("a tcp-server:// destination releases like any other", code == 0
              and "1d18de66e8a09a5e68170b3b3de6563b/sensors/radar1/radar-one/**" in (pod / "10-pod.yml").read_text(), out)
        code, out = run("release", "radar1", "--slot", str(slot))
        check("a later release with the same bundle needs no fingerprint: the pin answers",
              code == 0 and "pinned" not in out, out)
        code, out = run("revoke", "recv1")
        check("revoke closes both gates for that destination only", code == 0
              and key not in (pod / "10-pod.yml").read_text()
              and "radar-one" in (pod / "10-pod.yml").read_text(), out)
        code, out = run("remove", "recv1", "--yes")
        check("remove drops the declaration", code == 0
              and "recv1" not in (pod / "40-egress.yml").read_text(), out)


EGRESS_KEYS_OK = [
    "1d18de66e8a09a5e68170b3b3de6563b/sensors/radar1/asterix/data",
    "1d18de66e8a09a5e68170b3b3de6563b/sensors/**",
    "release/REL-ALL/c43671680793b76e9f9688e3911d8f23/echidna/v1",
    "release/REL-ALL/**",
]
EGRESS_KEYS_REFUSED = [
    "**", "*/sensors/x", "a$*/x", "release/**", "release/*/x",
    "ops/audit/x", "attest/x", "system/x", "downlink/x", "@/router/x",
]


def check_sapient_declaration_rules():
    """A sapient:// declaration registers with the controller using values the standard
    defines, so a bad one is refused before any unit exists, by the CLI and again by the
    play's validation. Two of these slipped through once: `--status-interval 0` read as
    "not given" and became 5, and `status_interval_s: true` passed `ge 1` and `le 300`."""
    import argparse
    import contextlib
    import importlib.machinery
    import importlib.util
    import io
    import os
    import shutil
    import tempfile

    print("\nsapient:// declarations — refused by the CLI and by the play")
    loader = importlib.machinery.SourceFileLoader(
        "egress_cli_sapient", str(HERE.parent.parent / "bin" / "octo-pod-egress"))
    spec = importlib.util.spec_from_loader("egress_cli_sapient", loader)
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    key = ["1d18de66e8a09a5e68170b3b3de6563b/sapient/**"]
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        (d / "00-site.yml").write_text('lan_ip: "192.0.2.5"\n')
        (d / "10-pod.yml").write_text('downlink_allow: ""\n')
        for n, (label, ok, extra) in enumerate([
                ("a radar node at the default interval", True, {"node_type": "radar"}),
                ("the longest interval, 300", True, {"status_interval": 300}),
                ("an explicit interval of 0", False, {"status_interval": 0}),
                ("an interval of 301", False, {"status_interval": 301}),
                ("a node type the standard does not define", False, {"node_type": "SPACESHIP"}),
                ("a capability without a type", False, {"capabilities": "justone"}),
                ("--from, which only tcp-server:// takes", False, {"src": "192.0.2.40"})]):
            args = dict(id=f"s{n}", dest=f"sapient://192.0.2.{30 + n}:48000", key=key, src=None,
                        max_clients=1, replay=0, description="", node_type=None,
                        status_interval=None, capabilities=None, prefix=None,
                        partner_bundle=None, no_verify_name=False)
            args.update(extra)
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    cli.cmd_add(argparse.Namespace(**args), d)
                accepted = True
            except SystemExit:
                accepted = False
            check(f"CLI {'accepts' if ok else 'refuses'} {label}", accepted == ok, out.getvalue())
    for bad in ("five", True, 2.5, None):
        check(f"CLI reports a hand-edited status_interval_s of {bad!r} instead of crashing",
              bool(cli.sapient_problems({"status_interval_s": bad})))

    if not shutil.which("ansible-playbook"):
        print("  SKIP  the play's validation (no ansible-playbook here; CI installs it)")
        return
    play = yaml.safe_load((PLATFORM / "configure-octo-pod.yml").read_text())
    task = next(t for t in leaf_tasks(play[0]["tasks"])
                if t.get("name") == "egress — validate declarations")
    cases = [("no sapient block", True, None),
             ("RADAR at interval 5", True, {"node_type": "RADAR", "status_interval_s": 5}),
             ("the NODE_TYPE_ prefix, in lower case", True, {"node_type": "node_type_radar"}),
             ("interval 0", False, {"status_interval_s": 0}),
             ("interval 301", False, {"status_interval_s": 301}),
             ("interval true", False, {"status_interval_s": True}),
             ("interval as a string", False, {"status_interval_s": "five"}),
             ("an unknown node type", False, {"node_type": "SPACESHIP"})]
    with tempfile.TemporaryDirectory() as tmp:
        pb = pathlib.Path(tmp) / "p.yml"
        pb.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False,
                                       "tasks": [task]}], sort_keys=False))
        for label, ok, sap in cases:
            dest = {"id": "s1", "enabled": True, "cert_cn": "octo-egress-s1",
                    "dest": "sapient://192.0.2.30:48000", "keys": key}
            if sap is not None:
                dest["sapient"] = sap
            ev = pathlib.Path(tmp) / "vars.json"
            ev.write_text(json.dumps({"egress": [dest], "peers": [], "arms": [],
                                      "bridge_allow": ""}))
            r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb),
                                "-e", f"@{ev}"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, cwd=tmp,
                               env=dict(os.environ, ANSIBLE_LOCALHOST_WARNING="false"))
            accepted = r.returncode == 0
            # A refusal must be the declared message, not a template error on the way.
            clean = accepted or "Bad egress declaration" in r.stdout
            check(f"play {'accepts' if ok else 'refuses'} {label}", accepted == ok and clean,
                  (r.stdout + r.stderr)[-300:])


def check_session_destinations_run_only_when_released():
    """A session destination's arm registers and heartbeats with the far side whether or
    not data flows, so an unreleased one must not run: not at boot (no [Install]), not
    after apply (the up task stops it), and verify must call a running one a fault. The
    scheme list lives in the CLI, group_vars and the fixtures; all three must agree."""
    import importlib.machinery
    import importlib.util
    import os
    import shutil
    import tempfile

    print("\nsession destinations — run only once released")
    loader = importlib.machinery.SourceFileLoader(
        "egress_cli_session", str(HERE.parent.parent / "bin" / "octo-pod-egress"))
    spec = importlib.util.spec_from_loader("egress_cli_session", loader)
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    shipped = yaml.safe_load((PLATFORM / "group_vars" / "all" / "10-infrastructure.yml").read_text())
    ctx = load_ctx()
    check("the CLI, group_vars and the fixtures name the same session schemes",
          list(cli.SESSION_SCHEMES) == shipped["egress_session_schemes"]
          == ctx["egress_session_schemes"],
          f"cli {cli.SESSION_SCHEMES} group_vars {shipped['egress_session_schemes']} "
          f"fixtures {ctx['egress_session_schemes']}")

    def session(e):
        return e["dest"].split("://")[0] in ctx["egress_session_schemes"]

    def released(e):
        return bool((e.get("release") or {}).get("from_fabric", False))

    shut_sessions = [e["id"] for e in ctx["active_egress"] if session(e) and not released(e)]
    check("the fixtures hold a declared, unreleased session destination", bool(shut_sessions))
    for e in ctx["active_egress"]:
        unit = render("egress/octo-egress.container.j2", dict(ctx, d=e))
        should_boot = released(e) or not session(e)
        check(f"{e['id']}: {'starts' if should_boot else 'does not start'} at boot",
              any(line.strip() == "[Install]" for line in unit.splitlines()) == should_boot
              and any(line.startswith("WantedBy=") for line in unit.splitlines()) == should_boot)

    if not shutil.which("ansible-playbook"):
        print("  SKIP  apply's and verify's expressions (no ansible-playbook here; CI installs it)")
        return
    play = yaml.safe_load((PLATFORM / "configure-octo-pod.yml").read_text())
    up = next(t for t in leaf_tasks(play[0]["tasks"]) if t.get("name") == "egress — up")
    vplay = yaml.safe_load((PLATFORM / "verify-octo-pod.yml").read_text())
    vt = next(t for t in leaf_tasks(vplay[0]["tasks"])
              if str(t.get("name", "")).startswith("egress — each destination's arm runs"))
    cases, tasks = [], []
    for e in ctx["active_egress"]:
        for changed in (False, True):
            want = ("stopped" if session(e) and not released(e)
                    else "restarted" if changed else "started")
            cases.append((f"{e['id']}: apply leaves it {want}"
                          + (" (unit changed)" if changed else ""), want))
            tasks.append({"name": "up", "vars": {"item": {"dest": e, "changed": changed}},
                          "ansible.builtin.debug": {"msg": up["ansible.builtin.systemd"]["state"]}})
        for active in (True, False):
            want = str(active != (released(e) or not session(e)))
            cases.append((f"{e['id']}: verify {'fails' if want == 'True' else 'passes'} with the "
                          f"arm {'running' if active else 'stopped'}", want))
            tasks.append({"name": "verify",
                          "vars": dict(vt.get("vars", {}), dest=e,
                                       _egress_active={"stdout": "active" if active else "inactive"}),
                          "ansible.builtin.debug": {"msg": "{{ " + str(vt["failed_when"]) + " }}"}})
    with tempfile.TemporaryDirectory() as tmp:
        pb = pathlib.Path(tmp) / "p.yml"
        pb.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"egress_session_schemes": ctx["egress_session_schemes"]},
            "tasks": tasks}], sort_keys=False))
        r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, ANSIBLE_STDOUT_CALLBACK="ansible.posix.json",
                                    ANSIBLE_LOCALHOST_WARNING="false"))
        try:
            got = [str(t["hosts"]["localhost"]["msg"]).strip()
                   for t in json.loads(r.stdout)["plays"][0]["tasks"]]
        except (ValueError, KeyError, IndexError):
            got = ["<error> " + (r.stdout + r.stderr)[-300:]] * len(cases)
    for (label, want), value in zip(cases, got):
        check(label, value == want, f"got {value!r}")


def check_egress_key_rule_parity():
    """One scope rule for egress keys, held in three places, agreeing on every vector:
    the CLI's key_problem(), configure's validation (evaluated by ansible-playbook), and
    the egress crate's gate.rs (its own unit tests use the same refused list)."""
    import importlib.machinery
    import importlib.util
    import os
    import shutil
    import tempfile

    print("\negress keys — one scope rule in the CLI, the playbook and gate.rs")
    loader = importlib.machinery.SourceFileLoader(
        "egress_cli", str(HERE.parent.parent / "bin" / "octo-pod-egress"))
    spec = importlib.util.spec_from_loader("egress_cli", loader)
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    cli_refused = {k for k in EGRESS_KEYS_OK + EGRESS_KEYS_REFUSED if cli.key_problem(k)}
    check("CLI: accepts every good key and refuses every bad one",
          cli_refused == set(EGRESS_KEYS_REFUSED), f"refused {sorted(cli_refused)}")

    rust = (HERE.parent.parent / "arms" / "egress" / "src" / "gate.rs").read_text()
    missing = [k for k in EGRESS_KEYS_REFUSED if f'"{k}"' not in rust]
    check("gate.rs's own tests refuse the same keys", not missing, f"not in gate.rs: {missing}")

    if not shutil.which("ansible-playbook"):
        print("  SKIP  the playbook's rule (no ansible-playbook here; CI installs ansible-core)")
        return
    play = yaml.safe_load((HERE.parent / "configure-octo-pod.yml").read_text())
    task = next(t for t in leaf_tasks(play[0]["tasks"]) if t.get("name") == "egress — validate declarations")
    rules = [c for c in task["ansible.builtin.assert"]["that"] if str(c).startswith("_keys")]
    check("the playbook's key rules were found", len(rules) == 2, str(rules))
    with tempfile.TemporaryDirectory() as tmp:
        pb = pathlib.Path(tmp) / "p.yml"
        # Each rule is "<filter chain> | length == 0"; the chain alone yields the refused keys.
        refused = " + ".join(f"({str(r).rsplit('| length', 1)[0]})" for r in rules)
        pb.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"_keys": EGRESS_KEYS_OK + EGRESS_KEYS_REFUSED},
            "tasks": [{"name": "out", "ansible.builtin.debug": {"msg": "{{ (" + refused + ") | unique }}"}}],
        }], sort_keys=False))
        r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, ANSIBLE_STDOUT_CALLBACK="ansible.posix.json",
                                    ANSIBLE_LOCALHOST_WARNING="false"))
        try:
            got = set(json.loads(r.stdout)["plays"][0]["tasks"][-1]["hosts"]["localhost"]["msg"])
        except (ValueError, KeyError, IndexError):
            got = {"<error>", (r.stdout + r.stderr)[-300:]}
    check("playbook: refuses exactly the bad keys", got == set(EGRESS_KEYS_REFUSED), f"refused {sorted(got)}")


def check_image_carries_what_the_playbooks_install():
    """Every package a playbook installs on an airgapped pod is one the golden image
    carries, and verify's zenoh client is the pen's and the arms' version.

    An airgapped pod cannot fetch a package, so the image must already hold it. The first
    image lacked python3-venv, and configure stopped on a lab pod."""
    print("\nimage — carries every package the playbooks install")
    prov = (HERE.parent.parent / "tools" / "vm-image-provision.sh").read_text()
    m = re.search(r"PLATFORM_PKGS=\(([^)]*)\)", prov)
    check("the image's package list was found (PLATFORM_PKGS)", m is not None)
    carried = set((m.group(1) if m else "").split()) | {"netbird"}   # netbird has its own step
    missing = []
    for name in ("bootstrap-octo-pod.yml", "configure-octo-pod.yml", "verify-octo-pod.yml"):
        for t in leaf_tasks((yaml.safe_load((HERE.parent / name).read_text()) or [{}])[0].get("tasks")):
            a = (t.get("ansible.builtin.apt") or t.get("apt")
                 or t.get("ansible.builtin.package") or t.get("package"))
            if not isinstance(a, dict) or not a.get("name"):
                continue
            if "bootstrap_mode == 'connected'" in str(t.get("when", "")):
                continue   # only a connected bootstrap runs it, and that one may fetch
            for pkg in a["name"] if isinstance(a["name"], list) else [a["name"]]:
                if pkg not in carried:
                    missing.append(f"{name}: '{t.get('name')}' installs {pkg}")
    check("every package a playbook installs without a network is in PLATFORM_PKGS", not missing,
          "\n    ".join(missing))
    gv = (HERE.parent / "group_vars" / "all" / "10-infrastructure.yml").read_text()
    pin = re.search(r'^verify_zenoh_version: "([^"]+)"', gv, re.M)
    pen = re.search(r'^zenoh_image_upstream: .*#\s*([0-9.]+)\s*$', gv, re.M)
    crate = re.search(r'^zenoh = \{ version = "=([0-9.]+)"',
                      (HERE.parent.parent / "arms" / "Cargo.toml").read_text(), re.M)
    versions = [x.group(1) if x else None for x in (pin, pen, crate)]
    check("verify's zenoh client, the pen and the arms run one zenoh version",
          None not in versions and len(set(versions)) == 1,
          f"verify {versions[0]}, pen {versions[1]}, arms {versions[2]}")
    # The image installs verify's client from a hashed lock; it must pin the same version.
    lock = (HERE.parent.parent / "tools" / "requirements" / "verify-zenoh.txt").read_text()
    locked = re.search(r"^eclipse-zenoh==([0-9.]+) \\$", lock, re.M)
    check("the verify-zenoh lock pins verify_zenoh_version, with hashes",
          locked is not None and pin is not None and locked.group(1) == pin.group(1)
          and "--hash=sha256:" in lock,
          f"lock {locked.group(1) if locked else None}, group_vars {pin.group(1) if pin else None}")


def check_declare_set_firewall():
    """`octo-pod set-firewall on|off` declares the firewall in 10-pod.yml, and says what
    SSH stays open to, on a scratch identity with the root check answered in-process."""
    import contextlib
    import io
    import os
    import runpy
    import tempfile

    print("\nset-firewall — bin/octo-pod-declare on a scratch identity")
    cli = str(HERE.parent.parent / "bin" / "octo-pod-declare")
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        pod = root / "etc" / "host_vars" / "pod-x"
        pod.mkdir(parents=True)
        (pod / "10-pod.yml").write_text('pen_id: "pod-x"\n')
        (pod / "00-site.yml").write_text("lan_iface: eth1\n")

        def run(*argv):
            out = io.StringIO()
            saved = (os.environ.get("OCTO_POD_ETC"), os.geteuid, sys.argv)
            os.environ["OCTO_POD_ETC"] = str(root / "etc")
            os.geteuid = lambda: 0
            sys.argv = ["octo-pod-declare", *argv]
            code = 0
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    runpy.run_path(cli, run_name="__main__")
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                out.write(str(e.code or ""))   # die() exits with its message
            finally:
                os.environ.pop("OCTO_POD_ETC") if saved[0] is None else os.environ.update(OCTO_POD_ETC=saved[0])
                os.geteuid, sys.argv = saved[1], saved[2]
            return code, out.getvalue()

        code, out = run("set-firewall", "on")
        podyml = (pod / "10-pod.yml").read_text()
        check("on: declares pod_firewall_enabled: true", code == 0
              and "pod_firewall_enabled: true" in podyml, out)
        check("on with no LAN SSH source: warns SSH is mesh-only", "mesh ONLY" in out, out)
        (pod / "00-site.yml").write_text('lan_iface: eth1\nmgmt_ssh_sources: ["192.0.2.14/32"]\n')
        code, out = run("set-firewall", "on")
        check("on: names the declared LAN SSH sources", "192.0.2.14/32" in out, out)
        code, out = run("set-firewall", "off")
        podyml = (pod / "10-pod.yml").read_text()
        check("off: flips the one line in place", code == 0
              and podyml.count("pod_firewall_enabled:") == 1 and "pod_firewall_enabled: false" in podyml,
              podyml)
        code, out = run("set-firewall", "maybe")
        check("anything but on or off is refused", code != 0 and "on|off" in out, out)



def check_arm_release_slot():
    """`octo-pod release <arm> --slot` authenticates the bundle before it opens either gate,
    and pins what authenticated it. On a scratch identity, with a synthetic signed bundle."""
    import contextlib
    import hashlib
    import io
    import os
    import runpy
    import shutil
    import tempfile

    print("\narm release - bin/octo-pod-declare release --slot on a scratch identity")
    if not shutil.which("openssl"):
        print("  SKIP  no openssl here to sign the synthetic bundle; CI and every pod have it")
        return
    cli = str(HERE.parent.parent / "bin" / "octo-pod-declare")
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        pod = root / "etc" / "host_vars" / "pod-x"
        pod.mkdir(parents=True)
        (pod / "10-pod.yml").write_text('pen_id: "pod-x"\nfabric_slot_anchor: ""\n'
                                        'goatnet_ca_fingerprint: ""\nfabric_ca_roots_sha256: ""\n'
                                        'bridge_allow: "raw/octo/**"\n')
        (pod / "30-arms.yml").write_text("arms: []\n")
        signer = SignedSlots(root / "bundles")
        slot = signer.make("slot")
        roots = hashlib.sha256((TEST_ROOT_PEM + OTHER_ROOT_PEM).encode()).hexdigest()

        def run(*argv):
            out = io.StringIO()
            saved = (os.environ.get("OCTO_POD_ETC"), os.geteuid, sys.argv)
            os.environ["OCTO_POD_ETC"] = str(root / "etc")
            os.geteuid = lambda: 0
            sys.argv = ["octo-pod-declare", *argv]
            code = 0
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    runpy.run_path(cli, run_name="__main__")
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                out.write(str(e.code or ""))   # die() exits with its message
            finally:
                os.environ.pop("OCTO_POD_ETC") if saved[0] is None else os.environ.update(OCTO_POD_ETC=saved[0])
                os.geteuid, sys.argv = saved[1], saved[2]
            return code, out.getvalue()

        code, out = run("add-arm", "radar1", "--udp", "48021", "--from", "10.1.2.3")
        check("the fixture declares an arm, shut", code == 0
              and "to_fabric: false" in (pod / "30-arms.yml").read_text(), out)
        before = {n: (pod / n).read_text() for n in ("10-pod.yml", "30-arms.yml")}

        def unchanged():
            return all((pod / n).read_text() == t for n, t in before.items())

        code, out = run("release", "radar1", "--slot", str(slot))
        check("no fingerprint to check the bundle against: refused, both gates still shut",
              code != 0 and "--ca-fingerprint" in out and unchanged(), out)
        code, out = run("release", "radar1", "--slot", str(slot), "--ca-fingerprint", GOAT_KEY_FINGERPRINT)
        check("another goatnet's fingerprint: refused, both gates still shut",
              code != 0 and "not the" in out and unchanged(), out)
        f = slot / "fabric-ca-root.pem"
        good = f.read_text()
        f.write_text(OTHER_ROOT_PEM)
        code, out = run("release", "radar1", "--slot", str(slot), "--ca-fingerprint", signer.fingerprint)
        check("CA roots swapped after signing: refused, both gates still shut",
              code != 0 and "not the CA roots" in out and unchanged(), out)
        f.write_text(good)
        code, out = run("release", "radar1", "--slot", str(slot), "--ca-fingerprint", signer.fingerprint)
        podyml = (pod / "10-pod.yml").read_text()
        check("the right fingerprint opens both gates", code == 0
              and "to_fabric: true" in (pod / "30-arms.yml").read_text()
              and "raw/radar1/**" in podyml, out)
        check("and pins the signing key and the CA roots' digest",
              f'goatnet_ca_fingerprint: "{signer.fingerprint}"' in podyml
              and f'fabric_ca_roots_sha256: "{roots}"' in podyml, podyml)
        check("the apply it prints names the fabric's CA roots, never the signing key",
              f"fabric_ca_root_src={slot / 'fabric-ca-root.pem'}" in out and "goat-cli-ca" not in out, out)
        code, out = run("release", "radar1", "--slot", str(slot))
        check("a later release with the same bundle needs no fingerprint: the pin answers",
              code == 0 and "already open" in out, out)


def check_ca_roots_pin_in_playbook():
    """configure's backstop: whichever CA roots an apply leaves in place must have the
    digest release pinned. Evaluated by ansible-playbook, on scratch files."""
    import hashlib
    import os
    import shutil
    import tempfile

    print("\nCA roots pin - configure refuses roots release did not authenticate")
    if not shutil.which("ansible-playbook"):
        print("  SKIP  no ansible-playbook here; CI installs ansible-core")
        return
    play = yaml.safe_load((HERE.parent / "configure-octo-pod.yml").read_text())
    names = ("sync-leg - read the CA roots this apply leaves in place",
             "sync-leg - refuse CA roots other than the ones release authenticated")
    tasks = [t for t in leaf_tasks(play[0]["tasks"]) if t.get("name") in names]
    check("both backstop tasks are in configure, in order",
          [t.get("name") for t in tasks] == list(names))
    if len(tasks) != 2:
        return
    good = hashlib.sha256((TEST_ROOT_PEM + OTHER_ROOT_PEM).encode()).hexdigest()

    def apply(extra, installed=None, src=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            uplink = tmp / "uplink"
            uplink.mkdir()
            if installed is not None:
                (uplink / "fabric-ca-root.pem").write_text(installed)
            v = {"fabric_uplink_dir": str(uplink), **extra}
            if src is not None:
                (tmp / "roots.pem").write_text(src)
                v.update(fabric_slot_cert_src=str(tmp / "cert.pem"), fabric_ca_root_src=str(tmp / "roots.pem"))
            pb = tmp / "p.yml"
            pb.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False,
                                           "vars": v, "tasks": tasks}], sort_keys=False))
            r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb)],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               env=dict(os.environ, ANSIBLE_STDOUT_CALLBACK="ansible.posix.json",
                                        ANSIBLE_LOCALHOST_WARNING="false"))
            try:
                results = [t["hosts"]["localhost"] for t in json.loads(r.stdout)["plays"][0]["tasks"]]
            except (ValueError, KeyError, IndexError):
                return {"rc": r.returncode, "error": (r.stdout + r.stderr)[-600:]}
            return {"rc": r.returncode, "skipped": [x.get("skipped", False) for x in results],
                    "msg": results[-1].get("msg", "")}

    both = TEST_ROOT_PEM + OTHER_ROOT_PEM
    o = apply({}, installed=OTHER_ROOT_PEM)
    check("no pin: both tasks skip, the play is unchanged", o.get("rc") == 0
          and o.get("skipped") == [True, True], str(o))
    o = apply({"fabric_ca_roots_sha256": good}, installed=both)
    check("installed roots with the pinned digest: passes", o.get("rc") == 0
          and o.get("skipped") == [False, False], str(o))
    o = apply({"fabric_ca_roots_sha256": good.upper()}, installed=both)
    check("the pin is compared without regard to case", o.get("rc") == 0, str(o))
    o = apply({"fabric_ca_roots_sha256": good}, installed=OTHER_ROOT_PEM)
    check("installed roots with another digest: the play fails, naming the file",
          o.get("rc") != 0 and "fabric-ca-root.pem" in str(o.get("msg")), str(o))
    o = apply({"fabric_ca_roots_sha256": good}, installed=OTHER_ROOT_PEM, src=both)
    check("rotation: the -e roots being placed are checked, not the old ones they replace",
          o.get("rc") == 0, str(o))
    o = apply({"fabric_ca_roots_sha256": good}, installed=both, src=OTHER_ROOT_PEM)
    check("-e roots that are not the pinned ones: the play fails before they are placed",
          o.get("rc") != 0 and "roots.pem" in str(o.get("msg")), str(o))
    o = apply({"fabric_ca_roots_sha256": good})
    check("a pod with no roots installed yet: passes, nothing to compare", o.get("rc") == 0, str(o))



def check_watchdog_drift(ctx):
    """The watchdog's reverse drift check names a running arm by its declared id, dashes
    and all. Arm ids may contain dashes (octo-pod declare allows [a-z0-9-]), and reading
    the id after the LAST dash turned radar-1 into "1": a false DRIFT every minute and a
    unit sitting in `systemctl --failed`. Run against a stub podman, so it needs no pod.
    """
    import shutil
    import tempfile

    print("\nwatchdog - declaration drift, read from real container names")
    stubs_body = {
        # A real podman writes one name per write and dies of SIGPIPE when the reader has
        # gone. The pause makes that deterministic: `grep -q` has exited by the second
        # name, so a `podman ps | grep -q` under pipefail fails on a container it found.
        "podman": ('case "$1" in ps) for n in $STUB_PS; do echo "$n" || exit 141; '
                   '/bin/sleep 0.2; done ;; esac\nexit 0\n'),
        "systemctl": "exit 0\n", "curl": "exit 0\n", "ss": "exit 0\n", "sleep": "exit 0\n",
    }

    def run(declared, running):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            stubs = tmp / "bin"
            stubs.mkdir()
            for name, body in stubs_body.items():
                (stubs / name).write_text("#!/bin/sh\n" + body)
                (stubs / name).chmod(0o755)
            # The script's python3 must be one that has pyyaml: this one does.
            (stubs / "python3").symlink_to(sys.executable)
            hv = tmp / "etc" / "host_vars" / "pod-test-01"
            hv.mkdir(parents=True)
            (hv / "30-arms.yml").write_text(yaml.safe_dump(
                {"arms": [{"id": i, "enabled": True} for i in declared]}))
            script = tmp / "watchdog.sh"
            script.write_text(render("host/octo-watchdog.sh.j2", dict(
                ctx, pen_state_dir=str(tmp), octo_pod_etc=str(tmp / "etc"), audit_metrics_port=9303)))
            env = dict(os.environ, PATH=f"{stubs}:{os.environ.get('PATH', '')}", STUB_PS=" ".join(running))
            r = subprocess.run([shutil.which("bash"), str(script)], env=env, capture_output=True, text=True)
            status = (tmp / "watchdog.status").read_text() if (tmp / "watchdog.status").exists() else ""
            return r.returncode, status, r.stderr

    rc, st, err = run(["radar-1", "loopback"], ["octo-udp-ingest-radar-1", "octo-tcp-ingest-loopback"])
    check("a dashed arm id that is declared and running is not drift",
          rc == 0 and "drift=0" in st, f"rc={rc} {st.strip()} {err.strip()}")
    rc, st, err = run(["radar-1"], ["octo-udp-ingest-radar-1", "octo-tcp-connect-spare-2"])
    check("an undeclared dashed arm is reported by its whole id",
          rc == 1 and "drift=1" in st and "spare-2 is not declared" in st, f"rc={rc} {st.strip()}")
    # The inverse of the false alarm: a declared id that happens to be the tail of an
    # undeclared one must not hide the undeclared listener.
    rc, st, err = run(["1"], ["octo-udp-ingest-1", "octo-udp-ingest-radar-1"])
    check("an undeclared arm is not excused by a declared id equal to its last segment",
          rc == 1 and "drift=1" in st and "radar-1 is not declared" in st, f"rc={rc} {st.strip()}")


def check_bootstrap_runtime_floor():
    """bootstrap refuses a box without podman 4.7 or later and skopeo, and names the
    public ways to get them. Run by ansible-playbook with a stub podman on PATH."""
    import os
    import shutil
    import tempfile

    print("\nbootstrap - the container runtime floor (podman 4.7 for Quadlet, and skopeo)")
    if not shutil.which("ansible-playbook"):
        print("  SKIP  no ansible-playbook here; CI installs ansible-core")
        return
    play = yaml.safe_load((HERE.parent / "bootstrap-octo-pod.yml").read_text())
    names = ("runtime — the base contract is present (podman + skopeo)",
             "runtime — refuse a box without podman 4.7 or later and skopeo")
    tasks = [t for t in leaf_tasks(play[0]["tasks"]) if t.get("name") in names]
    check("both runtime tasks are in bootstrap", [t.get("name") for t in tasks] == list(names))
    if len(tasks) != 2:
        return

    def run(podman_version, skopeo=True):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            if podman_version:
                (bindir / "podman").write_text(f"#!/bin/sh\necho {podman_version}\n")
                (bindir / "podman").chmod(0o755)
            if skopeo:
                (bindir / "skopeo").write_text("#!/bin/sh\nexit 0\n")
                (bindir / "skopeo").chmod(0o755)
            # Only the probe sees a PATH of stubs, so a runner's real podman can't
            # answer for them; Ansible itself keeps the normal PATH.
            probe = dict(tasks[0], environment={"PATH": str(bindir)})
            pb = tmp / "p.yml"
            pb.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False,
                                           "tasks": [probe, tasks[1]]}], sort_keys=False))
            r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb)],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               env=dict(os.environ, ANSIBLE_STDOUT_CALLBACK="ansible.posix.json",
                                        ANSIBLE_LOCALHOST_WARNING="false"))
            try:
                res = json.loads(r.stdout)["plays"][0]["tasks"][-1]["hosts"]["localhost"]
            except (ValueError, KeyError, IndexError):
                return r.returncode, (r.stdout + r.stderr)[-400:]
            return r.returncode, res.get("msg", "")

    rc, msg = run("4.9.3")
    check("podman 4.9.3 and skopeo: accepted", rc == 0, msg)
    rc, msg = run("4.7.0")
    check("podman 4.7.0, the first with healthy notification: accepted", rc == 0, msg)
    rc, msg = run("4.6.2")
    check("podman 4.6.2: refused", rc != 0 and "4.7 or later" in str(msg), msg)
    rc, msg = run("4.3.1")
    check("podman 4.3.1 (Debian 12): refused", rc != 0 and "4.7 or later" in str(msg), msg)
    rc, msg = run("3.4.4")
    check("podman 3.4.4 (Ubuntu 22.04): refused", rc != 0 and "4.7 or later" in str(msg), msg)
    rc, msg = run("4.9.3", skopeo=False)
    check("no skopeo: refused", rc != 0 and "missing" in str(msg), msg)
    rc, msg = run(None)
    check("no podman: refused", rc != 0 and "missing" in str(msg), msg)
    check("the refusal names public package installation and keeps LXD lab-only",
          "apt-get install podman skopeo" in str(msg)
          and "device/base-image pipeline" in str(msg)
          and "LXD" in str(msg) and "lab" in str(msg).lower()
          and "build-vm-image.sh" not in str(msg)
          and "usareur-af" not in str(msg).lower(), msg)


def check_egress_removal_deletes_every_key_the_playbook_writes():
    """`octo-pod egress remove` must delete every private key the play writes for a
    destination: the same contract, and the same reason, as the arm check below."""
    print("\ncredential removal — bin/octo-pod-egress vs configure-octo-pod.yml")
    play = (HERE.parent / "configure-octo-pod.yml").read_text()
    subs = {"{{ pen_state_dir }}": "/var/lib/octo-pen",
            "{{ item.name }}": "EGRESSID-egress",
            "{{ dest.id }}": "EGRESSID"}

    def expand(v):
        for k, r in subs.items():
            v = v.replace(k, r)
        return v

    outs = [expand(m.group(1)) for m in re.finditer(r'out="([^"]*\{\{[^"]*)"', play)]
    candidates = [m.group(1) for m in
                  re.finditer(r'^\s*(?:dest|path|creates|src):\s*"([^"]+)"', play, re.M)]
    candidates += re.findall(r'-out "([^"]+)"', play)
    written = set()
    for v in candidates:
        for base in (outs or [""]):
            w = expand(v.replace("${out}", base))
            if "EGRESSID" in w and "{{" not in w and "$" not in w:
                written.add(w)
    check("found the per-destination paths in the playbook", len(written) >= 6,
          f"only found {len(written)}: {sorted(written)}")

    cli = (HERE.parent.parent / "bin" / "octo-pod-egress").read_text()
    ns = {"pathlib": pathlib, "PEN_STATE": pathlib.Path("/var/lib/octo-pen"),
          "QUADLET_DIR": pathlib.Path("/etc/containers/systemd"),
          "EGRESS_ETC": pathlib.Path("/etc"),
          "args": type("A", (), {"id": "EGRESSID"})()}
    names = ("key", "csr", "egressdir", "unit")
    for line in cli.splitlines():
        m = re.match(r"^\s{4}(" + "|".join(names) + r") = (.+)$", line)
        if m:
            try:
                ns[m.group(1)] = eval(m.group(2), dict(ns))   # noqa: S307 - our own source
            except Exception:                                 # noqa: BLE001
                pass
    deleted = {str(ns[n]) for n in names if isinstance(ns.get(n), pathlib.Path)}
    check("the CLI's paths were readable", len(deleted) == len(names),
          f"parsed {sorted(deleted)}")

    def covered(path):
        return any(path == d or path.startswith(d.rstrip("/") + "/") for d in deleted)

    keys = sorted(w for w in written if "key" in w.rsplit("/", 1)[-1])
    uncovered = [w for w in keys if not covered(w)]
    check("every private key the playbook writes for a destination is one remove deletes",
          not uncovered, "survive `egress remove`:\n    " + "\n    ".join(uncovered))


def check_removal_deletes_every_key_the_playbook_writes():
    """The CLI's delete list must cover the playbook's write list, for anything holding
    a private key.

    THIS TEST EXISTS BECAUSE remove-arm SILENTLY REVOKED NOTHING. It derived the key
    path from the certificate CN (octo-arm-<id>-key.pem) while the playbook writes it
    from the id (<id>-arm-key.pem). Nothing matched, and both the plan line and the
    unlink were guarded by `if key.exists()`, so the absence read as "no work to do".
    The command printed a clean removal and exited 0 with the key still on disk. It had
    never once deleted a key.

    A comment saying "keep these in sync" would not have caught it. The two sides are in
    different languages, in different directories, written months apart. Only something
    that reads both can hold them together, so that is what this does.
    """
    print("\ncredential removal — bin/octo-pod-remove-arm vs configure-octo-pod.yml")
    play = (HERE.parent / "configure-octo-pod.yml").read_text()

    # What the playbook writes, per publisher. Rendered by substitution rather than by
    # Jinja: we want the literal shape, and a real render needs an inventory.
    subs = {
        "{{ pen_state_dir }}": "/var/lib/octo-pen",
        "{{ arm_repo_dir }}": "/var/lib/octo-pod",
        # An arm's leaf cert is minted under item.name, which the spec above builds as
        # `item.id ~ '-arm'`. That indirection is exactly where the original bug hid: the
        # CLI derived the filename from the CN instead, and CN != name.
        "{{ item.name }}": "ARMID-arm",
        "{{ item.id }}": "ARMID",
        "{{ item.cert_cn }}": "octo-arm-ARMID",
    }

    def expand(v):
        for k, r in subs.items():
            v = v.replace(k, r)
        return v

    # The key and the csr are written by openssl inside a shell block, off a shell
    # variable, so no module argument names them. Resolve `out=` and expand ${out}, or
    # the private key itself is invisible to this test and it passes while covering
    # everything except the one file that matters.
    outs = [expand(m.group(1))
            for m in re.finditer(r'out="([^"]*\{\{[^"]*)"', play)]

    written = set()
    candidates = [m.group(1) for m in
                  re.finditer(r'^\s*(?:dest|path|creates):\s*"([^"]+)"', play, re.M)]
    # Every -out argument openssl is given. A character class that excluded whitespace
    # could not span "{{ pen_state_dir }}", which silently dropped the csr.
    candidates += re.findall(r'-out "([^"]+)"', play)
    for v in candidates:
        for base in (outs or [""]):
            w = expand(v.replace("${out}", base))
            if "ARMID" in w and "{{" not in w and "$" not in w:
                written.add(w)
    check("found the per-publisher paths in the playbook", len(written) >= 8,
          f"only found {len(written)} — this test is looking in the wrong place and "
          "would silently pass while the CLI deletes nothing")

    # What the CLI deletes. Eval its own assignments so the test reads the real source
    # rather than a restatement of it that can drift the same way the original did.
    cli = (HERE.parent.parent / "bin" / "octo-pod-remove-arm").read_text()
    ns = {
        "Path": pathlib.Path,
        "PEN_STATE": pathlib.Path("/var/lib/octo-pen"),
        "ARM_REPO_DIR": pathlib.Path("/var/lib/octo-pod"),
        "args": type("A", (), {"id": "ARMID"})(),
        "cn": "octo-arm-ARMID",
    }
    names = ("key", "cert", "csr", "envf", "ovr", "armdir", "armkey", "bundle", "bundlekey")
    for line in cli.splitlines():
        m = re.match(r"^\s{4}(" + "|".join(names) + r") = (.+)$", line)
        if m:
            try:
                ns[m.group(1)] = eval(m.group(2), dict(ns))   # noqa: S307 — our own source
            except Exception:                                 # noqa: BLE001
                pass
    deleted = {str(ns[n]) for n in names if isinstance(ns.get(n), pathlib.Path)}
    check("the CLI's paths were readable", len(deleted) >= 7,
          f"only parsed {len(deleted)} of {len(names)} path assignments from the CLI; "
          "if the assignments were reformatted this test is no longer checking anything")

    # The actual assertion. Every written path that holds a private key must be covered,
    # either exactly or by a directory the CLI removes wholesale.
    def covered(path):
        return any(path == d or path.startswith(d.rstrip("/") + "/") for d in deleted)

    keys = sorted(w for w in written if "key" in w.rsplit("/", 1)[-1])
    uncovered = [w for w in keys if not covered(w)]
    check("every private key the playbook writes is one the CLI deletes", not uncovered,
          "these hold a private key and survive `remove-arm`, so the publisher keeps a "
          "usable credential after you believe you retired it:\n    "
          + "\n    ".join(uncovered)
          + "\n  the CLI deletes:\n    " + "\n    ".join(sorted(deleted)))

    # The reverse direction is worth one check too: a CLI path matching nothing the
    # playbook writes is the exact shape of the original bug, and it is invisible from
    # the CLI side because a stale path just never exists.
    stale = [d for d in sorted(deleted)
             if "octo-pen" in d or "octo-pod/compose" in d or "octo-arm" in d]
    orphaned = [d for d in stale
                if not any(w == d or w.startswith(d.rstrip("/") + "/") for w in written)]
    check("no CLI path points somewhere the playbook never writes", not orphaned,
          "the CLI would delete these, but nothing creates them, so they are dead "
          "aim and whatever they were meant to remove survives:\n    "
          + "\n    ".join(orphaned))


def check_the_cli_never_prints_a_setup_key():
    """The setup key must not survive into anything the operator can read back.

    It was passed to ansible on the command line, and both octo-pod and octo-pod-join
    echo every command they run. The same CLI tells operators to send that output with a
    support request. So the one credential that lets a box onto the mesh was printed to
    the terminal, held in scrollback, captured by any recording of an install, and pasted
    into bug reports.

    This runs the real redactor lifted out of each script, rather than grepping for the
    word 'redact', because a function that exists and does nothing would pass that.
    """
    print("\ncredentials — bin/octo-pod, bin/octo-pod-join")
    bindir = HERE.parent.parent / "bin"
    secret = "A1B2-THIS-MUST-NOT-APPEAR-C3D4"

    for name in ("octo-pod", "octo-pod-join"):
        src = (bindir / name).read_text()
        start = src.find("_redact() {")
        check(f"{name} has a redactor at all", start != -1,
              "run() prints the command it is about to execute; without this the setup "
              "key is printed verbatim")
        if start == -1:
            continue
        end = src.index("\n}\n", start) + 3
        harness = src[start:end] + f'\n_redact join --setup-key {secret} --mgmt https://m --pod p\n'
        out = subprocess.run(["bash", "-c", harness], capture_output=True, text=True).stdout
        check(f"{name} does not print the key", secret not in out,
              f"printed: {out.strip()!r}")
        check(f"{name} still shows the flag, so the command stays readable",
              "--setup-key" in out and "<setup-key>" in out,
              f"printed: {out.strip()!r}")

        # The =VALUE form is a separate branch and was the one easiest to forget.
        harness2 = src[start:end] + f'\n_redact join --setup-key={secret} --mgmt https://m\n'
        out2 = subprocess.run(["bash", "-c", harness2], capture_output=True, text=True).stdout
        check(f"{name} redacts the --setup-key=VALUE form too", secret not in out2,
              f"printed: {out2.strip()!r}")

    # A path is not a secret, and completing it is what makes the safe form the easy one.
    join = (bindir / "octo-pod-join").read_text()
    check("join accepts the key from a file", "--setup-key-file" in join,
          "an operator with no way to pass a key except on argv will paste it on argv")
    check("join accepts the key on stdin", 'SETUP_KEY" = "-"' in join,
          "a key piped from a password manager should never need to touch disk")


def tag_driven_playbooks():
    """Playbook filenames the CLI invokes with --tags, read from bin/."""
    found = set()
    bindir = HERE.parent.parent / "bin"
    for script in bindir.glob("octo-pod*"):
        try:
            text = script.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            if "ansible-playbook" in line and "--tags" in line and not line.strip().startswith("#"):
                found.update(re.findall(r"([\w-]+\.yml)", line))
    return found


def finish():
    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s): " + ", ".join(FAILURES))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
