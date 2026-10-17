#!/usr/bin/env bash
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$root/check.sh" --module softnet "$@"
