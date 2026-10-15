#!/usr/bin/env bash
# Bounded dvs runtime smoke; see docs/runtime-checks.md.
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
exec bash "$root/check.sh" --module dvs "$@"
