#!/usr/bin/env bash
# install-to-pod.sh — put octo-pod on a box, from a laptop, in one command.
#
#   tools/install-to-pod.sh <user>@<pod-ip>
#   tools/install-to-pod.sh <user>@<pod-ip> --version 0.1.0
#   tools/install-to-pod.sh <user>@<pod-ip> --with-images
#   tools/install-to-pod.sh <user>@<pod-ip> --deb dist/octo-pod_0.1.15_all.deb
#
# WHY THIS EXISTS. Installing was six commands across two machines: download three
# files, check a signature, check a hash, copy, install, install dependencies. Every
# one of them is easy to get subtly wrong, and the two that matter — the signature and
# the hash — are the two an operator in a hurry is most likely to skip. A step that is
# skippable under pressure is one that will be skipped, so it is not a step here; it is
# the part of the script that refuses to continue.
#
# WHY FROM A LAPTOP. A pod usually has no route to the release and holds no credential,
# and should not have one. The laptop downloads, VERIFIES, and only then copies. Nothing unverified is
# ever written to the pod.
#
# Once a site has a mirror (get.<site>/octo-pod), a pod can fetch and verify for itself
# with `octo-pod upgrade`, and this is only needed for the very first install.
set -euo pipefail

REPO_SLUG=""      # --repo, else the release or clone this script came from
IDENT=""          # --cert-identity, else the repository's release workflow on a release tag
ISSUER="https://token.actions.githubusercontent.com"
TARGET=""
VERSION=""
WITH_IMAGES=0
LOCAL_DEB=""
STAGE_DIR="/opt/octo-stage"

# COLOUR ONLY WHEN A HUMAN IS LOOKING. Disabled when stdout is not a terminal, so piping
# to a file or a log collector yields clean text, and when NO_COLOR is set, which is the
# convention every well-behaved CLI honours. Green means it worked, yellow means read
# this, red means it stopped.
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
# Show what these commands say. An earlier version sent the download and BOTH checks to
# /dev/null: a 185 MB transfer looked like a hang, and "Verified OK" — the one line that
# says the software is genuine — was never printed. A check the operator cannot see is
# a check they have no reason to believe.
indent() { sed 's/^/      /'; }

# A PROGRESS BAR, because `gh release download` prints NOTHING while it works and the
# image bundle is 272 MB. On a domestic uplink that is minutes of a cursor sitting on a
# blank line, which is indistinguishable from a hang — and the reasonable thing to do
# about a hang is ctrl-C, half way through a download that was fine.
#
# gh has no progress option, so the size is polled off the partial files on disk and
# measured against the sizes the release API already told us. Approximate by a poll
# interval, which is all a progress bar has ever needed to be.
human() { awk -v b="${1:-0}" 'BEGIN{split("B KB MB GB",u," ");i=1;
           while (b>=1024 && i<4){b/=1024;i++} printf (i==1?"%.0f%s":"%.1f%s"), b, u[i]}'; }
bytes_in() { du -sk "$1" 2>/dev/null | cut -f1 | awk '{print $1*1024}'; }
_LAST_BAR=""
draw() {  # draw <bytes-done> <bytes-total>
  # REDRAWN ON WHOLE PERCENT, NOT ON EVERY POLL. Rewriting the line seven times a second
  # reads as a strobe rather than as motion. Keying the redraw on the bar position and the
  # percent gives about a hundred updates across a download — often enough to look
  # continuous, rarely enough to sit still between steps.
  local d="${1:-0}" t="${2:-0}" w=32 p f done_ rest
  [ "$t" -gt 0 ] 2>/dev/null || return 0
  p=$(( d * 100 / t )); [ "$p" -gt 100 ] && p=100
  f=$(( p * w / 100 )); rest=$(( w - f ))
  [ "$f|$p" = "$_LAST_BAR" ] && return 0
  _LAST_BAR="$f|$p"
  done_=$(printf '%*s' "$f" '' | tr ' ' '=')
  printf '\r    %s[%s%s%*s]%s %3d%%  %s / %s   ' \
         "$C_CYA" "$C_GRN" "$done_" "$rest" '' "$C_CYA$C_RST" \
         "$p" "$(human "$d")" "$(human "$t")"
}

