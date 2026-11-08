# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Expected answers come from the written contract, not from the function."""
import unittest
from release_policy import Grant, may_release


class ReleasePolicyTests(unittest.TestCase):
    def test_active_before_expiry(self):
        self.assertTrue(may_release(Grant(True, 100), 99))

    def test_at_expiry(self):
        self.assertFalse(may_release(Grant(True, 100), 100))

    def test_after_expiry(self):
        self.assertFalse(may_release(Grant(True, 100), 101))

    def test_revoked_before_expiry(self):
        self.assertFalse(may_release(Grant(False, 100), 99))

    def test_missing_grant(self):
        self.assertFalse(may_release(None, 99))


if __name__ == '__main__':
    unittest.main()
