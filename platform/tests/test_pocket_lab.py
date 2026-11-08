#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Keep the pocket guide's executable teaching examples working in CI."""
from pathlib import Path
import subprocess
import sys
import unittest


class PocketLabTest(unittest.TestCase):
    def test_contract_and_deliberate_faults(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, str(root / 'docs/pocket-guides/test-lab/validate_tests.py')],
            cwd=root, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
