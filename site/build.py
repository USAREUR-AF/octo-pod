#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Check the landing page in site/ against the tree and the recorded capture.

    python3 site/build.py

Fails when a number the page states differs from what it is measured from, or when
the code shown faintly behind the first screen is no longer in the udp-ingest arm.
The page needs no build; this is only the check.
"""
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def measured():
    cap = json.loads((SITE / "assets/packets.json").read_text())
    ev = cap["events"]
    sent = {e["payload"]: e for e in ev if e["kind"] == "sent"}
    pub = [e for e in ev if e["kind"] == "published"]
    to1 = [e for e in sent.values() if e["to"] == "sensor1"]
    to2 = [e for e in sent.values() if e["to"] == "sensor2"]
    dropped = max((int(m.group(1)) for l in cap["logs"] if l["arm"] == "sensor2"
                   for m in [re.search(r"dropped_source=(\d+)", l["line"])] if m), default=0)
    members = re.search(r"^members\s*=\s*\[(.*?)\]", (ROOT / "arms/Cargo.toml").read_text(), re.M | re.S).group(1)
    return {
        # site/assets/packets.json: publications the router saw / datagrams sent to sensor1
        "published": str(len(pub)),
        "sent1": str(len(to1)),
        # median of (publication time - send time), milliseconds, same capture
        "median-ms": str(round(statistics.median(p["t"] - sent[p["payload"]]["t"] for p in pub))),
        # sensor2's last stats line, dropped_source=, and datagrams sent to it
        "dropped": str(dropped),
        "sent2": str(len(to2)),
        # members = [...] in arms/Cargo.toml
        "crates": str(len(re.findall(r'"[^"]+"', members))),
    }


def check():
    page = (SITE / "index.html").read_text()
    errors = []
    want = measured()
    for key, value in want.items():
        found = re.findall(rf'data-n="{key}">([^<]*)<', page)
        if not found:
            errors.append(f"{key}: the page does not state it")
        errors += [f"{key}: the page says {f}, measured {value}" for f in found if f.strip() != value]
    code = re.search(r'<pre class="bgcode"[^>]*>(.*?)</pre>', page, re.S).group(1)
    code = code.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    if code.strip("\n") not in (ROOT / "arms/udp-ingest/src/main.rs").read_text():
        errors.append("the code behind the first screen is no longer in arms/udp-ingest/src/main.rs")
    cli = re.search(r'<pre class="term" id="cli">(.*?)</pre>', page, re.S).group(1)
    cli = cli.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    if cli != (SITE / "captures/cli-session.txt").read_text():
        errors.append("the CLI session on the page differs from site/captures/cli-session.txt")
    for e in errors:
        print("site check:", e, file=sys.stderr)
    if not errors:
        print("site check: " + ", ".join(f"{k}={v}" for k, v in want.items()))
    return not errors


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
