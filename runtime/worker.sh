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
        NUMA_NATIVE="$PREFIX/bin/binary_data_collectors/data_collect_numa"
        [[ -x $NUMA_NATIVE ]] || blocked 'NUMA module requires its installed native collector'
        if "$NUMA_NATIVE" --describe > "$CASE/numa-description.json" 2> "$CASE/numa-description.stderr" &&
            grep -q '"schema":"remora-numa/v1"' "$CASE/numa-description.json"; then
            NUMA_KEYED=1
            "$NUMA_NATIVE" --check > "$CASE/numa-check.stdout" 2> "$CASE/numa-check.stderr"
            rc=$?
            ((rc != 69)) || unsupported 'NUMA sysfs nodes unavailable'
            ((rc == 0)) || blocked "NUMA required kernel fields unavailable/invalid (exit $rc)"
        else
            NUMA_KEYED=0
            require_tool numastat
            count=$(numastat -m | numa_node_count)
            [[ $count == 2 ]] || blocked "legacy C NUMA layout is fixed to two nodes; observed ${count:-unknown}; do not risk its fixed-size arrays"
        fi
        ;;
    eth)
        [[ -d /sys/class/net ]] || unsupported 'network interfaces unavailable'
        ;;
    ib | opa)
        [[ -d /sys/class/infiniband ]] || unsupported 'fabric devices unavailable'
        ;;
    gpu)
        [[ -e /dev/nvidiactl ]] || unsupported 'NVIDIA device interface not exposed in this execution context'
        require_tool nvidia-smi
        nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits > "$CASE/gpu-count.txt" 2> "$CASE/gpu-query.stderr" || blocked 'NVIDIA telemetry inaccessible; inspect gpu-query.stderr'
        GPU_COUNT=$(wc -l < "$CASE/gpu-count.txt")
        ((GPU_COUNT > 0)) || unsupported 'no visible NVIDIA devices'
        ;;
    power | temperature | hwmon | powercap | nvml)
        # Generic native discovery supports named powercap and hwmon backends.
        "$PREFIX/bin/binary_data_collectors/data_collect_$MODULE" --check > "$CASE/backend.stdout" 2> "$CASE/backend.stderr"
        backend_rc=$?
        [[ $backend_rc != 69 ]] || unsupported 'no supported sensor backend exposed'
        [[ $backend_rc == 0 ]] || blocked "sensor backend check failed (exit $backend_rc)"
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
        if [[ $MODULE == eth || $MODULE == ib || $MODULE == opa || $MODULE == netinfo || $MODULE == neterrors || $MODULE == ibcounters || $MODULE == mlxcounters || $MODULE == opacounters || $MODULE == netprotocol || $MODULE == softnet || $MODULE == nicstats || $MODULE == linkhealth ]] && ((init_rc == 69)); then
            unsupported 'no eligible network interface/port or installed reader'
        fi
        result FAIL "module init failed (exit $init_rc); backend was prechecked"
        exit 0
    fi
else
    # Prime stateful collectors; their first call intentionally has no interval sample.
    if [[ $MODULE == power || $MODULE == temperature || $MODULE == hwmon || $MODULE == powercap || $MODULE == nvml ]]; then
        "$ARTIFACT" --initialize "$NODE" "$CASE/out" "$RAW" > "$CASE/warmup.stdout" 2> "$CASE/warmup.stderr"
    else
        "$ARTIFACT" "$NODE" "$CASE/out" "$RAW" > "$CASE/warmup.stdout" 2> "$CASE/warmup.stderr"
    fi
    rc=$?
    printf '%s\n' "$rc" > "$CASE/warmup.rc"
    if ((rc != 0)); then
        if [[ $MODULE == eth || $MODULE == ib || $MODULE == opa || $MODULE == netinfo || $MODULE == neterrors || $MODULE == ibcounters || $MODULE == mlxcounters || $MODULE == opacounters || $MODULE == netprotocol || $MODULE == softnet || $MODULE == nicstats || $MODULE == linkhealth ]] && ((rc == 69)); then
            unsupported 'no eligible network interface/port'
        fi
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
        case $MODULE in
            netinfo | neterrors | ibcounters | mlxcounters | opacounters | netprotocol | softnet | nicstats | linkhealth)
                if ((rc == 69)); then unsupported 'no readable primary network resource fields; raw availability records retained'; fi
                ;;
        esac
        result FAIL "sample $sample failed (exit $rc)"
        exit 0
    fi
    # The shell CPU sampler backgrounds/disowns its one-second mpstat pipeline.
    if [[ $MODULE == cpu && $IMPLEMENTATION == shell ]]; then sleep 1.2; fi
    if ((sample < SAMPLES)); then sleep "$INTERVAL"; fi
