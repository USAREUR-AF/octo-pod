#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
# Run from the operator workstation: the target package is removed during cleanup.
set -euo pipefail
TARGET=""
ASSUME_YES=0
PURGE_DATA=0
WIPE_STAGE=0
KEEP_NETBIRD=0
KEEP_PEER=0
usage() {
  cat <<'HELP'
Usage: deprovision-pod.sh user@host [--yes] [--purge-data] [--wipe-stage]
Remove octo-pod services and software, preserving identity, keys and retained data.
  --purge-data     permanently erase pod identity, keys, data and NetBird state;
                   also remove NetBird and attempt removal of its mesh peer
  --wipe-stage     also erase /opt/octo-stage (independent of --purge-data)
  --keep-netbird   with --purge-data, preserve NetBird and its peer/state
  --keep-peer      skip server-side peer removal; requires manual retirement
  --keep-images   accepted for compatibility; shared container images always stay
  --yes, -y       skip confirmation, but never bypass preflight checks
Use a LAN or console-accessible address for --purge-data. See docs/upgrading.md.
HELP
}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1 ;;
    --purge-data) PURGE_DATA=1 ;;
    --wipe-stage) WIPE_STAGE=1 ;;
    --keep-netbird) KEEP_NETBIRD=1 ;;
    --keep-peer) KEEP_PEER=1 ;;
    --keep-images|--keep-docker) : ;;
    --help|-h) usage; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) [ -z "$TARGET" ] || die 'only one target is supported'; TARGET="$1" ;;
  esac
  shift
done
[ -n "$TARGET" ] || { usage >&2; exit 1; }
# Disallow shell syntax in the SSH destination; flags never become remote code.
[[ "$TARGET" =~ ^[a-zA-Z0-9_.@:%-]+$ ]] || die 'invalid SSH target'
REMOVE_NETBIRD=0
if [ "$PURGE_DATA" = 1 ] && [ "$KEEP_NETBIRD" = 0 ]; then REMOVE_NETBIRD=1; fi
if [ "$REMOVE_NETBIRD" = 1 ] && [ "$KEEP_PEER" = 0 ]; then
  command -v goat >/dev/null || die 'goat is required to retire the mesh peer; install it or plan manual retirement with --keep-peer'
  goat admin peer ls >/dev/null || die 'cannot read mesh peers; resolve operator credentials before purging'
fi

# Preflight is read-only, including with --yes. Keep the route check outside sudo
# so SSH_CONNECTION is available. NetBird uses wt0 by default; compare its address
# to the route source as well to cover a renamed tunnel interface.
ssh -o ConnectTimeout=10 "$TARGET" "bash -s -- $REMOVE_NETBIRD" <<'PREFLIGHT' || die 'preflight failed; refusing to deprovision'
set -euo pipefail
sudo -n true
if ! sudo -n test -f /etc/octo-pod/inventory.ini && ! sudo -n test -f /var/lib/octo-uninstall/receipt; then
  echo 'target has no octo-pod inventory or removal receipt; refusing to deprovision' >&2
  exit 1
fi
if [ "$1" = 1 ]; then
  client=${SSH_CONNECTION%% *}
  [ -n "$client" ] || { echo 'cannot establish SSH route; use a direct connection' >&2; exit 1; }
  route=$(ip route get "$client")
  mesh=$(sudo -n netbird status 2>/dev/null | sed -n 's/.*NetBird IP: *\([^ /]*\).*/\1/p') || mesh=""
  case " $route " in
    *' dev wt0 '*|*' dev netbird'*) echo 'refusing to remove the SSH mesh connection; use a LAN address' >&2; exit 1 ;;
  esac
  if [ -n "$mesh" ] && [[ " $route " == *" src $mesh "* ]]; then
    echo 'SSH uses the mesh address; use a LAN address' >&2; exit 1
  fi
fi
PREFLIGHT

printf 'Target: %s\n' "$TARGET"
if [ "$PURGE_DATA" = 1 ]; then
  printf 'PURGE: permanently erase pod identity, CA, private keys, retained data and audit records.\n'
else
  printf 'REMOVE: stop pod services and remove software; retain identity, keys, data and NetBird.\n'
