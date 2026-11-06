# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Drive a sapient:// egress destination end to end, as `apply` deploys it.

Started by run.sh, which builds the image, mints the pen certificates the way `apply`
does, renders the router configs and the units from the platform templates, and
generates the controller's protobuf modules from the arm's vendored ICD with protoc. The
controller shares no code with the arm, so a defect in the arm's encoder cannot be
mirrored on the other side and pass. It is adapted from an earlier standalone prove-sapient harness.

  released  the unit starts under systemd as uid 65532 and registers with the controller
            as a radar node. A SAPIENT detection the downlink bridge writes on a declared
            key reaches the controller under the arm's node id, and its audit line carries
            the hash of what came down. A task is refused with the TaskAck the standard
            requires. A restart registers under the same node id.
  gates     nothing reaches the controller from an undeclared key, from an identity with
            no write grant on the downlink namespace, or of a kind a sensor never sends.
  writes    with access control off, an observer on ** sees no write from the arm: no
            capture of the controller's frames, no egress mirror and no session records.
  shut      the unit apply writes for an unreleased destination does not start at boot,
            and started by hand anyway it receives nothing. Released, as apply releases
            it (a router change and the rewritten unit), it starts and delivers.
  refused   a destination whose allow list omits its own target exits 2, records why and
            never dials.

