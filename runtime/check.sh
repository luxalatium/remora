#!/usr/bin/env bash
# Stage-two availability checks; run inside an existing compute allocation.
set -o pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$ROOT/common.sh"
usage() {
    cat << 'HELP'
REMORA runtime capability smoke (Bash; never requests an allocation)
  check.sh --prefix INSTALLED --output NEW_DIR [--module all|NAME]
           [--implementation both|shell|collector] [--samples 3] [--interval 1]
           [--timeout 30] [--mpi-probe PRECOMPILED_EXE]
Run only in a verified compute allocation on HPC. Results are node/revision-specific;
availability PASS is not accuracy/overhead qualification. No legacy wrapper runs.
HELP
}
PREFIX='' OUTPUT='' MODULE_ARG=all IMPLEMENTATION_ARG=both SAMPLES=3 INTERVAL=1 LIMIT=30 MPI_PROBE=''
while (($#)); do
    option=$1
    shift
    if [[ $option == --help || $option == -h ]]; then
        usage
        exit 0
    fi
    (($#)) || {
        printf 'Missing value: %s\n' "$option" >&2
        exit 2
    }
    value=$1
    shift
    case $option in
        --prefix) PREFIX=$value ;;
        --output) OUTPUT=$value ;;
        --module) MODULE_ARG=$value ;;
        --implementation) IMPLEMENTATION_ARG=$value ;;
        --samples) SAMPLES=$value ;;
        --interval) INTERVAL=$value ;;
        --timeout) LIMIT=$value ;;
        --mpi-probe) MPI_PROBE=$value ;;
        *)
            printf 'Unknown option: %s\n' "$option" >&2
            exit 2
            ;;
    esac
done
[[ -d $PREFIX && -n $OUTPUT && ! -e $OUTPUT && ! -L $OUTPUT ]] || {
    usage >&2
    exit 2
}
[[ $SAMPLES =~ ^[2-5]$ && $INTERVAL =~ ^[1-5]$ && $LIMIT =~ ^[0-9]+$ ]] || exit 2
((LIMIT >= 10 && LIMIT <= 60 && LIMIT >= SAMPLES * INTERVAL + 5)) || exit 2
case $IMPLEMENTATION_ARG in both | shell | collector) ;; *) exit 2 ;; esac
[[ $(uname -s) == Linux ]] || {
    printf 'Runtime probes require native Linux.\n' >&2
    exit 2
}
for tool in jq setsid timeout readlink; do command -v "$tool" > /dev/null 2>&1 || {
    printf 'Missing tool: %s\n' "$tool" >&2
    exit 2
}; done
SETSID=$(command -v setsid)
TIMEOUT=$(command -v timeout)
PREFIX=$(readlink -f -- "$PREFIX") || exit 2
OUTPUT=$(readlink -m -- "$OUTPUT") || exit 2
[[ $OUTPUT != "$PREFIX" && $OUTPUT != "$PREFIX/"* && $OUTPUT != *[[:space:]]* ]] || {
    printf 'Use a new output outside the prefix without whitespace (legacy modules split paths).\n' >&2
    exit 2
}
if [[ $MODULE_ARG == all ]]; then
    MODULES=(mpstat cpu memory numa eth ib opa power temperature gpu mpip network lustre lnet dvs)
else
    [[ $MODULE_ARG =~ ^[a-z]+$ && -f $ROOT/modules/$MODULE_ARG.sh ]] || exit 2
    MODULES=("$MODULE_ARG")
fi
if [[ $IMPLEMENTATION_ARG == both ]]; then
    IMPLEMENTATIONS=(shell collector)