fi
[ "$REMOVE_NETBIRD" = 0 ] || printf 'NetBird will be stopped and removed; its mesh peer needs retirement.\n'
[ "$WIPE_STAGE" = 0 ] || printf 'The carried image bundle will also be erased.\n'
printf 'Podman, shared images, OS networking and system logs are retained.\n'
printf 'Only standard installation paths are cleaned; custom paths require a separate cleanup plan.\n'
if [ "$ASSUME_YES" = 0 ]; then
  printf 'Type %s to confirm: ' "${TARGET#*@}"
  read -r confirm
  [ "$confirm" = "${TARGET#*@}" ] || die 'confirmation did not match; nothing changed'
fi

# The entire payload is delivered from the laptop, not read from the package being
# removed. A protected receipt survives failures and records the mesh selector for retry.
# shellcheck disable=SC2029 # only locally parsed boolean flags expand here
ssh "$TARGET" "sudo -n bash -s -- $PURGE_DATA $WIPE_STAGE $REMOVE_NETBIRD" <<'CLEANUP'
set -euo pipefail
umask 077
purge=$1
wipe_stage=$2
remove_netbird=$3
mkdir -p /var/lib/octo-uninstall
chmod 700 /var/lib/octo-uninstall
if [ ! -f /var/lib/octo-uninstall/receipt ]; then
  printf 'octo-pod removal started\n' > /var/lib/octo-uninstall/receipt
fi
if [ "$remove_netbird" = 1 ] && { [ -f /etc/octo-pod/inventory.ini ] || [ ! -f /var/lib/octo-uninstall/mesh-ip ]; }; then
  mesh=$(netbird status 2>/dev/null | sed -n 's/.*NetBird IP: *\([^ /]*\).*/\1/p') || mesh=""
  if [ -n "$mesh" ]; then
    previous=$(cat /var/lib/octo-uninstall/mesh-ip 2>/dev/null || true)
    if [ -f /etc/octo-pod/inventory.ini ] || [ "$mesh" != "$previous" ]; then
      rm -f /var/lib/octo-uninstall/peer-retired
    fi
    printf '%s\n' "$mesh" > /var/lib/octo-uninstall/mesh-ip
  fi
fi
# Stop producers and timers before touching credentials or removing the firewall.
units=$(systemctl list-units --all --no-legend --plain 'octo-*' 'pen-cert-*' 'netbird-enrol-pending.*' 'net-isolation.service')
while read -r unit rest; do
  [ -n "$unit" ] || continue
  systemctl stop "$unit"
done <<< "$units"
# Disable only file-backed units; Quadlet's generated services are removed by
# deleting their source, not by attempting to disable a generated unit.
for unit in /etc/systemd/system/octo-*.service /etc/systemd/system/octo-*.timer \
            /etc/systemd/system/pen-cert-*.service /etc/systemd/system/pen-cert-*.timer \
            /etc/systemd/system/netbird-enrol-pending.* /etc/systemd/system/net-isolation.service; do
  [ -e "$unit" ] || continue
  systemctl disable --now "${unit##*/}"
  rm -f "$unit"
done
containers=$(podman ps -a --format '{{.Names}}')
while read -r container; do
  case "$container" in octo-*) podman rm -f "$container" ;; esac
done <<< "$containers"
containers=$(podman ps -a --format '{{.Names}}')
if printf '%s\n' "$containers" | grep '^octo-' >/dev/null; then
  echo 'pod containers remain; refusing firewall or data removal' >&2; exit 1
fi
rm -f /etc/containers/systemd/octo-*.container /etc/cron.d/netbird-selfheal
rm -f /usr/local/sbin/netbird-enrol-pending.sh /usr/local/sbin/netbird-selfheal.sh \
      /usr/local/sbin/octo-watchdog /usr/local/sbin/octo-watchdog.sh \
      /usr/local/sbin/pen-cert-expiry-check /usr/local/sbin/pen-cert-renew \
      /usr/local/sbin/pen-cert-*.sh
# List succeeds first: permission/runtime failures must not be mistaken for absence.
tables=$(nft list tables)
for table in octo_pod net_isolation; do
  if printf '%s\n' "$tables" | grep -Fx "table inet $table" >/dev/null; then
    nft delete table inet "$table"
  fi
