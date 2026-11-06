<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Vendored SAPIENT definitions

BSI Flex 335 v2.0, as published by Dstl. Copied verbatim, copyright headers intact, and
compiled by `../build.rs`.

Vendored rather than fetched so the arms build from a checkout with no network and no
protobuf toolchain: an edge appliance's build must not depend on a definition someone
else can move. The `sapient` session arm uses them, and an external translation gateway can take the
same definitions from this one crate.

Only the standard's own files are here. The well-known types it imports —
`google/protobuf/timestamp.proto` and `descriptor.proto`, which its custom options need —
come from the compiler, which carries its own copies. Vendoring those as well is worse
than redundant: the copies distributed alongside the standard are Editions-era, and their
`features` syntax is not what a compiler pinned to proto3 parses.

Updating to a later ICD version does not mean editing these files. A peer on another
version is a second directory beside this one and a second module in `src/lib.rs`,
selected by the `icd_version` a peer declares, because two peers on different versions
are a normal state for a fleet.
