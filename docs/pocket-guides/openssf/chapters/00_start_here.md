<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Put OpenSSF Into Practice

Use OpenSSF to organize security work and make the evidence inspectable. Begin with the software's risks and the people who maintain it. Then choose an assessment, implement the applicable controls, and record what actually operates.

This guide explains three tools you will encounter: OSPS Baseline, Best Practices badges, and Scorecard. It uses Baseline v2026.08.28 and the Passing questionnaire assessed on 2026-11-08. Recheck the official criteria before a later submission.

Read the companion open-source guide for the full publication lifecycle and the runnable test-validation lab. Keep the repository checklist alongside this guide: it contains the individual criteria; this guide explains how to satisfy and verify them.

Commands run from your repository root in zsh. A leading `$` is a prompt, not part of the command. Replace `<owner>`, `<repo>`, `<branch>`, `<sha>`, `<pr-number>`, `<run-id>`, and `<tag>` with the object you are inspecting. Use a full commit hash for `<sha>`. Never publish raw evidence containing credentials or private findings.

## Start With an Honest Assessment

Write down the repository URL, product scope, source commit, supported versions, framework version, assessor, evidence owner, and review date. Include the dependencies, build system, distribution channels, and related repositories on which the product depends.

A policy describes the intended rule. A configuration shows how a system is set up. A successful run shows one execution. Operating history shows whether a practice has been followed over time. Keep those four kinds of evidence distinct.

For each criterion, record the requirement, applicability, status, evidence, limitation, owner, and next action. An unknown is work to investigate. It is not permission to select Met.