# A value-taking flag with nothing after it used to exit silently: `shift 2` with one
# argument left fails, and set -e ends the script before any check can say why.
need_value() { [ $# -ge 2 ] || die "$1 needs a value"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version) need_value "$@"; VERSION="$2"; shift 2 ;;
    --repo) need_value "$@"; REPO_SLUG="$2"; shift 2 ;;
    --deb) need_value "$@"; LOCAL_DEB="$2"; shift 2 ;;
    --cert-identity) need_value "$@"; IDENT="$2"; shift 2 ;;
    --with-images) WITH_IMAGES=1; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *)  TARGET="$1"; shift ;;
  esac
done

[ -n "$TARGET" ] || die "usage: $0 <user@host> [--version X.Y.Z]

  <user@host>    the pod, over ssh. You must already be able to 'ssh <user@host>'.
  --version      which release. Defaults to the newest.
  --repo O/N     the repository whose signed releases to install. Defaults to the
                 one this script came from: its release's RELEASE file, or its clone.
  --deb <path>   install a LOCALLY BUILT package instead of a published release.
                 Skips the download and both integrity checks — see below.
  --cert-identity <regexp>
                 the signer to accept, replacing the default: the release workflow
                 of the repository above, running for an octo-pod-v tag. Only for a
                 release that was genuinely signed by something else.
  --with-images  also carry the container images (~185 MB), so the pod needs no
                 registry credential of its own. Required for a pod that cannot
                 reach a registry, and correct for any pod you would rather not
                 hand a credential to."

# WHOSE RELEASES. The signer is the release workflow of one repository, and that
# repository is an input rather than a name written into this script: a fork installs
# its own releases. Unpacked from a release, the script reads the RELEASE file beside
# it; run from a clone, the clone's GitHub remote.
if [ -z "$LOCAL_DEB" ]; then
  here="$(cd "$(dirname "$0")/.." && pwd)"
  if [ -z "$REPO_SLUG" ] && [ -f "$here/RELEASE" ]; then
    REPO_SLUG="$(sed -n 's/^repository=//p' "$here/RELEASE" | head -1)"
  fi
  if [ -z "$REPO_SLUG" ]; then
    remote="$(git -C "$here" remote get-url origin 2>/dev/null || true)"
    REPO_SLUG="$(printf '%s' "$remote" | sed -nE 's#^(https://github\.com/|git@github\.com:)([^/]+/[^/]+)$#\2#p' | sed 's/\.git$//')"
  fi
  [[ "$REPO_SLUG" =~ ^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$ ]] \
    || die "which repository's releases? Pass --repo <owner>/<name>."
  # THE SAME SIGNER octo-pod upgrade accepts, built the same way. A pattern that stops at
  # the repository accepts a keyless certificate from any workflow in it on any ref, and
  # this is the first install, the one moment the pod cannot check anything itself. The
  # repository name is quoted because its '.' would otherwise match any character.
  if [ -z "$IDENT" ]; then
    IDENT='^https://github\.com/'"$(printf '%s' "$REPO_SLUG" | sed 's/[][\\.*^$+?(){}|]/\\&/g')"'/\.github/workflows/release\.yml@refs/tags/octo-pod-v'
  else
    warn "signer: $IDENT (--cert-identity, not this repository's release workflow)"
  fi
fi

# CAN WE EVEN REACH IT — asked FIRST, before a 272 MB download and two cryptographic
# checks are spent on a box we may not be able to log in to. A mistyped username used to
# surface here as a password prompt AFTER all of that, which is both the slowest possible
# place to learn it and the most confusing: the prompt looks like the box asking for
# credentials, not like the script telling you the user does not exist.
#
# BatchMode first, so key auth either works or fails instantly instead of hanging on a
# prompt. Falling back to an interactive attempt rather than refusing outright, because
# password auth is legitimate — but saying plainly that an unexpected prompt usually means
# the username, since ssh cannot tell you "no such user" without leaking whether one exists.
step 1/7 "can we reach $TARGET"
if ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" true 2>/dev/null; then
  ok "ssh ok"
else
  printf '      key auth did not work, trying interactively.\n'
  printf '      Being asked for a password you did not expect? Check the username —\n'
  printf '      ssh cannot say "no such user", so a typo looks exactly like this.\n'
  printf '      You passed: %s\n' "${TARGET%%@*}"
  ssh -o ConnectTimeout=20 "$TARGET" true \
    || die "cannot ssh to $TARGET, so nothing was downloaded and nothing was changed.
  Check the username, the address, and that you can 'ssh $TARGET' by hand."
  ok "ssh ok"
