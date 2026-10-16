#!/usr/bin/env bash
# Standalone Bash dependency installer. Never publishes a REMORA prefix.
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
source "$ROOT/build_support/common.sh"
usage() {
    cat << 'HELP'
Third-party installation capability (runtime NOT_RUN)
  third_party_packages/install.sh check|build --package mpstat|mpip
      --build-dir NEW (--config FILE | --request FILE)
Results: NEW/result.json, NEW/logs/, NEW/stage/. Uses native Linux compute.
HELP
}
case ${1:-} in -h | --help)
    usage
    exit 0
    ;;
check | build)
    COMMAND=$1
    shift
    ;;
*)
    usage >&2
    exit 2
    ;;
esac
PHASE=third-party-$COMMAND
PACKAGE='' BUILD_DIR='' REQUEST='' CONFIG_ARG='' TP_PROGRESS_FD=''
while (($#)); do
    option=$1
    shift
    (($#)) || fatal "missing value for $option"
    value=$1
    shift
    case $option in
        --package) PACKAGE=$value ;;
        --build-dir) BUILD_DIR=$value ;;
        --request) REQUEST=$value ;;
        --config) CONFIG_ARG=$value ;;
        --progress-fd)
            [[ $value == 3 ]] || fatal 'invalid progress descriptor'
            TP_PROGRESS_FD=3
            ;;
        *) fatal "unknown option: $option" ;;
    esac
done
# The parent may duplicate its stdout to fd3; raw commands still stay in logs.
progress() {
    printf '[%s] %s\n' "$PHASE" "$*"
    if [[ $TP_PROGRESS_FD == 3 ]]; then printf '[%s] %s\n' "$PHASE" "$*" >&3; fi
}
if [[ $TP_PROGRESS_FD == 3 ]]; then
    { : >&3; } 2> /dev/null || fatal 'progress descriptor is unavailable'
fi
command -v jq > /dev/null 2>&1 || fatal 'jq is required'
REGISTRY="$ROOT/third_party_packages/packages.json"
jq -e --arg k "$PACKAGE" '.packages|has($k)' "$REGISTRY" > /dev/null || fatal 'unknown dependency'
[[ -n $BUILD_DIR && (-n $REQUEST || -n $CONFIG_ARG) ]] || fatal 'build directory and configuration required'
[[ -z $REQUEST || -z $CONFIG_ARG ]] || fatal 'choose request or config, not both'
BUILD_DIR=$(absolute "$BUILD_DIR") || fatal 'invalid dependency build path'
[[ ! -e $BUILD_DIR && ! -L $BUILD_DIR && $BUILD_DIR != "$ROOT" && $ROOT != "$BUILD_DIR/"* ]] || fatal 'dependency build directory must be new'
for path in src docs extra installer runtime build_support third_party_packages; do
    [[ $BUILD_DIR != "$ROOT/$path" && $BUILD_DIR != "$ROOT/$path/"* ]] || fatal 'dependency build directory cannot modify sources'
done
mkdir -p -- "$BUILD_DIR" && chmod 700 "$BUILD_DIR" || fatal 'cannot create dependency build directory'
WORK=$BUILD_DIR CONFIG="$BUILD_DIR/config.json" TOOLS="$BUILD_DIR/tools.json" FAILURES="$BUILD_DIR/tool-failures.json"
RESULT_FILE="$BUILD_DIR/result.json" STAGE="$BUILD_DIR/stage"
SYSTEM=$(uname -s) MACHINE=$(uname -m)
SETSID=$(command -v setsid) || SETSID=''
TIMEOUT=$(command -v timeout) || TIMEOUT=''
printf '{}\n' > "$TOOLS"
printf '{}\n' > "$FAILURES"
jq -n --arg package "$PACKAGE" --arg phase "$COMMAND" --arg stage "$STAGE" \
    '{schema:"remora-third-party-result/v1",package:$package,phase:$phase,status:"FAIL",stage:$stage,flag_probes:[],runtime_qualification:"NOT_RUN"}' > "$RESULT_FILE" || fatal 'cannot write dependency receipt'
if [[ -n $REQUEST ]]; then
    jq -e --arg package "$PACKAGE" '.schema=="remora-third-party-request/v1" and .package==$package and (.config|type)=="object" and (.tools|type)=="object"' "$REQUEST" > /dev/null || fatal 'invalid dependency request'
    jq .config "$REQUEST" > "$BUILD_DIR/input-config.json" || fatal 'invalid dependency config'
    CONFIG_ARG="$BUILD_DIR/input-config.json"
