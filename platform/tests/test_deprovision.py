#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Execute the remote cleanup payload against disposable paths and runtime stubs."""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile


def run_checks(repo, check):
    repo = Path(repo)
    source = (repo / 'tools/deprovision-pod.sh').read_text()
    subscriber_dir = '/etc/octo-pen-uplink'.lstrip('/')
    payload = source.split("<<'CLEANUP'\n", 1)[1].split('\nCLEANUP\n', 1)[0]
    preflight = source.split("<<'PREFLIGHT'", 1)[1].split('\n', 1)[1].split('\nPREFLIGHT\n', 1)[0]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bindir = root / 'bin'
        bindir.mkdir()
        driver = bindir / 'driver'
        driver.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
path = Path(os.environ['STATE'])
s = json.loads(path.read_text())
with open(os.environ['CALLS'], 'a') as log: log.write(name + ' ' + ' '.join(args) + '\\n')
if name == s.get('fail'): sys.exit(23)
if name == 'systemctl':
    if args[0] == 'list-units': print('octo-health-write.timer loaded active waiting')
elif name == 'podman':
    if args[0] == 'ps': print('\\n'.join(s['containers']))
    elif args[0] == 'rm' and not s.get('retain_container'): s['containers'].remove(args[-1])
elif name == 'nft':
    if args[:2] == ['list','tables']: print('\\n'.join('table inet ' + t for t in s['tables']))
    elif args[:2] == ['delete','table']: s['tables'].remove(args[-1])
elif name == 'dpkg-query':
    for p in s['packages']: print(p + ' installed')
elif name == 'apt-get':
    assert args[:2] == ['purge','-y'], args
    s['packages'].remove(args[-1])
elif name == 'netbird': print('NetBird IP: 100.127.0.8/16')
elif name == 'ip': print(s.get('route', '192.0.2.2 dev eth0 src 192.0.2.1'))
elif name == 'sudo':
    os.execvp(args[1], args[1:])
