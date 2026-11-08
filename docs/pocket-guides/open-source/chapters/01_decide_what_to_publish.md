<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Decide What You Can Publish

## Establish Ownership Before Changing Visibility

Confirm who owns the code and who can authorize its release. Inventory employee contributions, contractors, copied examples, vendored code, fonts, images, test data, generated code, and documentation. A dependency's availability on the Internet does not establish your right to redistribute it.

Choose a license with the people responsible for the project's legal and organizational obligations. Preserve third-party licenses and required notices. A contribution sign-off records a contributor's assertion about their right to contribute; it does not replace ownership review. GitHub's [open-source launch guide](https://opensource.guide/starting-a-project/) explains the role of a license and basic community documents.

For work subject to an employer's publication, records, or export review, obtain that review before publication. Do not infer that software is unrestricted because a repository is easy to make public. Keep the authorization record separate from public source when it contains private information.

**Evidence:** a release decision, license inventory, correct notices, and an accountable publication owner.

## Inspect More Than the Current Files

Inventory branches, tags, reachable Git history, large-file objects, submodules, releases, packages, images, CI logs, artifacts, issue attachments, wikis, screenshots, examples, and generated documentation. A deleted credential may remain in an earlier commit. A sanitized source tree can still build a package containing a private configuration file.

Run both credential detection and project-specific checks for internal identifiers. Review findings with context: a harmless fixture and a working token require different handling. A clean scanner result is evidence about that scan's scope, not proof that every disclosure risk is absent.

> **Warning.** If a credential was exposed, revoke or rotate it first. Removing a file or rewriting history does not invalidate copies already obtained by others. Coordinate history repair because it changes commit identities and can disrupt collaborators. See [GitHub's sensitive-data removal procedure](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

Inspect names and sizes before opening content that may be sensitive.

*Inventory the Git Surface*

```console
$ git branch -a
$ git tag --list
$ git ls-files
$ git submodule status
$ git log --all --oneline -20
```

## Make the Public Boundary Repeatable

If an internal repository feeds a public one, define which is authoritative for each class of change. Prefer an explicit export allowlist, synthetic examples, deterministic substitutions, and scans of the resulting tree and package. Keep the substitution dictionary and private findings private.

Record the source commit, export configuration version, public commit, and validation results. Test a subsequent export after a public contribution: it must retain the contribution or replay it through the documented process. Otherwise the next synchronization can erase an outsider's accepted work.

Do not migrate private issues by copying their titles and bodies onto a public roadmap. Publish safe feature descriptions. Keep embargoed defects in private reporting and review channels. Close a legacy tracker as migrated only after you verify its destination; migration does not mean the defect is fixed.

**Evidence:** the exact public output was inspected, its history and artifacts were assessed, and the next synchronization preserves public changes.
