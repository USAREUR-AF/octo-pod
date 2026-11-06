# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
# bash completion for octo-pod.  Installed to /usr/share/bash-completion/completions.
#
# WHAT IS WORTH COMPLETING, and it is not mainly the verbs. The verbs are on one help
# screen and you learn them in a day. What nobody holds in their head is the stuff that
# has to match something ELSE exactly:
#
#   --iface     the box's real interface names. Guessing eth1 on a Jetson where it is
#               enP8p1s0 is the single most common way set-lan gets written wrong, and
#               the resulting netplan applies cleanly and silently does nothing.
#   --framing   stream|newline|len32be|len32le. Four values, no way to derive them, and
#               a wrong one is a running arm that produces subtly corrupt frames.
#   arm ids     for release / remove-arm. These are whatever the operator named them
#               weeks ago, and a typo'd id is refused — but only after you have gone
#               and looked it up.
#
# All of it is read from the pod's own declaration, so completion cannot disagree with
# what the pod actually has. If /etc/octo-pod does not exist yet nothing is offered
# beyond the verbs, which is correct: there is nothing to name yet.
#
# zsh users: this works under bashcompinit. For zsh, enable bashcompinit before sourcing this file.

# Read completion candidates without Bash's mapfile builtin, which zsh does not supply.
_octo_pod_reply() {
  local line
  COMPREPLY=()
  while IFS= read -r line; do
    COMPREPLY+=("$line")
  done
}

_octo_pod_ifaces() {
  # /sys/class/net rather than `ip link`: no parsing, no ip(8), and it is a glob rather
  # than `ls | grep`, which mangles any name with a space in it.
  local n
  if [ -d /sys/class/net ]; then
    for n in /sys/class/net/*; do
      n="${n##*/}"
      [ "$n" = lo ] || printf '%s\n' "$n"
    done
  else
    # No sysfs — a Mac running the CLI out of a clone. The pod is always Linux, so this
    # branch only exists so tab does something sensible while developing.
    ifconfig -l 2>/dev/null | tr ' ' '\n' | grep -v '^lo0$'
  fi
}

_octo_pod_etc() { printf '%s' "${OCTO_POD_ETC:-/etc/octo-pod}"; }

# Publisher ids out of the pod's own declaration. sed rather than a yaml parser: a
# completion function runs on every tab and must not shell out to python.
_octo_pod_ids() {  # _octo_pod_ids <30-arms.yml|20-peers.yml|40-egress.yml>
  local f
  for f in "$(_octo_pod_etc)"/host_vars/*/"$1"; do
    [ -r "$f" ] || continue
    sed -n 's/^[[:space:]]*-[[:space:]]*id:[[:space:]]*"\{0,1\}\([A-Za-z0-9_.-]\{1,\}\)"\{0,1\}.*/\1/p' "$f"
  done 2>/dev/null
}