fi
jq -n --slurpfile input "$CONFIG_ARG" '
    {tools:{cc:"cc",cxx:"c++",fc:"gfortran",mpicc:"mpicc",mpicxx:"mpicxx",mpifc:"mpif77",nvcc:"nvcc",make:"make",bash:"bash",readelf:"readelf",mpip_python:"python3"},flags:{cflags:"",cxxflags:"",fflags:"",cppflags:"",ldflags:""}} * {tools:($input[0].tools//{}),flags:($input[0].flags//{})}' > "$CONFIG" || fatal 'invalid dependency configuration'
jq -e '(.tools|type)=="object" and ((.tools|keys)-["cc","cxx","fc","mpicc","mpicxx","mpifc","nvcc","make","bash","readelf","mpip_python"]|length)==0 and ((.flags|keys)-["cflags","cxxflags","fflags","cppflags","ldflags"]|length)==0 and (.tools|all(type=="string" and length>0 and (test("[\r\n]")|not))) and (.flags|type)=="object" and (.flags|all(type=="string" and (test("[\r\n]")|not)))' "$CONFIG" > /dev/null || fatal 'invalid tool or flag values'
platform_check || fatal "$LAST_ERROR"
for key in cflags fflags cppflags ldflags; do flags "$key" || fatal "$LAST_ERROR"; done
progress "Starting $PACKAGE $COMMAND; logs: $WORK/logs"
while IFS= read -r key; do need "$key" || fatal "$LAST_ERROR"; done < <(jq -r --arg k "$PACKAGE" '.packages[$k].tools[]' "$REGISTRY")
if [[ -n $REQUEST ]]; then
    jq -e --slurpfile actual "$TOOLS" '.tools as $expected | $actual[0] | to_entries | all(. as $item | $expected[$item.key] as $old | $item.value.path==$old.path and $item.value.sha256==$old.sha256 and $item.value.backend_executable==$old.backend_executable)' "$REQUEST" > /dev/null || fatal 'dependency tool identity differs from caller configuration'
fi
archive=$(jq -r --arg k "$PACKAGE" '.packages[$k].archive' "$REGISTRY")
[[ -f $ROOT/$archive && ! -L $ROOT/$archive ]] || fatal 'dependency archive missing or symlinked'
expected_archive_hash=$(jq -r --arg k "$PACKAGE" '.packages[$k].sha256 // empty' "$REGISTRY")
if [[ -n $expected_archive_hash && $(hash_file "$ROOT/$archive") != "$expected_archive_hash" ]]; then
    fatal 'dependency archive checksum differs from pinned release'
fi
jq .flags "$CONFIG" > "$WORK/requested-flags.json" || fatal 'cannot record requested flags'
json_update "$RESULT_FILE" --arg archive "$archive" --arg hash "$(hash_file "$ROOT/$archive")" \
    --arg version "$(jq -r --arg k "$PACKAGE" '.packages[$k].version' "$REGISTRY")" \
    --slurpfile tools "$TOOLS" --slurpfile flags "$WORK/requested-flags.json" \
    '.version=$version | .archive={path:$archive,sha256:$hash} | .tools=$tools[0] | .requested_flags=$flags[0]' || fatal 'cannot record inputs'
TP_PROBE_INDEX=0
C_PROBE='int main(void){return 0;}'
F_PROBE=$'program probe\nend program probe'

# Base flags are required. Defaults are optional only when the command rejects
# them, never when a timeout or invalid native artifact prevents a determination.
tp_base_flags() {
    local language=$1 compiler=$2 code=$3 suffix=$4 label=$1-base
    compile_probe "$label" "$compiler" "$code" "$suffix" "$language" || return 1
    if grep -Eiq 'unknown.*(switch|option|flag)|unrecognized.*(option|flag)|ignoring.*(unknown|unsupported)' "$WORK/logs/$label.stderr"; then
        error "user $language rejected or ignored by compiler"
        return 1
    fi
}

tp_default_flag() {
    local language=$1 compiler=$2 option=$3 required=${4:-false} label code suffix rc status=ACCEPTED reason='compile/link probe passed'
    label="flag-$TP_PROBE_INDEX"
    ((TP_PROBE_INDEX++))
    code=$C_PROBE suffix=.c
    [[ $language != fflags ]] || {
        code=$F_PROBE
        suffix=.f90
    }
    if ! compile_probe "$label" "$compiler" "$code" "$suffix" "$language" "$option"; then
        rc=$(jq -r '.exit_code // -1' "$WORK/logs/$label.json")
        if [[ $rc == 0 || $rc == 124 || $rc == 126 || $rc == 127 || $rc == -1 ]] || ((rc >= 128)); then return 1; fi
        status=REJECTED reason=$LAST_ERROR
    elif grep -Eiq 'unknown.*(switch|option|flag)|unrecognized.*(option|flag)|ignoring.*(unknown|unsupported)' "$WORK/logs/$label.stderr"; then
        status=REJECTED reason='compiler ignored or rejected option despite exit0'
    fi
    json_update "$RESULT_FILE" --arg language "$language" --arg flag "$option" --arg status "$status" --arg reason "$reason" --arg log "$WORK/logs/$label.json" \
        --argjson required "$required" '.flag_probes += [{language:$language,flag:$flag,status:$status,reason:$reason,required:$required,log:$log}]' || return 1
    if [[ $status == REJECTED ]]; then
        progress "Omitting unsupported default $option ($language); see $WORK/logs/$label.stderr"
        if [[ $required == true ]]; then
            error "required $option is unavailable for $language"
            return 1
        fi
    else
        json_update "$CONFIG" --arg k "$language" --arg flag "$option" '.flags[$k] += (if .flags[$k]=="" then "" else " " end)+$flag' || return 1
    fi
    LAST_ERROR=''
}

# PIC is required as a capability, not as one vendor-specific spelling.
tp_pic_flag() {
    local language=$1 compiler=$2 option
    for option in -fPIC -fpic; do
        tp_default_flag "$language" "$compiler" "$option" || return 1
        json_update "$RESULT_FILE" '.flag_probes[-1].capability="PIC"' || return 1
        if [[ $(jq -r '.flag_probes[-1].status' "$RESULT_FILE") == ACCEPTED ]]; then
            json_update "$RESULT_FILE" --arg language "$language" --arg flag "$option" '.required_capabilities[$language].PIC=$flag' || return 1
            return 0
        fi
    done
    error "required PIC capability is unavailable for $language"
    return 1
}

# Render tokens for upstream shell recipes, escaping make's dollar expansion.
tp_make_flags() {
    local value=$1 word rendered=''
    split_words "$value" || return 1
    for word in "${WORDS[@]}"; do rendered+="$(shell_quote "$word") "; done
    rendered=${rendered//\$/\$\$}
    printf '%s' "$rendered"
}

recipe=$(jq -r --arg k "$PACKAGE" '.packages[$k].recipe' "$REGISTRY")
source "$ROOT/third_party_packages/packages/$recipe"
recipe_prepare || fatal "$LAST_ERROR"
jq .flags "$CONFIG" > "$WORK/effective-flags.json" || fatal 'cannot record effective flags'
json_update "$RESULT_FILE" --slurpfile flags "$WORK/effective-flags.json" '.effective_flags=$flags[0]' || fatal 'cannot record flag choices'
if [[ -n $REQUEST ]] && jq -e 'has("expected_result")' "$REQUEST" > /dev/null; then
    jq -e --slurpfile actual "$RESULT_FILE" '.expected_result as $expected | $actual[0] | .archive==$expected.archive and .tools==$expected.tools and .requested_flags==$expected.requested_flags and .effective_flags==$expected.effective_flags' "$REQUEST" > /dev/null || fatal 'dependency inputs or flag capabilities changed; configure again'
fi
extract_archive "$ROOT/$archive" "$WORK/source" || fatal "$LAST_ERROR"
SOURCE=$EXTRACTED
mkdir -- "$STAGE" || fatal 'cannot create dependency stage'
recipe_build || fatal "$LAST_ERROR"
while IFS= read -r path; do
    [[ -f $STAGE/$path && ! -L $STAGE/$path && -s $STAGE/$path ]] || fatal "dependency artifact missing/empty: $path"
    case $path in *.a) ;; *)
        check_elf "$STAGE/$path" || fatal "$LAST_ERROR"
        run "$WORK" "$WORK/logs" "artifact-$PACKAGE" 60 "$(tool_path readelf)" -h -d "$STAGE/$path" || fatal "$LAST_ERROR"
        ;;
    esac
done < <(jq -r --arg k "$PACKAGE" '.packages[$k].artifacts[]' "$REGISTRY")
find "$STAGE" -type f -print0 | record_files "$STAGE" "$WORK/artifacts.json" || fatal 'cannot record dependency artifacts'
json_update "$RESULT_FILE" --slurpfile artifacts "$WORK/artifacts.json" '.status="BUILT" | .artifacts=$artifacts[0]' || fatal 'cannot save dependency result'
progress "$PACKAGE ready; receipt: $RESULT_FILE"
RESULT_FILE=''
