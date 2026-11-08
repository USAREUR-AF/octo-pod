<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Open Source and OpenSSF Pocket Guides

These guides explain the work behind the [organization checklist](../openssf-repository-checklist.md). Read them as practical engineering guidance; they do not certify Octo-Pod or any other repository.

## Open Sourcing a Repository

1. [Start Here](open-source/chapters/00_start_here.md)
2. [Decide What You Can Publish](open-source/chapters/01_decide_what_to_publish.md)
3. [Help a Newcomer Succeed](open-source/chapters/02_help_a_newcomer_succeed.md)
4. [Build Tests From Promises](open-source/chapters/03_build_tests_from_promises.md)
5. [Prove the Tests Can Detect a Defect](open-source/chapters/04_prove_the_tests_work.md)
6. [Deliver a Release You Can Support](open-source/chapters/05_deliver_and_maintain.md)
7. [Work as a Repository Maintainer](open-source/chapters/06_work_as_a_maintainer.md)
8. [Publication Review](open-source/chapters/07_release_review_and_cheat_sheet.md)
9. [Cheat Sheet](open-source/chapters/08_cheat_sheet.md)

## OpenSSF in Practice

1. [Start Here](openssf/chapters/00_start_here.md)
2. [Choose the Right Assessment](openssf/chapters/01_choose_the_assessment.md)
3. [Collect Evidence That Supports the Claim](openssf/chapters/02_collect_evidence.md)
4. [Turn Testing Requirements Into Protection](openssf/chapters/03_make_testing_meaningful.md)
5. [Maintain the Practice After Assessment](openssf/chapters/04_run_security_and_release_work.md)
6. [Worked Example](openssf/chapters/05_worked_example_and_cheat_sheet.md)
7. [Cheat Sheet](openssf/chapters/06_cheat_sheet.md)

Baseline references use v2026.08.28. [Octo-Pod's evidence worksheet](../openssf-passing.md) records remaining assessment work. Policies, repository settings, successful runs, and operating history are different kinds of evidence.

## Run the Teaching Lab

From the repository root, with Python 3.10 or later:

```sh
python3 -m unittest discover -s docs/pocket-guides/test-lab -v
python3 docs/pocket-guides/test-lab/validate_tests.py
```

The lab is a deliberately small contract, not production authorization code. It verifies five healthy cases, detects four deliberate implementation defects in temporary copies, and proves that its harness rejects zero discovered tests. `platform/tests/test_pocket_lab.py` runs it through the existing Python CI suite.

## Build the Pocket Editions

The Markdown chapters are canonical and readable without private tooling. Each `book.toml` uses the shared house builder's field theme and pocket trim. With the house-style tooling installed, replace `<house-style>` with its directory and run from the repository root:

```sh
python3 <house-style>/tools/housebook.py docs/pocket-guides/open-source/book.toml
python3 <house-style>/tools/housebook.py docs/pocket-guides/openssf/book.toml
```

Use the Python environment containing the house builder's dependencies. Output goes into the ignored `docs/pocket-guides/build/` directory. The renderer is not needed to build or test Octo-Pod, and these guides are not added to the appliance payload. Generated editions remain editorial drafts until their publication review is complete.