else IMPLEMENTATIONS=("$IMPLEMENTATION_ARG"); fi
mkdir -m 700 -p -- "$OUTPUT" || exit 2
ACTIVE_PID=''
stop_active() {
    [[ -n $ACTIVE_PID ]] || return 0
    kill -TERM -- "-$ACTIVE_PID" 2> /dev/null || kill -TERM "$ACTIVE_PID" 2> /dev/null || true
    sleep 0.2
    kill -KILL -- "-$ACTIVE_PID" 2> /dev/null || true
    wait "$ACTIVE_PID" 2> /dev/null || true
    ACTIVE_PID=''
}
cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    stop_active
    exit "$rc"
}
trap cleanup EXIT
trap 'printf "[runtime] interrupted; partial evidence retained\n" >&2; exit 130' INT TERM
uname -a > "$OUTPUT/platform.txt"
printf '%s\n' "${LOADEDMODULES:-}" > "$OUTPUT/modules.txt"
for MODULE in "${MODULES[@]}"; do
    for IMPLEMENTATION in "${IMPLEMENTATIONS[@]}"; do
        CASE="$OUTPUT/$MODULE/$IMPLEMENTATION"
        mkdir -p -- "$CASE"
        argv=(bash "$ROOT/worker.sh" "$MODULE" "$IMPLEMENTATION" "$PREFIX" "$CASE" "$SAMPLES" "$INTERVAL" "$MPI_PROBE")
        jq -n --argjson limit "$LIMIT" --args '{timeout_seconds:$limit,command:$ARGS.positional}' -- "${argv[@]}" > "$CASE/command.json" || exit 2
        printf '[runtime] %s/%s: checking\n' "$MODULE" "$IMPLEMENTATION"
        INTERRUPT_PENDING=0
        trap 'INTERRUPT_PENDING=1' INT TERM
        "$SETSID" "$TIMEOUT" --signal=TERM --kill-after=2 "$LIMIT" "${argv[@]}" > "$CASE/stdout.txt" 2> "$CASE/stderr.txt" &
        ACTIVE_PID=$!
        trap 'printf "[runtime] interrupted; partial evidence retained\n" >&2; exit 130' INT TERM
        ((INTERRUPT_PENDING == 0)) || exit 130
        rc=0
        wait "$ACTIVE_PID" || rc=$?
        stop_active
        printf '%s\n' "$rc" > "$CASE/exit-status.txt"
        NODE=$(hostname -s)
        ARTIFACT='' ARTIFACT_HASH=''
        if ((rc != 0)); then
            if [[ -f $CASE/result.json ]]; then cp -- "$CASE/result.json" "$CASE/result-before-failure.json"; fi
            result FAIL "worker failed/interrupted/timed out (exit $rc); partial evidence retained"
        elif [[ ! -s $CASE/result.json ]]; then
            result FAIL 'worker exited zero without a result'
        fi
        jq -e '.schema=="remora-runtime-check/v1" and
            (.status=="PASS" or .status=="FAIL" or .status=="SKIP" or .status=="BLOCKED")' "$CASE/result.json" > /dev/null || exit 2
        jq --argjson rc "$rc" '. + {exit_code:$rc}' "$CASE/result.json" > "$CASE/result.tmp" && mv -- "$CASE/result.tmp" "$CASE/result.json" || exit 2
        printf '[runtime] %s/%s: %s\n' "$MODULE" "$IMPLEMENTATION" "$(jq -r '.status+" - "+.reason' "$CASE/result.json")"
    done
done
jq -s --argjson expected "$((${#MODULES[@]} * ${#IMPLEMENTATIONS[@]}))" '
    if length != $expected then error("incomplete runtime case set") else {schema:"remora-runtime-summary/v1",cases:.,
    status:(if any(.[];.status=="FAIL") then "FAIL" elif any(.[];.status=="BLOCKED") then "BLOCKED"
    elif any(.[];.status=="PASS") then "PASS" else "SKIP" end)} end' "$OUTPUT"/*/*/result.json > "$OUTPUT/summary.json" || exit 2
printf '[runtime] Summary: %s\n' "$OUTPUT/summary.json"
case $(jq -r .status "$OUTPUT/summary.json") in FAIL) exit 1 ;; BLOCKED) exit 2 ;; *) exit 0 ;; esac
