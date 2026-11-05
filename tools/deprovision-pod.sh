#!/usr/bin/env bash
# deprovision-pod.sh — put a pod back to a bare box, so the install can be tested again.
#
#   tools/deprovision-pod.sh <user>@<pod-ip>
#   tools/deprovision-pod.sh <user>@<pod-ip> --yes        skip the confirmation
#   tools/deprovision-pod.sh <user>@<pod-ip> --wipe-stage also drop the carried bundle
#
# WHY THIS EXISTS. Testing the install path means running it on a box that has never had
# it, and there is exactly one of those per box unless something puts it back. Doing that
# by hand is nine commands, and the two that are easy to forget — the apt keyring
# directory and the netbird agent — are the two whose absence the install is supposed to
# handle. Forget them and you test the easy path every time, which is how a bug that only
# bites a fresh box survives a dozen "clean" runs.
#
# THE COUNTERPART TO install-to-pod.sh, and driven the same way: from a laptop, over ssh.
# That is not a stylistic choice. A deprovision that runs ON the pod would be removing the
# package it is executing from, and bash reads a script incrementally — deleting it
# mid-run is a hang or a syntax error at a random line, depending on timing.
#
# WHAT IT KEEPS BY DEFAULT. /opt/octo-stage — the carried image bundle. It is ~300 MB, it
# is not what any of this is testing, and re-copying it over a LAN before every run turns
# a two-minute test into a twenty-minute one. --wipe-stage when you want true bare metal.
#
# WHAT IT NEVER TOUCHES. The OS, the user, ssh, the network config. A box you cannot log
# back into is not deprovisioned, it is bricked.
set -euo pipefail

TARGET=""
ASSUME_YES=0
WIPE_STAGE=0
KEEP_IMAGES=0
KEEP_NETBIRD=0
KEEP_PEER=0
STAGE_DIR="/opt/octo-stage"

# Same colour contract as install-to-pod.sh: off when stdout is not a terminal, off when
# NO_COLOR is set. Green worked, yellow read this, red stopped.
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
indent() { sed 's/^/      /'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)      ASSUME_YES=1; shift ;;
    --wipe-stage)  WIPE_STAGE=1; shift ;;
    --keep-images) KEEP_IMAGES=1; shift ;;
    --keep-docker) KEEP_IMAGES=1; shift ;;   # legacy alias, pre-podman
    --keep-netbird) KEEP_NETBIRD=1; shift ;;
    --keep-peer)   KEEP_PEER=1; shift ;;
    -h|--help)     sed -n '2,8p' "$0"; exit 0 ;;
    -*)            die "unknown option: $1" ;;
    *)             TARGET="$1"; shift ;;
  esac
done

[ -n "$TARGET" ] || die "usage: $0 <user@host> [--yes] [--wipe-stage] [--keep-images] [--keep-netbird]

  <user@host>      the pod, over ssh. You must already be able to 'ssh <user@host>'.
  --yes            do not ask. For scripted test loops.
  --wipe-stage     also delete $STAGE_DIR, the carried image bundle (~300 MB).
                   Without this the bundle is kept, so the next install can run
                   airgapped without copying it again.
  --keep-images    leave octo-pod's container images in podman storage. Faster,
                   but then the next provision does NOT exercise the image path —
                   which is where fresh-box bugs live. (The runtime itself always
                   stays: podman belongs to the base image, not to octo-pod.)
  --keep-netbird   leave the netbird agent installed. Same trade-off.
  --keep-peer      leave this pod's peer registered on the mesh. Without this, the
                   peer is cut, because a wiped box cannot cut its own."

# WHAT IS ACTUALLY THERE, read before anything is destroyed rather than after. An
# operator about to wipe a box should see what they are wiping, and a box that turns out
# not to be a pod at all is the one where you want to notice before the rm.
step 1/3 "what is on $TARGET"
ssh -o ConnectTimeout=10 "$TARGET" 'test -f /etc/octo-pod/inventory.ini' \
  || die "target has no octo-pod inventory; refusing to deprovision"
INVENTORY="$(ssh -o ConnectTimeout=10 "$TARGET" '
  v=$(dpkg -s octo-pod 2>/dev/null | sed -n "s/^Version: //p")
  echo "octo-pod:   ${v:-not installed}"
  echo "identity:   $([ -d /etc/octo-pod ] && echo "present ($(ls /etc/octo-pod/host_vars 2>/dev/null | tr "\n" " "))" || echo none)"
  echo "pen state:  $([ -d /var/lib/octo-pen ] && du -sh /var/lib/octo-pen 2>/dev/null | cut -f1 || echo none)"
  echo "containers: $(sudo -n podman ps -aq 2>/dev/null | wc -l | tr -d " ") "
  echo "podman:     $(podman --version 2>/dev/null || echo "not installed")"
  echo "netbird:    $(netbird version 2>/dev/null || echo "not installed")"
  echo "bundle:     $([ -d '"$STAGE_DIR"' ] && du -sh '"$STAGE_DIR"' 2>/dev/null | cut -f1 || echo none)"
