<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Build Tests From Promises

## Start With a Failure That Matters

A test is an executable claim about behavior. You need it because code changes, dependencies change, and your memory of the original contract fades. A useful test lets a later maintainer distinguish an intentional change from an accidental break.

Write the promise before reading the implementation. “An expired grant must not release data” gives you a boundary, a result, and a consequence. “Call function X” only tells you which code to execute.

Define the *oracle*, the rule that determines the correct result. Use a specification, an independently calculated value, a published test vector, or a manually reviewed example. Computing the expected value with the same function being tested can reproduce the same defect on both sides of the assertion.

## Turn the Promise Into Cases

For the teaching lab, the contract is deliberately small: an existing active grant permits release strictly before its expiry time. At the expiry time, after expiry, after revocation, or without a grant, release is denied. Time is supplied by the caller so a test does not depend on the wall clock.

| Condition | Expected Result | Defect It Can Expose |
|---|---|---|
| Active, before expiry | Allow | An implementation that denies everything |
| Exactly at expiry | Deny | An inclusive boundary comparison |
| After expiry | Deny | Missing expiry enforcement |
| Revoked, before expiry | Deny | Missing revocation enforcement |
| No grant | Deny | A permissive missing-data default |

The successful case matters. A system that refuses every operation may pass all your denial tests while being unusable.

## Choose the Smallest Test That Proves the Claim

A *unit test* checks a small behavior in isolation. An *integration test* checks real components working together. An *end-to-end test* follows a user-visible operation through the installed system. Use each where it adds evidence.

Use a unit test for the expiry comparison. Use an integration test to prove the request handler consults current authorization. Use an end-to-end test to prove an expired identity cannot obtain data through the actual endpoint. A correct helper does not prove its caller uses it.

A *mock* replaces a collaborator with controlled behavior. Mock the clock or an external failure when necessary; do not mock away the authorization decision, database transaction, or filesystem behavior that the test is supposed to verify. Pair isolated tests with a smaller set of realistic integration tests.

## Write an Assertion With Consequences

Arrange the starting state, perform the operation, and assert the observable result. Check returned values, error types, exit status, persisted state, and forbidden side effects as appropriate. A process starting successfully is not proof that its output is correct.

For failure paths, check both refusal and absence of damage. An installer that exits nonzero after deleting user data has failed its preservation contract. An API that denies a request after logging its secret has still disclosed the secret.

Use fresh temporary directories, explicit fixtures, bounded timeouts, and teardown. Fix random seeds when reproducing a failure and retain the failing input. Avoid sleeps as synchronization; wait for a bounded, observable condition.

## Read the Implementation and Its Tests

The implementation accepts an explicit time value and returns a Boolean. The missing-grant check gives absence a defined result instead of leaving it to an accidental exception.

*The Teaching Contract in Python*

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Grant:
    active: bool
    expires_at: int


def may_release(grant: Grant | None, now: int) -> bool:
    """Allow an active grant strictly before expiry; missing grants deny."""
    if grant is None:
        return False
    return grant.active and now < grant.expires_at
```

The tests use fixed examples and explicit answers. Notice that the expected result does not call another copy of the expiry calculation.

*Five Assertions Against the Written Contract*

```python
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
```

Change the strict comparison from `<` to `<=` in a scratch copy. The exact-expiry assertion should fail while the before-expiry assertion still passes. That difference tells you the test checks the boundary rather than merely executing the function.

Run the companion lab from the repository root. Python's [unittest documentation](https://docs.python.org/3/library/unittest.html) describes discovery and assertion behavior.

*Run the Contract Tests*

```console
$ python3 -m unittest discover -s docs/pocket-guides/test-lab -p 'test_*.py' -v
```

Read `docs/pocket-guides/test-lab/test_release_policy.py` alongside the contract. Each test names a behavior and gives an explicit expected answer. The example intentionally omits identity verification, storage, and networking; those require their own contracts and tests in a real application.
