#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Carry digest-pinned images from a trusted checkout to an installed pod.

Default: build, verify and stage. --load explicitly seeds the pod's loopback registry.
No package installation, enrollment, apply or application restart is performed.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile

PINS = {'zenoh': 'zenoh', 'audit-sink': 'audit', 'udp-ingest': 'udp_ingest',
        'tcp-ingest': 'tcp_ingest', 'tcp-connect': 'tcp_connect',
        'health-pub': 'health_pub', 'egress': 'egress', 'sapient': 'sapient',
        'uplink-bridge': 'uplink_bridge'}
PINFILE = 'platform/group_vars/all/10-infrastructure.yml'
LIMIT = 32 * 1024**3
STAGES = Path('/var/lib/octo-pod/image-delivery')


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(1024*1024), b''):
            h.update(data)
    return h.hexdigest()


def pins(root):
    text = (Path(root)/PINFILE).read_text()
    result = {}
    for tag, var in PINS.items():
        matches = re.findall(r'^' + var + r'_image_upstream: *"([^"\n]+)" *(?:#.*)?$', text, re.M)
        require(len(matches) == 1 and re.fullmatch(r'[A-Za-z0-9._:/-]+@sha256:[a-f0-9]{64}', matches[0]),
                f'missing, duplicate or unpinned image: {tag}')
        result[tag] = matches[0]
    return result


def map_rows(directory):
    result = {}
    for line in (directory/'images/MAP').read_text().splitlines():
        parts = line.split()
        require(len(parts) == 3, 'invalid MAP entry')
        tag, path, digest = parts
        require(tag in PINS and tag not in result and re.fullmatch(r'[a-z0-9._/-]+', path)
                and '..' not in path.split('/') and not path.startswith('/')
                and re.fullmatch(r'sha256:[a-f0-9]{64}', digest), 'unsafe or duplicate MAP entry')
        result[tag] = (path, digest)
    require(set(result) == set(PINS), 'MAP must contain all eight pinned images')
    return result


def validate(directory, expected):
    rows = map_rows(directory)
    for tag, ref in expected.items():
        repo, digest = ref.split('@')
        path = repo.split('/', 1)[1] if '.' in repo.split('/')[0] or ':' in repo.split('/')[0] else repo
        require(rows[tag] == (path, digest), f'bundle pin mismatch: {tag}')
        blob = directory/'images/oci/blobs/sha256'/digest.split(':')[1]
        require(blob.is_file() and sha(blob) == digest.split(':')[1], f'index digest mismatch: {tag}')
        index = json.loads(blob.read_text())
        arches = {m.get('platform', {}).get('architecture') for m in index.get('manifests', [])
                  if m.get('platform', {}).get('os') == 'linux'}
        require({'amd64', 'arm64'} <= arches, f'{tag} must carry amd64 and arm64')
    # Verify every OCI descriptor recursively, including all layers, before transfer/load.
    visited = set()
    def descriptor(d):
        digest = d.get('digest', '')
        require(re.fullmatch(r'sha256:[a-f0-9]{64}', digest), 'unsupported OCI digest')
        path = directory/'images/oci/blobs/sha256'/digest[7:]
        require(path.is_file() and path.stat().st_size == d.get('size') and sha(path) == digest[7:],
                'missing or corrupt OCI blob')
        if digest in visited:
            return
        visited.add(digest)
        if 'manifest' in d.get('mediaType', '') or 'index' in d.get('mediaType', ''):
            item = json.loads(path.read_text())
            for child in item.get('manifests', []) + item.get('layers', []):
                descriptor(child)
            if 'config' in item:
                descriptor(item['config'])
    index = json.loads((directory/'images/oci/index.json').read_text())
    for entry in index['manifests']:
        descriptor(entry)
    require(all(ref.split('@')[1] in visited for ref in expected.values()), 'OCI index omits a required pin')
    for arch in ('amd64', 'arm64'):
        for tool in ('registry', 'skopeo'):
            require((directory/f'images/{tool}-{arch}.tar').is_file(), f'missing {tool} for {arch}')
    for tool in ('REGISTRY_IMAGE', 'SKOPEO_IMAGE'):
        value = (directory/'images'/tool).read_text().strip()
        require(re.fullmatch(r'[A-Za-z0-9._:/@-]+', value), 'invalid tool image reference')


