#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
# mint-key.sh — mint a mesh setup key and put it on the pod, without it ever being typed.
#
#   tools/mint-key.sh <user>@<pod-ip>
#   tools/mint-key.sh <user>@<pod-ip> --pod <pod-name>
#   tools/mint-key.sh <user>@<pod-ip> --print       show it instead of staging
#
# WHY THIS EXISTS. The documented way to do this was a three-command pipeline with a UUID
# regex in the middle. It is correct and nobody is going to type it, so in practice people
# paste the key on a command line instead, where it lands in shell history and in the
# scrollback of whoever is watching. A credential that is inconvenient to handle safely
# gets handled unsafely.
#
# WHAT IT DOES NOT DO. It does not enrol a machine. `goat machine enroll` decides a node
# class and a custodian, and those are choices with consequences that should not be
# defaulted by a convenience script. If the machine has no anchor yet this says so and
# names the command, rather than guessing a class on your behalf.
#
# WHY IT IS NOT PART OF install-to-pod.sh. Installing software and minting a credential
# are different acts, and a pod built with `octo-pod init` needs no key at all. Keeping
# them apart means installing never requires a mesh admin token.
set -euo pipefail

TARGET=""; POD=""; PRINT_ONLY=0; DEST="/root/nb.key"

# Same colour contract as the other tools: off when stdout is not a terminal, off when
# NO_COLOR is set.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RST=$'\033[0m'; C_RED=$'\033[31m'; C_GRN=$'\033[32m'
  C_YEL=$'\033[33m'; C_CYA=$'\033[36m'; C_BLD=$'\033[1m'
else
  C_RST=""; C_RED=""; C_GRN=""; C_YEL=""; C_CYA=""; C_BLD=""
fi
die()  { printf '\n%sERROR:%s %s\n' "${C_RED}${C_BLD}" "$C_RST" "$*" >&2; exit 1; }
step() { printf '\n%s[%s]%s %s\n' "$C_CYA" "$1" "$C_RST" "$2"; }
ok()   { printf '    %s✓%s %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '    %s!%s %s\n' "$C_YEL" "$C_RST" "$*"; }

# A value-taking flag with nothing after it used to exit silently: `shift 2` with one
# argument left fails, and set -e ends the script before any check can say why.
need_value() { [ $# -ge 2 ] || die "$1 needs a value"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --pod)     need_value "$@"; POD="$2"; shift 2 ;;
    --dest)    need_value "$@"; DEST="$2"; shift 2 ;;
    --print)   PRINT_ONLY=1; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    -*)        die "unknown option: $1" ;;
    *)         TARGET="$1"; shift ;;
  esac
done

[ -n "$TARGET" ] || [ "$PRINT_ONLY" = 1 ] || die "usage: $0 <user@host> [--pod <name>] [--print] [--dest <path>]

  <user@host>   the pod, over ssh. Omit only with --print.
  --pod <name>  the machine handle. Read from the pod's identity when it has one,
                which it does not immediately after a deprovision, so pass it then.
  --print       print the key instead of staging it, for a pod you cannot ssh to.
                It goes to stdout so you can pipe it; nothing else does.
  --dest        where to write it on the pod (default $DEST). /root is deliberate:
                it survives deprovision-pod.sh, so a key staged once serves a rebuild."

command -v goat >/dev/null 2>&1 \
  || die "goat is not on PATH, and it is what mints the key.
  Without it, mint one in the NetBird console and place it yourself:
    printf '%s' '<KEY>' | ssh $TARGET 'sudo sh -c \"umask 077 && tee $DEST >/dev/null\"'"

# NO TOKEN CHECK HERE, deliberately. `goat machine mesh-key` authenticates with the
# profile's mTLS client certificate; the NetBird admin token is needed by `goat admin
# peer`, which this script does not call. A pre-flight check for a credential the command
# does not use is a false alarm, and a warning that fires on every successful run teaches
# people to ignore warnings. Verified: mesh-key succeeds with neither variable set.

# THE POD NAME, from the identity when there is one. After a deprovision there is not,
# which is exactly when you need a key, so --pod covers it.
if [ -z "$POD" ] && [ -n "$TARGET" ]; then
  POD="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" \
        'ls /etc/octo-pod/host_vars 2>/dev/null | head -1' 2>/dev/null || true)"
  [ -n "$POD" ] && ok "pod name from its identity: $POD"