done
rm -f /etc/octo-pod-firewall.nft /etc/octo-pod-firewall.nft.new \
      /etc/nftables-net-isolation.nft /usr/local/sbin/octo-firewall-apply \
      /etc/containers/registries.conf.d/50-octo-mirror.conf \
      /etc/apt/sources.list.d/octo-pod.list /etc/apt/keyrings/octo-pod.asc
systemctl daemon-reload
# No autoremove and no image deletion: those resources can serve other workloads.
package_present() {
  local packages
  packages=$(dpkg-query -W -f='${binary:Package} ${db:Status-Status}\n') || exit "$?"
  printf '%s\n' "$packages" | grep -E "^$1 (installed|config-files)$" >/dev/null
}
if package_present octo-pod; then apt-get purge -y octo-pod; fi
if [ "$remove_netbird" = 1 ]; then
  if package_present netbird; then
    systemctl disable --now netbird
    apt-get purge -y netbird
  fi
  rm -rf /etc/netbird /var/lib/netbird
  rm -f /etc/apt/sources.list.d/netbird.* /etc/apt/keyrings/netbird.asc
fi
if [ "$purge" = 1 ]; then
  rm -rf /etc/octo-pod /var/lib/octo-pen /var/lib/octo-registry /var/lib/octo-pod \
         /var/lib/octo-egress /etc/octo-arm /etc/octo-arm-* /etc/octo-egress-* \
         /etc/octo-downlink /etc/fabric-uplink /etc/octo-pen-uplink
fi
if [ "$wipe_stage" = 1 ]; then rm -rf /opt/octo-stage; fi
# Verify the properties we promise. Any failure exits nonzero; no success banner.
if package_present octo-pod; then echo 'octo-pod package remains' >&2; exit 1; fi
containers=$(podman ps -a --format '{{.Names}}')
if printf '%s\n' "$containers" | grep '^octo-' >/dev/null; then echo 'pod containers remain' >&2; exit 1; fi
tables=$(nft list tables)
if printf '%s\n' "$tables" | grep -E '^table inet (octo_pod|net_isolation)$' >/dev/null; then
  echo 'pod firewall tables remain' >&2; exit 1
fi
for file in /etc/containers/systemd/octo-*.container /etc/systemd/system/octo-* \
            /etc/systemd/system/pen-cert-* /etc/systemd/system/netbird-enrol-pending.* \
            /etc/systemd/system/net-isolation.service /etc/cron.d/netbird-selfheal; do
  [ ! -e "$file" ] || { echo "cleanup incomplete: $file" >&2; exit 1; }
done
if [ "$remove_netbird" = 1 ] && package_present netbird; then echo 'NetBird package remains' >&2; exit 1; fi
printf 'local cleanup verified\n' > /var/lib/octo-uninstall/receipt
CLEANUP

if [ "$REMOVE_NETBIRD" = 1 ] && [ "$KEEP_PEER" = 0 ]; then
  mesh=$(ssh "$TARGET" 'sudo -n cat /var/lib/octo-uninstall/mesh-ip 2>/dev/null || true')
  [[ "$mesh" =~ ^[0-9a-fA-F:.]+$ ]] || die 'local cleanup complete; no unique mesh address recorded. Retire the peer manually, then rerun with --keep-peer'
  command -v goat >/dev/null || die 'local cleanup complete; goat is required on this workstation to retire the peer. Retry after installing it, or retire manually and use --keep-peer'
  if ! ssh "$TARGET" 'sudo -n test -f /var/lib/octo-uninstall/peer-retired'; then
    goat admin peer cut -y "$mesh" || die 'local cleanup complete; mesh peer retirement failed. Retry or verify retirement manually and use --keep-peer'
    ssh "$TARGET" 'sudo -n touch /var/lib/octo-uninstall/peer-retired'
  fi
fi
printf 'Removal complete. OS network configuration, shared images and runtime remain.\n'
[ "$PURGE_DATA" = 1 ] || printf 'Identity, keys, retained data and NetBird remain. Use --purge-data only after preserving required records.\n'
[ "$KEEP_PEER" = 0 ] || printf 'Mesh peer retirement was explicitly skipped; verify it separately.\n'
