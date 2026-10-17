#!/usr/bin/env bash
# mpiP 3.5 compatibility and upstream build details belong here.
# Preserve explicit debug choices such as -g0 instead of appending -g after them.
mpi_debug_default() {
    local language=$1 compiler=$2 word
    flags "$language" || return 1
    for word in "${WORDS[@]}"; do [[ $word != -g* ]] || return 0; done
    tp_default_flag "$language" "$compiler" -g
}

# Upstream 3.5's symbol probe predates C99. Repair only the extracted copy;
# do not relax compiler diagnostics for the entire dependency.
mpi_fix_fortran_symbol_probe() {
    local line count=0 old='main(){ FF(); return 0; }'
    local replacement='extern void FF(void); int main(void){ FF(); return 0; }'
    local temporary="$WORK/mpip-configure-compatible"
    while IFS= read -r line || [[ -n $line ]]; do
        [[ $line != *"$old"* ]] || count=$((count + 1))
    done < "$SOURCE/configure"
    [[ $count == 1 ]] || {
        error 'unexpected mpiP 3.5 Fortran-symbol probe; refusing compatibility rewrite'
        return 1
    }
    while IFS= read -r line || [[ -n $line ]]; do
        line=${line/$old/$replacement}
        printf '%s\n' "$line"
    done < "$SOURCE/configure" > "$temporary" || return 1
    mv -- "$temporary" "$SOURCE/configure" || return 1
    printf '[third-party] Updated mpiP Fortran-symbol probe for modern C compilers\n'
}

recipe_prepare() {
    local value
    MPIP_PATCH_TOOL=$(command -v patch) || {
        error 'patch is required for the mpiP 3.5 compatibility fixes'
        return 1
    }
    MPIP_PATCH="$ROOT/third_party_packages/patches/mpiP-3.5-fortran-io.patch"
    local expected
    expected=$(jq -r '.patches[0].sha256' "$ROOT/third_party_packages/mpiP-source.json")
    [[ -f $MPIP_PATCH && ! -L $MPIP_PATCH && $(hash_file "$MPIP_PATCH") == "$expected" ]] || {
        error 'mpiP compatibility patch checksum differs from its provenance'
        return 1
    }
    json_update "$RESULT_FILE" --arg path "$MPIP_PATCH" --arg hash "$expected" \
        --arg tool "$MPIP_PATCH_TOOL" --arg tool_hash "$(hash_file "$MPIP_PATCH_TOOL")" \
        '.patches=[{path:$path,sha256:$hash}] | .patch_tool={path:$tool,sha256:$tool_hash}' || return 1
    value=$(jq -r .mpip_python.version "$TOOLS")
    [[ $value == *'Python 3.'* ]] || {
        error 'bundled mpiP 3.5 requires Python 3'
        return 1
    }
    # Verify the actual interpreter, not just a version string or command name.
    run "$WORK" "$WORK/logs" mpip-python3 60 "$(tool_path mpip_python)" -c \
        'import sys; sys.exit(0 if sys.version_info.major == 3 else 1)' || return 1
    compile_probe mpi-c "$(tool_path mpicc)" $'#include <mpi.h>\nint main(int n,char**v){MPI_Init(&n,&v);MPI_Finalize();}' .c cflags || return 1
    compile_probe mpi-fortran "$(tool_path mpifc)" $'      program probe\n      include '\''mpif.h'\''
      integer ierr
      call MPI_Init(ierr)
      call MPI_Finalize(ierr)
      end' .f fflags || return 1
    compile_probe mpi-cxx "$(tool_path mpicxx)" $'#include <mpi.h>\nint main(int n,char**v){MPI_Init(&n,&v);MPI_Finalize();}' .cpp cxxflags || return 1
    tp_base_flags cflags "$(tool_path mpicc)" "$C_PROBE" .c || return 1
    tp_base_flags fflags "$(tool_path mpifc)" "$F_PROBE" .f90 || return 1
    if [[ -z $(jq -r .flags.cflags "$CONFIG") ]]; then tp_default_flag cflags "$(tool_path mpicc)" -O2 || return 1; fi
    if [[ -z $(jq -r .flags.fflags "$CONFIG") ]]; then tp_default_flag fflags "$(tool_path mpifc)" -O2 || return 1; fi
    mpi_debug_default cflags "$(tool_path mpicc)" || return 1
    mpi_debug_default fflags "$(tool_path mpifc)" || return 1
    tp_pic_flag cflags "$(tool_path mpicc)" || return 1
    tp_pic_flag fflags "$(tool_path mpifc)" || return 1
    # Command-line CFLAGS override target-specific additions, so include PIC's macro.
    json_update "$CONFIG" '.flags.cflags += " -DPIC"'
}
recipe_build() {
    local python cflags fflags cppflags ldflags build_env=()
    mpi_fix_fortran_symbol_probe || return 1
    run "$SOURCE" "$WORK/logs" mpip-patch-check 60 "$MPIP_PATCH_TOOL" \
        --dry-run --batch --forward --fuzz=0 -p1 -i "$MPIP_PATCH" || return 1
    run "$SOURCE" "$WORK/logs" mpip-patch 60 "$MPIP_PATCH_TOOL" \
        --batch --forward --fuzz=0 -p1 -i "$MPIP_PATCH" || return 1
    python=$(tp_make_flags "$(shell_quote "$(tool_path mpip_python)")") || return 1
    cflags=$(jq -r .flags.cflags "$CONFIG")
    fflags=$(jq -r .flags.fflags "$CONFIG")
    cppflags=$(jq -r .flags.cppflags "$CONFIG")
    ldflags=$(jq -r .flags.ldflags "$CONFIG")
    build_env=(env -u MAKEFLAGS -u MFLAGS -u MAKEOVERRIDES -u CONFIG_SITE "CC=$(tool_path mpicc)" "CXX=$(tool_path mpicxx)" "ac_cv_prog_HAVE_PYTHON=$(tool_path mpip_python)" "F77=$(tool_path mpifc)" "CFLAGS=$cflags" "FFLAGS=$fflags" "CPPFLAGS=$cppflags" "LDFLAGS=$ldflags")
    run "$SOURCE" "$WORK/logs" mpip-configure 180 "${build_env[@]}" "$(tool_path bash)" ./configure --disable-demangling --disable-bfd --disable-libunwind "--prefix=$STAGE" || return 1
    cflags=$(tp_make_flags "$cflags") || return 1
    fflags=$(tp_make_flags "$fflags") || return 1
    ldflags=$(tp_make_flags "$ldflags") || return 1
    run "$SOURCE" "$WORK/logs" mpip-make 240 "${build_env[@]}" "$(tool_path make)" -j1 "PYTHON=$python" "CFLAGS=$cflags" "FFLAGS=$fflags" "LDFLAGS=$ldflags" libmpiP.a || return 1
    run "$SOURCE" "$WORK/logs" mpip-shared 240 "${build_env[@]}" "$(tool_path make)" -j1 "PYTHON=$python" "CFLAGS=$cflags" "FFLAGS=$fflags" "LDFLAGS=$ldflags" shared || return 1
    mkdir -p -- "$STAGE/lib" && cp -p -- "$SOURCE/libmpiP.a" "$SOURCE/libmpiP.so" "$STAGE/lib/"
    source "$ROOT/third_party_packages/packages/mpip-f08.sh" || return 1
    mpi_f08_build
}
