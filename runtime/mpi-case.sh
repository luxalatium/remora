#!/usr/bin/env bash
# Execute exactly one bounded case; caller supplies verified allocation/CPU list.
set -euo pipefail
source_dir=$1 mode=$2 library=$3 executable=$4 case_name=$5 ranks=$6 output=$7
mkdir -p "$output/profile"
options="-k 1 -t 0 -e -v -f $output/profile"
printf '%q ' timeout --signal=TERM --kill-after=3 25s ibrun -n "$ranks" -o 0 \
    bash "$source_dir/runtime/fixtures/mpi-rank-exec.sh" "$mode" "$library" "$options" \
    "$executable" "$case_name" "$output/io.bin" > "$output/command.txt"
printf '\n' >> "$output/command.txt"
set +e
timeout --signal=TERM --kill-after=3 25s ibrun -n "$ranks" -o 0 \
    bash "$source_dir/runtime/fixtures/mpi-rank-exec.sh" "$mode" "$library" "$options" \
    "$executable" "$case_name" "$output/io.bin" > "$output/stdout" 2> "$output/stderr"
status=$?
set -e
printf '%s\n' "$status" > "$output/exit-code"