fi

# --deb EXISTS BECAUSE THE RELEASE IS NOT ALWAYS THE THING YOU WANT TO INSTALL. Testing a
# fix means installing the fix, and it is not a release yet — it may not even be a commit.
# Without this the only options were to cut a release for every test, or to bypass this
# script entirely with scp and dpkg, which is how the checks below stop being run at all.
# A tool that cannot do the thing people need will be worked around, and the workaround
# is always worse than the flag.
#
# WHAT IT GIVES UP, stated rather than buried: a local file has no signature to check and
# no published fingerprint to match, so steps 3 and 4 cannot run. It is trusted because
# YOU built it. That is a different claim from "the release workflow built it", and the
# script says so out loud rather than printing the same output either way.
if [ -n "$LOCAL_DEB" ]; then
  [ -f "$LOCAL_DEB" ] || die "no such file: $LOCAL_DEB
  Build one first:  tools/build-release.sh"
  case "$LOCAL_DEB" in *.deb) ;; *) die "--deb wants a .deb file, got: $LOCAL_DEB" ;; esac
  VERSION="$(basename "$LOCAL_DEB" | sed -n 's/^octo-pod_\(.*\)_all\.deb$/\1/p')"
  [ -n "$VERSION" ] || die "cannot read a version out of $(basename "$LOCAL_DEB").
  Expected octo-pod_<version>_all.deb — that name is how the pod reports what it runs."
  WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
  [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(~[a-z]+[0-9]+)?$ ]] || die "local package version is invalid"
  DEB="octo-pod_${VERSION}_all.deb"
  cp "$LOCAL_DEB" "$WORK/$DEB"

  step 2/4 "local package"
  ok "octo-pod $VERSION from $LOCAL_DEB"
  if command -v sha256sum >/dev/null; then ok "sha256 $(sha256sum "$WORK/$DEB" | cut -d' ' -f1)"
  else ok "sha256 $(shasum -a 256 "$WORK/$DEB" | cut -d' ' -f1)"; fi
  printf '\n      NOT A RELEASE. This package was not built by a release\n'
  printf '      workflow, so there is no signature to verify and no published\n'
  printf '      fingerprint to match it against. It is trusted because you built it.\n'
  printf '      For anything you did not build yourself, omit --deb.\n'
  if [ "$WITH_IMAGES" = 1 ]; then
    die "--with-images needs a published release: the image bundle is a release artifact.
  Build one locally instead:  tools/build-airgap-bundle.sh
  ...or deprovision with the bundle kept, which is the default:
      tools/deprovision-pod.sh $TARGET"
  fi
else

command -v gh     >/dev/null || die "the GitHub CLI (gh) is needed to download a private release"
command -v cosign >/dev/null || die "cosign is needed to check the release signature.
  brew install cosign      (macOS)
  https://github.com/sigstore/cosign/releases"
gh auth status >/dev/null 2>&1 || die "not logged in to GitHub — run: gh auth login"

step 2/7 "which version"
if [ -z "$VERSION" ]; then
  VERSION="$(gh release view --repo "$REPO_SLUG" --json tagName -q .tagName 2>/dev/null | sed 's/^octo-pod-v//')"
  [ -n "$VERSION" ] || die "could not read the newest release from $REPO_SLUG"
fi
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(~[a-z]+[0-9]+)?$ ]] || die "release version is invalid"
ok "octo-pod $VERSION"

# A CACHE, NOT A TEMP DIR. This used to be mktemp with an EXIT trap, so a failure at ANY
# later step — the copy, the install, a dropped ssh — threw away everything already
# downloaded and the next run re-fetched 261 MB. On the flaky link that caused the failure
# in the first place, that can mean it never completes at all: the tool got least usable
# exactly where it was needed most.
#
# Keeping the bytes is safe because NOTHING IS TRUSTED FOR BEING CACHED. The signature and
# the hash check below run on every invocation. A cached file that is corrupt, truncated
# or tampered with fails step 5 exactly as a bad download would, and is then deleted and
# fetched once more. What is skipped is the transfer, never the verification.
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/octo-pod/${VERSION}"
mkdir -p "$CACHE"
WORK="$CACHE"
DEB="octo-pod_${VERSION}_all.deb"

