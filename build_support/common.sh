#!/usr/bin/env bash
# Shared Bash 3.2 build primitives; no package recipe or module policy.
set -o pipefail
export LC_ALL=C
LAST_ERROR='' ACTIVE_PID='' ACTIVE_RECEIPT='' OWN_LOCK='' RESULT_FILE='' PHASE=''
WORDS=()

progress() {
    printf '[%s] %s\n' "${PHASE:-installer}" "$*"
}

error() {
    LAST_ERROR=$*
    return 1
}

fatal() {
    LAST_ERROR=$*
    progress "FAILED: $LAST_ERROR"
    printf 'ERROR: %s\n' "$LAST_ERROR" >&2
    exit 2
}

json_update() {
    local file=$1
    shift
    jq "$@" "$file" > "$file.tmp.$$" && mv -f -- "$file.tmp.$$" "$file"
}

hash_file() {
    local value
    if command -v sha256sum > /dev/null 2>&1; then
        value=$(sha256sum < "$1") || return 1
    else
        value=$(shasum -a 256 < "$1") || return 1
    fi
    printf '%s' "${value%% *}"
}

hash_stream() {
    local value
    if command -v sha256sum > /dev/null 2>&1; then
        value=$(sha256sum) || return 1
    else
        value=$(shasum -a 256) || return 1
    fi
    printf '%s' "${value%% *}"
}

json_hash() {
    jq -cSj "$2" "$1" | hash_stream
}

json_equal() {
    [[ $(jq -cS . "$1") == "$(jq -cS . "$2")" ]]
}

