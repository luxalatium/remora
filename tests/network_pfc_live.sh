#!/usr/bin/env bash
# Development-only, read-only allocated-node oracle. Never installed.
# Usage: bash tests/network_pfc_live.sh PREFIX NEW_OUTPUT_DIRECTORY
set -u -o pipefail
prefix=${1:?prefix required}
out=${2:?new output directory required}
[[ ! -e $out ]] || exit 65
mkdir -p -- "$out" || exit 74
command -v jq >/dev/null && command -v ethtool >/dev/null || exit 69
capture() {
    local label=$1; shift
    printf '%q ' "$@" > "$label.command"
    printf '\n' >> "$label.command"
    timeout 15 "$@" > "$label.stdout" 2> "$label.stderr"
    printf '%s\n' "$?" > "$label.rc"
}
stats_json() {
    awk '$1 ~ /:$/ && $2 ~ /^[0-9]+$/ {sub(/:$/,"",$1); print $1 "\t" $2}' "$1" |
        jq -Rn '[inputs | split("\t") | {key:.[0],value:.[1]}] | from_entries' > "$2"
}
uname -a > "$out/uname.txt"
id > "$out/identity.txt"
awk '/^(Uid|Gid|CapInh|CapPrm|CapEff|CapBnd|NoNewPrivs):/' /proc/self/status > "$out/capabilities.txt"
readlink /proc/self/ns/net > "$out/netns.txt"
ethtool --version > "$out/ethtool-version.txt" 2>&1
capture "$out/dcb-version" dcb -V
printf '' > "$out/matrix.jsonl"
failed=0
ixgbe=0
for path in /sys/class/net/*; do
    iface=${path##*/}
    [[ $iface != lo ]] || continue
    type=$(cat "$path/type")
    [[ $type == 1 || $type == 32 ]] || continue
    d=$out/$iface; mkdir -- "$d" || exit 74
    cat "$path/type" > "$d/type.txt"
    cat "$path/operstate" > "$d/state.txt"
    capture "$d/driver" ethtool -i "$iface"
    capture "$d/stats" ethtool -S "$iface"
    capture "$d/pause" ethtool -a "$iface"
    capture "$d/fec" ethtool --show-fec "$iface"
    capture "$d/pfc-config" dcb -j pfc show dev "$iface"
    capture "$d/pfc-counters" dcb -j pfc show dev "$iface" requests indications
    capture "$d/ets-config" dcb -j ets show dev "$iface"
    capture "$d/buffer-config" dcb -j buffer show dev "$iface"
    driver=$(awk '$1=="driver:" {print $2}' "$d/driver.stdout")
    [[ $driver == ixgbe && $type == 1 ]] || continue
    ((ixgbe+=1))
    for mode in collector shell; do
        c=$d/$mode; mkdir -p "$c/raw" || exit 74
        for rep in 0 1 2; do
            capture "$c/before-$rep" ethtool -S "$iface"
            if [[ $mode == collector ]]; then
                capture "$c/collect-$rep" env REMORA_ETH_INTERFACES="$iface" "$prefix/bin/binary_data_collectors/data_collect_linkhealth" node "$c/raw" "$c/raw"
            else
                capture "$c/collect-$rep" env REMORA_ETH_INTERFACES="$iface" REMORA_BIN="$prefix/bin" bash -c 'source "$1"; collect_data_linkhealth node "$2" "$2"' test "$prefix/bin/modules/linkhealth" "$c/raw"
            fi
            capture "$c/after-$rep" ethtool -S "$iface"
            stats_json "$c/before-$rep.stdout" "$c/before-$rep.json"
            stats_json "$c/after-$rep.stdout" "$c/after-$rep.json"
            tail -n 1 "$c/raw/linkhealth_node.jsonl" > "$c/sample-$rep.json"
            # Compare decimal uint64 strings without floating-point conversion.
            jq -n --arg iface "$iface" --arg mode "$mode" --argjson rep "$rep" \
                --argjson rc "$(cat "$c/collect-$rep.rc")" \
                --argjson before_rc "$(cat "$c/before-$rep.rc")" --argjson after_rc "$(cat "$c/after-$rep.rc")" \
                --slurpfile b "$c/before-$rep.json" --slurpfile a "$c/after-$rep.json" --slurpfile s "$c/sample-$rep.json" '
              def le($x;$y): ($x|length)<($y|length) or (($x|length)==($y|length) and $x<=$y);
              ([range(0;8) as $i | ["rx","tx"][] as $dir | ["pxon","pxoff"][] as $kind | "\($dir)_pb_\($i)_\($kind)"] +
               ["rx_flow_control_xon","rx_flow_control_xoff","tx_flow_control_xon","tx_flow_control_xoff"]) as $names |
              {interface:$iface, implementation:$mode, repetition:$rep, case:"ixgbe-pfc-selector-units-source-bracket", collector_rc:$rc, before_rc:$before_rc, after_rc:$after_rc,
               fields:[$names[] as $name | ([$s[0].records[] | select(.entity==$iface and .metric==$name)]) as $r |
                 {metric:$name, unit:"frames", scope:(if ($name|contains("_pb_")) then "physical_port_packet_buffer" else "physical_port" end),
                  status:(if $b[0][$name]==null and $a[0][$name]==null and ($r|length)==0 then "SKIP"
                    elif $before_rc!=0 or $after_rc!=0 or $rc!=0 then "FAIL"
                    elif ($r|length)!=1 then "FAIL"
                    elif $r[0].status!="OK" or $r[0].unit!="frames" or $r[0].value!=$r[0].raw or $r[0].scope!=(if ($name|contains("_pb_")) then "physical_port_packet_buffer" else "physical_port" end) then "FAIL"
                    elif $b[0][$name]==null or $a[0][$name]==null or (le($b[0][$name];$a[0][$name])|not) then "SKIP"
                    elif le($b[0][$name];$r[0].value) and le($r[0].value;$a[0][$name]) then "PASS" else "FAIL" end),
                  reason:(if $b[0][$name]==null and $a[0][$name]==null then "COUNTER_NOT_EXPOSED_BY_DRIVER" elif $b[0][$name]==null or $a[0][$name]==null or (le($b[0][$name];$a[0][$name])|not) then "SOURCE_MISSING_OR_RESET" else "READ_ONLY_SOURCE_BRACKET" end),
                  before:$b[0][$name],after:$a[0][$name],raw:$r[0].raw}],
               selector_status:(if ([$s[0].entities[].name]==[$iface]) then "PASS" else "FAIL" end)}' > "$c/result-$rep.json"
            rc=$?
            if ((rc)) || ! jq -e '.selector_status=="PASS" and .collector_rc==0 and ([.fields[].status]|all(.!="FAIL"))' "$c/result-$rep.json" >/dev/null; then failed=1;fi
            cat "$c/result-$rep.json" | jq -c . >> "$out/matrix.jsonl"
        done
    done
done
if ((ixgbe==0)); then
    printf '%s\n' '{"case":"ixgbe-pfc-selector-units-source-bracket","status":"SKIP","reason":"NO_IXGBE_ETHERNET_INTERFACE"}' >> "$out/matrix.jsonl"
fi
printf '%s\n' "$failed" > "$out/test.rc"
exit "$failed"
