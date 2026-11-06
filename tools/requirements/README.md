<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Python Dependency Locks

Every `pip install` in CI, the release workflow and the golden image build installs from a
lock that carries a SHA-256 hash for every package, with `--require-hashes`. A package that
does not match its hash fails the install, so a substituted or re-uploaded wheel cannot
reach a runner or an image.

Each lock has an input next to it, a `.in` file with the top-level requirements and the
reason for each pin:

| Input | Lock | Installed by |
|---|---|---|
| `platform/tests/requirements.in` | `platform/tests/requirements.txt` | the template and payload tests |
| `tools/requirements/ansible.in` | `tools/requirements/ansible.txt` | `ci.yml` offline job |
| `tools/requirements/coverage.in` | `tools/requirements/coverage.txt` | `coverage-python.yml` |
| `tools/requirements/pip-audit.in` | `tools/requirements/pip-audit.txt` | `ci.yml` security job |
| `tools/requirements/verify-zenoh.in` | `tools/requirements/verify-zenoh.txt` | `tools/vm-image-provision.sh` |

## Update a Lock

Edit the `.in` file, then regenerate its lock with [uv](https://docs.astral.sh/uv/). The
resolution is universal, so one lock serves Linux runners, the image build and a
developer's machine:

```zsh
uv pip compile --universal --generate-hashes --python-version 3.12 --no-header tools/requirements/pip-audit.in -o tools/requirements/pip-audit.txt
```

Run it from the directory that holds the `.in` file when the input includes another file
with `-r`, as `ansible.in` does.

`verify-zenoh.in` must pin the same version as `verify_zenoh_version` in
`platform/group_vars/all/10-infrastructure.yml`. `test_templates.py` checks that, and the
image build refuses a lock that disagrees.

The `security` job runs pip-audit over every lock, so a lock that pins a version with a
known vulnerability fails CI until it is regenerated.
