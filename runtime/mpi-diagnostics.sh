#!/usr/bin/env bash
# Read only this case's recorded rank identities. Never enumerate or signal jobs.
set -euo pipefail
output=$1
mkdir -p "$output/diagnostics"
node=$(hostname -s)
owner=$(id -u)
seen=' '
count=0
while read -r marker rank pid start worker_node extra; do
    [[ $marker == WORKER && -z ${extra:-} ]] || continue
    [[ $rank =~ ^[0-9]+$ && $pid =~ ^[0-9]+$ && $start =~ ^[0-9]+$ ]] || continue
    [[ $worker_node == "$node" && $pid -gt 1 ]] || continue
    [[ $seen != *" $pid:$start "* ]] || continue
    seen+="$pid:$start "
    count=$((count + 1))
    ((count <= 64)) || break
    process="/proc/$pid"
    [[ -r $process/stat ]] || continue
    [[ $(stat -c %u "$process" 2>/dev/null) == "$owner" ]] || continue
    record=$(cat "$process/stat" 2>/dev/null) || continue
    tail=${record##*) }
    read -r -a fields <<< "$tail"
    [[ ${fields[19]:-} == "$start" ]] || continue
    prefix="$output/diagnostics/rank-$rank-pid-$pid"
    printf '%s\n' "$record" > "$prefix.stat"
    printf 'rank=%s pid=%s start=%s node=%s state=%s\n' \
        "$rank" "$pid" "$start" "$node" "${fields[0]:-UNKNOWN}" > "$prefix.identity"
    for file in status wchan; do
        cat "$process/$file" > "$prefix.$file" 2> "$prefix.$file.stderr" || true
    done
done < "$output/stdout"