fi
[ -n "$POD" ] || die "could not tell which machine this is. The pod has no identity yet,
  which is normal right after a deprovision. Name it:
    $0 $TARGET --pod <machine-handle>"

step 1/2 "mint a one-off key for '$POD'"
# mesh-key, not enroll: quota-neutral, and it re-derives the groups from the machine's
# BORN-AS class rather than letting this script pick one. A convenience wrapper choosing
# a node class is how a taxonomy gets a catch-all.
MINT="$(goat machine mesh-key "$POD" 2>&1 || true)"
KEY="$(printf '%s' "$MINT" \
       | grep -oE '[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}' \
       | head -1)"

if [ -z "$KEY" ]; then
  printf '%s\n' "$MINT" | sed 's/^/      /' >&2
  die "no key in that output.
  If it says the machine is not enrolled, it has no anchor yet. Enrol it once, which
  DOES consume a unit of your quota and stamps a born-as class:
    goat machine enroll $POD --node-class KID --autonomy supervised --custodian <handle>
  An operator must always pass --custodian; self-service is for non-operators."
fi
ok "minted, one-off and single-use"
# The groups are worth showing and the key is not. A key carrying groups nobody chose
# grants access nobody decided on, so surface them where someone will read them.
#
# REDACTED, NOT FILTERED, and that distinction is the whole of it. The first version
# selected the lines to show with a case-insensitive grep for "groups|mesh", and goat
# labels the secret "MESH SETUP KEY", which starts with MESH. So the one script whose
# entire purpose is that the key is never displayed printed it, on its first run.
#
# A denylist of lines to hide fails the moment the format changes. Removing anything
# key-shaped from the whole block first cannot: a new line carrying a key arrives already
# redacted, whether or not this script knows the label.
printf '%s\n' "$MINT" \
  | sed -E 's/[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}/<setup-key>/g' \
  | grep -iE '^[[:space:]]*(groups|mesh)[[:space:]]' | sed 's/^/      /' || true

if [ "$PRINT_ONLY" = 1 ]; then
  step 2/2 "print it"
  warn "this goes to stdout, so it WILL be in your scrollback and history if you paste it"
  printf '%s\n' "$KEY"
  exit 0
fi

step 2/2 "stage it on $TARGET"
# Piped, never an argument: a key on a command line is readable from `ps` for as long as
# the command runs, and lands in the history of whoever ran it.
#
# CREATED 600, not chmod-ed to it afterwards. tee makes the file under the default umask,
# so the key sat in a 644 file until the chmod ran, readable by anyone who can reach
# --dest. The umask is set inside the root shell because sudo applies its own.
printf '%s' "$KEY" | ssh -o BatchMode=yes "$TARGET" \
  "sudo sh -c 'umask 077; t=\$(mktemp \"\$1.XXXXXXXX\") || exit 1; trap \"rm -f \\\"\$t\\\"\" EXIT; cat > \"\$t\" && chmod 600 \"\$t\" && mv -f \"\$t\" \"\$1\"' mint-key '$DEST'" \
  || die "could not write $DEST on $TARGET. Check you can 'ssh $TARGET' and sudo there."

SIZE="$(ssh -o BatchMode=yes "$TARGET" "sudo stat -c %s '$DEST'" 2>/dev/null || echo 0)"
[ "$SIZE" -ge 36 ] || die "$DEST is $SIZE bytes, which is too short to be a key. Nothing else was changed."
ok "$DEST  ($SIZE bytes, mode 600, root)"

cat <<NEXT

────────────────────────────────────────────────────────
  The key is on the pod and was never displayed. Next:

    ssh $TARGET
    sudo octo-pod join --setup-key-file $DEST --mgmt https://<netbird-host> --pod $POD

  It is ONE-OFF. Enrolling consumes it, and a second attempt with the same
  key does not fail cleanly: netbird neither refuses nor reports a used key,
  so the play waits 120 seconds and gives up. Re-run this script for every
  rebuild; it is quota-neutral.
────────────────────────────────────────────────────────
NEXT