else: raise AssertionError(name)
path.write_text(json.dumps(s))
''')
        driver.chmod(0o755)
        for name in ('systemctl', 'podman', 'nft', 'dpkg-query', 'apt-get', 'netbird', 'ip', 'sudo'):
            (bindir / name).symlink_to(driver)
        # No destructive command can address the real host: rewrite all absolute
        # filesystem roots in the production payload, including globs and redirections.
        def mapped(script, fs):
            return re.sub(r'/etc/|/var/|/usr/local/|/opt/', lambda m: str(fs) + m[0], script)

        def fixture(name, **overrides):
            fs = root / name
            fs.mkdir()
            paths = ['etc/octo-pod/inventory.ini', 'var/lib/octo-pen/ca.key',
                     'var/lib/octo-egress/audit', 'etc/octo-arm-demo/tls/key',
                     'etc/octo-egress-demo/tls/key', 'etc/octo-downlink/tls/key',
                     'etc/fabric-uplink/key', subscriber_dir + '/key',
                     'etc/netbird/config.json', 'opt/octo-stage/images',
                     'etc/cron.d/netbird-selfheal', 'etc/systemd/system/net-isolation.service',
                     'etc/systemd/system/octo-pod-firewall.service',
                     'etc/systemd/system/octo-health-write.timer',
                     'etc/systemd/system/pen-cert-renew.timer',
                     'etc/containers/systemd/octo-arm.container',
                     'etc/containers/systemd/other.container', 'etc/netplan/50-octo-pod.yaml',
                     'etc/octo-pod-firewall.nft', 'etc/nftables-net-isolation.nft',
                     'usr/local/sbin/octo-watchdog', 'usr/local/sbin/pen-cert-renew']
            for path in paths:
                target = fs / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('retained fixture\n')
            state = fs / 'state.json'
            state.write_text(json.dumps(dict(containers=['octo-arm', 'shared-app'],
                                            tables=['octo_pod', 'net_isolation', 'shared'],
                                            packages=['octo-pod', 'netbird', 'podman'], **overrides)))
            env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'],
                       STATE=str(state), CALLS=str(fs / 'calls'), SSH_CONNECTION='192.0.2.2 1234 192.0.2.1 22')
            return fs, env

        def execute(fs, env, flags, script=payload):
            return subprocess.run(['bash', '-s', '--', *map(str, flags)], input=mapped(script, fs),
                                  env=env, text=True, capture_output=True, timeout=10)

        fs, env = fixture('retain')
        result = execute(fs, env, [0, 0, 0])
        check('remove executes successfully', result.returncode == 0, result.stderr)
        state = json.loads((fs / 'state.json').read_text())
        check('remove retains shared runtime containers and firewall',
              state == dict(containers=['shared-app'], tables=['shared'], packages=['netbird', 'podman']), state)
        for path in ('etc/octo-pod/inventory.ini', 'var/lib/octo-pen/ca.key', 'var/lib/octo-egress/audit',
                     'etc/octo-arm-demo/tls/key', 'etc/netbird/config.json', 'opt/octo-stage/images',
                     'etc/netplan/50-octo-pod.yaml', 'etc/containers/systemd/other.container'):
            check('remove preserves ' + path, (fs / path).read_text() == 'retained fixture\n', path)
        for path in ('etc/cron.d/netbird-selfheal', 'etc/systemd/system/net-isolation.service',
                     'etc/systemd/system/octo-pod-firewall.service', 'etc/systemd/system/octo-health-write.timer',
                     'etc/systemd/system/pen-cert-renew.timer', 'usr/local/sbin/octo-watchdog',
                     'usr/local/sbin/pen-cert-renew', 'etc/octo-pod-firewall.nft'):
            check('remove cleans ' + path, not (fs / path).exists(), path)
        result = execute(fs, env, [0, 0, 0])
        check('repeated removal succeeds', result.returncode == 0, result.stderr)
        result = execute(fs, env, [1, 1, 1])
        check('purge after removal succeeds', result.returncode == 0, result.stderr)
        for path in ('etc/octo-pod', 'var/lib/octo-pen', 'var/lib/octo-egress', 'etc/octo-arm-demo',
                     'etc/octo-egress-demo', 'etc/octo-downlink', 'etc/fabric-uplink', subscriber_dir,
                     'etc/netbird', 'opt/octo-stage'):
            check('purge erases ' + path, not (fs / path).exists(), path)
        check('receipt keeps mesh selector for retry', (fs / 'var/lib/octo-uninstall/mesh-ip').read_text().strip() == '100.127.0.8', '')
        result = execute(fs, env, [1, 1, 1])
        check('repeated purge succeeds', result.returncode == 0, result.stderr)
        result = execute(fs, env, [0], preflight)
        check('receipt permits retry after identity deletion', result.returncode == 0, result.stderr)
        fs, env = fixture('reinstalled')
        receipt = fs / 'var/lib/octo-uninstall'
        receipt.mkdir()
        (receipt / 'mesh-ip').write_text('100.127.0.8\n')
        (receipt / 'peer-retired').touch()
        result = execute(fs, env, [1, 0, 1])
        check('reinstallation invalidates prior peer retirement even if address reused',
              result.returncode == 0 and not (receipt / 'peer-retired').exists(), result.stderr)
        fs, env = fixture('keep-netbird')
        result = execute(fs, env, [1, 0, 0])
        check('purge with keep-netbird preserves mesh state and bundle', result.returncode == 0 and
              (fs / 'etc/netbird/config.json').exists() and (fs / 'opt/octo-stage/images').exists(), result.stderr)
        fs, env = fixture('mesh', route='192.0.2.2 dev wt0 src 100.127.0.8')
        result = execute(fs, env, [1], preflight)
        check('mesh route refuses before mutation', result.returncode != 0 and not (fs / 'var/lib/octo-uninstall').exists(), result.stderr)
        fs, env = fixture('renamed-mesh', route='192.0.2.2 dev vpn0 src 100.127.0.8')
        result = execute(fs, env, [1], preflight)
        check('renamed mesh route refuses', result.returncode != 0, result.stderr)
        for tool in ('systemctl', 'podman', 'nft', 'apt-get', 'dpkg-query'):
            fs, env = fixture('fail-' + tool, fail=tool)
            result = execute(fs, env, [1, 1, 1])
            check('cleanup propagates ' + tool + ' failure', result.returncode != 0 and
                  (fs / 'etc/octo-pod/inventory.ini').exists(), result.stderr)
            state = json.loads((fs / 'state.json').read_text())
            state.pop('fail')
            (fs / 'state.json').write_text(json.dumps(state))
            result = execute(fs, env, [1, 1, 1])
            check('cleanup resumes after ' + tool + ' failure', result.returncode == 0, result.stderr)
        fs, env = fixture('lying-runtime', retain_container=True)
        result = execute(fs, env, [0, 0, 0])
        check('postcondition detects container left behind despite zero exit', result.returncode != 0 and 'containers remain' in result.stderr, result.stderr)


if __name__ == '__main__':
    failures = []
    def report(name, passed, detail):
        print(('PASS ' if passed else 'FAIL ') + name)
        if not passed:
            failures.append(name)
            print(detail)
    run_checks(Path(__file__).resolve().parents[2], report)
    raise SystemExit(bool(failures))
