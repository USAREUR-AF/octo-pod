<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Prove the Tests Can Detect a Defect

## Make the Right Test Fail

For a bug fix, reproduce the original failure, add a focused test, verify that the test fails against the broken version for the expected reason, apply the fix, and verify that it passes. Keep the test when you remove the temporary reproducer. This is a *regression test*: it prevents a known defect from returning unnoticed.

A red run caused by an import error proves little about the bug. Inspect the failure message and the observed value. Then confirm that the test runner reports failure through its exit status so CI can block the change.

## Deliberately Break the Implementation

*Mutation testing* introduces small defects and checks whether tests detect them. Try changing a strict comparison to an inclusive comparison, removing a denial, returning a constant, or ignoring a failed dependency. Use an isolated copy; never inject faults into a live service.

The lab copies its source into temporary directories, verifies a healthy baseline, and applies four deliberate mutations. It checks the expected test failure for each mutation. It also detects a test-discovery configuration that finds zero tests.

*Challenge the Test Suite*

```console
$ python3 docs/pocket-guides/test-lab/validate_tests.py
```

A detected mutation supports a specific claim: these tests distinguish this defect from the intended behavior. Surviving mutations need investigation. Some reveal missing assertions; others are behaviorally equivalent or outside the contract. Do not chase a mutation percentage without understanding which important defects remain invisible.

## Measure Coverage Without Confusing It With Correctness

*Statement coverage* counts executed statements. *Branch coverage* counts decision paths taken. Both can reveal neglected code, but execution alone does not establish a useful assertion. A test can execute a function and ignore everything it returns.

Read uncovered branches in context. Prioritize paths that protect data, enforce authorization, handle untrusted input, or recover state. Explain generated-code and platform exclusions. Set an attainable floor, improve it, and review changes to the denominator. There is no universal percentage that makes software safe.

Also measure the contract: supported platforms, input classes, protocol variants, lifecycle operations, and failure recovery. A narrow unit suite with high coverage can miss a broken package, database migration, or service restart.

## Validate the Harness and the Gate

Check that the intended tests are discovered, the expected count is nonzero, subprocess failures propagate, and failed setup cannot look like a pass. Review skip markers, ignored exit codes, unconditional success paths, and allowed failures. An artifact containing test output does not prove that a required job used it.

Run a deliberate failing test on a disposable branch and inspect the CI job and merge gate. Remove that deliberate failure afterward. Do not weaken production branch protection to demonstrate the check.

A flaky test sometimes passes and sometimes fails under the same meaningful conditions. Investigate timing, shared state, resource limits, and nondeterminism. If you must quarantine it, assign an owner, deadline, and alternate check; record the resulting gap. Retrying until green hides information.

## Review Tests Independently

Review the requirement and expected result before trusting generated tests. A person or AI that wrote the implementation can make the same mistaken assumption in the test. Ask another reviewer to challenge the boundary, construct a counterexample, and identify which layer the test does not exercise.

For parsers and protocols, add malformed inputs, size limits, property checks, published vectors, and fuzzing where appropriate. Fuzzing generates many inputs; a sanitizer or explicit invariant helps distinguish a meaningful failure from ordinary rejection. Preserve minimized failures as ordinary regression tests.

**Evidence:** a healthy baseline passes, relevant broken variants fail for the intended reasons, the harness reports failure, and the actual CI gate blocks that failure.
