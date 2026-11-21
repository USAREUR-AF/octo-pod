#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
Translation gateways (docs/adr/0004): what a declaration grants, what it renders, what
the CLI refuses, and what keeps the gateway crate separate from the arms.

WHY THIS EXISTS
    A gateway is the one identity on the pen, apart from the pod's own, that READS
    another publisher's keys. So the checks here are on the boundary: a pod that
    declares none gets exactly the ACL it had; a declared one reads its sources and
    writes its own namespace and nothing else; the playbook gate and the CLI refuse a
    source that reaches a gateway's own output or a reserved root; and `remove` deletes
    every private key the playbook writes.

    The last class holds the crate to what its ADR says: it is not in the arms
    workspace, its third-party crates are the arms' at the same versions, its
    dependency policy differs from the arms' by one named git remote, and its pack
    bundle matches its manifest and the commit the crate pins.

RUN
    python3 platform/tests/test_translate.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cli import Scratch  # noqa: E402
from test_templates import grants, json5_to_obj, leaf_tasks, load_ctx, load_shipped_ctx, render  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PLATFORM = REPO / "platform"
GATEWAY = REPO / "gateway" / "translate"
ACL = "pen/router-acl.json5.j2"
UNIT = "translate/octo-translate.container.j2"
CN = "octo-translate-xl1"


def gateway(**fields):
    base = {"id": "xl1", "kind": "translate", "enabled": True, "cert_cn": CN,
            "namespace": "raw/xl1/**", "sources": ["raw/radar1/sapient/*"],
            "from": "sapient", "to": ["cot-xml", "geojson"], "max_distribution": "public"}
    base.update(fields)
    return base


def acl_grants(ctx):
    return grants(json5_to_obj(render(ACL, ctx))["access_control"])


def of(g, cn):
    """The (message, flow, key) grants of one certificate CN."""
    return {tuple(x.split(" | ")[1:4]) for x in g if x.split(" | ")[0] == cn}


class Acl(unittest.TestCase):
    def test_grant_line_shape_is_what_this_file_reads(self):
        line = sorted(acl_grants(load_ctx()))[0]
        self.assertEqual(len(line.split(" | ")), 5, line)

    def test_a_pod_that_declares_none_renders_the_acl_it_had(self):
        for ctx in (load_ctx(), load_shipped_ctx()):
            without = dict(ctx)
            without.pop("active_translators", None)
            self.assertEqual(render(ACL, dict(ctx, active_translators=[])), render(ACL, without))
        golden = (PLATFORM / "tests" / "golden" / "acl-grants-default-pod.txt").read_text()
        self.assertNotIn("translate", golden)

    def test_a_gateway_reads_its_sources_and_writes_its_namespace_and_nothing_else(self):
        ctx = load_ctx()
        before = acl_grants(ctx)
        after = acl_grants(dict(ctx, active_translators=[gateway()]))
        self.assertEqual(of(after, CN), {
            ("put", "ingress", "raw/xl1/**"),
            ("declare_subscriber", "ingress", "raw/radar1/sapient/*"),
            ("put", "egress", "raw/radar1/sapient/*"),
            ("delete", "egress", "raw/radar1/sapient/*"),
        })
        # Everything else that changed: the pod's own identities may read the new
        # namespace, which is what gives it an audit sink and lets bridge_allow release
        # it. No other identity gained or lost anything.
        added = after - before
        self.assertFalse(before - after)
        for line in added:
            cn, message, flow, key, permission = line.split(" | ")
            self.assertEqual(permission, "allow")
            if cn != CN:
                self.assertEqual(key, "raw/xl1/**", line)
                self.assertIn((message, flow), {("declare_subscriber", "ingress"), ("query", "ingress"),
                                                ("put", "egress"), ("delete", "egress"),
                                                ("reply", "egress")}, line)
                self.assertIn(cn, ctx["pen_firstparty_cns"], line)

    def test_two_gateways_do_not_share_a_grant(self):
        second = gateway(id="xl2", cert_cn="octo-translate-xl2", namespace="raw/xl2/**",
                         sources=["cot/**"], **{"from": "cot-xml"}, to=["geojson"])
        g = acl_grants(dict(load_ctx(), active_translators=[gateway(), second]))
        self.assertEqual({k for _, _, k in of(g, "octo-translate-xl2")}, {"raw/xl2/**", "cot/**"})
        self.assertNotIn("cot/**", {k for _, _, k in of(g, CN)})

    def test_a_disabled_gateway_is_not_active(self):
        gv = (PLATFORM / "group_vars" / "all" / "10-infrastructure.yml").read_text()
        self.assertIn("active_translators: \"{{ translators | default([]) | selectattr('enabled') | list }}\"", gv)
        self.assertRegex(gv, r'(?m)^translate_image: ""$')
        self.assertEqual(load_shipped_ctx()["active_translators"], [])