')" || die "could not inspect $TARGET. Check you can 'ssh $TARGET'."

# THE PEER NAME, read while the identity still exists. A pod enrols under its pod name
# as hostname, and after the wipe there is nothing left on the box that knows what that
# was. Captured here or not at all.
POD_NAME="$(ssh -o ConnectTimeout=10 "$TARGET" 'ls /etc/octo-pod/host_vars 2>/dev/null | head -1' || true)"

# THE MESH IP, and it is the selector rather than the name. Every rebuild of a pod
# registers a NEW peer under the SAME hostname, so by the second rebuild the name matches
# several and `goat admin peer cut` refuses a selector that is not unique. Cutting by the
# address this box holds right now names exactly the peer this box is about to stop being.
POD_MESH_IP="$(ssh -o ConnectTimeout=10 "$TARGET" \
  'sudo -n netbird status 2>/dev/null | sed -n "s/.*NetBird IP: *\([0-9.]*\).*/\1/p" | head -1' || true)"
printf '%s\n' "$INVENTORY" | indent

if [ "$ASSUME_YES" != 1 ]; then
  printf '\n  This DESTROYS the pod on %s: package, identity, pen CA and every\n' "$TARGET"
  printf '  certificate it ever issued, all containers and images%s.\n' \
    "$([ "$WIPE_STAGE" = 1 ] && echo ", and the carried bundle")"
  printf '  The certificates cannot be recovered — publishers holding them will\n'
  printf '  need new ones from the rebuilt pod.\n\n'
  # NAME THE STRING, do not describe it. This asked for "the hostname part", which an
  # operator reasonably read as the box's hostname (for example, pod-01) when the target
  # was user@192.0.2.10 and the check wanted the address. A confirmation prompt whose
  # answer you have to infer is a prompt people learn to fight rather than read, and this
  # one guards an irreversible wipe.
  printf '  Type %s to confirm: ' "${TARGET#*@}"
  read -r CONFIRM
  [ "$CONFIRM" = "${TARGET#*@}" ] || die "did not match, nothing was changed. Expected: ${TARGET#*@}"
fi

step 2/3 "deprovision"
# Ordered by dependency, not by importance: containers before the images they hold open,
# images before the store that holds them, and every file path last so that a failure
# part-way through still leaves a box whose remaining state is consistent.
#
# Every value expanded into this remote script is a flag parsed above, not operator
# input — expanding client-side is the intent, not an oversight.
# shellcheck disable=SC2029
ssh "$TARGET" "
  set -u
  echo '-- containers (and the quadlet units that would resurrect them)'
  sudo systemctl stop \$(systemctl list-units --no-legend 'octo-*' 2>/dev/null | awk '{print \$1}') 2>/dev/null || true
  sudo rm -f /etc/containers/systemd/octo-*.container
  for c in \$(sudo podman ps -a --format '{{.Names}}' | grep '^octo-'); do
    sudo podman rm -f \"\$c\" || exit 1
  done
  echo '-- octo-pod package'
  sudo apt-get purge -y octo-pod >/dev/null 2>&1 || true
  echo '-- identity, pen CA, registry data'
  sudo rm -rf /etc/octo-pod /var/lib/octo-pen /var/lib/octo-registry /var/lib/octo-pod
  echo '-- systemd units this pod installed'
  for u in pen-cert-expiry.timer pen-cert-expiry.service pen-cert-renew.timer \
           pen-cert-renew.service octo-watchdog.timer octo-watchdog.service \
           netbird-enrol-pending.timer netbird-enrol-pending.service; do
    sudo systemctl disable --now \"\$u\" >/dev/null 2>&1 || true
    sudo rm -f \"/etc/systemd/system/\$u\"
  done
  sudo rm -f /usr/local/sbin/netbird-enrol-pending.sh /usr/local/sbin/octo-watchdog.sh \
             /usr/local/sbin/netbird-selfheal.sh /usr/local/sbin/pen-cert-*.sh
  sudo systemctl daemon-reload 2>/dev/null || true
  echo '-- netbird'
  sudo netbird down >/dev/null 2>&1 || true
  sudo systemctl stop netbird >/dev/null 2>&1 || true
  sudo rm -rf /etc/netbird /var/lib/netbird
  if [ '$KEEP_NETBIRD' != 1 ]; then
    sudo apt-get purge -y netbird >/dev/null 2>&1 || true
    sudo rm -f /etc/apt/sources.list.d/netbird.* /etc/apt/keyrings/netbird.asc
  fi
  echo '-- container runtime: kept, deliberately'
  # podman comes from the device base image's package manifest, not from octo-pod — deprovisioning the appliance never
  # uninstalls the substrate. Only octo-pod's images leave, so re-provision
  # exercises the real image path.
  if [ '$KEEP_IMAGES' != 1 ]; then
    sudo podman rmi -f \$(sudo podman images -q --filter 'reference=*octo*' 2>/dev/null) 2>/dev/null || true
    sudo podman rmi -f \$(sudo podman images -q --filter 'reference=*zenoh*' 2>/dev/null) 2>/dev/null || true
    # legacy docker-era keys, if this box predates the contract base
    sudo rm -f /etc/apt/sources.list.d/docker.* \
               /etc/apt/keyrings/docker.asc /etc/apt/keyrings/docker.gpg
  fi
  # THE KEYRING DIRECTORY, deliberately. A fresh box may not have it, bootstrap must
  # create it, and leaving it behind means every re-test silently skips the one task
  # whose absence broke a Pi. Only removed when it is empty — apt keys that belong to
  # something else on this box are not ours to delete.
  if [ '$KEEP_IMAGES' != 1 ] && [ '$KEEP_NETBIRD' != 1 ]; then
    sudo rmdir /etc/apt/keyrings 2>/dev/null || true
  fi
  if [ '$WIPE_STAGE' = 1 ]; then
    echo '-- carried bundle'
    sudo rm -rf '$STAGE_DIR'
  fi
  sudo apt-get autoremove -y >/dev/null 2>&1 || true
