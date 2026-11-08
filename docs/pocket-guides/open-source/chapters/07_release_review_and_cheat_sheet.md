<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Review the Decision and Find a Command

## Publication Review

Before publication, have an accountable reviewer establish these facts:

- You have authority to publish and the license and notices cover the material shipped.
- The public boundary includes history, artifacts, discussions, examples, and generated output.
- A newcomer can build and use the documented software without private team knowledge.
- Tests exercise important promises and detect meaningful deliberately introduced defects.
- CI isolates untrusted work, reports failures, and enforces the intended merge rules.
- A release has a traceable source, verified artifacts, useful notes, and a support decision.
- Installation, upgrade, recovery, and removal have explicit data-preservation contracts.
- Maintainers can receive ordinary reports and private security reports and act on them.

Record unresolved items with an owner, consequence, and next decision. A publication checklist is a record of a decision, not authority to disclose someone else's information.

## Diagnose a Misleading Success

| Symptom | Next Check |
|---|---|
| Tests pass instantly | Confirm discovery count and skipped suites. |
| Coverage is high; a defect survives | Inspect the oracle and assertions. |
| CI passes; merge remains blocked | Read the exact required check names and review rules. |
| Local build works; public build fails | Look for cached inputs and private dependency access. |
| Package installs; service fails | Test runtime dependencies and installed permissions. |
| Rollback restores code, not service | Inspect schema and data compatibility. |
| Export removes a public fix | Trace the authoritative source and replay record. |
| Scanner is quiet | Verify scan scope, permissions, configuration, and freshness. |

## Sources and Further Reading

Use the linked primary sources at the relevant step. For deeper security engineering, see [OpenSSF's developer guidance](https://best.openssf.org/) and the [OWASP testing framework](https://wstg.owasp.org/stable/3-The_OWASP_Testing_Framework/0-The_Web_Security_Testing_Framework/). The companion OpenSSF pocket guide turns project practices into versioned assessment evidence.
