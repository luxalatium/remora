#!/usr/bin/env bash
# Isolated per-implementation smoke. Never runs the legacy REMORA wrapper.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
MODULE=$1 IMPLEMENTATION=$2 PREFIX=$3 CASE=$4 SAMPLES=$5 INTERVAL=$6 MPI_PROBE=$7
NODE=$(hostname -s)
ARTIFACT='' ARTIFACT_HASH=''
mkdir -p -- "$CASE/raw" "$CASE/out" "$CASE/oracle"
RAW="$CASE/raw"
printf '%s\n' "$NODE" > "$CASE/out/remora_nodes.txt"
: > "$CASE/out/remora_env.txt"
export PATH="$PREFIX/bin:$PATH"
export REMORA_BIN="$PREFIX/bin" REMORA_MODE=FULL REMORA_SYMMETRIC=0
export REMORA_VERBOSE=0 REMORA_PLOT_RESULTS=0 REMORA_CUDA=1 REMORA_LUSTRE=1 REMORA_DVS=1

case $MODULE in
    mpstat)
        [[ $IMPLEMENTATION == collector ]] || unsupported 'mpstat is a standalone executable, not a shell module'
        ARTIFACT="$PREFIX/bin/mpstat"
        ;;
    mpip)
        [[ $IMPLEMENTATION == shell ]] || unsupported 'mpiP is a preload library, not a data_collect binary'
        ARTIFACT="$PREFIX/bin/modules/impi_mpip"
        ;;
    network | lustre | lnet | dvs)
        [[ $IMPLEMENTATION == shell ]] || unsupported 'this module has no C collector'
        ARTIFACT="$PREFIX/bin/modules/$MODULE"
        ;;
    *)
        if [[ $IMPLEMENTATION == shell ]]; then
            ARTIFACT="$PREFIX/bin/modules/$MODULE"
        else
            ARTIFACT="$PREFIX/bin/binary_data_collectors/data_collect_$MODULE"
        fi
        ;;
esac
[[ -f $ARTIFACT && -r $ARTIFACT ]] || blocked "installed artifact missing: $ARTIFACT"
ARTIFACT_HASH=$(hash_file "$ARTIFACT") || blocked 'cannot hash artifact'
if [[ $IMPLEMENTATION == collector ]]; then
    [[ -x $ARTIFACT ]] || blocked "artifact is not executable: $ARTIFACT"
    if grep -aEq '/dev/shm/remora_[a-z]+[.]tmp' "$ARTIFACT"; then
        blocked 'legacy collector writes global /dev/shm state; rebuild with run-local state support'
    fi
fi

case $MODULE in
    cpu)
        if [[ $IMPLEMENTATION == shell ]]; then
            require_tool bc
            require_tool mpstat
        fi
        require_file /proc/stat
        ;;
    memory)
        if [[ $IMPLEMENTATION == shell ]]; then
            require_tool bc
            require_tool df
        fi
        require_file /proc/meminfo
        ;;
    numa)
        require_tool numastat
        if [[ $IMPLEMENTATION == collector ]]; then
            count=$(numastat -m | numa_node_count)
            [[ $count == 2 ]] || blocked "legacy C NUMA layout is fixed to two nodes; observed ${count:-unknown}; do not risk its fixed-size arrays"
        fi
        ;;
    eth)
        [[ $IMPLEMENTATION != shell ]] || require_tool netstat
        [[ -d /sys/class/net ]] || unsupported 'network interfaces unavailable'
        ;;
    ib | opa)
        compgen -G '/sys/class/infiniband/*/ports/*/counters/port_xmit_data' > /dev/null || unsupported 'fabric counters unavailable'
        ;;
    gpu)
        [[ -e /dev/nvidiactl ]] || unsupported 'NVIDIA device interface not exposed in this execution context'
        require_tool nvidia-smi
        nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits > "$CASE/gpu-count.txt" 2> "$CASE/gpu-query.stderr" || blocked 'NVIDIA telemetry inaccessible; inspect gpu-query.stderr'
        GPU_COUNT=$(wc -l < "$CASE/gpu-count.txt")
        ((GPU_COUNT > 0)) || unsupported 'no visible NVIDIA devices'
        [[ $IMPLEMENTATION != shell ]] || require_tool bc
        ;;
    power)
        if [[ ! -r /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/energy_uj ]]; then
            if [[ $IMPLEMENTATION == shell ]] && command -v sensors > /dev/null 2>&1 && sensors | grep -q power1; then
                :
            else unsupported 'legacy Intel RAPL/power1 backend not readable on this node'; fi
        fi
        ;;
    temperature)
        compgen -G '/sys/devices/platform/coretemp.*/hwmon/hwmon*/temp*_input' > /dev/null || unsupported 'legacy coretemp backend not exposed on this node'
        ;;
    lnet) require_file /proc/sys/lnet/stats ;;
    lustre)
        grep -q ' lustre ' /proc/mounts || unsupported 'no mounted Lustre filesystem'
        [[ -d /proc/fs/lustre/llite ]] || blocked 'legacy Lustre statistics path unavailable'
        require_tool bc
        ;;
    dvs) [[ -d /proc/fs/dvs/mounts ]] || unsupported 'DVS backend unavailable' ;;
    network) unsupported 'postprocessing-only module: no live sampler; network trace qualification is separate' ;;
    mpip)
        [[ -s $PREFIX/lib/libmpiP.so ]] || blocked 'installed mpiP shared library missing'
        [[ -n $MPI_PROBE && -x $MPI_PROBE ]] || blocked 'supply --mpi-probe with a precompiled compatible MPI smoke executable'
        require_tool ibrun
        ;;
esac

