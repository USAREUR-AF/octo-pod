<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Collect Evidence That Supports the Claim

## Check Repository Facts

Confirm the repository you assessed is the repository users consume. Read its visibility, default branch, source history, releases, community documents, and effective protection settings. Organization defaults, repository rulesets, and classic branch protection can interact.

*Inspect Repository Identity and Rules*

```console
$ gh repo view <owner>/<repo>
$ gh api repos/<owner>/<repo>
$ gh api repos/<owner>/<repo>/rulesets
$ gh api repos/<owner>/<repo>/branches/<branch>/protection
```

Inspect rule details, enforcement state, bypass actors, required checks, and human review requirements. A ruleset's name is not evidence of what it enforces. A token that cannot read settings cannot establish that those settings are safe or unsafe.

Keep the raw response in an appropriately restricted evidence store. Publish a minimal explanation or safe link when the full response contains information outside the public scope.

## Bind Results to a Revision

Record the exact source commit and whether the run tested a branch head, a synthetic merge commit, or the release candidate. A successful run on a different commit cannot prove the candidate passed. Inspect the latest relevant attempt, not just an older successful run.

*Inspect Runs and Checks*

```console
$ git rev-parse HEAD
$ gh run list --repo <owner>/<repo>
$ gh run view <run-id> --repo <owner>/<repo>
$ gh api repos/<owner>/<repo>/commits/<sha>/check-runs
```

An overall successful workflow may contain skipped or nonblocking steps. Read its job results and configuration. Establish which result the merge or publication gate actually requires. For long lists, use the API's pagination support so the first page is not mistaken for a complete inventory.

## Separate Policy From History

For a documented response target, cite the policy. For an actual response-time criterion, inspect receipt and first-response timestamps over the required window. Do not reset project history merely because the public repository was created recently.

Retain private details privately and publish only safe aggregate evidence or an appropriate maintainer attestation. A claim about human secure-development knowledge needs a qualified human's confirmation. A generated security document cannot establish what a developer knows.

## Use a Small Evidence Record

| Field | Example |
|---|---|
| Claim | Expired credentials are refused. |
| Scope | HTTP handler and packaged service. |
| Revision | Full candidate commit hash. |
| Check | Named test and workflow run. |
| Result | Expected denial and no write side effect. |
| Limit | Other transports assessed separately. |
| Owner | Responsible maintainer role. |
| Recheck | Authorization or dependency changes. |

This record is more useful than a screenshot without a date. Keep failures and their resolution as well as the final success; they show whether your checks can detect defects.