License: Apache-2.0
"""
import glob
import hashlib
import json
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

import zenoh

WORK = os.environ["OCTO_PROVE_WORK"]
PORT = int(os.environ["OCTO_PROVE_PORT"])
PEER_PORT = int(os.environ["OCTO_PROVE_PEER_PORT"])
ZENOH_IMAGE = os.environ["OCTO_PROVE_ZENOH_IMAGE"]
TLS = f"{WORK}/tls"
UNITS = "/etc/containers/systemd"
ROUTER = "octo-prove-sapient-zenoh"
with open(f"{WORK}/publisher.txt") as publisher_file:
    PUBLISHER = publisher_file.read().strip()
OTHER = "ffeeddccbbaa99887766554433221100"
DECLARED = f"downlink/{PUBLISHER}/sapient/radar1"
sys.path.insert(0, f"{WORK}/gen")
from sapient_msg.bsi_flex_335_v2_0 import registration_pb2 as rg  # noqa: E402
from sapient_msg.bsi_flex_335_v2_0 import sapient_message_pb2 as sm  # noqa: E402
from sapient_msg.bsi_flex_335_v2_0 import task_ack_pb2 as ta  # noqa: E402
from sapient_msg.bsi_flex_335_v2_0 import task_pb2 as tk  # noqa: E402

PASS, FAIL = [], []
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"\n          {detail}"))


def wait_for(pred, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.2)
    return pred()


def sh(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"{' '.join(cmd)} failed ({r.returncode}): {r.stderr.strip()[:400]}")
    return r


def ulid():
    value = (int(time.time() * 1000) << 80) | random.getrandbits(80)
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def envelope(node_id):
    m = sm.SapientMessage()
    m.timestamp.GetCurrentTime()
    m.node_id = node_id
    return m


def detection(report_id):
    m = envelope("translator-node")
    m.detection_report.report_id = ulid()
    m.detection_report.object_id = ulid()
    m.detection_report.detection_confidence = 0.9
    m.detection_report.id = report_id
    return m.SerializeToString()


class Controller:
    """A SAPIENT controller on loopback. It accepts every registration, answers in the
    byte order the node chose, and records every message it receives."""

    ID = "3f1d6c2e-8a4b-4e1f-9c7d-2b5a6e8f0a11"

    def __init__(self, port):
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(4)
        self.lock = threading.Lock()
        self.got, self.connections, self.conn, self.order = [], 0, None, "<"
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with self.lock:
                self.connections += 1
                self.conn, order, buf = conn, None, b""
            try:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while len(buf) >= 4:
                        if order is None:
                            order = "<" if struct.unpack("<I", buf[:4])[0] <= 1_000_000 else ">"
                            self.order = order
                        n = struct.unpack(order + "I", buf[:4])[0]
                        if n > 1_000_000 or len(buf) < 4 + n:
                            break
                        body, buf = buf[4:4 + n], buf[4 + n:]
                        msg = sm.SapientMessage()
                        msg.ParseFromString(body)
                        kind = msg.WhichOneof("content")
                        with self.lock:
                            self.got.append((kind, msg))
                        if kind == "registration":
                            ack = envelope(self.ID)
                            ack.destination_id = msg.node_id
                            ack.registration_ack.acceptance = True
                            self.send(ack)
            except OSError:
                # Restart checks disconnect the peer; accept its next connection.
                pass
            finally:
                conn.close()

    def send(self, msg):
        body = msg.SerializeToString()
        self.conn.sendall(struct.pack(self.order + "I", len(body)) + body)

    def of(self, kind):
        with self.lock:
            return [m for k, m in self.got if k == kind]

    def reports(self, report_id):
        return [m for m in self.of("detection_report") if m.detection_report.id == report_id]

    def clear(self):
        with self.lock:
            self.got.clear()


def session(name):
    c = zenoh.Config()
    for k, v in (("mode", '"client"'), ("connect/endpoints", f'["tls/127.0.0.1:{PORT}"]'),
                 ("scouting/multicast/enabled", "false")):
        c.insert_json5(k, v)
    c.insert_json5("transport/link/tls", json.dumps({
        "root_ca_certificate": f"{TLS}/ca-roots.pem",
        "connect_certificate": f"{TLS}/{name}-cert.pem",
        "connect_private_key": f"{TLS}/{name}-key.pem",
        "enable_mtls": True, "verify_name_on_connect": False}))
    return zenoh.open(c)


def router(config):
    sh("podman", "rm", "-f", ROUTER, check=False)
    sh("podman", "run", "-d", "--name", ROUTER, "--network", "host",
       "-v", f"{WORK}/{config}:/etc/zenoh/config.json5:ro", "-v", f"{TLS}:/etc/zenoh/tls:ro",
       ZENOH_IMAGE, "-c", "/etc/zenoh/config.json5")

    def up():
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=0.5).close()
            return True
        except OSError:
            return False
    if not wait_for(up, 15):
        logs = sh("podman", "logs", ROUTER, check=False).stderr[-400:]
        sys.exit(f"the router did not listen on {PORT}: {logs}")


def install(dest):
    shutil.copy(f"{WORK}/{dest}.container", f"{UNITS}/octo-egress-{dest}.container")
    sh("systemctl", "daemon-reload")


def uninstall(dest):
    sh("systemctl", "stop", f"octo-egress-{dest}.service", check=False)
    try:
        os.remove(f"{UNITS}/octo-egress-{dest}.container")
    except FileNotFoundError:
        # Uninstall is also valid when the unit has already been removed.
        pass
    sh("systemctl", "daemon-reload")
    sh("systemctl", "reset-failed", f"octo-egress-{dest}.service", check=False)


def audit(dest):
    try:
        with open(f"{WORK}/audit/{dest}/{dest}-egress.ndjson") as audit_file:
            lines = audit_file.read().splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def session_events(dest):
    return [a.get("event") for a in audit(dest) if a.get("kind") == "session"]


def last_stat(unit, field, since):
    out = sh("journalctl", "-u", unit, "--since", since, "-o", "cat", "--no-pager").stdout
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    hits = re.findall(rf"\b{field}=(\d+)", plain)
    return int(hits[-1]) if hits else None


def registered(ctl, after=0, seconds=30):
    return wait_for(lambda: len(ctl.of("registration")) > after, seconds)


ctl = Controller(PEER_PORT)

# ======================================================================================
print("released: the unit as apply writes it, under systemd, on the real ACL")
gen = f"{WORK}/quadlet"
os.makedirs(gen, exist_ok=True)
shutil.copy(f"{WORK}/controller.container", f"{gen}/octo-egress-controller.container")
dry = subprocess.run(["/usr/lib/systemd/system-generators/podman-system-generator", "--dryrun"],
                     capture_output=True, text=True, env=dict(os.environ, QUADLET_UNIT_DIRS=gen))
exec_line = next((line for line in dry.stdout.splitlines() if line.startswith("ExecStart=")), "")
check("Podman's own Quadlet generator turns the rendered unit into a service that runs the "
      "sapient binary from the declared image, on the host network, with no new privileges",
      dry.returncode == 0 and "/usr/local/bin/sapient" in exec_line
      and "localhost/octo-sapient:prove" in exec_line
      and re.search(r"--net(work)?[= ]host\b", exec_line)
      and "no-new-privileges" in exec_line,
      f"rc={dry.returncode} {dry.stderr[-300:]} {exec_line[:400]}")

router("router-released.json5")
bridge = session("bridge")
outsider = session("uplink")
install("controller")
t0 = time.strftime("%Y-%m-%d %H:%M:%S")
started = sh("systemctl", "start", "octo-egress-controller.service", check=False)
check("systemd starts the unit", started.returncode == 0, started.stderr[-300:])
top = sh("podman", "top", "octo-egress-controller", "huser", check=False).stdout.split()
check("the arm runs as uid 65532, the uid the playbook builds the key and audit modes around",
      top[1:] == ["65532"], f"{top}")
start = [a for a in audit("controller") if a.get("kind") == "start"]
check("its audit record opens with its declared pen keys and its one destination",
      start and start[0].get("keys") == [f"downlink/{PUBLISHER}/sapient/**"]
      and start[0].get("destination") == f"sapient://127.0.0.1:{PEER_PORT}",
      f"{audit('controller')[:2]}")
check("it dials the controller and registers", registered(ctl), f"{ctl.got[:2]}")
reg = (ctl.of("registration") or [sm.SapientMessage()])[0]
check("as a radar node, at the declared heartbeat, under the standard's ICD version",
      reg.registration.icd_version == "BSI Flex 335 v2.0"
      and [d.node_type for d in reg.registration.node_definition]
      == [rg.Registration.NODE_TYPE_RADAR]
      and reg.registration.status_definition.status_interval.value == 1.0, str(reg)[:400])
check("an accepted node heartbeats under the node id it registered with",
      wait_for(lambda: len(ctl.of("status_report")) >= 3, 8)
      and all(s.node_id == reg.node_id for s in ctl.of("status_report")),
      f"{len(ctl.of('status_report'))} status reports")
node_files = glob.glob(f"{WORK}/audit/controller/state/node-id")
node_id_matches = False
if node_files and os.stat(node_files[0]).st_uid == 65532:
    with open(node_files[0]) as node_file:
        node_id_matches = node_file.read().strip() == reg.node_id
check("its node id is kept under its audit directory, written as uid 65532",
      node_id_matches, f"{node_files}")
check("the audit record holds the session's lifecycle, not the pen",
      wait_for(lambda: "registered" in session_events("controller"), 5),
      f"{session_events('controller')}")

payload = detection("relayed-1")
bridge.put(DECLARED, payload)
check("a SAPIENT detection the downlink bridge writes on a declared key reaches the "
      "controller, re-addressed under the arm's node id",
      wait_for(lambda: ctl.reports("relayed-1"), 8)
      and ctl.reports("relayed-1")[0].node_id == reg.node_id,
      f"{[(m.node_id, m.detection_report.id) for m in ctl.of('detection_report')]}")
sent = [a for a in audit("controller") if a.get("kind") == "sent"]
check("its audit line names the fabric key and the pen key, and hashes what came down",
      sent and sent[-1].get("fabric_key") == f"{PUBLISHER}/sapient/radar1"
      and sent[-1].get("pen_key") == DECLARED and sent[-1].get("len") == len(payload)
      and sent[-1].get("payload_hash") == "sha256:" + hashlib.sha256(payload).hexdigest(),
      f"{sent[-1:] or audit('controller')[-3:]}")

task = envelope(Controller.ID)
task.destination_id = reg.node_id
task.task.task_id = ulid()
task.task.control = tk.Task.CONTROL_START
ctl.send(task)
acks = lambda: [m for m in ctl.of("task_ack") if m.task_ack.task_id == task.task.task_id]
check("a task is answered with a rejection and a reason, because the node relays and "
      "cannot be tasked",
      wait_for(acks, 6) and acks()[0].task_ack.task_status == ta.TaskAck.TASK_STATUS_REJECTED
      and len(acks()[0].task_ack.reason) > 0, f"{ctl.of('task_ack')[:1]}")
check("and the task is in the audit record", wait_for(
    lambda: "task-received" in session_events("controller"), 5), f"{session_events('controller')}")

print("gates: what must not reach the controller")
sent_before = len([a for a in audit("controller") if a.get("kind") == "sent"])
bridge.put(f"downlink/{OTHER}/sapient/radar1", detection("undeclared-1"))
outsider.put(DECLARED, detection("outsider-1"))
bogus = envelope("translator-node")
bogus.registration.icd_version = "not this arm's"
bridge.put(DECLARED, bogus.SerializeToString())
time.sleep(3)
check("a detection on a downlink key the destination did not declare never arrives",
      not ctl.reports("undeclared-1"), "")
check("a detection written by an identity with no grant on the downlink namespace never "
      "arrives", not ctl.reports("outsider-1"), "")
check("a registration written on a declared key is refused by the arm, never sent, and "
      "recorded", not any(m.registration.icd_version == "not this arm's"
                          for m in ctl.of("registration"))
      and wait_for(lambda: "relay-dropped" in session_events("controller"), 5),
      f"{session_events('controller')[-4:]}")
check("none of the three has a sent line",
      len([a for a in audit("controller") if a.get("kind") == "sent"]) == sent_before, "")

first_node = reg.node_id
regs = len(ctl.of("registration"))
sh("systemctl", "restart", "octo-egress-controller.service")
check("after a restart the arm registers under the same node id",
      registered(ctl, regs) and ctl.of("registration")[-1].node_id == first_node,
      f"{first_node} then {[m.node_id for m in ctl.of('registration')]}")
uninstall("controller")
bridge.close()
outsider.close()

# ======================================================================================
print("writes: access control off, an observer on ** sees every write")
router("router-open.json5")
seen = []
observer = session("uplink")
watch = observer.declare_subscriber("**", lambda s: seen.append(str(s.key_expr)))
bridge = session("bridge")
time.sleep(1)
regs = len(ctl.of("registration"))
install("controller")
t1 = time.strftime("%Y-%m-%d %H:%M:%S")
sh("systemctl", "start", "octo-egress-controller.service")
check("the arm registers on the open router", registered(ctl, regs), "")
beats = len(ctl.of("status_report"))
wait_for(lambda: len(ctl.of("status_report")) >= beats + 2, 5)
task = envelope(Controller.ID)
task.destination_id = first_node
task.task.task_id = ulid()
task.task.control = tk.Task.CONTROL_START
ctl.send(task)
bridge.put(DECLARED, detection("open-1"))
check("the bridge's write reaches the controller", wait_for(lambda: ctl.reports("open-1"), 8), "")
check("the observer sees the bridge's write, so it is watching",
      wait_for(lambda: DECLARED in seen, 5), f"{seen[:5]}")
wait_for(lambda: last_stat("octo-egress-controller.service", "skipped", t1), 8)
time.sleep(2)
others = sorted(set(k for k in seen if k != DECLARED))
check("and nothing else: the arm wrote no capture, no egress mirror and no session record",
      not others, f"{others[:8]}")
skipped = last_stat("octo-egress-controller.service", "skipped", t1)
check("while the controller's frames were read and set aside, not published",
      (skipped or 0) > 0, f"skipped={skipped}")
uninstall("controller")
watch.undeclare()
observer.close()
bridge.close()

# ======================================================================================
print("shut: declared, not released")
router("router-shut.json5")
with open(f"{WORK}/controller-shut.container") as unit_file:
    shut_unit = unit_file.read()
sections = [line.strip() for line in shut_unit.splitlines() if line.startswith("[")]
check("the unit apply writes for an unreleased session destination has no install section, "
      "so nothing starts it at boot", "[Install]" not in sections
      and not any(line.startswith("WantedBy=") for line in shut_unit.splitlines()),
      f"{sections}")
bridge = session("bridge")
regs = len(ctl.of("registration"))
shutil.copy(f"{WORK}/controller-shut.container", f"{UNITS}/octo-egress-controller.container")
sh("systemctl", "daemon-reload")
wanted = sh("systemctl", "show", "-p", "WantedBy", "octo-egress-controller.service").stdout.strip()
check("and systemd has no target that pulls it in at boot", wanted == "WantedBy=", wanted)
# apply stops it. Started by hand anyway, the ACL still gives it nothing.
sh("systemctl", "start", "octo-egress-controller.service")
registered(ctl, regs, 15)
sent_before = len([a for a in audit("controller") if a.get("kind") == "sent"])
bridge.put(DECLARED, detection("shut-1"))
time.sleep(3)
check("started by hand anyway, an unreleased destination receives nothing: its certificate "
      "matches no subject", not ctl.reports("shut-1")
      and len([a for a in audit("controller") if a.get("kind") == "sent"]) == sent_before, "")
sh("systemctl", "stop", "octo-egress-controller.service")
bridge.close()

print("release: the router change and the rewritten unit, as apply makes them")
router("router-released.json5")
bridge = session("bridge")
regs = len(ctl.of("registration"))
install("controller")
sh("systemctl", "restart", "octo-egress-controller.service")
wanted = sh("systemctl", "show", "-p", "WantedBy", "octo-egress-controller.service").stdout
check("the released unit is wanted at boot again", "multi-user.target" in wanted, wanted)
check("it starts and registers", registered(ctl, regs), "")


def release_delivers():
    rid = f"released-{ulid()}"
    bridge.put(DECLARED, detection(rid))
    return wait_for(lambda: ctl.reports(rid), 2)


check("and delivers what the bridge writes", wait_for(release_delivers, 30),
      f"{session_events('controller')[-4:]}")
uninstall("controller")
bridge.close()

# ======================================================================================
print("refused: the allow list omits the destination's own target")
dials = ctl.connections
install("refused")
sh("systemctl", "start", "octo-egress-refused.service", check=False)
refusals = lambda: [a for a in audit("refused") if a.get("kind") == "refused"]
check("the arm records why it refused", wait_for(refusals, 10), f"{audit('refused')}")
check("naming the allow list", refusals() and "allow" in refusals()[0].get("reason", "").lower(),
      f"{refusals()[:1]}")
time.sleep(8)
status = sh("systemctl", "show", "octo-egress-refused.service",
            "-p", "ExecMainStatus", "-p", "NRestarts").stdout
check("it exits 2", "ExecMainStatus=2" in status, status)
check("and never dials the controller", ctl.connections == dials,
      f"{ctl.connections - dials} connections")
check("and systemd leaves it failed rather than restarting it into the same refusal",
      "NRestarts=0" in status and len(refusals()) == 1, f"{status} refusals={len(refusals())}")
uninstall("refused")

print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
sys.exit(1 if FAIL else 0)