step 3/7 "download"
IMAGES="octo-pod-images-${VERSION}.tar.gz"
DL=(-p "$DEB" -p SHA256SUMS -p SHA256SUMS.cosign-bundle)
[ "$WITH_IMAGES" = 1 ] && DL+=(-p "$IMAGES")
# Ask the release how big the pieces are BEFORE fetching them, so the bar has a
# denominator. If this fails the download still runs — a missing progress bar is not a
# reason to refuse to install.
WANT=("$DEB" SHA256SUMS SHA256SUMS.cosign-bundle)
[ "$WITH_IMAGES" = 1 ] && WANT+=("$IMAGES")
# NAMES AND SIZES OUT, MATCHING DONE IN awk. The first version built a jq `IN(...)` list
# by string-concatenating quotes, got one quote wrong, and produced an expression jq
# refused to parse — so TOTAL was empty, and because a missing bar is deliberately not
# fatal, the whole thing failed completely silently. The bug was invisible precisely
# because the feature degrades gracefully.
#
# Nothing is quoted into a query here, so there is no quoting left to get wrong.
# Joined with '|' because BSD awk rejects a literal newline inside -v ("newline in
# string") — and then split with "\\|", because split()'s separator is a REGEX and a bare
# "|" is alternation with two empty branches, which matches everywhere and silently
# yields nothing. Both of these are the same class of bug as the jq quoting this replaced:
# a wrong separator produces no error, just a total of zero and no bar.
SIZES=""
for _try in 1 2 3; do
  SIZES="$(gh release view "octo-pod-v${VERSION}" --repo "$REPO_SLUG" --json assets \
             --jq '.assets[] | "\(.name)\t\(.size)"' 2>/dev/null)"
  [ -n "$SIZES" ] && break
  sleep 2
done
TOTAL="$(printf '%s\n' "$SIZES" \
         | awk -F'\t' -v want="$(printf '%s|' "${WANT[@]}")" '
             BEGIN { n = split(want, w, "\\|"); for (i = 1; i <= n; i++) if (w[i] != "") keep[w[i]] = 1 }
             ($1 in keep) { total += $2 }
             END { print total + 0 }')"
[ -n "$TOTAL" ] || TOTAL=0
[ "$TOTAL" -gt 0 ] 2>/dev/null && ok "$(human "$TOTAL") to fetch"

# RETRIED, because GitHub returns 503 often enough to matter — measured at roughly two
# calls in five during the August 2026 incident, and a transient 503 halfway through an
# install is not a reason to make someone start over. An interrupted attempt can leave a
# half-written file under its final name, which --skip-existing then keeps; the hash
# check downstream catches it, and step 5 deletes it and fetches it again.
download() {
  local _try DL_PID
  for _try in 1 2 3; do
    # --skip-existing, not --clobber: a file already in the cache is not re-fetched. It is
    # still verified below, which is what makes skipping it safe.
    ( cd "$WORK" && gh release download "octo-pod-v${VERSION}" --repo "$REPO_SLUG" --skip-existing "${DL[@]}" ) &
    DL_PID=$!
    if [ "$TOTAL" -gt 0 ] 2>/dev/null; then
      while kill -0 "$DL_PID" 2>/dev/null; do draw "$(bytes_in "$WORK")" "$TOTAL"; sleep 0.15; done
    fi
    if wait "$DL_PID"; then
      [ "$TOTAL" -gt 0 ] 2>/dev/null && { draw "$TOTAL" "$TOTAL"; printf '\n'; }
      return 0
    fi
    [ "$TOTAL" -gt 0 ] 2>/dev/null && printf '\n'
    printf '      attempt %d failed (GitHub is returning errors) — retrying\n' "$_try"
    # Only temporary files are discarded, not the whole cache: a complete artifact from an
    # earlier run is still good and is about to be verified anyway.
    find "$WORK" \( -name '*.tmp' -o -name '*.part' \) -delete 2>/dev/null || true
    sleep 3
  done
  return 1
}
rm -f "$WORK/SHA256SUMS" "$WORK/SHA256SUMS.cosign-bundle"
download || die "could not download octo-pod-v${VERSION} from $REPO_SLUG after 3 tries.
  If GitHub is having an incident this is not your setup — try again shortly, or use
  --deb with a locally built package."
ok "$DEB and its signature"
if [ "$WITH_IMAGES" = 1 ]; then
  [ -f "$WORK/$IMAGES" ] || die "this release publishes no image bundle ($IMAGES).
  Only releases built with --with-images carry one."
  ok "$IMAGES ($(du -h "$WORK/$IMAGES" | cut -f1))"
