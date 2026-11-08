#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Run baseline, deliberate faults and a zero-discovery control in scratch copies."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
RUNNER = '''import json, sys, unittest
suite = unittest.defaultTestLoader.discover('.', pattern=sys.argv[1])
result = unittest.TextTestRunner(verbosity=2).run(suite)
print(json.dumps({'count': result.testsRun,
                  'failures': [test.id() for test, _ in result.failures],
                  'errors': [test.id() for test, _ in result.errors]}))
sys.exit(0 if result.wasSuccessful() and result.testsRun > 0 else 1)
'''


def run_case(source, pattern='test_*.py'):
    with tempfile.TemporaryDirectory(prefix='test-proof-') as directory:
        work = Path(directory)
        (work / 'release_policy.py').write_text(source)
        (work / 'test_release_policy.py').write_bytes((ROOT / 'test_release_policy.py').read_bytes())
        result = subprocess.run([sys.executable, '-c', RUNNER, pattern], cwd=work,
                                capture_output=True, text=True, timeout=30)
        return result.returncode, json.loads(result.stdout)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    source = (ROOT / 'release_policy.py').read_text()
    code, result = run_case(source)
    require(code == 0 and result['count'] == 5 and not result['errors'],
            'The healthy baseline must run and pass all five tests')
    print('PASS: healthy baseline runs five tests')
    decision = 'return grant.active and now < grant.expires_at'
    mutations = [
        ('inclusive expiry', 'now < grant.expires_at', 'now <= grant.expires_at', 'test_at_expiry'),
        ('ignored revocation', decision, 'return now < grant.expires_at', 'test_revoked_before_expiry'),
        ('always allow existing grants', decision, 'return True', 'test_after_expiry'),
        ('always deny', decision, 'return False', 'test_active_before_expiry'),
    ]
    for name, old, new, expected in mutations:
        require(source.count(old) == 1, 'Mutation no longer matches source: ' + name)
        code, result = run_case(source.replace(old, new, 1))
        require(code == 1 and result['count'] == 5 and not result['errors']
                and any(test.endswith('.' + expected) for test in result['failures']),
                'Expected assertion did not detect mutation: ' + name)
        print('PASS: tests detect ' + name)
    code, result = run_case(source, 'nothing_matches_*.py')
    require(code == 1 and result['count'] == 0, 'Zero discovered tests must fail the harness')
    print('PASS: harness rejects zero discovered tests')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
