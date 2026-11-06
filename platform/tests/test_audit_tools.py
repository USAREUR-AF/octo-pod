#!/usr/bin/env python3
"""Exercise installer refusals and remote command construction with offline stubs."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
from test_audit_shell import run, stub


def run_checks(repo, check):
    repo=Path(repo)
    for tool,flags in [('build-release.sh',['--repo','--stage-only']),('install-to-pod.sh',['--version','--repo','--deb','--cert-identity']),('mint-key.sh',['--pod','--dest']),('build-airgap-bundle.sh',['--out']),('build-vm-image.sh',['--host','--manifest','--alias','--cpus','--memory','--disk'])]:
        for flag in flags:
            code,out=run(['bash',str(repo/'tools'/tool),flag])
            check(tool+' diagnoses missing '+flag,code not in (0,124) and 'needs a value' in out,out)
    for version in ['1.2.3junk','1.2.3.4','v1.2.3']:
        code,out=run(['bash',str(repo/'tools/build-release.sh'),version])
        check('builder refuses '+version,code!=0 and 'is not X.Y.Z' in out,out)

    with tempfile.TemporaryDirectory() as td:
        build=Path(td)/'source'
        shutil.copytree(repo,build,ignore=shutil.ignore_patterns('.git','target','__pycache__','dist','.venv','._*'))
        code,out=run(['bash',str(build/'tools/build-release.sh'),'0.4.0','--repo','example/pod.name'],dict(os.environ,COPYFILE_DISABLE='1',WITH_IMAGES='0'),timeout=30)
        check('landing-page fixture builds actual release',code==0,out)
        stage = build/'dist/stage'
        check('1Password command and completion are not owned by octo-pod',
              not (stage/'usr/bin/op').exists()
              and not (stage/'usr/share/bash-completion/completions/op').exists())
        check('otp command alias resolves to the same executable',
              (stage/'usr/bin/otp').is_symlink() and os.readlink(stage/'usr/bin/otp') == 'octo-pod')
        check('otp completion alias resolves to the same candidates',
              (stage/'usr/share/bash-completion/completions/otp').is_symlink()
              and os.readlink(stage/'usr/share/bash-completion/completions/otp') == 'octo-pod')
        control = (stage/'DEBIAN/control').read_text()
        check('package installs Bash completion dependency', 'bash-completion' in control)
        bash_completion = stage/'usr/share/bash-completion/completions/octo-pod'
        zsh_completion = stage/'usr/share/zsh/vendor-completions/_octo-pod'
        check('package installs both shell completion entries', bash_completion.is_file() and zsh_completion.is_file())
        if bash_completion.is_file():
            code, output = run(['bash', '-c', 'source "$1"; COMP_WORDS=(octo-pod get flows ""); COMP_CWORD=3; _octo_pod; printf "%s\\n" "${COMPREPLY[@]}"', 'bash', str(bash_completion)])
            check('packaged Bash completion is context aware', code == 0 and set(output.split()) == {'-o', '--output'}, output)
        if shutil.which('zsh') and zsh_completion.is_file():
            # Substitute only the install prefix, as this package is not installed here.
            entry = Path(td)/'_octo-pod'
            entry.write_text(zsh_completion.read_text().replace('/usr/share/bash-completion/completions/octo-pod', str(bash_completion)))
            code, output = run(['zsh', '-c', 'compadd() { shift; printf "%s\\n" "$@"; }; words=(octo-pod describe bridges ""); CURRENT=4; run_completion() { source "$1"; }; run_completion "$1"', 'zsh', str(entry)])
            check('packaged Zsh entry resolves bridge candidates', code == 0 and output.split() == ['uplink', 'downlink'], output)
        page=(build/'dist/index.html').read_text() if (build/'dist/index.html').exists() else ''
        match=re.search(r"--certificate-identity-regexp '([^']+)'",page)
        check('landing page contains a signer verification command',match is not None,page)
        if match:
            pattern=match.group(1)
            prefix='https://github.com/example/pod.name/'
            for identity,allowed in [
                (prefix+'.github/workflows/release.yml@refs/tags/octo-pod-v0.4.0',True),
                (prefix+'.github/workflows/test.yml@refs/tags/octo-pod-v0.4.0',False),
                (prefix+'.github/workflows/release.yml@refs/heads/main',False),
                ('https://github.com/example/podXname/.github/workflows/release.yml@refs/tags/octo-pod-v0.4.0',False),
                ('https://githubXcom/example/pod.name/.github/workflows/release.yml@refs/tags/octo-pod-v0.4.0',False),
            ]:
                check('generated landing-page signer '+identity,(re.search(pattern,identity) is not None)==allowed,pattern)

    with tempfile.TemporaryDirectory() as td:
        t=Path(td);bin=t/'bin';bin.mkdir();assets=t/'assets';assets.mkdir()
        deb=assets/'octo-pod_0.4.0_all.deb';deb.write_bytes(b'synthetic deb')
        (assets/'SHA256SUMS').write_text(hashlib.sha256(deb.read_bytes()).hexdigest()+'  '+deb.name+'\n')
        (assets/'SHA256SUMS.cosign-bundle').write_text('{}')
        calls=t/'calls';ident=t/'identity'
        stub(bin,'ssh', '''printf '%s\\n' "$*" >> "$CALLS"
case "$*" in
  *'mktemp -d'*) echo /tmp/octo-pod-install.ABCD1234 ;;
  *'dpkg-deb -f'*) echo ansible-core ;;
  *'sudo dpkg -i'*) exit "${TEST_INSTALL_RC:-0}" ;;
esac
exit 0''')
        stub(bin,'scp','echo "scp $*" >> "$CALLS"; exit 0')
        stub(bin,'cosign','printf "%s\\n" "$@" > "$TEST_IDENT"; exit "${COSIGN_RC:-0}"')
        stub(bin,'gh', '''case "$*" in
  'auth status') exit 0 ;;
  *'release view'*) printf 'octo-pod_0.4.0_all.deb\\t0\\n' ;;
  *'release download'*) for f in "$ASSETS"/*; do [ -f "$(basename "$f")" ] || cp "$f" .; done ;;
esac''')
        env=dict(os.environ,PATH=f'{bin}:{os.environ["PATH"]}',XDG_CACHE_HOME=str(t/'cache'),CALLS=str(calls),TEST_IDENT=str(ident),ASSETS=str(assets),NO_COLOR='1')
        cmd=['bash',str(repo/'tools/install-to-pod.sh'),'pod@pod.invalid','--repo','example/pod.name','--version','0.4.0']
        code,out=run(cmd,dict(env,COSIGN_RC='1'))
        check('installer signature failure explains refusal',code!=0 and 'signature does not check out' in out,out)
        args=ident.read_text().splitlines();pattern=args[args.index('--certificate-identity-regexp')+1]
        for identity,allowed in [('https://github.com/example/pod.name/.github/workflows/release.yml@refs/tags/octo-pod-v0.4.0',True),('https://github.com/example/podXname/.github/workflows/release.yml@refs/tags/octo-pod-v0.4.0',False),('https://github.com/example/pod.name/.github/workflows/test.yml@refs/tags/octo-pod-v0.4.0',False),('https://github.com/example/pod.name/.github/workflows/release.yml@refs/heads/main',False),('https://githubXcom/example/pod.name/.github/workflows/release.yml@refs/tags/octo-pod-v0.4.0',False),('https://github.com/EXAMPLE/Pod.Name/.github/workflows/release.yml@refs/tags/octo-pod-v0.4.0',True),('https://github.com/example/pod.name/.github/workflows/Release.yml@refs/tags/octo-pod-v0.4.0',False)]:
            check('installer signer '+identity,(re.search(pattern,identity) is not None)==allowed,pattern)
        code,out=run(cmd+['--cert-identity','^https://example.invalid/'],dict(env,COSIGN_RC='1'))
        check('installer explicit signer override is honored and printed','^https://example.invalid/' in ident.read_text() and '--cert-identity' in out,out)
        cached=t/'cache/octo-pod/0.4.0'
        (cached/deb.name).write_bytes(b'truncated')
        code,out=run(cmd,env,timeout=20)
        check('installer repairs corrupt cached package',code==0 and (cached/deb.name).read_bytes()==deb.read_bytes(),out)
        recorded=calls.read_text()
        check('installer uses a private remote directory','mktemp -d /tmp/octo-pod-install.' in recorded and '/tmp/octo-pod-install.ABCD1234/'+deb.name in recorded,recorded)
        check('installer rechecks copied hash before dpkg', 'sha256sum -c --status -' in recorded and recorded.index('sha256sum -c --status -') < recorded.index('sudo dpkg -i'),recorded)
        code,out=run(cmd,dict(env,TEST_INSTALL_RC='3'))
        check('installer remote hash failure refuses clearly',code!=0 and 'does not match the package verified here' in out,out)
        code,out=run(cmd,dict(env,TEST_INSTALL_RC='1'))
        check('installer install failure explains recovery',code!=0 and "did not reach 'install ok installed'" in out,out)
        # A corrupt metadata cache must not wedge the next invocation either.
        (cached/'SHA256SUMS').write_text('truncated metadata')
        code,out=run(cmd,env)
        check('installer refreshes cached metadata',code==0 and (cached/'SHA256SUMS').read_bytes()==(assets/'SHA256SUMS').read_bytes(),out)
        # Do not execute destructive remote commands; the SSH boundary is the assertion.
        stub(bin,'ssh','echo "$*" >> "$CALLS"; exit 1')
        calls.write_text('')
        code,out=run(['bash',str(repo/'tools/deprovision-pod.sh'),'pod@pod.invalid','--yes'],env)
        check('deprovision refuses non-pod even with yes',code!=0 and 'refusing to deprovision' in out and 'rm -' not in calls.read_text(),out)
        stub(bin,'ssh','echo "$*" >> "$CALLS"; exit 0')
        calls.write_text('')
        code,out=run(['bash',str(repo/'tools/deprovision-pod.sh'),'pod@pod.invalid','--yes','--keep-peer'],env)
        check('deprovision selects only octo containers',"grep '^octo-'" in calls.read_text() and 'podman rm -f $(sudo podman ps -aq)' not in calls.read_text(),out)
        # Execute only the credential staging command in a scratch directory. sudo is a stub.
        dest=t/'key';secret='00000000-0000-0000-0000-000000000001'
        stub(bin,'goat','printf "%s\\n" "00000000-0000-0000-0000-000000000001"')
        stub(bin,'sudo','exec "$@"')
        stub(bin,'ssh', '''for arg do last="$arg"; done
case "$last" in
  *'sudo sh -c'*) exec sh -c "$last" ;;
  *'stat -c %s'*) echo 36 ;;
esac
exit 0''')
        # Existing permissive destination must also be replaced safely.
        dest.write_text('old');dest.chmod(0o644)
        code,out=run(['bash',str(repo/'tools/mint-key.sh'),'pod@pod.invalid','--pod','pod-a','--dest',str(dest)],env)
        check('mint-key stages private credential',code==0 and dest.exists() and dest.stat().st_mode & 0o777==0o600 and dest.read_text()==secret,out)
        check('mint-key sets restrictive creation umask', 'umask 077' in (repo/'tools/mint-key.sh').read_text())


if __name__=='__main__':
    failures=[]
    def check(label,ok,detail=''):
        print(('PASS ' if ok else 'FAIL ')+label)
        if not ok:failures.append(label);print(detail)
    run_checks(Path(__file__).resolve().parents[2],check)
    raise SystemExit(bool(failures))
