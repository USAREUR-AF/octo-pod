#!/usr/bin/env bash
# Mac-compatible entry point; Python handles quoting, manifests and safe extraction.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/push-images.py" "$@"
