#!/usr/bin/env bash
# Bash 3.2+ helpers; JSON is data, never sourced or evaluated as shell code.
# Bash 3.2 treats declared empty arrays as unset under nounset. Initialize state
# explicitly and check command failures instead of relying on errexit/nounset.
set -o pipefail
export LC_ALL=C
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/build_support/common.sh"
ENV_KEYS=(
    PATH CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH LIBRARY_PATH LD_LIBRARY_PATH
    LOADEDMODULES MODULEPATH CUDA_HOME CUDA_PATH NVHPC I_MPI_ROOT OPAL_PREFIX
    MPICH_CC MPICH_CXX MPICH_FC OMPI_CC OMPI_CXX OMPI_FC I_MPI_CC I_MPI_CXX
    I_MPI_F77 I_MPI_F90 TACC_CUDA_INC TACC_CUDA_LIB
)
TOOL_NAMES=(cc cxx fc mpicc mpicxx mpifc nvcc make bash readelf mpip_python)

source_manifest() {
    local output=$1 listing="$1.paths.$$" path
    : > "$listing" || return 1
    printf '%s\0' "$ROOT/install.sh" >> "$listing"
    for path in installer runtime src docs build_support third_party_packages; do
        [[ -z $(find "$ROOT/$path" -type l -print -quit) ]] || {
            error 'source symlinks are not supported'
            return 1
        }
        find "$ROOT/$path" -type f ! -path '*/binary_data_collectors/*' ! -path '*/__pycache__/*' \
            ! -name '*.o' ! -name '*.pyc' -print0 >> "$listing" || return 1
    done
    while IFS= read -r path; do
        printf '%s\0' "$ROOT/$path" >> "$listing"
    done < <(jq -r '.packages[].archive' "$ROOT/third_party_packages/packages.json")
    record_files "$ROOT" "$output" < "$listing" || return 1
    rm -f -- "$listing"
}

artifact_manifest() {
    [[ -z $(find "$1" -type l -print -quit) ]] || {
        error 'staged symlinks are not supported'
        return 1
    }
    find "$1" -type f -print0 | record_files "$1" "$2"
}

external_manifest() {
    local output=$1 include library cuda path listing="$1.paths.$$"
    include=$(jq -r .nvml_include "$CONFIG")
    library=$(jq -r .nvml_lib "$CONFIG")
    cuda=$(jq -r .cuda "$CONFIG")
    : > "$listing"
    if [[ -n $include && -f $include/nvml.h ]]; then
        printf '%s\0' "$include/nvml.h" >> "$listing"
    fi
    if [[ -n $library ]]; then
        for path in "$library"/libnvidia-ml.so*; do
            [[ ! -f $path ]] || printf '%s\0' "$path" >> "$listing"
        done
    fi
    if [[ -n $cuda ]]; then
        for path in "$cuda/version.json" "$cuda/version.txt"; do
            [[ ! -f $path ]] || printf '%s\0' "$path" >> "$listing"
        done
    fi
    # Explicit library symlinks are recorded under their invocation path, hashing the target.
    local records="$output.records.$$" hash
    : > "$records"
    while IFS= read -r -d '' path; do
        hash=$(hash_file "$path") || return 1
        printf '%s\0%s\0' "$path" "$hash" >> "$records"
    done < "$listing"
    # Keep empty values and the last pair intact with jq 1.5 as well as 1.6+.
    printf '\001' >> "$records" || return 1
    jq -Rs 'split("\u0000") | .[0:-1] as $a | reduce range(0; $a|length; 2) as $i ({}; .[$a[$i]]=$a[$i+1])' "$records" > "$output" || return 1
    rm -f -- "$records" "$listing"
}

environment_manifest() {
    local output=$1 key records="$1.records.$$"
    : > "$records"
    for key in "${ENV_KEYS[@]}"; do
        if [[ ${!key+x} ]]; then
            printf '%s\0%s\0' "$key" "${!key}" >> "$records"
        else
            printf '%s\0' "$key" >> "$records"
            printf '\001\0' >> "$records"
        fi
    done
    # Keep empty values and the last pair intact with jq 1.5 as well as 1.6+.
    printf '\001' >> "$records" || return 1
    jq -Rs 'split("\u0000") | .[0:-1] as $a | reduce range(0; $a|length; 2) as $i ({}; .[$a[$i]]= (if $a[$i+1]=="\u0001" then null else $a[$i+1] end))' "$records" > "$output" || return 1
    rm -f -- "$records"
}

gpu_flags() {
    local include library
    include=$(jq -r .nvml_include "$CONFIG")
    library=$(jq -r .nvml_lib "$CONFIG")
    GPU_ARGS=()
    [[ -z $include ]] || GPU_ARGS+=(-I "$include")
    [[ -z $library ]] || GPU_ARGS+=(-L "$library" "-Wl,-rpath,$library")
    GPU_ARGS+=(-lnvidia-ml)
}

