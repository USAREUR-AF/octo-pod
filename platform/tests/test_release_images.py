# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Exercise image/release workflow commands offline with a recording Docker fixture."""
import itertools
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

import yaml


def run_checks(repo, check):
    print("\nimages and offline bundles — release regressions")
    workflow = yaml.safe_load((repo / '.github/workflows/images.yml').read_text())
    release = yaml.safe_load((repo / '.github/workflows/release.yml').read_text())
    matrix = workflow['jobs']['build']['strategy']['matrix']
    axes = {k: v for k, v in matrix.items() if k not in ('include', 'exclude')}
    original = [dict(zip(axes, values)) for values in itertools.product(*axes.values())]
    expanded = [dict(row) for row in original]
    for addition in matrix.get('include', []):
        matched = False
        for index, row in enumerate(original):
            if all(k not in row or row[k] == v for k, v in addition.items()):
                expanded[index].update(addition)
                matched = True
        if not matched:
            expanded.append(addition)
    arms = workflow['jobs']['merge']['strategy']['matrix']['arm']
    check('each arm builds once on each native architecture',
          len(expanded) == 2 * len(arms) and all(
              {r.get('platform') for r in expanded if r.get('arm') == arm}
              == {'linux/amd64', 'linux/arm64'} for arm in arms), str(expanded))
    check('each architecture uses its native runner', all(
        r.get('runner') == {'linux/amd64': 'ubuntu-24.04', 'linux/arm64': 'ubuntu-24.04-arm'}.get(r.get('platform'))
        for r in expanded))

    def step(job, name):
        return next(s['run'] for s in workflow['jobs'][job]['steps'] if s.get('name') == name)

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        stubs = root / 'stubs'
        stubs.mkdir()
        docker = stubs / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
a=sys.argv[1:]
with open(os.environ['IMAGE_TEST_TRACE'],'a') as f: f.write(json.dumps(a)+'\\n')
if a[:3] == ['buildx','imagetools','inspect']:
    if '--format' in a: print('"sha256:' + '1'*64 + '"')
    else: print(json.dumps(json.loads(os.environ['IMAGE_TEST_MANIFEST']),indent=2))