class Unit(unittest.TestCase):
    def render(self, **fields):
        ctx = dict(load_ctx(), translate_image="localhost/octo-translate:dev",
                   translate_audit_dir="/var/lib/octo-translate")
        return render(UNIT, dict(ctx, t=gateway(**fields)))

    def test_the_unit_carries_the_declaration_and_its_own_identity(self):
        unit = self.render()
        for line in ("ContainerName=octo-translate-xl1",
                     "Image=localhost/octo-translate:dev",
                     "Environment=TRANSLATE_ID=xl1",
                     "Environment=TRANSLATE_SOURCES=raw/radar1/sapient/*",
                     "Environment=TRANSLATE_FROM=sapient",
                     "Environment=TRANSLATE_TO=cot-xml,geojson",
                     "Environment=TRANSLATE_NAMESPACE=raw/xl1",
                     "Environment=TRANSLATE_MAX_DISTRIBUTION=public",
                     "Environment=TRANSLATE_CERT=/etc/octo/tls/translate-cert.pem",
                     "Volume=/etc/octo-translate-xl1/tls:/etc/octo/tls:ro",
                     "Volume=/var/lib/octo-translate/xl1:/var/lib/octo-translate",
                     "PodmanArgs=--stop-signal=SIGINT",
                     "NoNewPrivileges=true"):
            self.assertIn(line + "\n", unit)
        self.assertRegex(unit, r"Environment=TRANSLATE_ENDPOINT=tls/\S+:\d+\n")
        for absent in ("TRANSLATE_CONTEXT", "TRANSLATE_ONLY_WHEN", "TRANSLATE_CARRY", "TRANSLATE_PACKS_DIR", "TRANSLATE_PUBLISH_KEY",
                       "TRANSLATE_STALE_AFTER_S", "TRANSLATE_STAMP_TIME", "PublishPort"):
            self.assertNotIn(absent, unit)

    def test_optional_fields_render_only_when_declared(self):
        unit = self.render(context={"how": "m-g", "callsign": "POD 1"}, stale_after_s=30,
                           stamp_time=True, publish_key="{namespace}/{to}/{tail}",
                           packs_dir="/etc/octo-pod/packs", max_distribution="controlled",
                           only_when={"sapient.location_coordinate_system": 1})
        for line in ('Environment="TRANSLATE_CONTEXT=callsign=POD 1;how=m-g"',
                     "Environment=TRANSLATE_ONLY_WHEN=sapient.location_coordinate_system=1",
                     "Environment=TRANSLATE_STALE_AFTER_S=30",
                     "Environment=TRANSLATE_STAMP_TIME=true",
                     "Environment=TRANSLATE_PUBLISH_KEY={namespace}/{to}/{tail}",
                     "Environment=TRANSLATE_MAX_DISTRIBUTION=controlled",
                     "Environment=TRANSLATE_PACKS_DIR=/etc/octo-translate/packs",
                     "Volume=/etc/octo-pod/packs:/etc/octo-translate/packs:ro"):
            self.assertIn(line + "\n", unit)

    def test_every_variable_the_unit_sets_is_one_the_binary_reads(self):
        main = (GATEWAY / "src" / "main.rs").read_text()
        reads = set(re.findall(r'env = "(TRANSLATE_[A-Z_]+)"', main))
        sets = set(re.findall(r"Environment=\"?(TRANSLATE_[A-Z_]+)=", (PLATFORM / "templates" / UNIT).read_text()))
        self.assertTrue(sets)
        self.assertFalse(sets - reads, "the unit sets a variable the binary does not read")


def gate_task():
    play = yaml.safe_load((PLATFORM / "configure-octo-pod.yml").read_text())
    return next(t for t in leaf_tasks(play[0]["tasks"])
                if t.get("name") == "translate — validate declarations")