done
snapshot after

case $MODULE in
    netinfo | neterrors | ibcounters | mlxcounters | opacounters | netprotocol | softnet | nicstats | linkhealth)
        file="$RAW/${MODULE}_${NODE}.jsonl"
        if [[ -s $file ]] && jq -e -s --arg module_name "$MODULE" --argjson samples "$SAMPLES" '
            length >= $samples and all(.[];
                .schema == "remora-network-resources/v1" and .module == $module_name and
                .status == "OK" and .readable_primary > 0 and
                (.records | length) == .record_count and
                all(.records[]; has("raw") and has("source") and has("unit") and
                    (if .status == "OK" then (.value | type) == "string"
                     else .value == null and .errno != 0 end)))' "$file" > /dev/null; then
            result PASS 'native snapshots preserve field scope, units, raw values and availability; independent source oracle qualification is separate' PASS OPEN
        else result FAIL 'network resource snapshot schema/coverage invalid' FAIL FAIL; fi
        exit 0
        ;;
esac

if [[ $MODULE == power || $MODULE == temperature || $MODULE == hwmon || $MODULE == powercap || $MODULE == nvml ]]; then
    case $MODULE in power | temperature) file="$RAW/${MODULE}_metadata_$NODE.jsonl" ;; *) file="$RAW/${MODULE}_$NODE.jsonl" ;; esac
    if [[ ! -s $file ]] || ! jq -s -e '''
       length>0 and all(.[]; .schema=="remora-sensor-sample/v1" and
         (.read_end_monotonic_s>=.read_start_monotonic_s) and
         (if .status=="PASS" then (.value|type)=="number" else .value==null end)) and
       any(.[]; .status=="PASS")
    ''' "$file" > "$CASE/schema-validation.json" 2> "$CASE/schema-validation.stderr"; then
        result FAIL 'native sensor contract empty or invalid'; exit 0
    fi
    if [[ $MODULE == power || $MODULE == temperature ]]; then
        if [[ $MODULE == power ]]; then wide=("$RAW/power_$NODE.txt"); else wide=("$RAW"/temperature_cpu*"_$NODE.txt"); fi
        for table in "${wide[@]}"; do
            [[ -s $table ]] && awk -v minimum="$SAMPLES" '''
              function number(x) { return x ~ /^[-+]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][-+]?[0-9]+)?$/ }
              NR==1 {width=NF;next}
              {if(NF!=width || !number($1) || (rows && $1<=previous))bad=1;
               for(i=2;i<=NF;i++)if($i!="NA" && !number($i))bad=1;previous=$1;rows++}
              END {exit bad || rows<minimum || width<2}
            ''' "$table" || { result FAIL 'legacy sensor table malformed/incomplete'; exit 0; }
        done
    fi
    result PASS 'native sensor samples valid; per-field unsupported/permission status retained' PASS OPEN
    exit 0
fi

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
        if [[ ${NUMA_KEYED:-0} == 1 ]]; then
            width=$(awk 'NR==1 {print NF;exit}' "${files[0]}")
        elif [[ $IMPLEMENTATION == collector ]]; then width=26; fi
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
    if head -n 1 "${files[0]}" | grep -q '_avail_GiB'; then
        result PASS 'GPU MiB converted to explicitly labelled GiB; source oracle qualification is separate' PASS OPEN
    else
        result FAIL 'legacy GPU MiB/1000 values have incorrect GB labels' PASS FAIL DERIVED
    fi
elif [[ $MODULE == ib || $MODULE == opa ]]; then
    if [[ -s $RAW/network-$MODULE-$NODE.raw.tsv ]] && grep -q '^# remora-network/v1' "$RAW/network-$MODULE-$NODE.raw.tsv"; then
        result PASS 'versioned source/unit evidence and complete intervals; independent oracle qualification still OPEN' PASS OPEN
    else result FAIL 'network unit/source contract missing' PASS FAIL; fi
else
    result PASS 'backend accessible; bounded samples and schema valid; semantic/oracle qualification still OPEN' PASS OPEN
fi