fi

# The two checks, on the laptop, BEFORE anything reaches the pod. Order matters: the
# signature establishes the fingerprint list, and only then does a fingerprint mean
# anything. Checked the other way round, a hostile file with a matching hash in its own
# forged list passes.
#
# Each check is the condition of an `if`. Run bare under set -e and pipefail, a failure
# ended the script inside the pipeline, before the refusal that says what it means.
step 4/7 "check the signature (who made this)"
if ! ( cd "$WORK" && cosign verify-blob --bundle SHA256SUMS.cosign-bundle \
         --certificate-identity-regexp "$IDENT" --certificate-oidc-issuer "$ISSUER" \
         SHA256SUMS ) 2>&1 | indent; then
  die "the signature does not check out. This release was NOT built by
  $REPO_SLUG's release workflow. Do not install it."
fi
ok "signed by the release workflow — genuine"

step 5/7 "check the file (is it what was signed)"
if command -v sha256sum >/dev/null; then SUMCHECK=(sha256sum -c --ignore-missing SHA256SUMS)
else SUMCHECK=(shasum -a 256 -c --ignore-missing SHA256SUMS); fi
sumcheck() { ( cd "$WORK" && "${SUMCHECK[@]}" ) 2>&1; }
# A FILE THAT FAILS IS DELETED AND FETCHED ONCE MORE. --skip-existing keeps whatever is
# in the cache, so a download interrupted once would otherwise fail here on every run
# after, as "tampered with", with nothing the operator could do about it from this
# script. Only the artifacts that failed go: the signed manifest checked out above.
if ! SUMS_OUT="$(sumcheck)"; then
  printf '%s\n' "$SUMS_OUT" | indent
  for f in "$DEB" "$IMAGES"; do
    case "$SUMS_OUT" in *"$f: FAILED"*) rm -f "$WORK/$f" ;; esac
  done
  warn "deleting what failed from $CACHE and fetching it again"
  download || die "could not download octo-pod-v${VERSION} from $REPO_SLUG after 3 tries."
  if ! SUMS_OUT="$(sumcheck)"; then
    printf '%s\n' "$SUMS_OUT" | indent
    die "a downloaded file does not match its signed fingerprint, fetched twice. It is
  corrupt or was tampered with in transit. Do not install it.
  The cached copy is in $CACHE; delete that directory to start over."
  fi
fi
printf '%s\n' "$SUMS_OUT" | indent
ok "matches the signed fingerprint$([ "$WITH_IMAGES" = 1 ] && echo ' (both artifacts)')"

fi

# The remaining steps are identical either way — what differs is only how the file got
# here and what could be proven about it. Numbered to match the path actually taken, so
# "3/3" does not appear halfway through a six-step run or vice versa.
if [ -n "$LOCAL_DEB" ]; then S_INSPECT="2b/4"; S_COPY="3/4"; S_INSTALL="4/4"
else                        S_INSPECT="5b/7"; S_COPY="6/7";  S_INSTALL="7/7"; fi

# WHAT IS ALREADY THERE. Installing onto a box that is already a pod is normal — that
# is what an upgrade is — but doing it without saying so is not. The package OWNS
# the installed platform tree, which on a running pod is what its units render from —
# containers were created from: they keep running, and the next time one is recreated it
# uses the new file. An operator should know that before it happens, not discover it
# when an arm will not restart.
step "$S_INSPECT" "what is on $TARGET already"
EXISTING="$(ssh "$TARGET" '
  v=$(dpkg -s octo-pod 2>/dev/null | sed -n "s/^Version: //p")
  [ -n "$v" ] && echo "installed: octo-pod $v" || echo "installed: nothing"
  [ -d /etc/octo-pod ] && echo "identity:  present (kept — an upgrade never touches it)" \
                       || echo "identity:  none (this box is not a pod yet)"
  c=$(podman ps --format "{{.Names}}" 2>/dev/null | grep -c "^octo-" || true)
  [ "$c" -gt 0 ] && echo "running:   $c octo-pod container(s) — they keep running" \
                 || echo "running:   no octo-pod containers"
' 2>/dev/null || echo "could not inspect $TARGET")"
printf '%s\n' "$EXISTING" | indent

