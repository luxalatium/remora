#!/usr/bin/env bash
# Native Bash installer; jq preserves JSON data contracts without Python.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$ROOT/installer/common.sh"
usage() {
    cat << 'HELP'
REMORA installation-capability CLI (Bash; runtime qualification NOT_RUN)
  install.sh configure --build-dir NEW --prefix NEW [options]
  install.sh show|build|install --plan PATH
  install.sh --legacy                  explicit historical installer
Options:
  --config FILE                       edit/reuse config in a NEW build directory
  --cc --cxx --fc --mpicc --mpicxx --mpifc --nvcc EXECUTABLE
  --make --bash --readelf EXECUTABLE
  --mpip-python EXECUTABLE            mpiP Python 3 interpreter (default python3)
  --cflags --cxxflags --fflags --cppflags --ldflags STRING
  --cuda PREFIX --nvml-include DIR --nvml-lib DIR
  --module NAME=on|auto|off             repeat per feature
Use --cflags=-O2 for flag strings. Configure/build compile code: use allocated
compute on HPC. Bash 3.2+, jq, SHA-256 utilities; Linux setsid/GNU timeout for checks.
HELP
}
case ${1:-} in
    -h | --help)
        usage
        exit 0
        ;;
    '')
        usage
        exit 2
        ;;
esac
COMMAND=$1
shift
case $COMMAND in
    configure | show | build | install) ;;
    *)
        fatal "unknown command: $COMMAND"
        ;;
esac
PHASE=$COMMAND
for option in "$@"; do
    if [[ $option == --help || $option == -h ]]; then
        usage
        exit 0
    fi
done
command -v jq > /dev/null 2>&1 || fatal 'jq is required for installer JSON; Python is not required'
command -v sha256sum > /dev/null 2>&1 || command -v shasum > /dev/null 2>&1 || fatal 'SHA-256 utility required'
REGISTRY="$ROOT/installer/modules.json"
SYSTEM=$(uname -s)
MACHINE=$(uname -m)
SETSID=$(command -v setsid) || SETSID=''
TIMEOUT=$(command -v timeout) || TIMEOUT=''
BUILD_DIR='' PREFIX_ARG='' CONFIG_ARG='' PLAN_ARG=''
OVERRIDES='{}'
MODULE_OVERRIDES='{}'
while (($#)); do
    option=$1
    shift
    [[ $option == --* ]] || fatal "unexpected argument: $option"
    if [[ $option == *=* ]]; then
        value=${option#*=}
        option=${option%%=*}
    else
        (($#)) || fatal "missing value for $option"
        value=$1
        shift
    fi
    key=${option#--}
    key=${key//-/_}
    if [[ $COMMAND != configure ]]; then
        [[ $key == plan && -z $PLAN_ARG ]] || fatal "unsupported/repeated option: $option"
        PLAN_ARG=$value
        continue
    fi
    case $key in
        build_dir)
            BUILD_DIR=$value
            ;;
        prefix)
            PREFIX_ARG=$value
            ;;
        config)
            CONFIG_ARG=$value
            ;;
        module)
            name=${value%%=*}
            mode=${value#*=}
            jq -e --arg name "$name" '.modules|has($name)' "$REGISTRY" > /dev/null || fatal "unknown module: $name"
            [[ $value == *=* ]] && [[ $mode == on || $mode == auto || $mode == off ]] || fatal "invalid module choice: $value"
            MODULE_OVERRIDES=$(jq -cn --argjson old "$MODULE_OVERRIDES" --arg k "$name" --arg v "$mode" '$old+{($k):$v}') || fatal 'invalid module choice'
            ;;
        cc | cxx | fc | mpicc | mpicxx | mpifc | nvcc | make | bash | readelf | mpip_python)
            OVERRIDES=$(jq -cn --argjson old "$OVERRIDES" --arg k "$key" --arg v "$value" '$old | .tools[$k]=$v | .requested_tools=((.requested_tools//[])+[$k]|unique)') || fatal 'invalid tool setting'
            ;;
        cflags | cxxflags | fflags | cppflags | ldflags)
            OVERRIDES=$(jq -cn --argjson old "$OVERRIDES" --arg k "$key" --arg v "$value" '$old | .flags[$k]=$v') || fatal 'invalid flags'
            ;;
        cuda | nvml_include | nvml_lib)
            OVERRIDES=$(jq -cn --argjson old "$OVERRIDES" --arg k "$key" --arg v "$value" '$old+{($k):$v}') || fatal 'invalid path'
            ;;
        *)
            fatal "unknown option: $option"
            ;;
    esac
