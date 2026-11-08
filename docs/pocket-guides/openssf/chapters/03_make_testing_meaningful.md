<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Turn Testing Requirements Into Protection

## Prove Three Things

First, the test must express a correct requirement. Second, it must distinguish correct behavior from an important defect. Third, the project's automation must run it and act on failure. A gap at any stage weakens the protection.

Review the oracle independently of the implementation. Use positive cases, denial cases, boundaries, malformed inputs, and recovery paths according to the risk. The open-source guide explains the layers and the companion lab demonstrates deliberate defects.

*Exercise the Test Evidence*

```console
$ python3 -m unittest discover -s docs/pocket-guides/test-lab -v
$ python3 docs/pocket-guides/test-lab/validate_tests.py
```

The lab's mutation checks validate only its small contract. They do not establish test quality for the rest of the repository. Apply the method to the actual security boundaries and failure modes of your product.

## Keep the Kinds of Analysis Distinct

*Static analysis* examines code or configuration without executing the application. *Dynamic analysis* observes running code. *Software composition analysis*, or SCA, examines dependencies and their known risks. Credential scanning looks for exposed secrets. They find different classes of problem and need different triage.

Configure each tool for the languages, paths, generated sources, and dependencies you actually ship. Review ignored directories and suppression files. A tool installed in CI but never reached, or reached only after a failing setup step, supplies no analysis result.

Use compiler warnings and linters, security-focused static rules, dependency checks, and suitable dynamic techniques. For a parser, fuzzing with explicit invariants is useful. For a web application, integration tests and application security testing may cover different threats. Choose by the exposed behavior rather than by the number of tool logos.

## Fix Findings With Scope and Evidence

Confirm the affected component, reachable path, default configuration, severity, supported release, and possible mitigations. Do not equate a scanner's confidence with exploitability. Equally, do not assume a finding is harmless because your current tests pass.

Write a regression test for the confirmed failure when feasible. Re-run the relevant analysis on the fix and verify the packaged result when the defect affects installation or runtime behavior. Record how the fix reaches supported users.

For a non-exploitable dependency finding, a *Vulnerability Exploitability eXchange*, or VEX, document can express a product-specific applicability decision. It needs evidence and maintenance as the product changes; it is not a general waiver for an outdated dependency.

## Keep Release Evidence Complete

Assess the candidate that will ship. Record static and dynamic checks, dependencies, artifact hashes, signatures, provenance, and known limitations. If a release is assembled from several repositories, track each source revision and the security requirements of its components.

The policy should explain what blocks a release and who may decide an exception. An exception needs a named owner, justification, compensating controls, and expiry. Repeatedly bypassing failed checks does not become a testing strategy.