# IS A BUNDLE ALREADY THERE. A bundle carried by an earlier run outlives the pod that
# used it — tools/deprovision-pod.sh keeps it deliberately — so "did THIS run carry
# images" is the wrong question to decide what to tell the operator to run next. Asked
# the wrong way, the closing message sends someone with a perfectly good bundle on the
# box to a connected bootstrap that then cannot pull a private image.
# shellcheck disable=SC2029  # $STAGE_DIR expands here deliberately; the path is ours
if ssh "$TARGET" "[ -d '$STAGE_DIR/images' ]" 2>/dev/null; then
  BUNDLE_ON_POD=1
  [ "$WITH_IMAGES" = 1 ] || ok "bundle:    already staged at $STAGE_DIR (kept from an earlier run)"
else
  BUNDLE_ON_POD=0
fi

step "$S_COPY" "copy to $TARGET"
# A PRIVATE DIRECTORY, NOT /tmp/<name>. The package is installed as root, and a name
# anyone can predict from the public version is a name another local user can plant or
# swap before dpkg reads it. mktemp -d is mode 700 and owned by the ssh user, and the
# install below checks the copy against the hash verified here before dpkg sees it.
if command -v sha256sum >/dev/null; then DEB_SHA="$(sha256sum "$WORK/$DEB" | cut -d' ' -f1)"
else DEB_SHA="$(shasum -a 256 "$WORK/$DEB" | cut -d' ' -f1)"; fi
RDIR="$(ssh "$TARGET" 'mktemp -d /tmp/octo-pod-install.XXXXXXXX')" \
  || die "could not create a directory to copy into on $TARGET.
  Check you can 'ssh $TARGET' first."
# It is expanded into every remote command after this, so it must be exactly what
# mktemp makes and nothing else.
[[ "$RDIR" =~ ^/tmp/octo-pod-install\.[A-Za-z0-9]+$ ]] \
  || die "$TARGET answered mktemp with '$RDIR', which is not a directory this script made"
scp -q "$WORK/$DEB" "$TARGET:$RDIR/$DEB" || die "could not copy to $TARGET.
  Check you can 'ssh $TARGET' first."
ok "$RDIR/$DEB"
if [ "$WITH_IMAGES" = 1 ]; then
  # Unpacked where bootstrap already looks. The script's job ends at delivery: loading
  # the registry and seeding it is convergence, and that is Ansible's — which is why
  # this stops here rather than running docker on the far end.
  # shellcheck disable=SC2029  # $STAGE_DIR expands here deliberately; it is ours
  ssh "$TARGET" "sudo mkdir -p '$STAGE_DIR' && sudo chown \"\$(id -un)\" '$STAGE_DIR'" \
    || die "could not create $STAGE_DIR on $TARGET"
  # RSYNC, NOT SCP, FOR THE BIG ONE. scp restarts from zero every time. A pod on poor
  # wifi stalls part-way through 272 MB, and each retry re-sends everything already
  # transferred — so on a bad enough link the copy can never finish, which is precisely
  # the link a border appliance is on.
  #
  # FLAGS ARE PROBED, NOT ASSUMED. macOS ships openrsync, which announces itself as
  # "rsync 2.6.9 compatible" and rejects both --append-verify and --info=progress2. Naming
  # them unconditionally made the copy fail instantly on the most common operator laptop
  # there is, with a usage dump instead of an error.
  #
  # --partial alone is already resumable AND verified: rsync's delta algorithm checksums
  # the existing prefix and sends only what differs. --append-verify just skips that pass
  # when the receiver is known to hold a correct prefix, so losing it costs a little time
  # and no safety.
  RS=(--partial "--timeout=60")
  rsync --help 2>&1 | grep -q -- '--append-verify' && RS+=(--append-verify)
  if rsync --help 2>&1 | grep -q -- '--info'; then RS+=("--info=progress2"); else RS+=(--progress); fi

  if command -v rsync >/dev/null && ssh "$TARGET" 'command -v rsync >/dev/null' 2>/dev/null; then
    rsync "${RS[@]}" "$WORK/$IMAGES" "$TARGET:$STAGE_DIR/$IMAGES" \
      || die "could not copy the image bundle to $TARGET.
  The transfer is resumable — run the same command again and it continues from where it
  stopped rather than starting over."
  else
    warn "rsync is not on both ends; falling back to scp, which cannot resume"
    scp "$WORK/$IMAGES" "$TARGET:$STAGE_DIR/$IMAGES" \
      || die "could not copy the image bundle to $TARGET, and scp restarts from zero.
  Install rsync on the pod to make this resumable:  sudo apt-get install -y rsync"
  fi
  # shellcheck disable=SC2029  # paths expand here on purpose; all of them are ours
  ssh "$TARGET" "sudo rm -rf '$STAGE_DIR/images' \
                 && cd '$STAGE_DIR' && tar -xzf '$IMAGES' && rm -f '$IMAGES'" \
    || die "could not unpack the image bundle on $TARGET"
  ok "$STAGE_DIR/images (unpacked, replacing any earlier bundle)"
