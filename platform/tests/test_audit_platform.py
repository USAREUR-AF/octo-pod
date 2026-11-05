#!/usr/bin/env python3
"""Exercise rendered platform scripts and release predicates using scratch state."""
import os
import itertools
from pathlib import Path
import re
import subprocess
import tempfile
import yaml
from jinja2 import Environment, FileSystemLoader
from test_audit_shell import run, stub


def run_checks(repo, check):
    repo=Path(repo)
    envj=Environment(loader=FileSystemLoader(str(repo/'platform/templates')),trim_blocks=True)
    envj.filters['regex_replace']=lambda s,p,r='':re.sub(p,r,str(s))
    play=yaml.safe_load((repo/'platform/verify-octo-pod.yml').read_text())[0]
    gate=next(t for t in play['tasks'] if t['name'].startswith('gate '))
    expr=gate['ansible.builtin.assert']['that'][0]
    pred=envj.compile_expression(expr)
    for flag,allow,want in [(True,'raw/a/**',True),(False,'raw/a/**extra',True),(True,'raw/a/**extra',False),(True,'other, raw/a/** ',True)]:
        got=pred(item={'release':{'to_fabric':flag},'namespace':'raw/a/**'},bridge_allow=allow)
        check(f'release predicate exact membership {flag} {allow}',got==want,str(got))
    router=(repo/'platform/templates/pen/octo-pen-zenoh-router.container.j2').read_text()
    check('router explicitly requests healthy notification on 4.x','PodmanArgs=--sdnotify=healthy' in router)
    bootstrap=(repo/'platform/bootstrap-octo-pod.yml').read_text()
    check('bootstrap holds healthy-notification runtime floor',"version('4.7', '>=')" in bootstrap)
    package=(repo/'tools/build-release.sh').read_text()
    check('package holds same runtime floor','podman (>= 4.7)' in package)
    configure=yaml.safe_load((repo/'platform/configure-octo-pod.yml').read_text())[0]
    envj.filters['product']=lambda a,b:list(itertools.product(a,b))
    envj.filters['bool']=lambda v:v is True or str(v).lower() in ('true','yes','1')
    declared=next(t['vars']['_declared'] for t in configure['tasks'] if '_declared' in t.get('vars',{}))
    known=envj.compile_expression(declared.strip()[2:-2].strip())
    check('enabled legacy arms are declared for orphan reaping',set(known(arm_enabled=True)) == {'octo-tcp-ingest','octo-udp-ingest'})
    check('disabled legacy arms remain eligible for explicit orphan reaping',known(arm_enabled=False)==[])
    reap=next((t for t in configure['tasks'] if t.get('name')=='sync-leg - no slot, no uplink bridge'),None)
    check('uplink removal is converged',reap is not None)
    if reap:
        pred=envj.compile_expression(reap['when'])
        check('uplink reap requires absent slot',pred(fabric_slot_anchor='') and not pred(fabric_slot_anchor='slot'))
        with tempfile.TemporaryDirectory() as td:
            t=Path(td);bin=t/'bin';bin.mkdir();calls=t/'calls'
            stub(bin,'podman','echo "podman $*" >> "$CALLS"; exit 0')
            stub(bin,'systemctl','echo "systemctl $*" >> "$CALLS"; exit 0')
            stub(bin,'rm','echo "rm $*" >> "$CALLS"; exit 0')
            code,out=run(['bash','-c',reap['ansible.builtin.shell']],dict(os.environ,PATH=f'{bin}:{os.environ["PATH"]}',CALLS=str(calls)))
            text=calls.read_text()
            check('uplink reap stops and removes only uplink',code==0 and 'stop octo-uplink-bridge.service' in text and 'rm -f octo-uplink-bridge' in text,out)
    with tempfile.TemporaryDirectory() as td:
        t=Path(td);bin=t/'bin';bin.mkdir();calls=t/'calls'
        stub(bin,'systemctl','echo "$*" >> "$CALLS"; exit 0')
        stub(bin,'podman','exit 0')
        ca=t/'ca';tls=t/'zenoh/tls';ca.mkdir();tls.mkdir(parents=True)
        egress=t/'octo-egress-test/tls';egress.mkdir(parents=True)
        down=t/'downlink/tls';down.mkdir(parents=True)
        def openssl(*args):
            subprocess.run(['openssl',*map(str,args)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        openssl('req','-x509','-newkey','rsa:2048','-nodes','-subj','/CN=test-ca','-days','3650','-keyout',ca/'ca-key.pem','-out',tls/'ca-roots.pem')
        (ca/'index').write_text('')
        (ca/'serial').write_text('1000\n')
        (ca/'issued').mkdir()
        (ca/'config').write_text(f"""[ca]
default_ca = fixture
[fixture]
database = {ca}/index
serial = {ca}/serial
new_certs_dir = {ca}/issued
certificate = {tls}/ca-roots.pem
private_key = {ca}/ca-key.pem
default_md = sha256
policy = names
unique_subject = no
[names]
commonName = supplied
""")
        for d in [egress,down]:
            openssl('req','-new','-newkey','rsa:2048','-nodes','-subj','/CN=fixture','-keyout',d/'leaf-key.pem','-out',t/'leaf.csr')
            openssl('ca','-batch','-notext','-config',ca/'config','-startdate','20200101000000Z','-enddate','20200102000000Z','-in',t/'leaf.csr','-out',d/'leaf-cert.pem')
        for d in [egress,down]:
            expired=subprocess.run(['openssl','verify','-CAfile',str(tls/'ca-roots.pem'),str(d/'leaf-cert.pem')],capture_output=True,text=True)
            check('renewal fixture is genuinely expired '+d.parent.name, expired.returncode != 0 and 'expired' in expired.stderr, expired.stderr)
        foreign=t/'octo-egress-foreign/tls';foreign.mkdir(parents=True)
        openssl('req','-x509','-newkey','rsa:2048','-nodes','-subj','/CN=foreign','-days','1','-keyout',foreign/'leaf-key.pem','-out',foreign/'leaf-cert.pem')
        foreign_before=(foreign/'leaf-cert.pem').read_bytes()
        originals=[(d/'leaf-cert.pem').read_bytes() for d in [egress,down]]
        ctx=dict(pen_state_dir=str(t),sub_leaf_dir=str(t/'sub'),arm_tls_dir=str(t/'arm'),downlink_tls_dir=str(down),cert_renew_days=60,cert_leaf_days=825)
        script=envj.get_template('pen/cert-renew.sh.j2').render(**ctx)
        script=script.replace('/etc/octo-egress-',str(t)+'/octo-egress-').replace('/etc/octo-arm-',str(t)+'/octo-arm-')
        code,out=run(['bash','-c',script],dict(os.environ,PATH=f'{bin}:{os.environ["PATH"]}',CALLS=str(calls)),timeout=30)
        check('renewal visits egress and downlink leaves',code==0 and all((d/'leaf-cert.pem').read_bytes()!=old for d,old in zip([egress,down],originals)),out)
        check('renewal leaves foreign signer untouched',(foreign/'leaf-cert.pem').read_bytes()==foreign_before)
        for d in [egress,down]:
            valid=subprocess.run(['openssl','verify','-CAfile',str(tls/'ca-roots.pem'),str(d/'leaf-cert.pem')],capture_output=True,text=True)
            check('renewed leaf validates with time checking '+d.parent.name,valid.returncode==0,valid.stderr)
        text=calls.read_text() if calls.exists() else ''
        check('renewal restarts both consumers','restart octo-egress-test.service' in text and 'restart octo-downlink-bridge.service' in text,text)
        # Render timer command with no network probe and execute with a recording timeout.
        ctx=dict(netbird_pending_key_file=str(t/'pending'),netbird_mgmt_url='https://mesh.example.invalid',inventory_hostname='pod-a',octo_apt_repo_url='')
        (t/'pending').write_text('synthetic')
        script=envj.get_template('host/netbird-enrol-pending.sh.j2').render(**ctx).replace('LOG=/var/log/netbird-enrol.log','LOG='+str(t/'enrol.log'))
        stub(bin,'netbird','exit 1')
        stub(bin,'timeout','echo "$*" >> "$CALLS"; case "$*" in *"netbird up"*) exit 124;; esac; exit 0')
        calls.write_text('')
        code,out=run(['bash','-c',script],dict(os.environ,PATH=f'{bin}:{os.environ["PATH"]}',CALLS=str(calls)))
        check('pending enrollment bounds netbird up and retains key', '--kill-after=5 30 netbird up' in calls.read_text() and (t/'pending').exists(),out)


if __name__=='__main__':
    failures=[]
    def check(label,ok,detail=''):
        print(('PASS ' if ok else 'FAIL ')+label)
        if not ok:failures.append(label);print(detail)
    run_checks(Path(__file__).resolve().parents[2],check)
    raise SystemExit(bool(failures))