def gate(translators, arms=()):
    """The playbook's own validation task, run by ansible on one set of declarations.
    True when it passes, False when it refuses."""
    with tempfile.TemporaryDirectory() as tmp:
        pb = Path(tmp) / "p.yml"
        pb.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"peers": [], "arms": list(arms), "egress": [], "translators": list(translators)},
            "tasks": [gate_task()]}], sort_keys=False))
        r = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, ANSIBLE_STDOUT_CALLBACK="json",
                                    ANSIBLE_LOCALHOST_WARNING="false"))
        try:
            res = json.loads(r.stdout)["plays"][0]["tasks"][-1]["hosts"]["localhost"]
        except (ValueError, KeyError, IndexError):
            raise AssertionError(f"ansible gave no result: {r.stdout[-400:]} {r.stderr[-400:]}")
        return not res.get("failed", False)


@unittest.skipUnless(shutil.which("ansible-playbook"), "no ansible-playbook here; CI installs ansible-core")
class Gate(unittest.TestCase):
    def gate(self, translators, arms=()):
        return gate(translators, arms)

    def test_the_gate_is_always_run(self):
        self.assertEqual(gate_task().get("tags"), ["always"])

    def test_a_pod_with_no_gateway_and_a_good_gateway_pass(self):
        self.assertTrue(self.gate([]))
        self.assertTrue(self.gate([gateway()]))
        self.assertTrue(self.gate([gateway(sources=["raw/radar1/**", "cot/tak/*"],
                                           context={"how": "m-g"}, stale_after_s=30,
                                           only_when={"sapient.location_coordinate_system": 1},
                                           publish_key="{namespace}/{to}/{tail}")]))
        self.assertTrue(self.gate([gateway(enabled=False)]))

    def test_the_gate_refuses(self):
        arm = {"id": "xl1", "enabled": True}
        cases = {
            "an id an arm holds": ([gateway()], [arm]),
            "no enabled": ([{k: v for k, v in gateway().items() if k != "enabled"}], []),
            "a namespace that is not raw/<id>/**": ([gateway(namespace="raw/radar1/**")], []),
            "a first-party namespace": ([gateway(namespace="cot/**")], []),
            "no sources": ([gateway(sources=[])], []),
            "sources written as a string": ([gateway(sources="raw/radar1/**")], []),
            "the whole pen": ([gateway(sources=["**"])], []),
            "a wildcard first chunk": ([gateway(sources=["*/radar1/**"])], []),
            "a wildcard directly under raw/": ([gateway(sources=["raw/*/sapient/*"])], []),
            "raw/** itself": ([gateway(sources=["raw/**"])], []),
            "its own namespace": ([gateway(sources=["raw/xl1/cot-xml/**"])], []),
            "another gateway's namespace": (
                [gateway(), gateway(id="xl2", cert_cn="octo-translate-xl2", namespace="raw/xl2/**",
                                    sources=["raw/xl1/**"])], []),
            "data that came down": ([gateway(sources=["downlink/x/**"])], []),
            "a reserved root": ([gateway(sources=["ops/pod/health/x"])], []),
            "one chunk only": ([gateway(sources=["raw"])], []),
            "no from": ([{k: v for k, v in gateway().items() if k != "from"}], []),
            "to written as a string": ([gateway(to="cot-xml")], []),
            "no targets": ([gateway(to=[])], []),
            "a target that is not a pack name": ([gateway(to=["cot xml"])], []),
            "an unknown distribution": ([gateway(max_distribution="secret")], []),
            "a publish key outside the namespace": ([gateway(publish_key="cot/{to}/{tail}")], []),
            "a context value the unit cannot carry": ([gateway(context={"how": 'm-g";x'})], []),
            "a context value with a specifier": ([gateway(context={"how": "%h"})], []),
            "a relative packs_dir": ([gateway(packs_dir="packs")], []),
            "an only_when value the unit cannot carry": ([gateway(only_when={"a.b": "1;x=2"})], []),
            "an only_when key that is not a model key": ([gateway(only_when={"A B": "1"})], []),
            "a carry rule (a field mapping belongs in the packs)": (
                [gateway(carry={"uid": "sapient.detection_report_object_id"})], []),
            "stale_after_s as text": ([gateway(stale_after_s="soon")], []),
        }
        for label, (translators, arms) in cases.items():
            with self.subTest(label):
                self.assertFalse(self.gate(translators, arms))


