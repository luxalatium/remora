#!/usr/bin/env bash
# Key-contract tests only; this does not qualify ELF files or MPI runtime.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
output=${1:?usage: test_optional_dependency_artifacts.sh NEW_OUTPUT_DIRECTORY}
[[ ! -e $output && ! -L $output ]]
mkdir -p -- "$output"
source "$root/build_support/common.sh"
registry="$root/third_party_packages/packages.json"
count=0

check() {
    local name=$1 expected=$2 package=$3 data=$4 actual=FAIL
    printf '%s\n' "$data" > "$output/$name.json"
    if dependency_artifact_contract "$output/$name.json" "$registry" "$package" \
        > "$output/$name.stdout" 2> "$output/$name.stderr"; then
        actual=PASS
    fi
    [[ $actual == "$expected" ]] || {
        printf 'FAIL %s: expected %s, got %s\n' "$name" "$expected" "$actual" >&2
        return 1
    }
    count=$((count + 1))
    printf 'PASS %s\n' "$name"
}

check legacy PASS mpip '{"artifacts":{"lib/libmpiP.a":"hash","lib/libmpiP.so":"hash"}}'
check explicit_empty PASS mpip '{"optional_artifacts":[],"artifacts":{"lib/libmpiP.a":"hash","lib/libmpiP.so":"hash"}}'
check native_f08 PASS mpip '{"optional_artifacts":["lib/libmpiP-f08.a"],"artifacts":{"lib/libmpiP.a":"hash","lib/libmpiP.so":"hash","lib/libmpiP-f08.a":"hash"}}'
check missing_required FAIL mpip '{"artifacts":{"lib/libmpiP.a":"hash"}}'
check missing_selected FAIL mpip '{"optional_artifacts":["lib/libmpiP-f08.a"],"artifacts":{"lib/libmpiP.a":"hash","lib/libmpiP.so":"hash"}}'
check unknown_file FAIL mpip '{"artifacts":{"lib/libmpiP.a":"hash","lib/libmpiP.so":"hash","lib/unknown.a":"hash"}}'
check undeclared_optional FAIL mpip '{"artifacts":{"lib/libmpiP.a":"hash","lib/libmpiP.so":"hash","lib/libmpiP-f08.a":"hash"}}'
check unknown_selection FAIL mpip '{"optional_artifacts":["lib/unknown.a"],"artifacts":{"lib/libmpiP.a":"hash","lib/libmpiP.so":"hash","lib/unknown.a":"hash"}}'
check duplicate_selection FAIL mpip '{"optional_artifacts":["lib/libmpiP-f08.a","lib/libmpiP-f08.a"],"artifacts":{"lib/libmpiP.a":"hash","lib/libmpiP.so":"hash","lib/libmpiP-f08.a":"hash"}}'
check selection_type FAIL mpip '{"optional_artifacts":"lib/libmpiP-f08.a","artifacts":{"lib/libmpiP.a":"hash","lib/libmpiP.so":"hash"}}'
check artifact_type FAIL mpip '{"artifacts":[]}'
check traversal FAIL mpip '{"optional_artifacts":["../../outside"],"artifacts":{"lib/libmpiP.a":"hash","lib/libmpiP.so":"hash","../../outside":"hash"}}'
check unknown_package FAIL unknown '{"artifacts":{}}'
check mpstat PASS mpstat '{"artifacts":{"bin/mpstat":"hash"}}'
check mpstat_extra FAIL mpstat '{"optional_artifacts":["lib/libmpiP-f08.a"],"artifacts":{"bin/mpstat":"hash","lib/libmpiP-f08.a":"hash"}}'
printf 'PASS %s artifact-contract cases; output: %s\n' "$count" "$output"
