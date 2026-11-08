<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Open Source a Repository

You are ready to publish when someone outside your team can obtain the software, understand its limits, build it, test it, use it, report a problem, and recover from a failed change. A visible repository is only one part of that work.

This pocket guide follows that journey. You work from an internal codebase to a maintained public project, with practical checks at each boundary. The companion OpenSSF guide explains how to assess those practices without confusing a badge with evidence.

The procedures are recommended engineering practice unless a section identifies a project policy or an external requirement. Adapt the depth to the software: a document repository and an appliance installer do not carry the same operational risk.

## Use the Guides

Start with the publication decision, then work through the newcomer experience, tests, delivery, and maintenance. Each chapter explains the consequence of getting the task wrong and the evidence you should retain.

Commands run from the repository root in zsh unless stated otherwise. A leading `$` is a prompt; do not type it. Replace `<owner>`, `<repo>`, `<branch>`, `<tag>`, `<run-id>`, and `<pr-number>` with the repository or object you are inspecting. GitHub commands require the GitHub CLI and appropriate read access. Do not paste command output publicly until you have checked it for sensitive values.

The included Python lab needs Python 3.10 or later and uses only the standard library. It changes temporary files, never a running service. The lab teaches test design; it is not a production authorization library.

## Keep One Evidence Record

For each repository, record its purpose, owner, public URL, assessed commit, supported platforms, release version, decision, and unresolved work. Link each acceptance claim to a command, result, reviewer, and date.

A useful record says, “The packaged CLI rejects the expired fixture at commit X; test Y passed.” “Security done” gives the next maintainer nothing to reproduce.

Begin by identifying the checkout you are assessing.

*Inspect the Working Copy*

```console
$ git status --short
$ git rev-parse HEAD
$ git remote -v
```