class Cli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.s = Scratch(REPO, self._tmp.name)
        self.s.env["OCTO_TRANSLATE_ETC"] = str(self.s.root / "translate-etc")
        self.s.write("30-arms.yml", "arms:\n  - id: radar1\n    enabled: true\n")

    def run_cli(self, *argv, answer=None):
        if answer is None:
            return self.s.run("octo-pod-translate", *argv)
        from test_cli import SHIM
        r = subprocess.run([sys.executable, "-c", SHIM, str(REPO / "bin" / "octo-pod-translate"), *argv],
                           capture_output=True, text=True, env=self.s.env, input=answer, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def add(self, ident="xl1", *extra):
        return self.run_cli("add", ident, "--source", "raw/radar1/sapient/*", "--from", "sapient",
                            "--to", "cot-xml,geojson", *extra)

    def declared(self):
        path = self.s.pod / "50-translate.yml"
        return (yaml.safe_load(path.read_text()) or {}).get("translators") if path.exists() else None

    def test_nothing_is_declared_until_add_and_add_writes_what_the_playbook_gate_accepts(self):
        self.assertIsNone(self.declared())
        rc, out = self.run_cli("list")
        self.assertEqual(rc, 0, out)
        self.assertIn("no translation gateway is declared", out)

        rc, out = self.add("xl1", "--set", "how=m-g", "--stale-after", "30", "--stamp-time",
                           "--only-when", "sapient.location_coordinate_system=1")
        self.assertEqual(rc, 0, out)
        self.assertIn("Nothing on the pod has changed yet", out)
        self.assertIn("until raw/xl1/** is in bridge_allow", out)
        (entry,) = self.declared()
        self.assertEqual(entry["cert_cn"], CN)
        self.assertEqual(entry["namespace"], "raw/xl1/**")
        self.assertEqual(entry["sources"], ["raw/radar1/sapient/*"])
        self.assertEqual((entry["from"], entry["to"]), ("sapient", ["cot-xml", "geojson"]))
        self.assertEqual(entry["context"], {"how": "m-g"})
        self.assertEqual(entry["only_when"], {"sapient.location_coordinate_system": "1"})
        self.assertNotIn("carry", entry)
        self.assertEqual((entry["stale_after_s"], entry["stamp_time"], entry["max_distribution"]),
                         (30, True, "public"))
        self.assertIs(entry["enabled"], True)
        if shutil.which("ansible-playbook"):
            self.assertTrue(gate([entry]))
        # 10-pod.yml is untouched: nothing is released by declaring a gateway.
        self.assertIn('bridge_allow: ""', self.s.read("10-pod.yml"))

        rc, out = self.run_cli("add", "xl2", "--source", "cot/**", "--from", "cot-xml", "--to", "sapient")
        self.assertEqual(rc, 0, out)
        self.assertEqual([e["id"] for e in self.declared()], ["xl1", "xl2"])
        rc, out = self.run_cli("list")
        self.assertIn("xl1  enabled  sapient -> cot-xml, geojson", out)

    def test_add_refuses_and_changes_nothing(self):
        self.assertEqual(self.add()[0], 0)
        before = self.s.read("50-translate.yml")
        base = ["--from", "sapient", "--to", "cot-xml"]
        cases = [
            ("already declared", ["add", "xl1", "--source", "raw/radar1/**", *base]),
            ("already declared", ["add", "radar1", "--source", "raw/radar1/**", *base]),
            ("must be lowercase", ["add", "XL", "--source", "raw/radar1/**", *base]),
            ("reads as", ["add", "off", "--source", "raw/radar1/**", *base]),
            ("usage:", ["add", "xl3", *base]),
            ("must begin with a literal chunk", ["add", "xl3", "--source", "**", *base]),
            ("wildcard directly under raw/", ["add", "xl3", "--source", "raw/*/sapient/*", *base]),
            ("wildcard directly under raw/", ["add", "xl3", "--source", "raw/**", *base]),
            ("does not convert converted output", ["add", "xl3", "--source", "raw/xl1/**", *base]),
            ("does not convert converted output", ["add", "xl3", "--source", "raw/xl3/**", *base]),
            ("reserved namespace", ["add", "xl3", "--source", "downlink/a/b", *base]),
            ("reserved namespace", ["add", "xl3", "--source", "@/router/x", *base]),
            ("must name what to read", ["add", "xl3", "--source", "raw", *base]),
            ("not a well-formed", ["add", "xl3", "--source", "raw//x", *base]),
            ("names the source format", ["add", "xl3", "--source", "raw/radar1/**",
                                         "--from", "sapient", "--to", "sapient"]),
            ("names a format twice", ["add", "xl3", "--source", "raw/radar1/**",
                                      "--from", "sapient", "--to", "geojson,geojson"]),
            ("must be pack names", ["add", "xl3", "--source", "raw/radar1/**",
                                    "--from", "sapient", "--to", "cot xml"]),
            ("must be <model key>=<value>", ["add", "xl3", "--source", "raw/radar1/**", *base, "--set", "how"]),
            ("the value may hold only", ["add", "xl3", "--source", "raw/radar1/**", *base, "--set", "how=%h"]),
            ("--only-when 'x' must be", ["add", "xl3", "--source", "raw/radar1/**", *base, "--only-when", "x"]),
            ("needs --packs-dir", ["add", "xl3", "--source", "raw/radar1/**", *base,
                                   "--max-distribution", "controlled"]),
            ("must be an absolute path", ["add", "xl3", "--source", "raw/radar1/**", *base,
                                          "--packs-dir", "packs"]),
            ("must begin with {namespace}/", ["add", "xl3", "--source", "raw/radar1/**", *base,
                                              "--publish-key", "cot/{to}/{tail}"]),
            ("must contain {to}", ["add", "xl3", "--source", "raw/radar1/**", *base,
                                   "--publish-key", "{namespace}/{source}"]),
            ("at least 1 second", ["add", "xl3", "--source", "raw/radar1/**", *base, "--stale-after", "0"]),
            ("is not declared in 50-translate.yml. Known: xl1", ["remove", "ghost", "--yes"]),
        ]
        for needle, argv in cases:
            with self.subTest(argv=argv):
                rc, out = self.run_cli(*argv)
                self.assertNotEqual(rc, 0, out)
                self.assertIn(needle, out)
                self.assertEqual(self.s.read("50-translate.yml"), before)

    def test_remove_asks_for_the_id_then_stops_deletes_and_forgets(self):
        self.assertEqual(self.add()[0], 0)
        tls = self.s.pen / "zenoh" / "tls"
        tls.mkdir(parents=True)
        key, cert = tls / "xl1-translate-key.pem", tls / "xl1-translate-cert.pem"
        csr = self.s.pen / "ca" / "xl1-translate.csr"
        csr.parent.mkdir(parents=True)
        own = Path(self.s.env["OCTO_TRANSLATE_ETC"]) / "octo-translate-xl1" / "tls"
        own.mkdir(parents=True)
        unit = Path(self.s.env["OCTO_QUADLET_DIR"]) / "octo-translate-xl1.container"
        unit.parent.mkdir(parents=True)
        for f in (key, cert, csr, own / "translate-key.pem", unit):
            f.write_text("synthetic\n")

        before = self.s.read("50-translate.yml")
        rc, out = self.run_cli("remove", "xl1", answer="xl\n")
        self.assertNotEqual(rc, 0)
        self.assertIn("not confirmed; nothing changed", out)
        self.assertEqual(self.s.read("50-translate.yml"), before)
        self.assertTrue(key.exists() and unit.exists())

        rc, out = self.run_cli("remove", "xl1", answer="xl1\n")
        self.assertEqual(rc, 0, out)
        self.assertFalse(key.exists() or csr.exists() or unit.exists() or own.parent.exists())
        self.assertTrue(cert.exists())
        self.assertEqual(self.declared(), [])
        calls = (self.s.state / "calls").read_text()
        self.assertLess(calls.index("systemctl stop octo-translate-xl1.service"),
                        calls.index("systemctl daemon-reload"))

    def test_remove_deletes_every_private_key_the_playbook_writes_for_a_gateway(self):
        """The playbook and the CLI are in two languages and nothing else reads both."""
        play = (PLATFORM / "configure-octo-pod.yml").read_text()
        written = set(re.findall(r'(?:src|dest): "([^"]*translate-key\.pem)"', play))
        self.assertEqual(written, {
            "{{ pen_state_dir }}/zenoh/tls/{{ xl.id }}-translate-key.pem",
            "/etc/octo-translate-{{ xl.id }}/tls/translate-key.pem"})
        self.assertIn("'name': xl.id ~ '-translate'", play)
        cli = (REPO / "bin" / "octo-pod-translate").read_text()
        self.assertIn('PEN_STATE / "zenoh" / "tls" / f"{ident}-translate-key.pem"', cli)
        self.assertIn('TRANSLATE_ETC / f"octo-translate-{ident}"', cli)
        # ...and the unattended paths know the same directory.
        self.assertIn("/etc/octo-translate-*/tls", (PLATFORM / "templates/pen/cert-renew.sh.j2").read_text())
        self.assertIn("/etc/octo-translate-*", (REPO / "tools/deprovision-pod.sh").read_text())


class Separation(unittest.TestCase):
    def test_the_gateway_is_not_in_the_arms_workspace(self):
        arms = tomllib.loads((REPO / "arms" / "Cargo.toml").read_text())
        self.assertFalse([m for m in arms["workspace"]["members"] if "translate" in m])
        own = tomllib.loads((GATEWAY / "Cargo.toml").read_text())
        self.assertEqual(own["workspace"]["members"], ["."])
        # No arm depends on the mapping engine.
        for manifest in (REPO / "arms").glob("*/Cargo.toml"):
            self.assertNotIn("goat-transformers", manifest.read_text(), manifest)
        self.assertNotIn("goat-transformers", (REPO / "arms" / "Cargo.lock").read_text())

    def test_the_engine_is_pinned_by_commit_and_the_zenoh_pin_is_the_arms(self):
        own = tomllib.loads((GATEWAY / "Cargo.toml").read_text())["dependencies"]
        arms = tomllib.loads((REPO / "arms" / "Cargo.toml").read_text())["workspace"]["dependencies"]
        self.assertEqual(own["zenoh"], arms["zenoh"])
        engine = own["goat-transformers"]
        self.assertRegex(engine["rev"], r"^[0-9a-f]{40}$")
        self.assertNotIn("branch", engine)
        self.assertNotIn("tag", engine)

    def test_every_third_party_crate_is_in_the_arm_images_at_the_same_version(self):
        """So arms/THIRD_PARTY_LICENSES.md, which the image ships, covers the gateway."""
        def packages(lock):
            return {(p["name"], p["version"]): p.get("source", "")
                    for p in tomllib.loads(lock.read_text())["package"]}
        arms, own = packages(REPO / "arms" / "Cargo.lock"), packages(GATEWAY / "Cargo.lock")
        extra = {k: v for k, v in own.items() if v and k not in arms}
        # Ahead of the arms' lock for a security advisory, by a patch release of a crate
        # the arms also hold, so the license text the image ships still covers it. Remove
        # an entry when the arms' lock reaches the same version.
        ahead = {("quinn-proto", "0.11.19"): "RUSTSEC-2026-0523, RUSTSEC-2026-0524",
                 ("quinn-udp", "0.5.16"): "RUSTSEC-2026-0538"}
        for name, version in ahead:
            held = [v for n, v in arms if n == name]
            self.assertTrue(held, f"the arms do not hold {name}")
            self.assertNotIn(version, held, f"the arms reached {name} {version}: drop the exception")
            self.assertEqual(extra.pop((name, version), None),
                             "registry+https://github.com/rust-lang/crates.io-index")
        self.assertEqual(list(extra), [next(k for k in own if k[0] == "goat-transformers")])
        (source,) = extra.values()
        rev = tomllib.loads((GATEWAY / "Cargo.toml").read_text())["dependencies"]["goat-transformers"]["rev"]
        self.assertEqual(source, f"git+https://github.com/usareur-af/goat-transformers?rev={rev}#{rev}")

    def test_the_dependency_policy_is_the_arms_plus_one_named_git_remote(self):
        arms = (REPO / "arms" / "deny.toml").read_text().splitlines()
        own = (GATEWAY / "deny.toml").read_text().splitlines()
        self.assertEqual(len(arms), len(own))
        differing = [(a, b) for a, b in zip(arms, own) if a != b]
        self.assertEqual(differing, [
            ("allow-git = []", 'allow-git = ["https://github.com/usareur-af/goat-transformers"]')])

    def test_the_pack_bundle_matches_its_manifest_and_the_pinned_commit(self):
        r = subprocess.run([sys.executable, str(GATEWAY / "tools" / "refresh-packs.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        manifest = json.loads((GATEWAY / "packs" / "MANIFEST.json").read_text())
        self.assertEqual(manifest["formats"], ["cot-xml", "geojson", "sapient"])
        for entry in manifest["files"]:
            if entry["path"].endswith(".def"):
                text = (GATEWAY / "packs" / entry["path"]).read_text()
                self.assertRegex(text, r"(?m)^distribution\s+public\s*$", entry["path"])

    def test_a_changed_pack_fails_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "translate"
            shutil.copytree(GATEWAY, copy, ignore=shutil.ignore_patterns("target"))
            pack = copy / "packs" / "packs" / "cot-xml" / "pack.def"
            pack.write_text(pack.read_text() + "field   callsign  detail/@other  string\n")
            r = subprocess.run([sys.executable, str(copy / "tools" / "refresh-packs.py"), "--check"],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("packs/cot-xml/pack.def: differs from the manifest", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
