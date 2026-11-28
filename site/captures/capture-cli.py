#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Record site/captures/cli-session.txt: the real octo-pod CLI declaring a sensor arm.

Runs bin/ commands on a scratch pod identity in a temporary directory, with the root
check and podman/systemctl answered by the test harness in platform/tests/test_cli.py,
exactly as the CLI tests do. Nothing touches a real pod. The temporary path in the
output is shown as /etc/octo-pod.

    python3 site/captures/capture-cli.py
"""
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "tests"))
from test_cli import Scratch  # noqa: E402

STEPS = [
    ("octo-pod add-arm sensor1 --udp 48021 --from 192.0.2.30 --bind 192.0.2.10",
     ("octo-pod-declare", "add-arm", "sensor1", "--udp", "48021", "--from", "192.0.2.30", "--bind", "192.0.2.10")),
    ("octo-pod get arms", ("octo-pod-inspect", "get", "arms")),
]

with tempfile.TemporaryDirectory() as tmp:
    s = Scratch(ROOT, tmp)
    out = []
    for shown, argv in STEPS:
        rc, text = s.run(*argv)
        text = text.replace(str(pathlib.Path(tmp) / "etc"), "/etc/octo-pod")
        out.append(f"$ sudo {shown}\n{text.rstrip()}\n")
        if rc:
            out.append(f"[exit {rc}]\n")
    arm = (s.pod / "30-arms.yml").read_text()
    out.append("$ cat /etc/octo-pod/host_vars/pod-x/30-arms.yml\n" + arm)
(ROOT / "site" / "captures" / "cli-session.txt").write_text("\n".join(out))
print("wrote site/captures/cli-session.txt")
