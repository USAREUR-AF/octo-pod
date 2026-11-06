#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
# check-reproducible.sh: build the same commit twice and require the same bytes.
#
#   tools/check-reproducible.sh                   the release artifacts (tarball, .deb)
#   tools/check-reproducible.sh --docs D          also the documentation site at D
#   tools/check-reproducible.sh --arm udp-ingest  also one arm binary, built with cargo
#                                                 (Linux: on macOS the linker writes
#                                                 object file paths into the binary's UUID)
#   tools/check-reproducible.sh --plant           prove the comparison can fail: the
#                                                 second build gets a different commit
#                                                 time, and the check must notice
#
# Each build runs in its own fresh checkout of HEAD, at a different path, seconds apart,
# under a different umask, time zone and locale. Anything the artifacts pick up from the
# builder rather than from the commit shows up as a difference. Commit first: this
# checks HEAD, not the working tree.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCS=""
ARM=""
PLANT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --docs) [ $# -ge 2 ] || { echo "--docs needs a value" >&2; exit 2; }; DOCS="$(cd "$2" && pwd)"; shift 2 ;;
    --arm)  [ $# -ge 2 ] || { echo "--arm needs a value" >&2; exit 2; }; ARM="$2"; shift 2 ;;
    --plant) PLANT=1; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
if [ -n "$ARM" ] && ! [[ "$ARM" =~ ^[a-z][a-z0-9-]*$ && -f "$REPO/arms/$ARM/Cargo.toml" ]]; then
  echo "--arm $ARM: no such arm under arms/" >&2; exit 2
fi

WORK="$(mktemp -d)"
A="$WORK/a"
B="$WORK/second/build/b"
cleanup() {
  git -C "$REPO" worktree remove --force "$A" >/dev/null 2>&1 || true
  git -C "$REPO" worktree remove --force "$B" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

SDE="$(git -C "$REPO" log -1 --format=%ct)"
REPO_NAME="${GITHUB_REPOSITORY:-example/octo-pod}"
args=(--repo "$REPO_NAME")
if [ -n "$DOCS" ]; then args+=(--docs "$DOCS"); fi

git -C "$REPO" worktree add -q --detach "$A" HEAD
sleep 2
mkdir -p "$(dirname "$B")"
git -C "$REPO" worktree add -q --detach "$B" HEAD

echo "build 1: $A"
( cd "$A" && umask 022 && TZ=UTC LC_ALL=C tools/build-release.sh "${args[@]}" >/dev/null )
sleep 2
second_sde="$SDE"
if [ "$PLANT" = 1 ]; then second_sde=$((SDE + 1)); fi
echo "build 2: $B"
( cd "$B" && umask 002 && TZ=Pacific/Auckland LC_ALL=C.UTF-8 SOURCE_DATE_EPOCH="$second_sde" \
    tools/build-release.sh "${args[@]}" >/dev/null )

# Explain a difference, as far as the tools at hand allow.
explain() {  # explain <artifact>
  local f="$1"
  if command -v diffoscope >/dev/null 2>&1; then
    diffoscope --text - "$A/dist/$f" "$B/dist/$f" | head -60 || true
    return
  fi
  case "$f" in
    *.tar.gz)
      local t=tar
      if command -v gtar >/dev/null 2>&1; then t=gtar; fi
      diff <("$t" --full-time -tvzf "$A/dist/$f" 2>/dev/null || "$t" -tvzf "$A/dist/$f") \
           <("$t" --full-time -tvzf "$B/dist/$f" 2>/dev/null || "$t" -tvzf "$B/dist/$f") | head -20 || true ;;
    *.deb)    diff <(dpkg-deb -c "$A/dist/$f") <(dpkg-deb -c "$B/dist/$f") | head -20 || true ;;
  esac
}

same=1
if cmp -s "$A/dist/SHA256SUMS" "$B/dist/SHA256SUMS"; then
  echo "release artifacts: identical"
  sed 's/^/  /' "$A/dist/SHA256SUMS"
else
  same=0
  echo "release artifacts: DIFFERENT"
  diff "$A/dist/SHA256SUMS" "$B/dist/SHA256SUMS" | sed 's/^/  /' || true
  while read -r _ f; do
    if ! cmp -s "$A/dist/$f" "$B/dist/$f"; then echo "  -- $f"; explain "$f"; fi
  done < "$A/dist/SHA256SUMS"
fi

# ONE ARM, BUILT ON THE HOST in both checkouts. The image builds always run at /build,
# so this is the stricter test: different paths, different target directories. Path
# remapping keeps the checkout and cargo home out of panic locations.
if [ -n "$ARM" ]; then
  bin="$(sed -n '/^\[\[bin\]\]/,/^name/ s/^name *= *"\(.*\)"/\1/p' "$REPO/arms/$ARM/Cargo.toml" | head -1)"
  pkg="$(sed -n 's/^name *= *"\(.*\)"/\1/p' "$REPO/arms/$ARM/Cargo.toml" | head -1)"
  cargo_home="${CARGO_HOME:-$HOME/.cargo}"
  for d in "$A" "$B"; do
    echo "arm $pkg: building in $d"
    ( cd "$d/arms" && RUSTFLAGS="--remap-path-prefix=$d/arms=/build --remap-path-prefix=$cargo_home=/cargo" \
        cargo build --release --locked --quiet -p "$pkg" )
  done
  a_sum="$(shasum -a 256 "$A/arms/target/release/$bin" | cut -d' ' -f1)"
  b_sum="$(shasum -a 256 "$B/arms/target/release/$bin" | cut -d' ' -f1)"
  if [ "$a_sum" = "$b_sum" ]; then
    echo "arm $bin: identical  $a_sum"
  else
    same=0
    echo "arm $bin: DIFFERENT  $a_sum  $b_sum"
  fi
fi

if [ "$PLANT" = 1 ]; then
  if [ "$same" = 1 ]; then
    echo "FAIL: a planted difference in the commit time went unnoticed" >&2; exit 1
  fi
  echo "ok: the planted difference was caught"; exit 0
fi
[ "$same" = 1 ] || { echo "FAIL: the same commit built twice produced different bytes" >&2; exit 1; }
echo "ok: reproducible"
