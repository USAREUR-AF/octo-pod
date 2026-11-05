"""Offline controls for PR image selection and final-image loader refusals."""
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('matrix', REPO / 'tools/pr-image-matrix.py')
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


class MatrixTests(unittest.TestCase):
    images = frozenset({'egress', 'udp-ingest', 'sapient'})

    def test_dockerfile_selects_only_changed_arm(self):
        self.assertEqual(['egress'], matrix.select_images(['arms/egress/Dockerfile'], self.images))

    def test_shared_inputs_select_every_image(self):
        for path in ['arms/Cargo.lock', 'arms/Cargo.toml', 'arms/common/src/lib.rs',
                     'arms/THIRD_PARTY_LICENSES.md', '.github/workflows/pr-images.yml',
                     'tools/check-pr-image.sh', 'tools/pr-image-matrix.py']:
            with self.subTest(path=path):
                self.assertEqual(sorted(self.images), matrix.select_images([path], self.images))

    def test_docs_only_has_no_builds(self):
        self.assertEqual([], matrix.select_images(['docs/deployment.md'], self.images))

    def test_multiple_arms_are_deduplicated(self):
        self.assertEqual(['egress', 'sapient'], matrix.select_images(
            ['arms/sapient/Dockerfile', 'arms/egress/src/lib.rs', 'arms/egress/Dockerfile'], self.images))

    def test_new_shared_inputs_fail_toward_full_coverage(self):
        self.assertEqual(sorted(self.images), matrix.select_images(['arms/new-shared-input'], self.images))


class LoaderGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'calls.jsonl'
        fake = self.root / 'docker'
        fake.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['FIXTURE_DOCKER_LOG']).open('a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:2] == ['image', 'inspect']:
    if args[-1] == '{{.Architecture}}':
        print(os.environ.get('FIXTURE_ARCH', 'arm64'))
    else:
        print(os.environ.get('FIXTURE_USER', '65532:65532'))
    sys.exit(0)
if args[0] == 'run':
    print('Usage: synthetic fixture; no container or network')
    sys.exit(int(os.environ.get('FIXTURE_RUN_STATUS', '0')))
sys.exit(99)
''')
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'],
                        FIXTURE_DOCKER_LOG=str(self.log))

    def guard(self, dockerfile=None):
        return subprocess.run(['bash', str(REPO / 'tools/check-pr-image.sh'), 'local/fixture:pr',
                               'arm64', str(dockerfile or REPO / 'arms/egress/Dockerfile')],
                              env=self.env, capture_output=True, text=True, check=False)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_every_egress_binary_starts_with_network_disabled_and_no_user_override(self):
        result = self.guard()
        self.assertEqual(0, result.returncode, result.stderr)
        runs = [c for c in self.calls() if c[0] == 'run']
        self.assertEqual({'/usr/local/bin/egress-arm', '/usr/local/bin/downlink-bridge',
                          '/usr/local/bin/zenoh-egress-arm'},
                         {c[c.index('--entrypoint')+1] for c in runs})
        for call in runs:
            self.assertIn('--read-only', call)
            self.assertEqual('none', call[call.index('--network')+1])
            self.assertEqual('ALL', call[call.index('--cap-drop')+1])
            self.assertNotIn('--user', call)
            self.assertEqual('--help', call[-1])

    def test_wrong_architecture_is_refused_before_startup(self):
        self.env['FIXTURE_ARCH'] = 'amd64'
        self.assertNotEqual(0, self.guard().returncode)
        self.assertFalse(any(c[0] == 'run' for c in self.calls()))

    def test_root_image_is_refused_before_startup(self):
        self.env['FIXTURE_USER'] = '0:0'
        self.assertNotEqual(0, self.guard().returncode)
        self.assertFalse(any(c[0] == 'run' for c in self.calls()))

    def test_loader_failure_is_not_ignored(self):
        self.env['FIXTURE_RUN_STATUS'] = '127'
        self.assertNotEqual(0, self.guard().returncode)
        self.assertEqual(1, sum(c[0] == 'run' for c in self.calls()))

    def test_missing_binary_contract_is_refused(self):
        dockerfile = self.root / 'Dockerfile'
        dockerfile.write_text('FROM scratch\n')
        self.assertNotEqual(0, self.guard(dockerfile).returncode)
        self.assertFalse(any(c[0] == 'run' for c in self.calls()))


if __name__ == '__main__':
    unittest.main()
