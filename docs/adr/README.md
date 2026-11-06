<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Architecture Decision Records

An ADR records one architectural decision: the context, the choice, the alternatives and
the consequences. ADRs are numbered, never renumbered, and superseded rather than
rewritten.

To add one:

1. Copy [TEMPLATE.md](TEMPLATE.md) to `NNNN-short-title.md`, using the next free number in
   [INDEX.md](INDEX.md).
2. Fill it in, with status `Proposed`.
3. Add its row to [INDEX.md](INDEX.md) in the same pull request.

Status values: `Proposed`, `Accepted`, `Superseded`, `Deprecated`.

Use the structure of [GOAT's ADR template](https://github.com/usareur-af/goatcore/blob/main/docs/adr/TEMPLATE.md):
plain-language summary, context, decision, alternatives, consequences, assessor view and
observable revisit triggers. This repository has independent numbering; link upstream
decisions, check their status, and state any implementation gap rather than claiming
that a local record accepts or supersedes GOAT policy.

The longer design notes in [docs/](../) predate this folder and remain the implementation
references. New records start Proposed even when they document an existing mechanism;
acceptance is a separate review decision.