absolute() {
    local path=$1 part old_ifs=$IFS result='' parent suffix parts=() stack=()
    [[ $path != *$'\n'* && $path != *$'\r'* ]] || return 1
    [[ $path == /* ]] || path="$PWD/$path"
    IFS=/ read -r -a parts <<< "$path"
    IFS=$old_ifs
    for part in "${parts[@]}"; do
        case $part in
            '' | .) ;;
            ..)
                ((${#stack[@]})) && unset 'stack[${#stack[@]}-1]'
                ;;
            *)
                stack[${#stack[@]}]=$part
                ;;
        esac
    done
    for part in "${stack[@]}"; do
        result="$result/$part"
    done
    [[ -n $result ]] || {
        printf /
        return 0
    }
    parent=${result%/*}
    suffix=${result##*/}
    [[ -n $parent ]] || parent=/
    while [[ ! -d $parent ]]; do
        [[ ! -e $parent && ! -L $parent ]] || return 1
        suffix="${parent##*/}/$suffix"
        parent=${parent%/*}
        [[ -n $parent ]] || parent=/
    done
    parent=$(cd -- "$parent" && pwd -P) || return 1
    printf '%s/%s' "${parent%/}" "$suffix"
}

split_words() {
    local text=$1 state=plain word='' started=0 c next i
    WORDS=()
    for ((i = 0; i < ${#text}; i++)); do
        c=${text:i:1}
        case $state in
            single)
                if [[ $c == "'" ]]; then
                    state=plain
                else
                    word+=$c
                fi
                ;;
            double)
                case $c in
                    '"')
                        state=plain
                        ;;
                    '\')
                        ((i++))
                        ((i < ${#text})) || {
                            error 'unfinished flag escape'
                            return 1
                        }
                        next=${text:i:1}
                        case $next in
                            '"' | '\')
                                word+=$next
                                ;;
                            *)
                                word+="\$next"
                                ;;
                        esac
                        ;;
                    *)
                        word+=$c
                        ;;
                esac
                ;;
            plain)
                case $c in
                    "'")
                        state=single
                        started=1
                        ;;
                    '"')
                        state=double
                        started=1
                        ;;
                    '\')
                        ((i++))
                        ((i < ${#text})) || {
                            error 'unfinished flag escape'
                            return 1
                        }
                        word+=${text:i:1}
                        started=1
                        ;;
                    ' ' | $'\t' | $'\n' | $'\r')
                        if ((started)); then
                            WORDS[${#WORDS[@]}]=$word
                            word=''
                            started=0
                        fi
                        ;;
                    *)
                        word+=$c
                        started=1
                        ;;
                esac
                ;;
        esac
    done
    [[ $state == plain ]] || {
        error 'unclosed flag quote'
        return 1
    }
    if ((started)); then
        WORDS[${#WORDS[@]}]=$word
    fi
}

shell_quote() {
    local value=$1
    value=${value//\'/\'\\\'\'}
    printf "'%s'" "$value"
}

flags() {
    local value
    value=$(jq -r --arg k "$1" '.flags[$k]' "$CONFIG") || return 1
    split_words "$value"
}

cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    if [[ -n $ACTIVE_PID ]]; then
        terminate_active
    fi
    if [[ -n $RESULT_FILE && -f $RESULT_FILE && $rc -ne 0 ]]; then
        json_update "$RESULT_FILE" --arg reason "${LAST_ERROR:-interrupted or incomplete operation}" \
            '.status="FAIL" | .reason=$reason | if .modules then .modules |= with_entries(if .value=="BUILDING" then .value="FAIL" else . end) else . end' || true
    fi
    if [[ -n $OWN_LOCK ]]; then
        rmdir -- "$OWN_LOCK" 2> /dev/null || true
    fi
    exit "$rc"
}

terminate_active() {
    local pid=$ACTIVE_PID i
    [[ -n $pid ]] || return 0
    kill -TERM -- "-$pid" 2> /dev/null || kill -TERM "$pid" 2> /dev/null || true
    for ((i = 0; i < 30; i++)); do
        if ! kill -0 -- "-$pid" 2> /dev/null && ! kill -0 "$pid" 2> /dev/null; then
            break
        fi
        sleep 0.1
    done
    kill -KILL -- "-$pid" 2> /dev/null || kill -KILL "$pid" 2> /dev/null || true
    wait "$pid" 2> /dev/null || true
    ACTIVE_PID=''
}

interrupted() {
    LAST_ERROR='installation interrupted; retained work must be reviewed'
    progress "INTERRUPTED: $LAST_ERROR"
    if [[ -n $ACTIVE_PID ]]; then
        terminate_active
    fi
    if [[ -n $ACTIVE_RECEIPT ]]; then
        json_update "$ACTIVE_RECEIPT" '.exit_code=130' || true
    fi
    printf 'ERROR: %s\n' "$LAST_ERROR" >&2
    exit 130
}

run() {
    local directory=$1 logs=$2 label=$3 limit=$4 rc=0 file
    shift 4
    file="$logs/$label"
    mkdir -p -- "$logs" || {
        error "cannot create logs: $logs"
        return 1
    }
    [[ ! -e $file.json && ! -e $file.stdout && ! -e $file.stderr ]] || {
        error "refusing to overwrite logs: $file"
        return 1
    }
    ACTIVE_RECEIPT="$file.json"
    jq -n --arg cwd "$directory" --argjson timeout "$limit" --args \
        '{cwd:$cwd, timeout_seconds:$timeout, command:$ARGS.positional, exit_code:null}' -- "$@" > "$file.json" || return 1
    [[ -n $SETSID && -n $TIMEOUT ]] || {
        error 'Linux setsid and GNU timeout are required'
        return 1
    }
    case $label in
        *-configure | *-make | *-shared | *-installer)
            progress "Running $label (timeout ${limit}s; logs: $file.stdout, $file.stderr)"
            ;;
    esac
    INTERRUPT_PENDING=0
    trap 'INTERRUPT_PENDING=1' INT TERM
    (cd -- "$directory" && exec "$SETSID" "$TIMEOUT" --signal=TERM --kill-after=3 "$limit" "$@") \
        > "$file.stdout" 2> "$file.stderr" &
    ACTIVE_PID=$!
    ACTIVE_RECEIPT="$file.json"
    trap interrupted INT TERM
    ((INTERRUPT_PENDING == 0)) || interrupted
    wait "$ACTIVE_PID" || rc=$?
    ACTIVE_PID=''
    ACTIVE_RECEIPT=''
    json_update "$file.json" --argjson rc "$rc" '.exit_code=$rc' || return 1
    if ((rc)); then
        progress "Command $label failed (exit $rc); inspect $logs"
        error "$label failed (exit $rc); inspect $logs"
        return 1
    fi
}

record_files() {
    local base=$1 output=$2 path relative hash records="$output.records.$$"
    : > "$records" || return 1
    while IFS= read -r -d '' path; do
        [[ -f $path && ! -L $path ]] || {
            error "non-regular manifest source: $path"
            return 1
        }
        hash=$(hash_file "$path") || return 1
        if [[ -n $base ]]; then
            relative=${path#"$base"/}
        else
            relative=$path
        fi
        printf '%s\0%s\0' "$relative" "$hash" >> "$records" || return 1
    done
    # jq 1.5 drops the trailing empty split field. A nonempty sentinel makes
    # removing the final field deterministic across supported jq versions.
    printf '\001' >> "$records" || return 1
    jq -Rs 'split("\u0000") | .[0:-1] as $a | reduce range(0; $a|length; 2) as $i ({}; .[$a[$i]]=$a[$i+1])' \
        "$records" > "$output" || return 1
    rm -f -- "$records"
}

platform_check() {
    [[ $SYSTEM == Linux ]] || error 'Installation probes require native Linux; runtime remains NOT_RUN'
}

need() {
    local name=$1 requested path version hash target backend first backend_path saved index option
    saved=$(jq -r --arg k "$name" '.[$k] // empty' "$FAILURES")
    [[ -z $saved ]] || {
        error "$saved"
        return 1
    }
    if jq -e --arg k "$name" 'has($k)' "$TOOLS" > /dev/null; then
        return 0
    fi
    requested=$(jq -r --arg k "$name" '.tools[$k]' "$CONFIG")
    path=$(command -v -- "$requested") || path=''
    if [[ ! -f $path || ! -x $path ]]; then
        error "$name not found: $requested"
        json_update "$FAILURES" --arg k "$name" --arg reason "$LAST_ERROR" '.[$k]=$reason' || return 1
        return 1
    fi
    path=$(absolute "$path") || return 1
    if ! run "$WORK" "$WORK/logs" "$name-version" 60 "$path" --version; then
        json_update "$FAILURES" --arg k "$name" --arg reason "$LAST_ERROR" '.[$k]=$reason' || return 1
        return 1
    fi
    version=$(cat "$WORK/logs/$name-version.stdout" "$WORK/logs/$name-version.stderr")
    hash=$(hash_file "$path") || return 1
    json_update "$TOOLS" --arg k "$name" --arg path "$path" --arg hash "$hash" --arg version "${version:-unknown}" \
        '.[$k]={path:$path,sha256:$hash,version:$version}' || return 1
    case $name in
        cc | cxx | fc | mpicc | mpicxx | mpifc | nvcc)
            target=UNKNOWN
            if run "$WORK" "$WORK/logs" "$name-target" 60 "$path" -dumpmachine; then
                target=$(cat "$WORK/logs/$name-target.stdout")
            fi
            json_update "$TOOLS" --arg k "$name" --arg value "$target" '.[$k].target=$value' || return 1
            ;;
    esac
    case $name in
        mpicc | mpicxx | mpifc)
            index=0
            for option in --showme -show; do
                if run "$WORK" "$WORK/logs" "$name-backend-$index" 60 "$path" "$option"; then
                    backend=$(cat "$WORK/logs/$name-backend-$index.stdout")
                    json_update "$TOOLS" --arg k "$name" --arg value "$backend" '.[$k].backend=$value' || return 1
                    if split_words "$backend" && ((${#WORDS[@]})); then
                        first=${WORDS[0]}
                        backend_path=$(command -v -- "$first") || backend_path=''
                        if [[ -f $backend_path ]]; then
                            backend_path=$(absolute "$backend_path")
                            hash=$(hash_file "$backend_path") || return 1
                            json_update "$TOOLS" --arg k "$name" --arg path "$backend_path" --arg hash "$hash" \
                                '.[$k].backend_executable={path:$path,sha256:$hash}' || return 1
                        fi
                    fi
                    break
                fi
                ((index++))
            done
            ;;
    esac
}

tool_path() {
    jq -r --arg k "$1" '.[$k].path' "$TOOLS"
}

check_elf() {
    local path=$1 bytes=() data native actual
    data=$(od -An -v -tu1 -N20 "$path") || {
        error "cannot read ELF: $path"
        return 1
    }
    data=${data//$'\n'/ }
    read -r -a bytes <<< "$data"
    if ((${#bytes[@]} < 20)) || [[ ${bytes[0]} != 127 || ${bytes[1]} != 69 || ${bytes[2]} != 76 || ${bytes[3]} != 70 ]]; then
        error "missing ELF artifact: $path"
        return 1
    fi
    case $MACHINE in
        x86_64)
            native=62
            ;;
        aarch64)
            native=183
            ;;
        *)
            error "unsupported native architecture: $MACHINE"
            return 1
            ;;
    esac
    case ${bytes[5]} in
        1)
            actual=$((bytes[18] + 256 * bytes[19]))
            ;;
        2)
            actual=$((256 * bytes[18] + bytes[19]))
            ;;
        *)
            error 'invalid ELF byte order'
            return 1
            ;;
    esac
    [[ $actual == "$native" ]] || error "ELF architecture differs from native target: $path"
}

compile_probe() {
    local label=$1 compiler=$2 code=$3 suffix=$4 language=$5 args=() source="$WORK/$1$4" dest="$WORK/$1.probe"
    shift 5
    printf '%s\n' "$code" > "$source" || return 1
    flags cppflags || return 1
    args=("${WORDS[@]}")
    flags "$language" || return 1
    args+=("${WORDS[@]}" "$source")
    flags ldflags || return 1
    args+=("${WORDS[@]}" "$@" -o "$dest")
    run "$WORK" "$WORK/logs" "$label" 60 "$compiler" "${args[@]}" || return 1
    check_elf "$dest"
}

extract_archive() {
    local archive=$1 destination=$2 name type roots=() path
    mkdir -- "$destination" || return 1
    tar -tzf "$archive" > "$destination.list" 2> "$destination.list.stderr" || {
        error 'cannot list dependency archive'
        return 1
    }
    tar -tvzf "$archive" > "$destination.verbose" 2>> "$destination.list.stderr" || return 1
    while IFS= read -r name; do
        case $name in
            /* | .. | ../* | */../* | */..)
                error "unsafe dependency archive member: $name"
                return 1
                ;;
        esac
        [[ -n $name ]] || {
            error 'empty archive member'
            return 1
        }
    done < "$destination.list"
    while IFS= read -r type; do
        case ${type:0:1} in
            - | d) ;;
            *)
                error 'dependency links/special members are not supported'
                return 1
                ;;
        esac
    done < "$destination.verbose"
    tar -xzf "$archive" --no-same-owner --no-same-permissions -C "$destination" || {
        error 'dependency extraction failed'
        return 1
    }
    for path in "$destination"/* "$destination"/.[!.]*; do
        [[ ! -e $path ]] || roots+=("$path")
    done
    ((${#roots[@]} == 1)) && [[ -d ${roots[0]} ]] || {
        error 'dependency archive must have one source directory'
        return 1
    }
    EXTRACTED=${roots[0]}
}

trap cleanup EXIT
trap interrupted INT TERM
