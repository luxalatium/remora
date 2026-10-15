#!/usr/bin/env bash
# Runtime check helpers. JSON is data; sources are only reviewed module scripts.
set -o pipefail
export LC_ALL=C

hash_file() {
    if command -v sha256sum > /dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

result() {
    local status=$1 reason=$2 availability=${3:-$1} correctness=${4:-NOT_RUN}
    local correctness_claim=${5:-OBSERVED}
    jq -n --arg module_name "$MODULE" --arg implementation "$IMPLEMENTATION" \
        --arg status "$status" --arg reason "$reason" --arg availability "$availability" \
        --arg correctness "$correctness" --arg node "$NODE" --arg artifact "${ARTIFACT:-}" \
        --arg hash "${ARTIFACT_HASH:-}" --arg scope availability_smoke --arg correctness_claim "$correctness_claim" \
        '{schema:"remora-runtime-check/v1","module":$module_name,implementation:$implementation,
          status:$status,reason:$reason,availability:$availability,correctness:$correctness,
          node:$node,artifact:$artifact,artifact_sha256:$hash,qualification_scope:$scope,
          availability_claim_class:"OBSERVED",correctness_claim_class:$correctness_claim}' \
        > "$CASE/result.json" || {
        printf "Cannot serialize runtime result; partial evidence retained\n" >&2
        exit 2
    }
}

blocked() {
    result BLOCKED "$*"
    exit 0
}

unsupported() {
    result SKIP "$*"
    exit 0
}

require_tool() {
    command -v "$1" > /dev/null 2>&1 || blocked "required runtime tool missing: $1"
}

require_file() {
    [[ -e $1 ]] || unsupported "backend not exposed: $1"
    [[ -r $1 ]] || blocked "backend is not readable: $1"
}

numa_node_count() {
    awk '/Node [0-9]/ {
        count=0
        for (i=1;i<NF;i++) if ($i=="Node" && $(i+1) ~ /^[0-9]+$/) count++
        print count
        exit
    }'
}

# Raw observations are saved without changing counters, services or permissions.
snapshot() {
    local label=$1 path
    mkdir -p -- "$CASE/oracle/$label"
    for path in /proc/stat /proc/meminfo /proc/uptime /proc/mounts; do
        [[ ! -r $path ]] || cp -- "$path" "$CASE/oracle/$label/${path##*/}"
    done
    if command -v numastat > /dev/null 2>&1; then
        numastat -m > "$CASE/oracle/$label/numastat-m.txt" 2>&1
        numastat -c > "$CASE/oracle/$label/numastat-c.txt" 2>&1
    fi
    if [[ $MODULE == gpu ]]; then
        nvidia-smi --query-gpu=index,memory.total,memory.free,memory.used \
            --format=csv,noheader,nounits > "$CASE/oracle/$label/gpu.csv" 2>&1
    fi
    for path in /sys/class/net/*/statistics/{tx_bytes,rx_bytes} \
        /sys/class/infiniband/*/ports/*/{state,counters/port_xmit_data,counters/port_rcv_data} \
        /sys/devices/virtual/powercap/intel-rapl/intel-rapl:*/energy_uj \
        /sys/devices/platform/coretemp.*/hwmon/hwmon*/temp*_input; do
        [[ -f $path && -r $path ]] || continue
        printf '%s\t%s\n' "$path" "$(cat "$path")"
    done > "$CASE/oracle/$label/counters.tsv"
}

validate_raw() {
    local file=$1 width=$2 minimum=$3 cpu=$4
    awk -v width="$width" -v minimum="$minimum" -v cpu="$cpu" '
      function number(x) { return x ~ /^[-+]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][-+]?[0-9]+)?$/ }
      NF == 0 { next }
      cpu && $1 == "%time" {
          if (blocks && groups != 3) bad=1
          if (!number($2) || (blocks && $2 <= previous)) bad=1
          previous=$2; blocks++; groups=0; next
      }
      cpu {
          expected=(groups==0?"%usr":groups==1?"%sys":"%idle")
          if ($1 != expected || NF < 2 || !blocks) bad=1
          if (fields && NF != fields) bad=1
          fields=NF
          for (i=2;i<=NF;i++) if (!number($i) || $i < 0 || $i > 100.001) bad=1
          groups++; next
      }
      !number($1) { if (rows) bad=1; next }
      {
          if (width && NF != width) bad=1
          if (fields && NF != fields) bad=1
          fields=NF
          if (rows && $1 <= previous) bad=1
          previous=$1
          for (i=1;i<=NF;i++) if (!number($i) || $i < 0) bad=1
          rows++
      }
      END {
          if (cpu) { if (blocks < minimum || groups != 3) bad=1 }
          else if (rows < minimum || fields < 2) bad=1
          exit bad ? 1 : 0
      }' "$file"
}