snapshot before
if [[ $MODULE == mpstat ]]; then
    "$ARTIFACT" -P ALL 1 "$SAMPLES" > "$RAW/mpstat.txt" 2> "$CASE/command.stderr"
    rc=$?
    printf '%s\n' "$rc" > "$CASE/command.rc"
    if ((rc == 0)) && awk '/Average:/ && $2=="all" {n++; for(i=3;i<=NF;i++) if ($i !~ /^[0-9.]+$/ || $i<0 || $i>100.001) bad=1} END{exit !(n && !bad)}' "$RAW/mpstat.txt"; then
        snapshot after
        result PASS 'mpstat sampled aggregate/per-CPU utilization' PASS OPEN
    else result FAIL 'mpstat execution/output validation failed'; fi
    exit 0
fi
if [[ $MODULE == mpip ]]; then
    (
        cd -- "$RAW" || exit 2
        ibrun -n 2 env LD_PRELOAD="$PREFIX/lib/libmpiP.so" "$MPI_PROBE"
    ) > "$CASE/mpi.stdout" 2> "$CASE/mpi.stderr"
    rc=$?
    printf '%s\n' "$rc" > "$CASE/command.rc"
    if ((rc == 0)) && grep -qE 'MPI_?Allreduce|Allreduce' "$RAW"/*.mpiP 2> /dev/null; then
        result PASS 'two-rank MPI workload produced mpiP statistics' PASS OPEN
    else result FAIL 'MPI launch/preload/report failed; inspect mpi logs'; fi
    exit 0
fi

if [[ $IMPLEMENTATION == shell ]]; then
    source "$ARTIFACT"
    "init_module_$MODULE" "$NODE" "$CASE/out" "$RAW" > "$CASE/init.stdout" 2> "$CASE/init.stderr"
    init_rc=$?
    printf '%s\n' "$init_rc" > "$CASE/init.rc"
    if ((init_rc != 0)); then
        result FAIL "module init failed (exit $init_rc); backend was prechecked"
        exit 0
    fi
else
    # Prime stateful collectors; their first call intentionally has no interval sample.
    "$ARTIFACT" "$NODE" "$CASE/out" "$RAW" > "$CASE/warmup.stdout" 2> "$CASE/warmup.stderr"
    rc=$?
    printf '%s\n' "$rc" > "$CASE/warmup.rc"
    if ((rc != 0)); then
        result FAIL "collector warmup failed (exit $rc)"
        exit 0
    fi
    sleep "$INTERVAL"
fi
for ((sample = 1; sample <= SAMPLES; sample++)); do
    if [[ $IMPLEMENTATION == shell ]]; then
        "collect_data_$MODULE" "$NODE" "$CASE/out" "$RAW" > "$CASE/sample-$sample.stdout" 2> "$CASE/sample-$sample.stderr"
    else
        "$ARTIFACT" "$NODE" "$CASE/out" "$RAW" > "$CASE/sample-$sample.stdout" 2> "$CASE/sample-$sample.stderr"
    fi
    rc=$?
    printf '%s\n' "$rc" > "$CASE/sample-$sample.rc"
    if ((rc != 0)); then
        result FAIL "sample $sample failed (exit $rc)"
        exit 0
    fi
    # The shell CPU sampler backgrounds/disowns its one-second mpstat pipeline.
    if [[ $MODULE == cpu && $IMPLEMENTATION == shell ]]; then sleep 1.2; fi
    if ((sample < SAMPLES)); then sleep "$INTERVAL"; fi
done
snapshot after

width=0 cpu=0
case $MODULE in
    cpu)
        files=("$RAW/cpu_$NODE.txt")
        cpu=1
        ;;
    memory)
        files=("$RAW/memory_stats_$NODE.txt")
        width=8
        ;;
    numa)
        files=("$RAW/numa_stats_$NODE.txt")
        if [[ $IMPLEMENTATION == collector ]]; then width=26; fi
        ;;
    eth)
        files=("$RAW/network_eth_traffic-$NODE.txt")
        width=9
        ;;
    ib | opa)
        files=("$RAW/${MODULE}_packets-$NODE.txt")
        width=5
        ;;
    gpu)
        if [[ $IMPLEMENTATION == shell ]]; then
            files=("$RAW/gpu_memory_stats_$NODE.txt")
        else files=("$RAW/gpu_$NODE.txt"); fi
        width=$((1 + 2 * GPU_COUNT))
        ;;
    power) files=("$RAW/power_$NODE.txt") ;;
    temperature) files=("$RAW"/temperature_cpu*"_$NODE.txt") ;;
    lustre) files=("$RAW/lustre_stats_$NODE.txt") ;;
    lnet)
        files=("$RAW/lnet_counters_$NODE.txt")
        width=12
        ;;
    dvs) files=("$RAW/dvs_stats_$NODE.txt") ;;
esac
for file in "${files[@]}"; do
    if [[ ! -s $file ]] || ! validate_raw "$file" "$width" "$SAMPLES" "$cpu"; then
        result FAIL "empty/malformed/incomplete/nonmonotonic samples: $file" FAIL FAIL
        exit 0
    fi
done
# A bounded smoke is not a numerical-accuracy claim. Preserve known unit defects.
if [[ $MODULE == gpu && $IMPLEMENTATION == shell ]]; then
    result FAIL 'available, but shell module divides nvidia-smi MiB by 1000 and labels GB; decimal GB needs MiB*1048576/1e9' PASS FAIL DERIVED
elif [[ $MODULE == ib || $MODULE == opa ]]; then
    result FAIL 'available, but port_*_data counters are four-octet units and legacy output treats them as bytes' PASS FAIL DERIVED
else
    result PASS 'backend accessible; bounded samples and schema valid; semantic/oracle qualification still OPEN' PASS OPEN
fi
