#!/usr/bin/env bash
# Development-only direct DCB GET / independent CLI configuration oracle.
# Usage: bash tests/network_pfc_probe_live.sh NEW_OUTPUT_DIRECTORY
set -u -o pipefail
out=${1:?new output directory required}
[[ $(uname -s) == Linux ]] || exit 69
mkdir -- "$out" || exit 65
src=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/network_pfc_probe.c
cp "$src" "$out/source.c" || exit 74
cp /usr/include/linux/dcbnl.h "$out/site-uapi-dcbnl.h" || exit 74
sha256sum "$out/source.c" "$out/site-uapi-dcbnl.h" > "$out/source-sha256.txt"
id > "$out/identity.txt"
awk '/^(Uid|Gid|CapInh|CapPrm|CapEff|CapBnd|NoNewPrivs):/' /proc/self/status > "$out/capabilities.txt"
readlink /proc/self/ns/net > "$out/netns.txt"
gcc --version > "$out/compiler-version.txt"
gcc -std=c99 -O2 -Wall -Wextra -Werror "$out/source.c" -o "$out/probe" > "$out/build.stdout" 2> "$out/build.stderr"
rc=$?;printf '%s\n' "$rc" > "$out/build.rc"
((rc==0)) || exit 1
sha256sum "$out/probe" > "$out/binary-sha256.txt"
capture() {
    local label=$1;shift
    printf '%q ' "$@" > "$label.command";printf '\n' >> "$label.command"
    timeout 8 "$@" > "$label.stdout" 2> "$label.stderr"
    printf '%s\n' "$?" > "$label.rc"
}
failed=0
printf '' > "$out/matrix.jsonl"
for path in /sys/class/net/*; do
    iface=${path##*/}
    [[ $(cat "$path/type") == 1 ]] || continue
    capture "$out/$iface-driver" ethtool -i "$iface"
    [[ $(awk '$1=="driver:" {print $2}' "$out/$iface-driver.stdout") == ixgbe ]] || continue
    for rep in 0 1 2; do
        d=$out/$iface-$rep;mkdir "$d" || exit 65
        date -u +%FT%TZ > "$d/start.txt"
        cat "$path/ifindex" > "$d/ifindex-before.txt"
        capture "$d/pfc-before" dcb -j pfc show dev "$iface"
        capture "$d/ets-before" dcb -j ets show dev "$iface"
        capture "$d/probe" "$out/probe" "$iface"
        capture "$d/pfc-after" dcb -j pfc show dev "$iface"
        capture "$d/ets-after" dcb -j ets show dev "$iface"
        cat "$path/ifindex" > "$d/ifindex-after.txt"
        date -u +%FT%TZ > "$d/end.txt"
        status=SKIP;reason=GET_UNAVAILABLE
        if [[ $(cat "$d/probe.rc") == 0 ]]; then
            if jq -e --arg iface "$iface" '.interface==$iface and .status=="OK" and .errno==0 and (.pfc.priority_enabled|length)==8 and (.pfc.requests|length)==8 and (.pfc.indications|length)==8' "$d/probe.stdout" >/dev/null && cmp -s "$d/ifindex-before.txt" "$d/ifindex-after.txt"; then
                status=PASS;reason=NATIVE_GET_READABLE_ORACLE_UNAVAILABLE
                if [[ $(cat "$d/pfc-before.rc") == 0 && $(cat "$d/pfc-after.rc") == 0 && $(cat "$d/ets-before.rc") == 0 && $(cat "$d/ets-after.rc") == 0 ]]; then
                    if jq -en --slurpfile p "$d/probe.stdout" --slurpfile b "$d/pfc-before.stdout" --slurpfile a "$d/pfc-after.stdout" --slurpfile eb "$d/ets-before.stdout" --slurpfile ea "$d/ets-after.stdout" '$b[0].prio_pfc==$a[0].prio_pfc and $eb[0].prio_tc==$ea[0].prio_tc and $p[0].pfc.priority_enabled==$b[0].prio_pfc and $p[0].pfc.cap==$b[0].pfc_cap and $p[0].priority_to_tc==$eb[0].prio_tc' >/dev/null; then reason=CLI_NATIVE_ENABLE_MAPPING_AGREE;else status=FAIL;reason=CONFIGURATION_CHANGED_OR_ORACLE_MISMATCH;fi
                fi
            else status=FAIL;reason=SCHEMA_OR_INTERFACE_IDENTITY;fi
        elif [[ $(cat "$d/probe.rc") != 69 ]]; then status=FAIL;reason=UNEXPECTED_EXIT;fi
        [[ $status != FAIL ]] || failed=1
        jq -cn --arg iface "$iface" --argjson rep "$rep" --arg status "$status" --arg reason "$reason" '{case:"native-DCB-PFC-enable-and-ETS-mapping",interface:$iface,repetition:$rep,status:$status,reason:$reason}' >> "$out/matrix.jsonl"
    done
done
if [[ ! -s $out/matrix.jsonl ]]; then printf '%s\n' '{"case":"native-DCB-PFC-enable-and-ETS-mapping","status":"SKIP","reason":"NO_IXGBE_ETHERNET_INTERFACE"}' >> "$out/matrix.jsonl";fi
printf '%s\n' "$failed" > "$out/test.rc"
exit "$failed"
