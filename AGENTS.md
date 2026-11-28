<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# AGENTS.md

Instructions for AI coding agents, and a short version for anyone working here. [CONTRIBUTING.md](CONTRIBUTING.md) is the full process.

## What this repository is

octo-pod is an edge gateway appliance. It runs on one small computer where sensors are, takes in their feeds, records and audits them locally, and releases only what the operator chooses onto a shared Zenoh network. It can also deliver data from that network to a local receiver.

## Layout

| Path | What |
|---|---|
| `arms/` | the Rust workspace: the ingest, egress, health and SAPIENT programs, and `fuzz/` |
| `platform/` | the Ansible playbooks, templates and image pins that configure a pod; `platform/tests/` holds the platform, CLI and release tests |
| `bin/` | the `octo-pod` command and its join, identity and upgrade tools |
| `tools/` | release, image, air-gap and verification scripts |
| `example/` | an example inventory and host variables |
| `docs/` | deployment, operations, designs, and `adr/` |
| `REUSE.toml`, `LICENSES/` | license and copyright information, checked by `reuse lint` |

## Before you push

Run what CI runs (`.github/workflows/ci.yml`). The Rust checks run from `arms/`:

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace --locked
```

The platform checks run from `platform/`:

```bash
python3 tests/test_templates.py
python3 tests/test_release.py
```

A changed shell script must pass `shellcheck`. The "Run the Checks" section of [CONTRIBUTING.md](CONTRIBUTING.md) lists the rest, including the playbook syntax check.

## Rules

1. **Every file states its license.** A new file starts with the SPDX copyright and license lines, or is listed in `REUSE.toml`; `reuse lint` checks every pull request.
2. **Commits.** Every commit is signed off (`git commit -s`, the DCO) and signed with a key GitHub verifies.
3. **Pull request titles** follow Conventional Commits, `<type>(<scope>): <subject>`, with one lowercase scope and 100 characters or fewer in all. The title becomes the commit on `main`.
4. **Branches** are named `<type>/<slug>` and target `main`. Stacked pull requests are not accepted.
5. **Tests come with the change.** A bug fix includes a test that fails without the fix. Rust has no `unsafe` code.
6. **Vulnerabilities** go through [SECURITY.md](SECURITY.md), never a public issue.

octo-pod connects to a goat data fabric. The contribution guide for goat is [goatcore CONTRIBUTING.md](https://github.com/USAREUR-AF/goatcore/blob/main/CONTRIBUTING.md); this repository's own [CONTRIBUTING.md](CONTRIBUTING.md) is the one that applies here.