def files(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        require(not path.is_symlink() and (path.is_file() or path.is_dir()), 'bundle contains link or special file')
        if path.is_file():
            name = path.relative_to(directory).as_posix()
            require(name.startswith('images/'), 'image delivery carries only images/')
            result[name] = {'sha256': sha(path), 'size': path.stat().st_size}
    require(sum(v['size'] for v in result.values()) <= LIMIT, 'bundle exceeds 32 GiB limit')
    return result


def extract(archive, target):
    with tarfile.open(archive, 'r:gz') as tf:
        members = tf.getmembers()
        require(len(members) <= 100000, 'too many archive members')
        names = set()
        for member in members:
            path = Path(member.name)
            require(not path.is_absolute() and '..' not in path.parts and member.name not in names
                    and member.isfile() and (member.name.startswith('images/') or member.name == 'DELIVERY.json'),
                    'unsafe archive member')
            names.add(member.name)
        require(sum(m.size for m in members) <= LIMIT, 'archive expands beyond limit')
        require(shutil.disk_usage(target).free > sum(m.size for m in members)+128*1024**2, 'insufficient extraction space')
        for member in members:
            path = target/member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(member) as src, path.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            path.chmod(0o600)


def preflight():
    require(os.geteuid() == 0, 'remote operations require root or passwordless sudo')
    root = Path('/opt/octo-pod')
    require((root/'bin/octo-pod').is_file(), 'install octo-pod and OS dependencies before carrying images')
    arch = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    require(arch in ('amd64', 'arm64'), 'unsupported destination architecture')
    for tool in ('podman', 'skopeo', 'ansible-playbook'):
        require(shutil.which(tool), f'missing destination prerequisite: {tool}')
    return {'pins': pins(root), 'architecture': arch, 'free': shutil.disk_usage('/var/lib').free}


def receive_unlocked(archive, expected_sha, load):
    info = preflight()
    require(re.fullmatch(r'[a-f0-9]{64}', expected_sha), 'invalid transfer digest')
    require(re.fullmatch(r'/tmp/octo-images\.[A-Za-z0-9_]+/bundle.tar.gz', archive), 'invalid transfer path')
    require(not STAGES.is_symlink(), 'stage root must not be a symlink')
    STAGES.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(STAGES.stat().st_uid == 0 and not STAGES.stat().st_mode & 0o077, 'stage root must be root-only')
    with tempfile.TemporaryDirectory(prefix='.incoming-', dir=STAGES) as tmp:
        tmp = Path(tmp)
        local = tmp/'bundle.tar.gz'
        require(Path(archive).stat().st_size <= LIMIT, 'transfer exceeds limit')
        require(shutil.disk_usage(STAGES).free > Path(archive).stat().st_size*2+128*1024**2, 'insufficient staging space')
        shutil.copyfile(archive, local)
        require(sha(local) == expected_sha, 'transferred archive digest mismatch')
        stage = tmp/'stage'
        stage.mkdir()
        extract(local, stage)
        receipt = json.loads((stage/'DELIVERY.json').read_text())
        require(receipt.get('schema') == 1, 'unsupported delivery receipt')
        require(receipt['pins'] == info['pins'], 'installed pins changed or bundle targets a different package')
        (stage/'DELIVERY.json').unlink()
        require(files(stage) == receipt['files'], 'transferred file hashes differ')
        validate(stage, info['pins'])
        (stage/'DELIVERY.json').write_text(json.dumps(receipt, indent=2)+'\n')
        final = STAGES/expected_sha
        require(not final.exists(), 'this bundle is already staged; use its printed local bootstrap command')
        stage.rename(final)
    command = ['/opt/octo-pod/bin/octo-pod', 'bootstrap', '--tags', 'images',
               '-e', 'bootstrap_mode=airgap', '-e', 'image_mirror=localhost:5000', '-e', f'stage_dir={final}']
    print('Verified stage:', final, flush=True)
    print('Load command:', shlex.join(command), flush=True)
    if load:
        subprocess.run(command, check=True)
        for path, digest in map_rows(final).values():
            raw = subprocess.check_output(['skopeo', 'inspect', '--tls-verify=false', '--raw',
                                           f'docker://127.0.0.1:5000/{path}@{digest}'])
            require('sha256:'+hashlib.sha256(raw).hexdigest() == digest, 'registry digest verification failed')
        print('All eight pinned indexes resolve from the pod registry. No apply was run.')
    return final


def receive(archive, expected_sha, load):
    require(os.geteuid() == 0, 'remote operations require root')
    STAGES.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in (STAGES, *STAGES.parents):
        require(not path.is_symlink() and path.stat().st_uid == 0
                and not path.stat().st_mode & 0o022, 'unsafe staging directory ownership or permissions')
    with (STAGES/'.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return receive_unlocked(archive, expected_sha, load)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('target', help='SSH alias or user@host; configure ProxyJump in ~/.ssh/config if needed')
    p.add_argument('--bundle', type=Path, help='reuse a trusted operator-built stage instead of building')
    p.add_argument('--load', action='store_true', help='seed and verify the local registry; may restart registry only')
    args = p.parse_args()
    require(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*', args.target), 'use a simple SSH alias or user@host')
    root = Path(__file__).resolve().parents[1]
    source = Path(__file__).read_text()
    # The target came from argv, so it could carry an option such as -oProxyCommand=.
    # The pattern above refuses a leading '-', and '--' ends ssh's options as well.
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15',
           '--', args.target]
    def remote(arguments, capture=False):
        command = shlex.join(['sudo', '-n', 'python3', '-c', source, *arguments])
        result = subprocess.run([*ssh, command], check=False, text=True, capture_output=capture)
        if result.returncode:
            detail = result.stderr.strip() if capture else 'see the preceding remote diagnostic'
            raise ValueError(f'remote operation failed ({result.returncode}): {detail}')
        return result
    info = json.loads(remote(['--remote-preflight'], True).stdout)
    expected = pins(root)
    require(info['pins'] == expected, 'checkout pins differ from installed pod; use the matching reviewed checkout')
    with tempfile.TemporaryDirectory(prefix='octo-image-delivery-') as tmp:
        tmp = Path(tmp)
        bundle = args.bundle
        if bundle is None:
            bundle = tmp/'bundle'
            subprocess.run(['bash', str(root/'tools/build-airgap-bundle.sh'), '--out', str(bundle)],
                           check=True)
        validate(bundle, expected)
        payload = tmp/'payload'
        payload.mkdir()
        shutil.copytree(bundle/'images', payload/'images', symlinks=True)
        manifest = {'schema': 1, 'pins': expected, 'files': files(payload)}
        (payload/'DELIVERY.json').write_text(json.dumps(manifest, sort_keys=True)+'\n')
        archive = tmp/'bundle.tar.gz'
        with tarfile.open(archive, 'w:gz') as tf:
            for path in sorted(payload.rglob('*')):
                if path.is_file():
                    tf.add(path, arcname=path.relative_to(payload).as_posix(), recursive=False)
        require(archive.stat().st_size <= LIMIT and info['free'] > archive.stat().st_size*3,
                'bundle too large for destination')
        # ssh's argv is fixed but for the validated target; the remote command is a constant.
        directory = subprocess.check_output([*ssh, 'mktemp -d /tmp/octo-images.XXXXXXXX'], text=True).strip()  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args
        require(re.fullmatch(r'/tmp/octo-images\.[A-Za-z0-9_]+', directory), 'unexpected remote temporary directory')
        try:
            subprocess.run(['scp', '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes', '--', str(archive),
                            f'{args.target}:{directory}/bundle.tar.gz'], check=True)
            remote(['--remote-receive', directory+'/bundle.tar.gz', sha(archive), 'load' if args.load else 'stage'])
        finally:
            # directory matched /tmp/octo-images.<alnum> above, and shlex.join quotes it.
            subprocess.run([*ssh, shlex.join(['rm', '-f', directory+'/bundle.tar.gz'])+' && '+  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args
                            shlex.join(['rmdir', directory])], check=False)
    print('Image delivery complete. Identity and application configuration were not changed.')


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--remote-preflight']:
            print(json.dumps(preflight()))
        elif len(sys.argv) == 5 and sys.argv[1] == '--remote-receive':
            receive(sys.argv[2], sys.argv[3], sys.argv[4] == 'load')
        else:
            main()
    except subprocess.CalledProcessError as error:
        print(f'Image delivery failed: command exited {error.returncode}; see preceding diagnostic.', file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError, KeyError, tarfile.TarError) as error:
        print(f'Image delivery refused: {error}', file=sys.stderr)
        sys.exit(1)
