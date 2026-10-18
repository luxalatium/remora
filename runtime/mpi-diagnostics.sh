#!/usr/bin/env bash
# Read only this case's recorded rank identities. Never enumerate or signal jobs.
set -euo pipefail
output=$1
mkdir -p "$output/diagnostics"
node=$(hostname -s)
owner=$(id -u)
seen=' '
count=0
deadline=$((SECONDS + 3))
while read -r marker rank pid start worker_node extra; do
    ((SECONDS < deadline)) || break
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
    # Optional kernel diagnostics can be denied by proc/ptrace policy. Preserve
    # stderr; absence is not a zero-valued observation. Recheck birth/owner
    # before each read so a recycled PID is never deliberately inspected.
    for file in status wchan syscall stack; do
        ((SECONDS < deadline)) || break
        [[ $(stat -c %u "$process" 2>/dev/null) == "$owner" ]] || break
        current=$(cat "$process/stat" 2>/dev/null) || break
        current_tail=${current##*) }
        read -r -a current_fields <<< "$current_tail"
        [[ ${current_fields[19]:-} == "$start" ]] || break
        timeout --signal=TERM --kill-after=1 1s cat "$process/$file" \
            > "$prefix.$file" 2> "$prefix.$file.stderr" || true
    done
done < "$output/stdout"