done

module_status() {
    json_update "$MODULES" --arg name "$1" --arg requested "$2" --arg status "$3" --arg reason "$4" \
        '.[$name]={requested:$requested,status:$status,reason:$reason,runtime:"NOT_RUN"}' || fatal 'cannot save module result'
    progress "Module $1: $3 ($4)"
}

check_status() {
    json_update "$CHECKS" --arg name "$1" --arg status "$2" --arg reason "${3:-}" \
        '.[$name]={status:$status} | if $reason!="" then .[$name].reason=$reason else . end' || fatal 'cannot save toolchain check'
    progress "Check $1: $2${3:+ ($3)}"
}

module_check() {
    local name=$1 kind=$2 stage="$WORK/$1-stage" dep status entry
    platform_check || return 1
    while IFS= read -r dep; do
        status=$(jq -r --arg k "$dep" '.[$k].status // "NOT_RUN"' "$MODULES")
        [[ $status == READY ]] || {
            error "requires READY module: $dep"
            return 1
        }
    done < <(jq -r --arg k "$name" '.modules[$k].requires[]' "$REGISTRY")
    need bash || return 1
    mkdir -- "$stage" || return 1
    if [[ $kind != shell ]]; then
        need cc && need readelf || return 1
        if ! jq -e 'has("cc-language")' "$CHECKS" > /dev/null; then
            if compile_probe cc-language "$(tool_path cc)" 'int main(void){return 0;}' .c cflags; then
                check_status cc-language READY
            else
                check_status cc-language FAIL "$LAST_ERROR"
            fi
        fi
        status=$(jq -r '."cc-language".status' "$CHECKS")
        [[ $status == READY ]] || {
            error "$(jq -r '."cc-language".reason' "$CHECKS")"
            return 1
        }
    fi
    case $kind in
        third_party)
            need make && third_party_build "$name" "$stage" || return 1
            ;;
        collector | nvml)
            if [[ $kind == nvml ]]; then
                gpu_flags
                compile_probe nvml "$(tool_path cc)" $'#include <nvml.h>\nint main(void){return (int)nvmlInit();}' .c cflags "${GPU_ARGS[@]}" || return 1
            fi
            collector "$name" "$stage" || return 1
            ;;
    esac
    entry=$(jq -r --arg k "$name" '.modules[$k].entry // empty' "$REGISTRY")
    if [[ -n $entry ]]; then
        run "$WORK" "$WORK/logs" "shell-$name" 60 "$(tool_path bash)" -n "$ROOT/src/modules/${entry%%,*}" || return 1
    fi
    inspect_stage "$stage" "$name"
}
MPI_C=$'#include <mpi.h>\nint main(int n,char**v){MPI_Init(&n,&v);MPI_Finalize();}'
MPI_F=$'      program probe\n      include '\''mpif.h'\''
      integer ierr
      call MPI_Init(ierr)
      call MPI_Finalize(ierr)
      end'
