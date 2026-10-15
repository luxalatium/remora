#!/usr/bin/env bash
# mpiP 3.4.1 compatibility and upstream build details belong here.
# Preserve explicit debug choices such as -g0 instead of appending -g after them.
mpi_debug_default() {
    local language=$1 compiler=$2 word
    flags "$language" || return 1
    for word in "${WORDS[@]}"; do [[ $word != -g* ]] || return 0; done
    tp_default_flag "$language" "$compiler" -g
}

recipe_prepare() {
    local description value
    run "$WORK" "$WORK/logs" mpi-description 60 "$(tool_path mpicc)" -v || return 1
    description=$(jq -r '.mpicc|.version+" "+(.backend//"")' "$TOOLS")
    description+=$(cat "$WORK/logs/mpi-description.stdout" "$WORK/logs/mpi-description.stderr")
    printf '%s' "$description" | grep -Eiq 'intel(\(r\))? mpi|mvapich' || {
        error 'legacy statistics backend supports Intel MPI/MVAPICH2 only'
        return 1
    }
    value=$(jq -r .mpip_python.version "$TOOLS")
    [[ $value == *'Python 2.'* ]] || {
        error 'bundled mpiP 3.4.1 requires Python 2'
        return 1
    }
    compile_probe mpi-c "$(tool_path mpicc)" $'#include <mpi.h>\nint main(int n,char**v){MPI_Init(&n,&v);MPI_Finalize();}' .c cflags || return 1
    compile_probe mpi-fortran "$(tool_path mpifc)" $'      program probe\n      include '\''mpif.h'\''
      integer ierr
      call MPI_Init(ierr)
      call MPI_Finalize(ierr)
      end' .f fflags || return 1
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
    local python defs line cflags fflags cppflags ldflags build_env=()
    python=$(shell_quote "$(tool_path mpip_python)")
    python=${python//\$/\$\$}
    python=${python//#/\\#}
    defs="$SOURCE/Defs.mak.in"
    [[ -f $defs ]] || {
        error 'mpiP Defs.mak.in missing'
        return 1
    }
    while IFS= read -r line || [[ -n $line ]]; do
        [[ $line != 'PYTHON  = python' ]] || line="PYTHON  = $python"
        printf '%s\n' "$line"
    done < "$defs" > "$defs.new" || return 1
    mv -- "$defs.new" "$defs" || return 1
    cflags=$(jq -r .flags.cflags "$CONFIG")
    fflags=$(jq -r .flags.fflags "$CONFIG")
    cppflags=$(jq -r .flags.cppflags "$CONFIG")
    ldflags=$(jq -r .flags.ldflags "$CONFIG")
    build_env=(env -u MAKEFLAGS -u MFLAGS -u MAKEOVERRIDES -u CONFIG_SITE "CC=$(tool_path mpicc)" "F77=$(tool_path mpifc)" "CFLAGS=$cflags" "FFLAGS=$fflags" "CPPFLAGS=$cppflags" "LDFLAGS=$ldflags")
    run "$SOURCE" "$WORK/logs" mpip-configure 180 "${build_env[@]}" "$(tool_path bash)" ./configure --disable-demangling --disable-bfd --disable-libunwind "--prefix=$STAGE" || return 1
    cflags=$(tp_make_flags "$cflags") || return 1
    fflags=$(tp_make_flags "$fflags") || return 1
    ldflags=$(tp_make_flags "$ldflags") || return 1
    run "$SOURCE" "$WORK/logs" mpip-make 240 "${build_env[@]}" "$(tool_path make)" -j1 "CFLAGS=$cflags" "FFLAGS=$fflags" "LDFLAGS=$ldflags" libmpiP.a || return 1
    run "$SOURCE" "$WORK/logs" mpip-shared 240 "${build_env[@]}" "$(tool_path make)" -j1 "CFLAGS=$cflags" "FFLAGS=$fflags" "LDFLAGS=$ldflags" shared || return 1
    mkdir -p -- "$STAGE/lib" && cp -p -- "$SOURCE/libmpiP.a" "$SOURCE/libmpiP.so" "$STAGE/lib/"
}