fi

step "$S_INSTALL" "install"
# DEPENDENCIES FIRST, so dpkg has nothing to complain about. This used to run `dpkg -i`
# straight away, which on a fresh box ALWAYS printed
#
#   dpkg: dependency problems prevent configuration of octo-pod
#   dpkg: error processing package octo-pod (--install)
#   Errors were encountered while processing: octo-pod
#
# and was then fixed by `apt-get install -f`. The sequence is correct and documented, and
# it still reads as a failed install — three lines of the word "error" in the middle of
# the happy path. Telling the operator in advance that the error is expected does not
# help much either: the fix for output that looks like a failure is output that does not
# look like a failure.
#
# The dependency list is read from the package ITSELF, on the pod, so it cannot drift
# from what the .deb declares. Version constraints are stripped — apt resolves those.
# shellcheck disable=SC2029  # $DEB expands here on purpose; the pod cannot know the name
DEPS="$(ssh "$TARGET" "echo '$DEB_SHA  $RDIR/$DEB' | sha256sum -c --status - && dpkg-deb -f '$RDIR/$DEB' Depends 2>/dev/null" \
        | tr ',' '\n' | sed 's/(.*)//' | tr -d ' ' | grep -v '^$' | tr '\n' ' ')"
[ -n "$DEPS" ] && ok "dependencies: $DEPS"

# apt-get -f stays as the safety net. It should now be a no-op, and on the day the
# package gains a dependency this script did not anticipate it is the thing that still
# finishes the job rather than leaving a half-configured package.
#
# The hash is checked again on the pod, last thing before dpkg reads the file: exit 3
# says the copy is not what was verified on this laptop, and nothing was installed.
# The directory goes once the package is in; on a failure it stays, for the commands
# printed below.
INSTALL_RC=0
# shellcheck disable=SC2029  # every expansion here is ours and validated above
ssh "$TARGET" "
  set -e
  sudo apt-get update -qq 2>/dev/null || true
  ${DEPS:+sudo apt-get install -y -qq $DEPS 2>&1 | tail -2}
  echo '$DEB_SHA  $RDIR/$DEB' | sha256sum -c --status - || exit 3
  sudo dpkg -i '$RDIR/$DEB' 2>&1 | grep -vE '^\(Reading database|^Preparing to unpack' || true
  sudo apt-get install -f -y -qq 2>&1 | tail -2 || true
  dpkg -s octo-pod 2>/dev/null | grep -q '^Status: install ok installed'
  rm -rf '$RDIR'
" 2>&1 | indent || INSTALL_RC="${PIPESTATUS[0]}"
[ "$INSTALL_RC" != 3 ] \
  || die "the copy on $TARGET does not match the package verified here, so it was not
  installed. Something changed it after the copy. Remove it and run this again:
    ssh $TARGET rm -rf $RDIR"
[ "$INSTALL_RC" = 0 ] \
  || die "the package did not reach 'install ok installed'. Run these on the box:
    sudo apt-get install -y $DEPS
    sudo dpkg -i $RDIR/$DEB
    sudo apt-get install -f -y"
ok "installed"

printf '\n────────────────────────────────────────────────────────\n'
ssh "$TARGET" "octo-pod version" 2>/dev/null || true
cat <<NEXT
────────────────────────────────────────────────────────

The software is on the box. It is not a pod yet — it has no identity and
nothing is running. Next:

  ssh $TARGET
$(if [ "$WITH_IMAGES" = 1 ] || [ "$BUNDLE_ON_POD" = 1 ]; then
cat <<WITH
  sudo octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000

That starts a small registry ON the pod and loads the carried images into it.
A registry rather than a plain 'docker load' because this pod is pinned to exact
image fingerprints, and load does not preserve them — a registry does.
WITH
else
cat <<PLAIN
  sudo octo-pod bootstrap        prepare the box   (safe to re-run)
PLAIN
fi)

NEXT
