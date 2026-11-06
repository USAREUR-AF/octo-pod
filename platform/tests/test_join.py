#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Run the join driver against command doubles, without root or a live mesh."""
import json
import os
from pathlib import Path
import shutil
import socket
import yaml
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class JoinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'repo/bin'
        self.bin.mkdir(parents=True)
        (self.root / 'repo/platform').mkdir()
        self.log = self.root / 'commands'
        self.log.touch()
        for name in ['octo-pod-join', 'octo_pod_mesh.py']:
            shutil.copy(ROOT / 'bin' / name, self.bin / name)
        self.doubles = self.root / 'doubles'
        self.doubles.mkdir()
        self.script(self.bin / 'octo-pod-identity', 'echo identity >> "$TEST_LOG"')
        self.script(self.doubles / 'id', 'echo 0')
        self.script(self.doubles / 'podman', 'exit 0')
        self.script(self.doubles / 'sleep', 'echo sleep >> "$TEST_LOG"')
        self.script(self.doubles / 'netbird', '''[ "$*" = 'status --json' ] || exit 9
echo netbird >> "$TEST_LOG"
if [ -n "${FIXTURE_LATER:-}" ]; then
  if [ -f "$TEST_LOG.first" ]; then printf '%s' "$FIXTURE_LATER"; exit 0; fi
  touch "$TEST_LOG.first"
fi
printf '%s' "$FIXTURE_STATUS"
exit "${NETBIRD_RC:-0}"''')
        self.script(self.doubles / 'ansible-playbook', '''echo "$*" >> "$TEST_LOG"
case "$*" in
  *verify-octo-pod.yml*) exit "${VERIFY_RC:-0}" ;;
  *configure-octo-pod.yml*) exit "${CONFIGURE_RC:-0}" ;;
esac''')

    @staticmethod
    def script(path, body):
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    @staticmethod
    def status(url='https://mgmt.example.invalid', connected=True):
        return json.dumps({'management': {'url': url, 'connected': connected}})

    def run_join(self, requested='https://mgmt.example.invalid', status=None, **extra):
        env = dict(os.environ, PATH=str(self.doubles) + os.pathsep + os.environ['PATH'],
                   TEST_LOG=str(self.log), OCTO_POD_ETC=str(self.root / 'etc'),
                   FIXTURE_STATUS=self.status() if status is None else status, **extra)
        r = subprocess.run(['bash', str(self.bin / 'octo-pod-join'),
                            '--setup-key', 'synthetic-key', '--mgmt', requested, '--pod', 'pod-test'],
                           env=env, capture_output=True, text=True, timeout=10)
        self.assertNotIn('synthetic-key', r.stdout + r.stderr)
        return r, self.log.read_text().splitlines()

    def assert_refused_before_images(self, r, calls):
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn('Joined.', r.stdout)
        self.assertFalse(any('--tags images' in c or 'configure-octo' in c
                             or 'verify-octo' in c for c in calls), calls)

    def test_matching_dns_connects_before_images_and_verifies(self):
        r, calls = self.run_join()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Joined.', r.stdout)
        self.assertLess(calls.index('netbird'), next(i for i,c in enumerate(calls) if '--tags images' in c))
        self.assertIn('verify-octo-pod.yml', calls[-1])

    def test_matching_ipv6(self):
        r, _ = self.run_join('https://[2001:db8::1]', self.status('https://[2001:db8::1]'))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_equivalent_ipv6_and_default_port(self):
        r, _ = self.run_join('https://[2001:db8:0::1]:443/', self.status('https://[2001:db8::1]'))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dns_case_and_default_port(self):
        r, _ = self.run_join('https://MGMT.example.invalid:443/', self.status())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wrong_ipv6_is_refused(self):
        r, calls = self.run_join('https://[2001:db8::1]', self.status('https://[2001:db8::2]'))
        self.assert_refused_before_images(r, calls)
        self.assertEqual(calls.count('netbird'), 12)
        self.assertEqual(calls.count('sleep'), 11)

    def test_wrong_dns_is_refused(self):
        self.assert_refused_before_images(*self.run_join(status=self.status('https://other.example.invalid')))

    def test_wrong_port_is_refused(self):
        self.assert_refused_before_images(*self.run_join(status=self.status('https://mgmt.example.invalid:8443')))

    def test_wrong_scheme_is_refused(self):
        self.assert_refused_before_images(*self.run_join(status=self.status('http://mgmt.example.invalid')))

    def test_disconnected_is_refused(self):
        self.assert_refused_before_images(*self.run_join(status=self.status(connected=False)))

    def test_truthy_string_is_not_connection_proof(self):
        self.assert_refused_before_images(*self.run_join(status=self.status(connected='false')))

    def test_failed_status_command_cannot_supply_connection_proof(self):
        for rc in ['7', '124']:
            with self.subTest(rc=rc):
                self.assert_refused_before_images(*self.run_join(NETBIRD_RC=rc))

    def test_status_shapes_fail_closed(self):
        for status in ['invalid json', 'null', '[]', '{}', '{"management":null}',
                       '{"management":{"connected":true}}',
                       self.status('https://[invalid]')]:
            with self.subTest(status=status):
                self.assert_refused_before_images(*self.run_join(status=status))

    def test_retry_can_observe_connection(self):
        r, calls = self.run_join(status=self.status(connected=False), FIXTURE_LATER=self.status())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls.count('netbird'), 2)
        self.assertEqual(calls.count('sleep'), 1)

    def test_verification_failure_has_no_success_banner(self):
        r, calls = self.run_join(VERIFY_RC='7')
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('Joined.', r.stdout)
        self.assertIn('verification failed', r.stderr)
        self.assertIn('verify-octo-pod.yml', calls[-1])

    def test_convergence_failure_does_not_run_verification(self):
        r, calls = self.run_join(CONFIGURE_RC='7')
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('Joined.', r.stdout)
        self.assertFalse(any('verify-octo' in c for c in calls))

    def test_invalid_requested_url_is_rejected_before_identity(self):
        for url in ['mgmt.example.invalid', 'https://', 'https://[invalid]',
                    'https://mgmt.example.invalid:bad', 'https://mgmt.example.invalid:0',
                    'https://user:password@mgmt.example.invalid', 'https://mgmt.example.invalid/path',
                    'https://mgmt.example.invalid?query', 'https://mgmt.example.invalid#fragment',
                    'https://mgmt.example.invalid\nother']:
            with self.subTest(url=url):
                r, calls = self.run_join(requested=url)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(calls, [])


