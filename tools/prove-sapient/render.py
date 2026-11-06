# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Render what the SAPIENT egress proof runs, from the platform's own templates.

  router-released.json5  the pen ACL with the destination released
  router-shut.json5      the same destination declared but not released
  router-open.json5      the released config with access control off, so an observer on
                         ** sees every write any identity makes
  controller.container   the destination's Quadlet unit, as `apply` writes it
  controller-shut.container
                         the same unit while the destination is declared but not released
  refused.container      a second destination whose allow list omits its own target

Usage: render.py <repo> <work dir> <pen port> <controller port> <image>

Two edits are made to each unit, both printed: the TLS volume points into the work
directory instead of /etc/octo-egress-<id>/tls, and a stats interval is added so the proof
can read the arm's counters. Everything else is the template's output.
License: Apache-2.0
"""
import copy
import importlib.util
import json
import pathlib
import sys
from importlib.machinery import SourceFileLoader

repo, work = sys.argv[1], pathlib.Path(sys.argv[2])
port, peer_port, image = int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
spec = importlib.util.spec_from_loader(
    "suite", SourceFileLoader("suite", f"{repo}/platform/tests/test_templates.py"))
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)

PUBLISHER = "aa11bb22cc33dd44ee55ff6677889900"
CONTROLLER_DEST = {
    "id": "controller", "description": "prove: a SAPIENT controller",
    "cert_cn": "octo-egress-controller",
    "enabled": True, "dest": f"sapient://127.0.0.1:{peer_port}",
    "allow_dest": f"127.0.0.1:{peer_port}",
    "sapient": {"node_type": "RADAR", "status_interval_s": 1},
    "keys": [f"{PUBLISHER}/sapient/**"], "release": {"from_fabric": True},
}
REFUSED = dict(copy.deepcopy(CONTROLLER_DEST), id="refused", cert_cn="octo-egress-refused",
               allow_dest="127.0.0.1:48199")


def ctx_for(*dests):
    ctx = suite.load_ctx()
    ctx.update(pen_listen_port=port, mesh_ip="127.0.0.1", health_pub_enabled=False,
               active_peers=[], active_arms=[], egress=list(dests),
               active_egress=list(dests), sapient_image=image,
               egress_audit_dir=str(work / "audit"))
    return ctx


def router(*dests, acl=True):
    text = suite.render("pen/router-acl.json5.j2", ctx_for(*dests))
    text = text.replace(f'"tls/0.0.0.0:{port}"', f'"tls/127.0.0.1:{port}"')
    if f'"tls/127.0.0.1:{port}"' not in text:
        sys.exit("the rendered router config has no listener to move onto loopback")
    obj = suite.json5_to_obj(text)
    if not acl:
        obj["access_control"]["enabled"] = False
    # The router container mounts the work directory's tls/ at /etc/zenoh/tls.
    return json.dumps(obj, indent=2)


def unit(d):
    text = suite.render("egress/octo-egress.container.j2", dict(ctx_for(d), d=d))
    host_tls = f"/etc/octo-egress-{d['id']}/tls"
    if f"Volume={host_tls}:" not in text or "Environment=RUST_LOG=" not in text:
        sys.exit(f"the {d['id']} unit no longer has the lines this proof edits")
    text = text.replace(f"Volume={host_tls}:", f"Volume={work}/tls-{d['id']}:")
    text = text.replace("Environment=RUST_LOG=",
                        "Environment=SAPIENT_STATS_SECS=2\nEnvironment=RUST_LOG=", 1)
    shut_note = "" if (d.get("release") or {}).get("from_fabric") else " (shut)"
    print(f"  {d['id']}{shut_note}.container: TLS volume -> {work}/tls-{d['id']}, "
          "SAPIENT_STATS_SECS=2")
    return text


shut = dict(copy.deepcopy(CONTROLLER_DEST), release={"from_fabric": False})
(work / "router-released.json5").write_text(router(CONTROLLER_DEST))
(work / "router-shut.json5").write_text(router(shut))
(work / "router-open.json5").write_text(router(CONTROLLER_DEST, acl=False))
(work / "controller.container").write_text(unit(CONTROLLER_DEST))
(work / "controller-shut.container").write_text(unit(shut))
(work / "refused.container").write_text(unit(REFUSED))
(work / "publisher.txt").write_text(PUBLISHER)
