#!/usr/bin/env bash
# Configure/build/install frontend. Legacy is explicit opt-in.
set -euo pipefail
installer_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ${1:-} == --legacy ]]; then
    shift
    printf "%s\n" "WARNING: legacy installer can mask failures; use configure/build/install for verified installation." >&2
    exec bash "$installer_source/installer/legacy-install.sh" "$@"
fi
exec python3 -B "$installer_source/install.py" "$@"