@unittest.skipUnless(shutil.which('ansible-playbook'), 'ansible-core is required in CI')
class BootstrapProbeTests(unittest.TestCase):
    def test_real_probe_preserves_url_host_port_and_timeout_failure(self):
        play = yaml.safe_load((ROOT / 'platform/bootstrap-octo-pod.yml').read_text())
        task = next(t for t in play[0]['tasks'] if t.get('register') == '_nb_reach')
        for family, host in [(socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')]:
            for reachable in [True, False]:
                with self.subTest(host=host, reachable=reachable), socket.socket(family) as sock:
                    sock.bind((host, 0))
                    port = sock.getsockname()[1]
                    if reachable:
                        sock.listen(1)
                    url = f'https://[{host}]:{port}' if family == socket.AF_INET6 else f'https://{host}:{port}'
                    probe = dict(task)
                    probe['ansible.builtin.wait_for'] = dict(task['ansible.builtin.wait_for'], timeout=1)
                    assertion = {'ansible.builtin.assert': {'that': [
                        '_nb_reach is not failed' if reachable else '_nb_reach is failed',
                        '_nb_reach.elapsed is defined']}}
                    fixture = [{'hosts': 'localhost', 'gather_facts': False,
                                'vars': {'netbird_mgmt_url': url, 'netbird_setup_key': 'synthetic',
                                         '_nb_status': {'stdout': ''}},
                                'tasks': [probe, assertion]}]
                    with tempfile.TemporaryDirectory() as tmp:
                        pb = Path(tmp) / 'probe.yml'
                        pb.write_text(yaml.safe_dump(fixture, sort_keys=False))
                        result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(pb)],
                                                capture_output=True, text=True, timeout=20,
                                                env=dict(os.environ, ANSIBLE_NOCOLOR='1'))
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
