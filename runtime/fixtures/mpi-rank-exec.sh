#!/usr/bin/env bash
# One MPI rank: bind a verified physical CPU, record identity, preload only app.
set -euo pipefail
mode=$1 library=$2 options=$3 executable=$4
shift 4
local_rank=${OMPI_COMM_WORLD_LOCAL_RANK:-${MPI_LOCALRANKID:-${PMI_RANK:-${SLURM_LOCALID:-}}}}
[[ $local_rank =~ ^[0-9]+$ ]] || {
    echo 'BLOCKED: local rank unavailable' >&2
    exit 95
}
IFS=, read -r -a cpus <<< "${REMORA_TEST_CPUS:?verified CPU list required}"
[[ -n ${cpus[$local_rank]:-} ]] || {
    echo 'BLOCKED: rank exceeds CPU list' >&2
    exit 95
}
start=$(awk '{print $22}' /proc/$$/stat)
printf 'WORKER %s %s %s %s\n' "$local_rank" "$$" "$start" "$(hostname -s)"
case $mode in
    preload) exec taskset -c "${cpus[$local_rank]}" env LD_PRELOAD="$library" MPIP="$options" "$executable" "$@" ;;
    static) exec taskset -c "${cpus[$local_rank]}" env -u LD_PRELOAD MPIP="$options" "$executable" "$@" ;;
    baseline) exec taskset -c "${cpus[$local_rank]}" env -u LD_PRELOAD -u MPIP "$executable" "$@" ;;
    *)
        echo 'invalid instrumentation mode' >&2
        exit 96
        ;;
esac