collector() {
    local name=$1 stage=$2 args=() dest="$2/bin/binary_data_collectors/data_collect_$1"
    mkdir -p -- "${dest%/*}" || return 1
    flags cppflags || return 1
    args=("${WORDS[@]}")
    flags cflags || return 1
    args+=("${WORDS[@]}" "$ROOT/src/C_data_collectors_src/data_collect_$name.c")
    flags ldflags || return 1
    args+=("${WORDS[@]}")
    if [[ $name == gpu ]]; then
        gpu_flags
        args+=("${GPU_ARGS[@]}")
    fi
    args+=(-o "$dest")
    run "$WORK" "$WORK/logs" "collector-$name" 60 "$(tool_path cc)" "${args[@]}" || return 1
    check_elf "$dest"
}

inspect_stage() {
    local stage=$1 label=$2 path index=0 magic
    while IFS= read -r -d '' path; do
        magic=$(od -An -tx1 -N4 "$path") || return 1
        if [[ ${magic// /} == 7f454c46 ]]; then
            check_elf "$path" || return 1
            run "$WORK" "$WORK/logs" "$label-elf-$index" 60 "$(tool_path readelf)" -h -d "$path" || return 1
        fi
        ((index++))
    done < <(find "$stage" -type f -print0)
    return 0
}

# Only the boundary lives here; package policies and commands belong to the child.
third_party_build() {
    local name=$1 stage=$2 package key directory request result path hash rc=0
    package=$(jq -r --arg k "$name" '.modules[$k].third_party_package' "$REGISTRY") || return 1
    while IFS= read -r key; do need "$key" || return 1; done < <(jq -r --arg k "$package" '.packages[$k].tools[]' "$ROOT/third_party_packages/packages.json")
    directory="$WORK/third_party_packages/$package"
    mkdir -p -- "${directory%/*}" || return 1
    request="$directory-request.json"
    jq -n --arg package "$package" --slurpfile config "$CONFIG" --slurpfile tools "$TOOLS" \
        '{schema:"remora-third-party-request/v1",package:$package,config:($config[0] | .tools += ($tools[0]|map_values(.path))),tools:$tools[0]}' > "$request" || return 1
    if [[ $PHASE == build ]]; then
        json_update "$request" --arg name "$name" --slurpfile plan "$PLAN" '.expected_result=$plan[0].third_party_packages[$name]' || return 1
    fi
    progress "Delegating $name to third-party installer; receipts: $directory"
    run "$WORK" "$WORK/logs" "$name-installer" 900 "$(tool_path bash)" "$ROOT/third_party_packages/install.sh" \
        "$([[ $PHASE == configure ]] && printf check || printf build)" --package "$package" --request "$request" --build-dir "$directory" --progress-fd 3 3>&1 || rc=$?
    result="$directory/result.json"
    if [[ -s $result ]]; then
        json_update "$THIRD_PARTY_RESULTS" --arg name "$name" --slurpfile receipt "$result" '.[$name]=$receipt[0]' || return 1
    fi
    if ((rc)); then
        if [[ -s $result ]]; then LAST_ERROR=$(jq -r '.reason // "third-party installer failed"' "$result"); fi
        return 1
    fi
    jq -e --arg package "$package" '.schema=="remora-third-party-result/v1" and .package==$package and .status=="BUILT" and .runtime_qualification=="NOT_RUN" and (.artifacts|type)=="object"' "$result" > /dev/null || {
        error 'invalid dependency receipt'
        return 1
    }
    if [[ $PHASE == build ]]; then
        jq -e --arg name "$name" --slurpfile actual "$result" \
            '.third_party_packages[$name] as $expected | $actual[0] | .archive==$expected.archive and .tools==$expected.tools and .requested_flags==$expected.requested_flags and .effective_flags==$expected.effective_flags and (.optional_artifacts // [])==($expected.optional_artifacts // []) and (.fortran_f08 // null)==($expected.fortran_f08 // null)' "$PLAN" > /dev/null || {
            error 'dependency inputs or flag capabilities changed; configure again'
            return 1
        }
    fi
    dependency_artifact_contract "$result" "$ROOT/third_party_packages/packages.json" "$package" || {
        error 'dependency artifact contract mismatch'
        return 1
    }
    while IFS= read -r path; do
        hash=$(jq -r --arg path "$path" '.artifacts[$path]' "$result")
        [[ -f $directory/stage/$path && ! -L $directory/stage/$path && $(hash_file "$directory/stage/$path") == "$hash" ]] || {
            error "dependency artifact changed: $path"
            return 1
        }
        mkdir -p -- "$stage/${path%/*}" && cp -p -- "$directory/stage/$path" "$stage/$path" || return 1
    done < <(jq -r '.artifacts|keys[]' "$result")
}
