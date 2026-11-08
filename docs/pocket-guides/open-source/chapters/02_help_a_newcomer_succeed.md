<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Help a Newcomer Succeed

## Write the Path Through the Project

Write the README for someone who cannot ask your team a question. Explain the problem, intended users, supported environments, limitations, quick start, expected result, documentation, license, and support routes. Show a small synthetic example that demonstrates useful behavior.

Keep the quick start short, but make its prerequisites explicit. Specify runtime and toolchain versions, hardware requirements, network access, privileges, and external services. Identify features that require a paid service or private integration. A hidden dependency on your laptop prevents an outsider from reproducing your result.

Have a second person follow the instructions from a clean checkout or disposable environment. Record the first point where they need information outside the documentation, then fix that gap.

## Give Each Document a Job

| Document | Reader's Question |
|---|---|
| README | What does it do, and where do I start? |
| Installation And Operations | How do I install, configure, observe, and recover it? |
| Interface Reference | What inputs, outputs, errors, and compatibility guarantees exist? |
| CONTRIBUTING | How do I build, test, propose, and revise a change? |
| SECURITY | How do I report a vulnerability privately, and what happens next? |
| GOVERNANCE And Maintainers | Who decides, reviews, releases, and grants access? |
| Code Of Conduct | What behavior is expected, and where do I report misconduct? |
| LICENSE And Notices | What permissions and obligations apply? |
| CHANGELOG And Support Policy | What changed, what breaks, and which versions receive fixes? |

Link documents instead of maintaining competing copies. Keep secrets out of examples, terminal captures, and generated reference pages. Provide alt text for diagrams, readable headings, searchable text, and English that translates without local idioms.

## Make Contributions Possible

Document the supported build command, test command, coding conventions, contribution rights, branch target, review expectations, and release process. State whether public contributions are accepted. Label suitable starter work and explain how maintainers resolve disagreements.

Issue forms should ask for version, environment, expected behavior, actual behavior, and a minimal reproducer. Provide a separate private security route. Do not require users to paste entire environment dumps, access tokens, or production logs.

A PR should explain the problem, observable behavior after the change, tests, documentation, compatibility, and remaining risks. Assign ownership, labels, and the established project. A draft signals unfinished work; it does not make a public PR confidential.

## Document the Whole Installation Life

Include upgrade, downgrade limits, migration order, backup, restore, rollback, uninstall, and data retention. Say which files and identities survive uninstall and which are deliberately removed. Require a separate explicit action for destructive purge where appropriate.

Test installation, interruption, restart, reboot, upgrade, uninstall, and reinstall on a disposable supported system. Compare the service, filesystem, firewall, and retained data against the documented contract. A successful command exit does not prove that the system is usable or that cleanup was complete.

**Evidence:** a newcomer completes the documented path; links and examples work; lifecycle tests demonstrate both useful operation and recovery.
