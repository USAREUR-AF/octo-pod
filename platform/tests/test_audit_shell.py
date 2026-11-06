#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Failure-path tests for pod shell commands; temporary files and stubbed commands only."""
import os
from pathlib import Path
import subprocess
import tempfile
from test_upgrade import Pod, publish, sha256


def run(argv, env=None, timeout=10):
    try:
        r = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 124, 'timed out'


def stub(d, name, text):
    p = d / name
    p.write_text('#!/bin/sh\n' + text + '\n')
    p.chmod(0o755)


def run_checks(repo, check):
    repo = Path(repo)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        bin = t / 'bin'; bin.mkdir()
        etc = t / 'etc'; etc.mkdir()
        (etc / 'inventory.ini').write_text('[octopod]\npod-a\n')
        calls = t / 'calls'
        stub(bin, 'id', 'echo 0')
        stub(bin, 'ansible-playbook', 'printf "%s\\n" "$@" > "$CALLS"; echo "pod-a : changed=${CHANGED:-0} failed=${RC:-0}"; exit "${RC:-0}"')
        env = dict(os.environ, PATH=f'{bin}:{os.environ["PATH"]}', OCTO_POD_ETC=str(etc), CALLS=str(calls))
        cli = ['bash', str(repo / 'bin/octo-pod')]
        for rc, changed, success in [('0','0',True), ('1','0',False), ('0','1',False)]:
            code, out = run(cli + ['verify-clean'], dict(env, RC=rc, CHANGED=changed))
            check(f'verify-clean rc={rc} changed={changed}', (code == 0) == success and (success or '\nCLEAN' not in out), out)
        code, out = run(cli + ['init', '--pod'], env)
        check('init missing pod is a bounded usage failure', code not in (0,124) and 'needs' in out, out)
        for verb, play, flags in [('check','configure-octo-pod.yml',['--check','--diff']), ('apply','configure-octo-pod.yml',[]), ('verify','verify-octo-pod.yml',[])]:
            code,out = run(cli+[verb],env)
            args=calls.read_text().splitlines()
            check(f'dispatch {verb} selects its play and flags',code==0 and play in args and all(f in args for f in flags) and (verb=='check' or '--check' not in args),out)
        code,out=run(cli,env)
        check('help lists set-mesh and remote', 'octo-pod set-mesh' in out and 'octo-pod remote' in out,out)
        check('help explains immediate removal', 'Removal verbs stop containers immediately' in out,out)
        join=['bash',str(repo/'bin/octo-pod-join')]
        code,out=run(join+['--help'],env)
        check('join help includes synopsis',code==0 and '--setup-key-file' in out and '--mgmt' in out and '--pod' in out,out)
        for flag in ['--setup-key','--setup-key-file','--mgmt','--pod']:
            code,out=run(join+[flag],env)
            check(f'join {flag} missing value',code not in (0,124) and 'needs a value' in out,out)
        secret='synthetic-key-do-not-print'
        # Name each case: a label built from the arguments would print the credential
        # itself for the bare-argument case, in a test that exists to keep it unprinted.
        for case,args in [('--setup-key',['--setup-key='+secret]),('--typo',['--typo='+secret]),
                          ('as a bare argument',[secret])]:
            code,out=run(join+args,env)
            check('join never echoes rejected credential '+case,code!=0 and secret not in out,
                  out.replace(secret,'<credential>'))
        code,out=run(join+['--setup-key='+secret],env)
        check('join accepts equals form before checking required fields','unknown option' not in out,out)
        # A quoted token path is data, not Python source; invalid pod names write nothing.
        src=t/"token's directory";src.mkdir()
        identity=['bash',str(repo/'bin/octo-pod-identity'),'write',str(src)]
        for name in ['../escape','bad/name','Upper','bad name']:
            (src/'manifest.yaml').write_text('pod_name: '+name+'\n')
            fresh=t/'identity'
            code,out=run(identity,dict(env,OCTO_POD_ETC=str(fresh),OCTO_FABRIC_DIR=str(t/'fabric')))
            check('identity rejects path-like pod name '+name,code!=0 and not fresh.exists() and 'SyntaxError' not in out,out)
        (src/'manifest.yaml').write_text('pod_name: pod-safe\n')
        code,out=run(identity,dict(env,OCTO_POD_ETC=str(t/'identity'),OCTO_FABRIC_DIR=str(t/'fabric')))
        check('identity accepts a quoted token directory',code==0 and (t/'identity/host_vars/pod-safe/10-pod.yml').exists(),out)

    script=repo/'bin/octo-pod-upgrade'
    for args in [('--check','--rollback'),('--rollback','--check'),('--images','--check'),('--images','--rollback'),('--list','--describe')]:
        with tempfile.TemporaryDirectory() as td:
            pod=Pod(Path(td),script,'0.4.0',installed=('0.3.9',))
            before=pod.root.resolve()
            code,out=pod.run(*args)
            check('upgrade refuses conflicting modes '+' '.join(args),code!=0 and pod.root.resolve()==before,out)
    for defect in ['missing-cli','missing-platform','ln','state']:
        with tempfile.TemporaryDirectory() as td:
            pod=Pod(Path(td),script,'0.4.0',installed=('0.3.9',))
            target=pod.opt/'octo-pod-0.3.9'
            if defect=='missing-cli':(target/'bin/octo-pod').unlink()
            if defect=='missing-platform':(target/'platform/configure-octo-pod.yml').unlink()
            if defect=='ln':pod.stub('ln','#!/bin/sh\nexit 1\n')
            if defect=='state':
                pod.state.mkdir(exist_ok=True);(pod.state/'upgrade-previous').mkdir()
            before=pod.root.resolve()
            code,out=pod.run('--rollback')
            check('swap refuses '+defect,code!=0 and pod.root.resolve()==before and 'Now on' not in out,out)
    with tempfile.TemporaryDirectory() as td:
        pod=Pod(Path(td),script,'0.4.0');publish(pod,'0.4.1',latest=True,images=True)
        old=pod.stage/'images';old.mkdir(parents=True);(old/'MAP').write_text('old stage')
        bundle=pod.mirror/'0.4.1/octo-pod-images-0.4.1.tar.gz'
        bundle.write_bytes(b'not a gzip archive')
        (bundle.parent/'SHA256SUMS').write_text(f'{sha256(bundle)}  {bundle.name}\n')
        code,out=pod.run('--images')
        check('failed unpack preserves old stage and fails',code!=0 and (old/'MAP').read_text()=='old stage' and 'unpacked to' not in out,out)
    with tempfile.TemporaryDirectory() as td:
        pod=Pod(Path(td),script,'0.4.0')
        (pod.mirror/'LATEST').write_text('\x1b[31mnot-a-version\x07\n')
        code,out=pod.run('--check')
        check('mirror version is terminal-safe',code!=0 and '\x1b' not in out and '\x07' not in out,out)
        publish(pod,'0.4.1',latest=True)
        pod.cosign_says(1,'\x1b[31mdenied\x07')
        code,out=pod.run()
        check('cosign failure is terminal-safe',code!=0 and '\x1b' not in out and '\x07' not in out,out)


if __name__=='__main__':
    failures=[]
    def check(label,ok,detail=''):
        print(('PASS ' if ok else 'FAIL ')+label)
        if not ok:failures.append(label);print(detail)
    run_checks(Path(__file__).resolve().parents[2],check)
    raise SystemExit(bool(failures))