_octo_pod_pods() {
  local d
  for d in "$(_octo_pod_etc)"/host_vars/*/; do
    [ -d "$d" ] && basename "$d"
  done 2>/dev/null
}

_octo_pod() {
  # Normalize global options before the verb so every existing positional completion
  # uses the same indexes. Candidate strings are never translated.
  local lang_codes="en bg cnr cs da de el es et fi fr hr hu is it lb lt lv mk nb nl pl pt ro sk sl sq sv tr uk"
  local global_index=1
  while [ "$global_index" -lt "$COMP_CWORD" ]; do
    case "${COMP_WORDS[global_index]}" in
      --lang)
        if [ "$COMP_CWORD" -eq "$((global_index + 1))" ]; then
          _octo_pod_reply < <(compgen -W "$lang_codes" -- "${COMP_WORDS[COMP_CWORD]}"); return
        fi
        global_index=$((global_index + 2)) ;;
      --lang=*|--bilingual) global_index=$((global_index + 1)) ;;
      *) break ;;
    esac
  done
  if [ "$global_index" -gt 1 ]; then
    local -a normalized=("${COMP_WORDS[0]}" "${COMP_WORDS[@]:global_index}")
    COMP_CWORD=$((COMP_CWORD - global_index + 1))
    COMP_WORDS=("${normalized[@]}")
  fi
  local cur prev verb i
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"

  local verbs="bootstrap join init apply check status top get describe logs certs edit
               verify verify-clean verify-flowing
               upgrade version identity test fetch-images build-images
               add-arm add-peer set-lan set-mesh set-firewall release remove-arm remove-peer egress peer remote"

  # Which verb are we in? First non-flag word after the command.
  for (( i=1; i < COMP_CWORD; i++ )); do
    case "${COMP_WORDS[i]}" in
      -*) ;;
      *) verb="${COMP_WORDS[i]}"; break ;;
    esac
  done

  if [ -z "$verb" ]; then
    _octo_pod_reply < <(compgen -W "$verbs --lang --bilingual" -- "$cur")
    return
  fi

  # VALUE COMPLETION FIRST: what the previous flag wants. This has to come before the
  # flag list, or tabbing after `--iface` offers `--addr` instead of the interfaces.
  case "$prev" in
    --iface)
      _octo_pod_reply < <(compgen -W "$(_octo_pod_ifaces)" -- "$cur"); return ;;
    --framing)
      _octo_pod_reply < <(compgen -W "stream newline len32be len32le" -- "$cur"); return ;;
    --slot|--dir|--partner-bundle)
      _octo_pod_reply < <(compgen -d -- "$cur"); return ;;
    --pod)
      _octo_pod_reply < <(compgen -W "$(_octo_pod_pods)" -- "$cur"); return ;;
    # Deliberately not completed from anything: the management URL must be the address
    # that resolves from OUTSIDE the mesh, and only the operator knows which that is.
    --mgmt|--repo|--portal)
      _octo_pod_reply < <(compgen -W "https://" -- "$cur"); return ;;
    # Deliberately NOT completed: --addr, --from, --udp, --tcp, --dial, --setup-key,
    # --feed, --description. An address or a port completed from anything but the
    # operator's own knowledge is a guess wearing the costume of a fact, and --from in
    # particular is the ONLY access control an arm has.
    --addr|--from|--bind|--udp|--tcp|--dial|--setup-key|--feed|--description|--dest|--key|--max-clients|--replay|--ca-fingerprint|--router-ip|--prefix)
      return ;;
    # A PATH, unlike the key itself, is a fine thing to complete: it is not the secret,
    # and completing it is what makes --setup-key-file the easier habit than pasting the
    # key inline. Completion nudging people toward the safer form is free.
    --setup-key-file)
      _octo_pod_reply < <(compgen -f -- "$cur"); return ;;
    -o|--output)
      _octo_pod_reply < <(compgen -W "table wide json" -- "$cur"); return ;;
    # A number or a time only the operator can choose, like --addr above.
    --tail|--since|--days)
      return ;;
  esac

  # Then the flags this verb takes.
  case "$verb" in
    add-arm)
      _octo_pod_reply < <(compgen -W "--udp --tcp --dial --from --bind --feed --framing --description" -- "$cur") ;;
    add-peer)
      _octo_pod_reply < <(compgen -W "--from --feed --description" -- "$cur") ;;
    set-lan)
      _octo_pod_reply < <(compgen -W "--iface --addr --ssh-from" -- "$cur") ;;
    set-firewall)
      _octo_pod_reply < <(compgen -W "on off" -- "$cur") ;;
    set-mesh)
      _octo_pod_reply < <(compgen -W "--mgmt --repo --portal --mirror" -- "$cur") ;;
    release)
      # An id first, then --slot. Offering both is right: you may be completing either.
      _octo_pod_reply < <(compgen -W "--slot --ca-fingerprint $(_octo_pod_ids 30-arms.yml) $(_octo_pod_ids 20-peers.yml)" -- "$cur") ;;
    remove-arm)
      _octo_pod_reply < <(compgen -W "$(_octo_pod_ids 30-arms.yml)" -- "$cur") ;;
    remove-peer)
      _octo_pod_reply < <(compgen -W "$(_octo_pod_ids 20-peers.yml)" -- "$cur") ;;
    join)
      _octo_pod_reply < <(compgen -W "--setup-key-file --setup-key --mgmt --pod" -- "$cur") ;;
    init)
      _octo_pod_reply < <(compgen -W "--pod" -- "$cur") ;;
    edit)
      if [ "$COMP_CWORD" -eq 2 ]; then
        _octo_pod_reply < <(compgen -W "arms" -- "$cur")
      elif [ "$COMP_CWORD" -eq 3 ]; then
        case "${COMP_WORDS[2]}" in
          arm|arms) _octo_pod_reply < <(compgen -W "$(_octo_pod_ids 30-arms.yml)" -- "$cur") ;;
        esac
      else
        _octo_pod_reply < <(compgen -W "--from --bind --udp --tcp --dial --dry-run --acknowledge-trust-change" -- "$cur")
      fi ;;
    status)
      _octo_pod_reply < <(compgen -W "--json" -- "$cur") ;;
    top)
      _octo_pod_reply < <(compgen -W "--once --json" -- "$cur") ;;
    get)
      local choices="-o --output"
      local has_kind=0 skip_value=0
      for (( i=2; i < COMP_CWORD; i++ )); do
        if [ "$skip_value" = 1 ]; then skip_value=0; continue; fi
        case "${COMP_WORDS[i]}" in
          -o|--output) skip_value=1 ;;
          -*) ;;
          *) has_kind=1 ;;
        esac
      done
      [ "$has_kind" = 1 ] || choices="flows arms ingress peers egress bridges mesh problems certs $choices"
      _octo_pod_reply < <(compgen -W "$choices" -- "$cur") ;;
    # Any declared id. The bridges are named too: they have no declaration to read.
    describe|logs)
      local flags="--follow -f --tail --since" kind="" count=0 skip_value=0 word
      [ "$verb" = describe ] && flags=""
      for (( i=2; i < COMP_CWORD; i++ )); do
        word="${COMP_WORDS[i]}"
        if [ "$skip_value" = 1 ]; then skip_value=0; continue; fi
        case "$word" in
          --tail|--since) skip_value=1 ;;
          -*) ;;
          *) count=$((count + 1)); [ "$count" != 1 ] || kind="$word" ;;
        esac
      done
      local names="" types=""
      if [ "$count" = 0 ]; then
        types="flows arms ingress peers egress bridges"
        names="$(_octo_pod_ids 30-arms.yml) $(_octo_pod_ids 20-peers.yml) $(_octo_pod_ids 40-egress.yml) uplink downlink"
      elif [ "$count" = 1 ]; then
        case "$kind" in
          flows|flow) names="$(_octo_pod_ids 30-arms.yml) $(_octo_pod_ids 20-peers.yml) $(_octo_pod_ids 40-egress.yml) uplink downlink" ;;
          arms|arm) names="$(_octo_pod_ids 30-arms.yml) $(_octo_pod_ids 40-egress.yml)" ;;
          ingress) names="$(_octo_pod_ids 30-arms.yml)" ;;
          peers|peer) names="$(_octo_pod_ids 20-peers.yml)" ;;
          egress|destination|destinations) names="$(_octo_pod_ids 40-egress.yml)" ;;
          bridges|bridge) names="uplink downlink" ;;
        esac
      fi
      _octo_pod_reply < <(compgen -W "$types $names $flags" -- "$cur") ;;
    certs)
      _octo_pod_reply < <(compgen -W "--check --days" -- "$cur") ;;
    verify)
      _octo_pod_reply < <(compgen -W "--tags" -- "$cur") ;;
    bootstrap)
      _octo_pod_reply < <(compgen -W "-e --tags" -- "$cur") ;;
    # `egress` takes a subcommand first, then a destination id for all but add and check.
    egress)
      if [ "$COMP_CWORD" -le 2 ]; then
        _octo_pod_reply < <(compgen -W "add release revoke check remove" -- "$cur")
      elif [ "${COMP_WORDS[2]}" = "add" ]; then
        _octo_pod_reply < <(compgen -W "--prefix --partner-bundle --no-verify-name --dest --key --from --max-clients --replay --description --node-type --status-interval --capabilities" -- "$cur")
      elif [ "${COMP_WORDS[2]}" = "release" ]; then
        _octo_pod_reply < <(compgen -W "$(_octo_pod_ids 40-egress.yml) --slot --router-ip --ca-fingerprint" -- "$cur")
      else
        _octo_pod_reply < <(compgen -W "$(_octo_pod_ids 40-egress.yml) --yes" -- "$cur")
      fi ;;
    # `peer` takes a subcommand first, then a peer id for all but check.
    peer)
      if [ "$COMP_CWORD" -le 2 ]; then
        _octo_pod_reply < <(compgen -W "allow-from allow-read revoke-read bundle check" -- "$cur")
      else
        case "${COMP_WORDS[2]}" in
          allow-from)  _octo_pod_reply < <(compgen -W "$(_octo_pod_ids 20-peers.yml) --from --clear" -- "$cur") ;;
          allow-read)  _octo_pod_reply < <(compgen -W "$(_octo_pod_ids 20-peers.yml) --key --slot --router-ip --ca-fingerprint" -- "$cur") ;;
          revoke-read) _octo_pod_reply < <(compgen -W "$(_octo_pod_ids 20-peers.yml) --key" -- "$cur") ;;
          bundle)      _octo_pod_reply < <(compgen -W "$(_octo_pod_ids 20-peers.yml) --out" -- "$cur") ;;
        esac
      fi ;;
    # `remote` takes a subcommand first, then a user@host this cannot know. Complete the
    # subcommands; leave the address alone, for the same reason --addr is left alone.
    remote)
      if [ "$COMP_CWORD" -le 2 ]; then
        _octo_pod_reply < <(compgen -W "install deprovision mint-key" -- "$cur")
      else
        _octo_pod_reply < <(compgen -W "--with-images --version --pod --yes --wipe-stage --print" -- "$cur")
      fi ;;
    *)
      COMPREPLY=() ;;
  esac
}

complete -F _octo_pod octo-pod otp