" 2>&1 | indent

# THE POD CANNOT CUT ITS OWN PEER. It holds no NetBird management credential, by design:
# a box that can be stolen should not carry one. So the peer it registered outlives every
# wipe, and a box rebuilt N times leaves N dead peers behind. Four accumulated in one
# afternoon of testing before anyone noticed.
#
# This script runs on the operator's workstation, which is exactly where that credential
# lives, so this is the only place in the whole flow that CAN do it. Advisory rather than
# fatal: the box is already bare, and a leftover peer is untidy rather than dangerous. It
# holds no certificate this pod will ever use again.
if [ "$KEEP_PEER" != 1 ] && [ -n "$POD_NAME" ]; then
  step "2b/3" "cut the mesh peer ${POD_MESH_IP:-for $POD_NAME}"
  if ! command -v goat >/dev/null 2>&1; then
    warn "goat is not on PATH, so the peer is still registered. Cut it with:"
    printf '        goat admin peer cut %s\n' "$POD_NAME"
  elif [ -z "${DEFAULT_NB_API_TOKEN:-}${GOAT_NB_API_TOKEN:-}" ]; then
    warn "no NetBird admin token in the environment, so the peer is still registered."
    printf '      That token is an admin secret and goat reads it from the environment,\n'
    printf '      never a profile. Export it and re-run, or cut the peer by hand:\n'
    printf '        goat admin peer cut %s\n' "$POD_NAME"
  elif [ -z "$POD_MESH_IP" ]; then
    warn "this pod had no mesh address, so there is no unique peer to name. If it did"
    warn "enrol at some point, cut it by id from:"
    printf '        goat admin peer ls | grep %s\n' "$POD_NAME"
  elif goat admin peer cut -y "$POD_MESH_IP" >/dev/null 2>&1; then
    ok "peer $POD_MESH_IP ('$POD_NAME') cut from the mesh"
  else
    warn "could not cut the peer at $POD_MESH_IP. It may already be gone. Check with:"
    printf '        goat admin peer ls | grep %s\n' "$POD_NAME"
  fi
fi

step 3/3 "what is left"
ssh "$TARGET" '
  s=$(dpkg -s octo-pod 2>/dev/null | sed -n "s/^Status: //p"); echo "octo-pod:   ${s:-removed}"
  echo "identity:   $([ -d /etc/octo-pod ] && echo STILL PRESENT || echo gone)"
  echo "pen state:  $([ -d /var/lib/octo-pen ] && echo STILL PRESENT || echo gone)"
  echo "podman:     $(podman --version 2>/dev/null || echo gone)"
  echo "netbird:    $(netbird version 2>/dev/null || echo gone)"
  echo "keyrings:   $([ -d /etc/apt/keyrings ] && echo present || echo gone)"
  echo "bundle:     $([ -d '"$STAGE_DIR"' ] && du -sh '"$STAGE_DIR"' 2>/dev/null | cut -f1 || echo gone)"
' 2>&1 | indent

cat <<NEXT

────────────────────────────────────────────────────────
  Bare box. To build it again:

    tools/install-to-pod.sh $TARGET$([ "$WIPE_STAGE" = 1 ] && echo " --with-images")
NEXT
if [ "$WIPE_STAGE" != 1 ]; then
cat <<KEPT
    ssh $TARGET
    sudo octo-pod bootstrap -e bootstrap_mode=airgap -e image_mirror=localhost:5000

  The bundle was kept, so that bootstrap needs no network and no registry
  credential — it re-seeds the on-pod registry from $STAGE_DIR.
KEPT
fi
printf '────────────────────────────────────────────────────────\n\n'