configure() {
    local defaults loaded key env_key i name value kind mode stage result status flag code suffix extra=() path output
    progress 'Starting configuration'
    [[ -n $BUILD_DIR ]] || fatal '--build-dir is required'
    BUILD_DIR=$(absolute "$BUILD_DIR") || fatal 'invalid build path'
    [[ ! -e $BUILD_DIR && ! -L $BUILD_DIR ]] || fatal 'build directory must be new; use a separate directory for reconfigure'
    for path in src docs extra installer runtime build_support third_party_packages; do
        [[ $BUILD_DIR != "$ROOT/$path" && $BUILD_DIR != "$ROOT/$path/"* ]] || fatal 'build directory cannot be inside source/dependency directories'
    done
    mkdir -p -- "$BUILD_DIR" && chmod 700 "$BUILD_DIR" || fatal 'cannot create build directory'
    CONFIG="$BUILD_DIR/config.json"
    WORK="$BUILD_DIR/checks"
    progress "Build state: $BUILD_DIR; check logs: $WORK/logs"
    mkdir -- "$WORK" || fatal 'cannot create checks directory'
    TOOLS="$WORK/tools.json"
    FAILURES="$WORK/tool-failures.json"
    CHECKS="$WORK/checks.json"
    MODULES="$WORK/modules.json"
    THIRD_PARTY_RESULTS="$WORK/third_party_packages.json"
    for path in "$TOOLS" "$FAILURES" "$CHECKS" "$MODULES" "$THIRD_PARTY_RESULTS"; do
        printf '{}\n' > "$path"
    done
    jq -n --slurpfile registry "$REGISTRY" \
        '{tools:{cc:"cc",cxx:"c++",fc:"gfortran",mpicc:"mpicc",mpicxx:"mpicxx",mpifc:"mpif77",nvcc:"nvcc",make:"make",bash:"bash",readelf:"readelf",mpip_python:"python3"},
        flags:{cflags:"",cxxflags:"",fflags:"",cppflags:"",ldflags:""},
        modules:($registry[0].modules|map_values(.default)),prefix:"",nvml_include:"",nvml_lib:"",cuda:"",requested_tools:[]}' > "$CONFIG" || fatal 'cannot create configuration'
    if [[ -n $CONFIG_ARG ]]; then
        progress "Loading configuration: $CONFIG_ARG"
        jq -e --slurpfile defaults "$CONFIG" '
          type=="object" and ((keys-($defaults[0]|keys))|length==0) and
          (["tools","flags","modules"]|all(. as $k | true))' "$CONFIG_ARG" > /dev/null || fatal 'invalid configuration object/keys'
        for key in tools flags modules; do
            jq -e --arg k "$key" --slurpfile defaults "$CONFIG" \
                '(.[$k]//{}|type)=="object" and (((.[$k]//{}|keys)-($defaults[0][$k]|keys))|length==0)' "$CONFIG_ARG" > /dev/null || fatal "unknown configuration $key"
        done
        jq --slurpfile loaded "$CONFIG_ARG" '. * $loaded[0] | .requested_tools=($loaded[0].requested_tools // ($loaded[0].tools//{}|keys))' "$CONFIG" > "$CONFIG.new" && mv -- "$CONFIG.new" "$CONFIG" || fatal 'invalid saved config'
    else
        env_names=(CC CXX FC MPICC MPICXX MPIFC NVCC MAKE BASH READELF MPIP_PYTHON)
        for ((i = 0; i < ${#TOOL_NAMES[@]}; i++)); do
            key=${TOOL_NAMES[i]}
            env_key=${env_names[i]}
            # BASH is the current shell identity, not a requested tool override.
            [[ $key != bash ]] || continue
            if [[ ${!env_key+x} && -n ${!env_key} ]]; then
                json_update "$CONFIG" --arg k "$key" --arg v "${!env_key}" '.tools[$k]=$v' || fatal 'invalid environment tool'
            fi
        done
        flag_names=(cflags cxxflags fflags cppflags ldflags)
        flag_env=(CFLAGS CXXFLAGS FFLAGS CPPFLAGS LDFLAGS)
        for ((i = 0; i < ${#flag_names[@]}; i++)); do
            key=${flag_names[i]}
            env_key=${flag_env[i]}
            if [[ ${!env_key+x} ]]; then
                json_update "$CONFIG" --arg k "$key" --arg v "${!env_key}" '.flags[$k]=$v' || fatal 'invalid environment flags'
            fi
        done
    fi
    json_update "$CONFIG" --argjson overrides "$OVERRIDES" --argjson modules "$MODULE_OVERRIDES" \
        '.requested_tools as $old | . * $overrides | .requested_tools=($old+($overrides.requested_tools//[])|unique) | .modules += $modules' || fatal 'invalid configuration overrides'
    [[ -z $PREFIX_ARG ]] || json_update "$CONFIG" --arg v "$PREFIX_ARG" '.prefix=$v' || fatal 'invalid prefix'
    jq -e --argjson names "$(printf '%s\n' "${TOOL_NAMES[@]}" | jq -R . | jq -s .)" '
      (.prefix|type)=="string" and .prefix!="" and
      ([.cuda,.nvml_include,.nvml_lib]|all(type=="string")) and
      (.tools|all(type=="string" and length>0 and (contains("\n")|not) and (contains("\r")|not))) and
      (.flags|all(type=="string")) and (.modules|all(.=="on" or .=="auto" or .=="off")) and
      (.requested_tools|type)=="array" and ((.requested_tools-$names)|length==0)' "$CONFIG" > /dev/null || fatal 'invalid tool/flag/module/path settings; prefix is required'
    PREFIX=$(absolute "$(jq -r .prefix "$CONFIG")") || fatal 'invalid prefix'
    [[ ! -e $PREFIX && ! -L $PREFIX ]] || fatal 'prefix must be a new version directory; existing installations are preserved'
    [[ $PREFIX != "$BUILD_DIR" && $PREFIX != "$BUILD_DIR/"* && $BUILD_DIR != "$PREFIX/"* ]] || fatal 'build and prefix directories must be separate'
    [[ $PREFIX != "$ROOT" && $PREFIX != "$ROOT/"* && $ROOT != "$PREFIX/"* && $PREFIX != / ]] || fatal 'prefix must be outside the source tree'
    json_update "$CONFIG" --arg v "$PREFIX" '.prefix=$v' || fatal 'cannot save prefix'
    value=$(jq -r .cuda "$CONFIG")
    if [[ -n $value ]]; then
        value=$(absolute "$value") || fatal 'invalid CUDA prefix'
        json_update "$CONFIG" --arg v "$value" '.cuda=$v | if .nvml_include=="" then .nvml_include=($v+"/include") else . end | if (.requested_tools|index("nvcc"))==null then .tools.nvcc=($v+"/bin/nvcc") else . end' || fatal 'invalid CUDA config'
    fi
    for key in nvml_include nvml_lib; do
        value=$(jq -r --arg k "$key" '.[$k]' "$CONFIG")
        if [[ -n $value ]]; then
            value=$(absolute "$value") || fatal 'invalid NVML path'
            json_update "$CONFIG" --arg k "$key" --arg v "$value" '.[$k]=$v' || fatal 'invalid NVML config'
        fi
    done
    for key in cflags cxxflags fflags cppflags ldflags; do
        flags "$key" || fatal "$LAST_ERROR"
    done
    LAST_ERROR=''
    progress 'Checking platform and base scripts'
    if platform_check && need bash; then
        i=0
        result=READY
        for path in "$ROOT/src/remora" "$ROOT"/src/scripts/*.sh; do
            if ! run "$WORK" "$WORK/logs" "base-script-$i" 60 "$(tool_path bash)" -n "$path"; then
                result=BLOCKED
                break
            fi
            ((i++))
        done
        check_status base-scripts "$result" "$([[ $result == READY ]] || printf '%s' "$LAST_ERROR")"
    else
        check_status base-scripts BLOCKED "$LAST_ERROR"
    fi
    for name in cxx fc mpicc mpifc mpicxx nvcc; do
        if ! jq -e --arg k "$name" '(.requested_tools|index($k))!=null or ($k=="nvcc" and .cuda!="")' "$CONFIG" > /dev/null; then
            continue
        fi
        progress "Checking toolchain: $name"
        case $name in
            cxx)
                code='int main(){return 0;}'
                suffix=.cpp
                flag=cxxflags
                ;;
            fc)
                code=$'program probe\nend program probe'
                suffix=.f90
                flag=fflags
                ;;
            mpicc)
                code=$MPI_C
                suffix=.c
                flag=cflags
                ;;
            mpicxx)
                code=$MPI_C
                suffix=.cpp
                flag=cxxflags
                ;;
            mpifc)
                code=$MPI_F
                suffix=.f
                flag=fflags
                ;;
            nvcc)
                code=$'__global__ void k(){}\nint main(){return 0;}'
                suffix=.cu
                flag=cxxflags
                ;;
        esac
        LAST_ERROR=''
        extra=()
        if platform_check && need "$name"; then
            if [[ $name == nvcc ]] && jq -e '.requested_tools|index("cxx")!=null' "$CONFIG" > /dev/null; then
                if need cxx; then
                    extra=(-ccbin "$(tool_path cxx)")
                else
                    check_status "$name" BLOCKED "$LAST_ERROR"
                    continue
                fi
            fi
            if compile_probe "$name-language" "$(tool_path "$name")" "$code" "$suffix" "$flag" "${extra[@]}"; then
                check_status "$name" READY
            else
                check_status "$name" BLOCKED "$LAST_ERROR"
            fi
        else
            check_status "$name" BLOCKED "$LAST_ERROR"
        fi
    done
    while IFS= read -r name; do
        mode=$(jq -r --arg k "$name" '.modules[$k]' "$CONFIG")
        kind=$(jq -r --arg k "$name" '.modules[$k].kind' "$REGISTRY")
        if [[ $mode == off ]]; then
            module_status "$name" "$mode" DISABLED 'disabled by configuration'
            continue
        fi
        progress "Checking module: $name (requested $mode)"
        LAST_ERROR=''
        if module_check "$name" "$kind"; then
            module_status "$name" "$mode" READY 'installation checks passed; runtime NOT_RUN'
        else
            status=SKIPPED
            [[ $mode != on ]] || status=BLOCKED
            module_status "$name" "$mode" "$status" "${LAST_ERROR:-module check failed}"
        fi
    done < <(jq -r '.modules|keys_unsorted[]' "$REGISTRY")
    progress 'Recording source, dependency and environment checksums'
    source_manifest "$WORK/source.json" || fatal "${LAST_ERROR:-source manifest failed}"
    external_manifest "$WORK/external.json" || fatal 'dependency manifest failed'
    environment_manifest "$WORK/environment.json" || fatal 'environment manifest failed'
    jq -n --slurpfile modules "$MODULES" --slurpfile registry "$REGISTRY" \
        '[$registry[0].modules|keys_unsorted[] | select($modules[0][.].status=="READY")]' > "$WORK/selected.json" || fatal 'cannot resolve selected modules'
    jq -n --slurpfile selected "$WORK/selected.json" --slurpfile registry "$REGISTRY" '
      (["bin/remora","bin/config/modules","bin/runtime/check.sh","bin/runtime/worker.sh","bin/runtime/common.sh"] + [$selected[0][] as $k | $registry[0].modules[$k] |
        ((.artifacts//[])[]), (if .kind=="collector" then "bin/binary_data_collectors/data_collect_"+$k else empty end),
        (if .entry then "bin/modules/"+(.entry|split(",")[0]) else empty end)] | unique)' > "$WORK/expected.json" || fatal 'artifact contract failed'
    status=READY
    if jq -e 'any(.[];.status=="BLOCKED")' "$MODULES" > /dev/null || jq -e 'any(.[];.status=="BLOCKED")' "$CHECKS" > /dev/null; then
        status=BLOCKED
    fi
    jq -n --arg root "$ROOT" --arg build "$BUILD_DIR" --arg system "$SYSTEM" --arg machine "$MACHINE" --arg status "$status" \
        --slurpfile config "$CONFIG" --slurpfile tools "$TOOLS" --slurpfile checks "$CHECKS" --slurpfile modules "$MODULES" \
        --slurpfile source "$WORK/source.json" --slurpfile external "$WORK/external.json" --slurpfile environment "$WORK/environment.json" \
        --slurpfile selected "$WORK/selected.json" --slurpfile expected "$WORK/expected.json" --slurpfile third_party "$THIRD_PARTY_RESULTS" \
        '{schema:"remora-build-plan/v2",seal_algorithm:"jq-cS-utf8/v1",source:$root,source_files:$source[0],build_dir:$build,config:$config[0],tools:$tools[0],
        checks:$checks[0],modules:$modules[0],third_party_packages:$third_party[0],external_dependencies:$external[0],environment:$environment[0],platform:{system:$system,machine:$machine},
        selected:$selected[0],expected_artifacts:$expected[0],status:$status,runtime_qualification:"NOT_RUN"}' > "$BUILD_DIR/plan.json" || fatal 'cannot save plan'
    PLAN="$BUILD_DIR/plan.json"
    value=$(json_hash "$PLAN" 'del(.plan_sha256)') || fatal 'cannot hash plan'
    json_update "$PLAN" --arg value "$value" '.plan_sha256=$value' || fatal 'cannot seal plan'
    progress "Configuration result: $status; plan: $PLAN"
    show_plan
    [[ $status == READY ]] || return 2
}

show_plan() {
    jq -r '"Installation plan: \(.status); runtime NOT_RUN", "Prefix: \(.config.prefix)",
      (.tools|to_entries[]|"Tool \(.key): \(.value.path) [\(.value.version|split("\n")[0])]", (if .value.backend then "  wrapper backend: "+.value.backend else empty end)),
      "Module\tRequested\tResult\tReason", (.modules|to_entries[]|"\(.key)\t\(.value.requested)\t\(.value.status)\t\(.value.reason)"),
      (.checks|to_entries[]|select(.value.status=="BLOCKED")|"Toolchain check BLOCKED: \(.key): \(.value.reason)"),
      "Selected modules: "+(.selected|join(", "))' "$PLAN" || fatal 'invalid plan content'
}

read_plan() {
    local claimed actual
    [[ -n $PLAN_ARG ]] || fatal '--plan is required'
    PLAN=$(absolute "$PLAN_ARG") || fatal 'invalid plan path'
    jq -e '.schema=="remora-build-plan/v2" and .seal_algorithm=="jq-cS-utf8/v1"' "$PLAN" > /dev/null || fatal 'plan schema/integrity mismatch; configure again (Python v1 plans remain historical)'
    claimed=$(jq -r .plan_sha256 "$PLAN")
    actual=$(json_hash "$PLAN" 'del(.plan_sha256)') || fatal 'cannot hash plan'
    [[ $claimed == "$actual" ]] || fatal 'plan integrity mismatch; configure again'
}

verify_plan() {
    local path hash name backend verify_dir
    [[ $(jq -r .status "$PLAN") == READY ]] || fatal 'plan is BLOCKED; configure again after correcting required features'
    [[ $(jq -r .source "$PLAN") == "$ROOT" ]] || fatal 'source changed; configure again'
    [[ $(jq -r .platform.system "$PLAN") == "$SYSTEM" && $(jq -r .platform.machine "$PLAN") == "$MACHINE" ]] || fatal 'platform changed; configure again'
    BUILD_DIR=$(jq -r .build_dir "$PLAN")
    verify_dir=$(mktemp -d "$BUILD_DIR/verify.XXXXXX") || fatal 'cannot create verification evidence'
    CONFIG="$verify_dir/config.json"
    TOOLS="$verify_dir/tools.json"
    jq .config "$PLAN" > "$CONFIG" && jq .tools "$PLAN" > "$TOOLS" || fatal 'invalid plan config/tools'
    source_manifest "$verify_dir/source.json" || fatal "${LAST_ERROR:-source manifest failed}"
    jq .source_files "$PLAN" > "$verify_dir/expected-source.json"
    json_equal "$verify_dir/source.json" "$verify_dir/expected-source.json" || fatal 'source changed; configure again'
    environment_manifest "$verify_dir/environment.json" || fatal 'environment manifest failed'
    jq .environment "$PLAN" > "$verify_dir/expected-environment.json"
    json_equal "$verify_dir/environment.json" "$verify_dir/expected-environment.json" || fatal 'toolchain environment changed; configure again'
    external_manifest "$verify_dir/external.json" || fatal 'dependency manifest failed'
    jq .external_dependencies "$PLAN" > "$verify_dir/expected-external.json"
    json_equal "$verify_dir/external.json" "$verify_dir/expected-external.json" || fatal 'configured dependency files changed; configure again'
    while IFS= read -r name; do
        path=$(jq -r --arg k "$name" '.[$k].path' "$TOOLS")
        hash=$(jq -r --arg k "$name" '.[$k].sha256' "$TOOLS")
        [[ -f $path && $(hash_file "$path") == "$hash" ]] || fatal "tool changed: $path"
        backend=$(jq -r --arg k "$name" '.[$k].backend_executable.path // empty' "$TOOLS")
        if [[ -n $backend ]]; then
            hash=$(jq -r --arg k "$name" '.[$k].backend_executable.sha256' "$TOOLS")
            [[ -f $backend && $(hash_file "$backend") == "$hash" ]] || fatal "MPI backend compiler changed: $backend"
        fi
    done < <(jq -r 'keys[]' "$TOOLS")
}

build_plan() {
    local stage source name kind path expected
    progress "Starting build; verifying plan: $PLAN"
    verify_plan
    WORK="$BUILD_DIR/build"
    PHASE=build
    [[ ! -e $WORK && ! -L $WORK ]] || fatal 'build already attempted; configure a new build directory'
    mkdir -- "$WORK" || fatal 'cannot create build directory'
    # Build reuses verified tools, but need() also reads per-phase failure state.
    FAILURES="$WORK/tool-failures.json"
    printf '{}\n' > "$FAILURES" || fatal 'cannot initialize tool failure state'
    THIRD_PARTY_RESULTS="$WORK/third_party_packages.json"
    printf '{}\n' > "$THIRD_PARTY_RESULTS" || fatal 'cannot initialize dependency receipts'
    stage="$WORK/stage"
    mkdir -- "$stage" || fatal 'cannot create stage'
    RESULT_FILE="$WORK/result.json"
    jq '{schema:"remora-build-result/v1",plan_sha256:.plan_sha256,status:"FAIL",modules:(.selected|map({key:.,value:"NOT_RUN"})|from_entries)}' "$PLAN" > "$RESULT_FILE" || fatal 'cannot record build result'
    mkdir -p -- "$stage/bin" "$stage/docs" || fatal 'cannot create staged layout'
    cp -Rp -- "$ROOT/runtime" "$stage/bin/runtime" || fatal 'cannot stage runtime checks'
    progress "Preparing staged scripts and documentation: $stage"
    for source in "$ROOT/src"/*; do
        case ${source##*/} in
            C_data_collectors_src | binary_data_collectors)
                continue
                ;;
        esac
        cp -Rp -- "$source" "$stage/bin/" || fatal 'cannot copy installation scripts'
    done
    for name in modules_help modules_whatis remora_user_guide.pdf; do
        cp -p -- "$ROOT/docs/$name" "$stage/docs/" || fatal 'cannot copy documentation'
    done
    jq -r --slurpfile registry "$REGISTRY" '.selected[] as $k | $registry[0].modules[$k].entry // empty' "$PLAN" > "$stage/bin/config/modules" || fatal 'cannot save selected config'
    # Keep empty feature configurations a regular nonempty file, matching v1.
    [[ -s $stage/bin/config/modules ]] || printf '\n' > "$stage/bin/config/modules"
    while IFS= read -r name; do
        progress "Building module: $name"
        json_update "$RESULT_FILE" --arg k "$name" '.modules[$k]="BUILDING"' || fatal 'cannot record module build'
        kind=$(jq -r --arg k "$name" '.modules[$k].kind' "$REGISTRY")
        LAST_ERROR=''
        case $kind in
            third_party)
                third_party_build "$name" "$stage" || fatal "${LAST_ERROR:-dependency build failed}"
                ;;
            collector | nvml)
                collector "$name" "$stage" || fatal "${LAST_ERROR:-collector build failed}"
                ;;
        esac
        json_update "$RESULT_FILE" --arg k "$name" '.modules[$k]="BUILT"' || fatal 'cannot record module build'
        progress "Module $name: BUILT"
    done < <(jq -r '.selected[]' "$PLAN")
    progress 'Verifying expected artifacts, ELF metadata and checksums'
    while IFS= read -r path; do
        [[ -f $stage/$path && -s $stage/$path ]] || fatal "missing/empty expected artifact: $path"
    done < <(jq -r '.expected_artifacts[]' "$PLAN")
    inspect_stage "$stage" build || fatal "${LAST_ERROR:-artifact inspection failed}"
    artifact_manifest "$stage" "$WORK/artifacts.json" || fatal 'artifact manifest failed'
    json_update "$RESULT_FILE" --slurpfile artifacts "$WORK/artifacts.json" '.status="BUILT" | .artifacts=$artifacts[0] | .runtime_qualification="NOT_RUN"' || fatal 'cannot save build receipt'
    json_update "$RESULT_FILE" --slurpfile third_party "$THIRD_PARTY_RESULTS" '.third_party_packages=$third_party[0]' || fatal 'cannot save dependency receipts'
    jq -n --slurpfile plan "$PLAN" --slurpfile build "$RESULT_FILE" '{plan:$plan[0],build:$build[0]}' > "$stage/build-manifest.json" || fatal 'cannot save installed provenance'
    progress "Build complete; receipt: $RESULT_FILE; logs: $WORK/logs"
    printf '%s\n' 'BUILT; runtime NOT_RUN. Review logs, then run install with the same plan.'
    RESULT_FILE=''
}

install_plan() {
    local stage prefix parent result claimed current candidate lock work
    progress "Starting installation; verifying plan: $PLAN"
    verify_plan
    work="$BUILD_DIR/build"
    stage="$work/stage"
    result="$work/result.json"
    jq -e --arg hash "$(jq -r .plan_sha256 "$PLAN")" '.status=="BUILT" and .plan_sha256==$hash' "$result" > /dev/null || fatal 'no successful build for this plan'
    progress 'Verifying staged artifacts and provenance'
    artifact_manifest "$stage" "$work/install-check.json" || fatal 'artifact manifest failed'
    json_update "$work/install-check.json" 'del(.["build-manifest.json"])' || fatal 'artifact check failed'
    jq .artifacts "$result" > "$work/expected-artifacts.json"
    json_equal "$work/install-check.json" "$work/expected-artifacts.json" || fatal 'staged artifacts changed; refuse installation'
    jq -n --slurpfile plan "$PLAN" --slurpfile build "$result" '{plan:$plan[0],build:$build[0]}' > "$work/expected-provenance.json"
    json_equal "$stage/build-manifest.json" "$work/expected-provenance.json" || fatal 'staged provenance changed'
    prefix=$(jq -r .config.prefix "$PLAN")
    parent=${prefix%/*}
    [[ -n $parent ]] || parent=/
    mkdir -p -- "$parent" || fatal 'cannot create prefix parent'
    [[ ! -e $prefix && ! -L $prefix ]] || fatal 'prefix exists; refusing to overwrite installation'
    lock="$parent/.${prefix##*/}.install-lock"
    mkdir -m700 -- "$lock" || fatal "installation lock exists or cannot be created: $lock"
    OWN_LOCK=$lock
    PHASE=install
    RESULT_FILE="$work/install-result.json"
    jq -n --arg hash "$(jq -r .plan_sha256 "$PLAN")" '{status:"FAIL",plan_sha256:$hash,published:false,candidate:null}' > "$RESULT_FILE" || fatal 'cannot save installation receipt'
    candidate=$(mktemp -d "$parent/.${prefix##*/}.staging-XXXXXX") || fatal 'cannot create destination stage'
    json_update "$RESULT_FILE" --arg path "$candidate" '.candidate=$path' || fatal 'cannot save staging receipt'
    progress "Copying installation to destination stage: $candidate"
    cp -Rp -- "$stage/." "$candidate/" || fatal 'destination copy failed'
    progress 'Verifying destination copy checksums'
    artifact_manifest "$candidate" "$work/destination-check.json" || fatal 'destination manifest failed'
    artifact_manifest "$stage" "$work/source-check.json" || fatal 'source manifest failed'
    json_equal "$work/destination-check.json" "$work/source-check.json" || fatal 'destination copy checksum mismatch'
    [[ ! -e $prefix && ! -L $prefix ]] || fatal 'prefix appeared during installation'
    # GNU mv -T avoids nesting the candidate if a non-cooperating writer creates
    # the destination; -n preserves that destination. Verify that it really moved.
    progress "Publishing installation: $prefix"
    mv -T -n -- "$candidate" "$prefix" || fatal 'destination publication failed'
    [[ ! -e $candidate && -d $prefix ]] || fatal 'prefix appeared during publication; candidate retained'
    json_update "$RESULT_FILE" --arg path "$prefix" '.status="INSTALLED" | .prefix=$path | .published=true | .runtime_qualification="NOT_RUN"' || fatal 'cannot save installation receipt'
    rmdir -- "$OWN_LOCK" || fatal 'cannot release installation lock'
    OWN_LOCK=''
    RESULT_FILE=''
    progress "Installation complete; receipt: $work/install-result.json"
    printf 'INSTALLED: %s; runtime NOT_RUN\n' "$prefix"
}

case $COMMAND in
    configure)
        configure
        exit $?
        ;;
    show)
        read_plan
        show_plan
        ;;
    build)
        read_plan
        build_plan
        ;;
    install)
        read_plan
        install_plan
        ;;
esac