if a and a[0] == 'save': pathlib.Path(a[a.index('-o')+1]).write_text('synthetic tool image')
''')
        docker.chmod(0o755)
        trace = root / 'commands.jsonl'
        digests = root / 'digests'
        digests.mkdir()
        for arm in arms:
            (digests / (arm + '@' + '1' * 64)).touch()
        summary = root / 'summary'
        env = os.environ.copy()
        env.update(PATH=str(stubs) + ':' + env['PATH'], IMAGE_TEST_TRACE=str(trace),
                   GITHUB_REPOSITORY_OWNER='Example', GITHUB_SHA='a' * 40,
                   GITHUB_REF='refs/heads/packaging-test', GITHUB_STEP_SUMMARY=str(summary),
                   REGISTRY_AUTH_FILE=str(root / 'missing-auth'))
        bash = '/opt/homebrew/bin/bash' if pathlib.Path('/opt/homebrew/bin/bash').exists() else shutil.which('bash')
        good = {'manifests': [{'platform': {'os': 'linux', 'architecture': a}}
                              for a in ('amd64', 'arm64', 'unknown')]}
        missing = {'manifests': [{'platform': {'os': 'linux', 'architecture': a}}
                                 for a in ('arm64', 'unknown')]}

        def run(body, manifest=good, arm='sapient'):
            trace.write_text('')
            env['IMAGE_TEST_MANIFEST'] = json.dumps(manifest)
            body = body.replace('${{ matrix.arm }}', arm).replace('/tmp/digests', str(digests))
            p = subprocess.run([bash, '-c', body], env=env, cwd=repo, capture_output=True, text=True)
            calls = [json.loads(line) for line in trace.read_text().splitlines()]
            return p, calls

        create = step('merge', 'create the manifest list')
        for arm in arms:
            p, calls = run(create, arm=arm)
            expected = arm.replace('-', '_') + '_image_upstream'
            check(f'{arm}: merge emits the correct upstream pin',
                  p.returncode == 0 and f'PIN-THIS {expected}:' in p.stdout, p.stdout + p.stderr)
            check(f'{arm}: review build does not move latest',
                  not any(':latest' in value for call in calls for value in call))
        promotion = next((s for s in workflow['jobs']['merge']['steps'] if s.get('name') == 'promote a verified main build'), {})
        check('latest promotion is gated on main', promotion.get('if') == "github.ref == 'refs/heads/main'")

        merge_guard = step('merge', 'prove it is a real manifest list')
        coverage = step('coverage', 'every built image is multi-arch')
        for name, body in [('merge', merge_guard), ('coverage', coverage)]:
            p, calls = run(body)
            check(f'{name}: real amd64 and arm64 manifests pass', p.returncode == 0, p.stdout + p.stderr)
            check(f'{name}: checks this build rather than latest', all(
                ':latest' not in value for call in calls for value in call))
            p, _ = run(body, missing)
            check(f'{name}: arm64 plus attestation cannot masquerade as multi-arch', p.returncode != 0)

        # Run the real bundle builder. Its registry/tool client is stubbed, so no
        # credential or remote registry is involved; MAP and tool-architecture files
        # still come from the builder's actual argument selection and writes.
        fixture = root / 'fixture'
        (fixture / 'tools').mkdir(parents=True)
        varsdir = fixture / 'platform/group_vars/all'
        varsdir.mkdir(parents=True)
        shutil.copy2(repo / 'tools/build-airgap-bundle.sh', fixture / 'tools/build-airgap-bundle.sh')
        pins = [('zenoh', 'zenoh_image'), ('audit-sink', 'audit_image'),
                ('udp-ingest', 'udp_ingest_image'), ('tcp-ingest', 'tcp_ingest_image'),
                ('tcp-connect', 'tcp_connect_image'), ('health-pub', 'health_pub_image'),
                ('egress', 'egress_image'), ('sapient', 'sapient_image'),
                ('uplink-bridge', 'uplink_bridge_image')]
        refs = {var: 'registry.example/octo-' + tag + '@sha256:' + f'{i + 1:064x}'
                for i, (tag, var) in enumerate(pins)}
        varsfile = varsdir / '10-infrastructure.yml'

        def write_pins(overrides=None):
            values = refs | (overrides or {})
            varsfile.write_text(''.join(f'{var}_upstream: "{ref}"\n' for var, ref in values.items()))

        def bundle(label, overrides=None):
            write_pins(overrides)
            trace.write_text('')
            dest = root / label
            p = subprocess.run([bash, str(fixture / 'tools/build-airgap-bundle.sh'), '--out', str(dest)], env=env, capture_output=True, text=True)
            return p, dest

        p, dest = bundle('complete')
        check('bundle builds with complete immutable pins', p.returncode == 0, p.stdout + p.stderr)
        if p.returncode == 0:
            entries = [line.split() for line in (dest / 'images/MAP').read_text().splitlines()]
            check('bundle MAP includes both egress and SAPIENT',
                  {row[0] for row in entries} == {tag for tag, _ in pins}, str(entries))
            check('bundle carries tools for both architectures', all(
                (dest / f'images/{tool}-{arch}.tar').exists()
                for tool in ('registry', 'skopeo') for arch in ('amd64', 'arm64')))
            env['IMAGE_TEST_BUNDLE'] = str(dest)
            # Exercise the real release verification shell against the builder MAP.
            verification = next(s['run'] for s in release['jobs']['build']['steps']
                                if s.get('name') == 'the bundle carries every pinned digest')
            verification = verification.replace("v='${{ steps.v.outputs.version }}'", "v='0.0.0'")
            verification = verification.replace('tar -xzf "dist/octo-pod-images-${v}.tar.gz" -C /tmp', ':')
            verification = verification.replace('/tmp/images/MAP', str(dest / 'images/MAP'))
            q = subprocess.run([bash, '-c', verification], env=env, cwd=fixture, capture_output=True, text=True)
            check('release accepts the complete bundle MAP', q.returncode == 0, q.stdout + q.stderr)
            original_map = (dest / 'images/MAP').read_text()
            for omitted in ('health-pub', 'egress', 'sapient'):
                (dest / 'images/MAP').write_text(''.join(line + '\n' for line in original_map.splitlines()
                                                        if not line.startswith(omitted + ' ')))
                q = subprocess.run([bash, '-c', verification], env=env, cwd=fixture, capture_output=True, text=True)
                check(f'release refuses a bundle missing {omitted}', q.returncode != 0)
        for var in ('egress_image', 'sapient_image'):
            p, _ = bundle('missing-' + var, {var: ''})
            check(f'bundle refuses an empty {var} pin', p.returncode != 0)
            p, _ = bundle('mutable-' + var, {var: 'registry.example/octo-arm:latest'})
            check(f'bundle refuses a mutable {var} reference', p.returncode != 0)
