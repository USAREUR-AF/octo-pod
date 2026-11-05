#!/usr/bin/env python3
"""Exercise endpoint corrections on a scratch identity; never touch a real pod."""
import copy
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import yaml
from jinja2 import Environment, FileSystemLoader
from test_cli import Scratch, ARMS_YML, ARM_A, SHIM


def run_checks(repo, check):
    repo = Path(repo)
    with tempfile.TemporaryDirectory() as td:
        s = Scratch(repo, td)
        s.write('30-arms.yml', ARMS_YML)
        path = s.pod/'30-arms.yml'; path.chmod(0o640)
        original = yaml.safe_load(ARMS_YML)
        key = s.root/'private-key';key.write_bytes(b'unchanged-test-key')
        before_files = {p: p.read_bytes() for p in s.root.rglob('*') if p.is_file()}
        rc, out = s.run('octo-pod-edit', 'arms', ARM_A, '--from', '192.0.2.20', '--udp', '8601', '--bind', '192.0.2.1')
        changed = yaml.safe_load(path.read_text())
        expected = copy.deepcopy(original); expected['arms'][0].update(allow_sources='192.0.2.20',port=8601,bind_host='192.0.2.1')
        check('edit changes only requested endpoint fields',rc==0 and changed==expected,out)
        check('edit preserves comments and file mode','# shut until' in path.read_text() and path.stat().st_mode & 0o777==0o640)
        check('edit touches no other identity or key file',all(p.read_bytes()==v for p,v in before_files.items() if p!=path))
        check('edit starts no service',not (s.state/'calls').exists())
        text=path.read_bytes()
        rc,out=s.run('octo-pod-edit','arms',ARM_A,'--udp','8601')
        check('unchanged edit is a byte-preserving no-op',rc==0 and path.read_bytes()==text and 'No changes' in out,out)
        for args in [('--udp','48022'),('--udp','0'),('--udp','65536'),('--udp','22'),('--udp','7447'),('--from','not-an-ip'),('--from','192.0.2.1,'),('--from','::1'),('--bind','bad'),('--tcp','8000'),('--dial','192.0.2.1:8000'),('--udp','8000','--tcp','8001'),(),('--feed','new')]:
            before=path.read_bytes();rc,out=s.run('octo-pod-edit','arms',ARM_A,*args)
            check('edit rejects '+repr(args),rc!=0 and path.read_bytes()==before,out)
        rc,out=s.run('octo-pod-edit','arms','missing','--udp','8000')
        check('edit refuses missing identity',rc!=0 and path.read_bytes()==text,out)
        dial=copy.deepcopy(original);dial['arms'][0].update(kind='tcp-connect',transport='tcp',target='192.0.2.10:7000');dial['arms'][0].pop('port')
        s.write('30-arms.yml',yaml.safe_dump(dial))
        rc,out=s.run('octo-pod-edit','arm',ARM_A,'--dial','192.0.2.20:7001')
        current=yaml.safe_load(path.read_text())['arms'][0]
        check('dial correction updates endpoint and exact source together',rc==0 and current['target']=='192.0.2.20:7001' and current['allow_sources']=='192.0.2.20',out)
        before=path.read_bytes();rc,out=s.run('octo-pod-edit','arms',ARM_A,'--dial','sensor.example:7001')
        check('hostname correction requires explicit source trust',rc!=0 and path.read_bytes()==before,out)
        rc,out=s.run('octo-pod-edit','arms',ARM_A,'--dial','sensor.example:7001','--from','192.0.2.30')
        check('hostname with explicit source works',rc==0 and yaml.safe_load(path.read_text())['arms'][0]['allow_sources']=='192.0.2.30',out)
        tcp=copy.deepcopy(original);tcp['arms'][0].update(kind='tcp-ingest',transport='tcp')
        s.write('30-arms.yml',yaml.safe_dump(tcp));s.write('40-egress.yml','egress:\n- id: receiver\n  dest: tcp-server://192.0.2.1:9000\n')
        before=path.read_bytes();rc,out=s.run('octo-pod-edit','arms',ARM_A,'--tcp','9000')
        check('TCP port cannot collide with egress listener',rc!=0 and path.read_bytes()==before,out)
        released=copy.deepcopy(original);released['arms'][0]['release']['to_fabric']=True
        s.write('30-arms.yml',yaml.safe_dump(released));before=path.read_bytes()
        rc,out=s.run('octo-pod-edit','arms',ARM_A,'--from','192.0.2.30','--dry-run')
        check('dry run previews released source correction without writing',rc==0 and path.read_bytes()==before and 'requires --acknowledge-trust-change' in out,out)
        rc,out=s.run('octo-pod-edit','arms',ARM_A,'--from','192.0.2.30')
        check('released source cannot change silently',rc!=0 and path.read_bytes()==before,out)
        rc,out=s.run('octo-pod-edit','arms',ARM_A,'--from','192.0.2.30','--acknowledge-trust-change')
        expected_released=copy.deepcopy(released);expected_released['arms'][0]['allow_sources']='192.0.2.30'
        check('acknowledged correction preserves identity and release grant',rc==0 and yaml.safe_load(path.read_text())==expected_released,out)
        for flag in ['--id','--cert-cn','--namespace','--kind','--feed','--trust-tier','--release']:
            before=path.read_bytes();rc,out=s.run('octo-pod-edit','arms',ARM_A,flag,'changed')
            check('protected field refused '+flag,rc!=0 and path.read_bytes()==before,out)
        s.write('30-arms.yml',yaml.safe_dump(tcp));before=path.read_bytes()
        # Inject a write failure: the declaration must survive and private scratch be removed.
        shim=SHIM.replace('p = sys.argv[1];', 'os.replace = lambda *a: (_ for _ in ()).throw(OSError("synthetic rename failure")); p = sys.argv[1];')
        p=subprocess.run([sys.executable,'-c',shim,str(repo/'bin/octo-pod-edit'),'arms',ARM_A,'--tcp','8602'],env=s.env,capture_output=True,text=True)
        check('failed atomic replacement leaves original untouched',p.returncode!=0 and path.read_bytes()==before and not list(s.pod.glob('.octo-edit-*')),p.stderr)
        # The actual platform template must carry the changed endpoint into the service.
        env=Environment(loader=FileSystemLoader(str(repo/'platform/templates')))
        env.filters['regex_replace']=lambda s,p,r:re.sub(p,r,s)
        template=env.get_template('arm/octo-arm.container.j2')
        context=dict(udp_ingest_image='image:test',tcp_ingest_image='image:test',tcp_connect_image='image:test',mesh_ip='192.0.2.1',pen_listen_port=7447,audit_runtime_uid=1000)
        old_unit=template.render(a=original['arms'][0],**context)
        new_unit=template.render(a=expected['arms'][0],**context)
        check('rendered arm uses corrected source and port',old_unit!=new_unit and 'feed1=8601' in new_unit and 'ALLOW_SOURCES=192.0.2.20' in new_unit)
        play=yaml.safe_load((repo/'platform/configure-octo-pod.yml').read_text())[0]
        tasks=play['tasks']; rendered=next(t for t in tasks if t.get('name')=='partner-arm — quadlet'); start=next(t for t in tasks if t.get('name')=='partner-arm — up')
        check('apply consumes the render results',rendered.get('register')=='_rendered_partner_arms' and '_rendered_partner_arms.results' in start['loop'])
        for changed_flag, state in [(True,'restarted'),(False,'started')]:
            item={'item':original['arms'][0], 'changed':changed_flag}
            opts={k:env.from_string(v).render(item=item) for k,v in start['ansible.builtin.systemd'].items()}
            check('changed arm restart policy '+str(changed_flag),opts=={'name':f'octo-udp-ingest-{ARM_A}.service','state':state},str(opts))


if __name__=='__main__':
    failures=[]
    def check(label,ok,detail=''):
        print(('PASS ' if ok else 'FAIL ')+label)
        if not ok: failures.append(label);print(detail)
    run_checks(Path(__file__).resolve().parents[2],check)
    raise SystemExit(bool(failures))
