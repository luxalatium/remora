#!/usr/bin/env bash
# Optional native F08 adapters; all descriptors/handles remain in Fortran.
mpi_f08_unavailable() {
    printf '[third-party] mpi_f08 adapter unavailable: %s (legacy MPI remains available)\n' "$1"
    json_update "$RESULT_FILE" --arg reason "$1" \
        '.fortran_f08={status:"UNAVAILABLE",reason:$reason,runtime:"NOT_RUN"} | .optional_artifacts=[]'
}

mpi_f08_build() {
    local directory="$WORK/f08" support="$ROOT/third_party_packages/compatibility/mpiP-f08"
    local compiler nm_tool objcopy_tool ar_tool line symbol kind remainder routine
    local target defined abi selected= name lower_name normalized declaration
    local subarrays=unknown choice expression order=(address)
    local fortran_flags=() c_flags=() link_flags=()

    compiler=$(tool_path mpifc)
    nm_tool=$(command -v nm) || { mpi_f08_unavailable 'nm is required for verified symbol mapping'; return $?; }
    objcopy_tool=$(command -v objcopy) || { mpi_f08_unavailable 'objcopy is required for verified symbol mapping'; return $?; }
    ar_tool=$(command -v ar) || { mpi_f08_unavailable 'ar is required for the static adapter'; return $?; }
    mkdir -p -- "$directory" || return 1
    flags fflags || return 1
    fortran_flags=("${WORDS[@]}")
    flags cflags || return 1
    c_flags=("${WORDS[@]}")
    flags ldflags || return 1
    link_flags=("${WORDS[@]}")

    cat > "$directory/probe.f90" <<'FORTRAN'
subroutine mpip_probe_f08()
  use iso_c_binding
  use mpi_f08
  implicit none
  interface
    subroutine mpip_c_integer_probe(value) bind(C)
      import c_int
      integer(c_int) :: value
    end subroutine
  end interface
  integer :: err,provided
  err=0
  call mpip_c_integer_probe(err)
  call MPI_Init(err)
  call MPI_Init_thread(MPI_THREAD_FUNNELED,provided,err)
  call MPI_Pcontrol(0)
  call MPI_Finalize(err)
end subroutine
FORTRAN
    if ! run "$directory" "$WORK/logs" mpip-f08-probe 60 "$compiler" \
        "${fortran_flags[@]}" -c probe.f90 -o probe.o; then
        mpi_f08_unavailable 'configured compiler rejected mpi_f08/default INTEGER ABI'
        return $?
    fi
    "$nm_tool" -P -u "$directory/probe.o" > "$directory/lifecycle-symbols.txt" || return 1
    : > "$directory/symbols.h"
    for name in INIT INIT_THREAD FINALIZE PCONTROL; do
        target=
        lower_name=$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')
        while read -r symbol kind remainder; do
            normalized=$(printf '%s' "$symbol" | tr '[:upper:]' '[:lower:]')
            [[ $normalized == mpi_${lower_name}_f08* ]] || continue
            [[ $symbol =~ ^[a-zA-Z_][a-zA-Z_0-9]*$ && -z $target ]] || return 1
            target=$symbol
        done < "$directory/lifecycle-symbols.txt"
        if [[ -z $target ]]; then
            error "unrecognized mpi_f08 lifecycle symbol for $name; refusing an ABI guess"
            return 1
        fi
        printf '#define MPIP_F08_%s %s\n' "$name" "$target" >> "$directory/symbols.h"
    done

    # Some compilers accept assumed-rank actual arguments even when the MPI
    # entry itself takes addresses. Compiler acceptance alone is not an ABI
    # test: inspect the module's declared subarray support and actual MPI symbols.
    for choice in yes no; do
        expression=MPI_SUBARRAYS_SUPPORTED
        [[ $choice == yes ]] || expression='.not.MPI_SUBARRAYS_SUPPORTED'
        printf 'subroutine mpip_subarrays_probe()\n  use mpi_f08\n  integer, parameter :: guard_kind=merge(kind(0),-999,%s)\n  integer(kind=guard_kind) :: guard\nend subroutine\n' \
            "$expression" > "$directory/subarrays-$choice.f90"
        if run "$directory" "$WORK/logs" "mpip-f08-subarrays-$choice" 60 "$compiler" \
            "${fortran_flags[@]}" -c "subarrays-$choice.f90" -o "subarrays-$choice.o"; then
            subarrays=$choice
            break
        fi
    done
    [[ $subarrays != unknown ]] || {
        mpi_f08_unavailable 'cannot establish MPI_SUBARRAYS_SUPPORTED from the configured module'
        return $?
    }
    [[ $subarrays != yes ]] || order=(descriptor address)
    for abi in "${order[@]}"; do
        if [[ $abi == descriptor ]]; then declaration='..'; else declaration='*'; fi
        : > "$directory/native.F90"
        while IFS= read -r line || [[ -n $line ]]; do
            printf '%s\n' "${line//@BUFFER@/$declaration}"
        done < "$support/native.F90.in" > "$directory/native.F90"
        if run "$directory" "$WORK/logs" "mpip-f08-$abi" 120 "$compiler" \
            "${fortran_flags[@]}" -c native.F90 -o native.o; then
            selected=$abi
            break
        fi
    done
    if [[ -z $selected ]]; then
        mpi_f08_unavailable 'native F08 adapter compiler probes failed'
        return $?
    fi
    "$nm_tool" -P -u "$directory/native.o" > "$directory/pmpi-symbols.txt" || return 1
    "$nm_tool" -P --defined-only "$directory/native.o" > "$directory/native-symbols.txt" || return 1
    # The module's PMPI binding may use an internal prefix (e.g. Intel pmpir_).
    # Compile the corresponding MPI calls too; never infer an MPI entry name by
    # dropping characters from a vendor's profiling symbol.
    while IFS= read -r line || [[ -n $line ]]; do
        printf '%s\n' "${line//PMPI_/MPI_}"
    done < "$directory/native.F90" > "$directory/native-mpi-probe.F90"
    run "$directory" "$WORK/logs" mpip-f08-mpi-symbol-probe 120 "$compiler" \
        "${fortran_flags[@]}" -c native-mpi-probe.F90 -o native-mpi-probe.o || return 1
    "$nm_tool" -P -u "$directory/native-mpi-probe.o" > "$directory/mpi-symbols.txt" || return 1
    if [[ $selected == address ]]; then
        while read -r symbol kind remainder; do
            normalized=$(printf '%s' "$symbol" | tr '[:upper:]' '[:lower:]')
            [[ $normalized == mpi_*_f08ts_* ]] || continue
            cp -p -- "$directory/native.o" "$directory/native-address.o" || return 1
            while IFS= read -r line || [[ -n $line ]]; do
                printf '%s\n' "${line//@BUFFER@/..}"
            done < "$support/native.F90.in" > "$directory/native.F90"
            run "$directory" "$WORK/logs" mpip-f08-ts-descriptor 120 "$compiler" \
                "${fortran_flags[@]}" -c native.F90 -o native.o || return 1
            selected=descriptor
            "$nm_tool" -P -u "$directory/native.o" > "$directory/pmpi-symbols.txt" || return 1
            "$nm_tool" -P --defined-only "$directory/native.o" > "$directory/native-symbols.txt" || return 1
            break
        done < "$directory/mpi-symbols.txt"
    fi
    : > "$directory/renames.txt"
    while IFS= read -r routine; do
        target= defined=
        while read -r symbol kind remainder; do
            normalized=$(printf '%s' "$symbol" | tr '[:upper:]' '[:lower:]')
            [[ $normalized == mpi_${routine}_f08* ]] || continue
            [[ $symbol =~ ^[a-zA-Z_][a-zA-Z_0-9]*$ && -z $target ]] || return 1
            target=$symbol
        done < "$directory/mpi-symbols.txt"
        while read -r symbol kind remainder; do
            normalized=$(printf '%s' "$symbol" | tr '[:upper:]' '[:lower:]')
            [[ $normalized == mpip_f08_${routine} ||
                $normalized == mpip_f08_${routine}_ ||
                $normalized == mpip_f08_${routine}__ ]] || continue
            [[ $symbol =~ ^[a-zA-Z_][a-zA-Z_0-9]*$ && -z $defined ]] || return 1
            defined=$symbol
        done < "$directory/native-symbols.txt"
        [[ -n $target && -n $defined ]] || {
            error "unrecognized native F08 mapping for $routine; refusing an ABI guess"
            return 1
        }
        printf '%s %s\n' "$defined" "$target" >> "$directory/renames.txt"
    done < "$support/routines.txt"
    run "$directory" "$WORK/logs" mpip-f08-symbol-map 60 "$objcopy_tool" \
        "--redefine-syms=$directory/renames.txt" native.o f08.o || return 1
    run "$directory" "$WORK/logs" mpip-f08-stats 120 "$(tool_path mpicc)" \
        "${c_flags[@]}" -I"$SOURCE" -c "$support/stats.c" -o stats.o || return 1
    run "$directory" "$WORK/logs" mpip-f08-lifecycle 120 "$(tool_path mpicc)" \
        "${c_flags[@]}" -I"$directory" -c "$support/lifecycle.c" -o lifecycle.o || return 1

    # Keep one set of mpiP globals and lifecycle symbols in each library.
    # A dependent sidecar shared library can resolve MPI_Init to the MPI vendor
    # while resolving statistics to mpiP, leaving the latter uninitialized.
    run "$directory" "$WORK/logs" mpip-f08-shared 120 "$compiler" \
        "${fortran_flags[@]}" -shared -o libmpiP.so f08.o stats.o lifecycle.o \
        -Wl,--whole-archive "$SOURCE/libmpiP.a" -Wl,--no-whole-archive \
        "${link_flags[@]}" -ldl -lm -lpthread -lrt || return 1
    cp -p -- "$SOURCE/libmpiP.a" "$directory/libmpiP-f08.a" || return 1
    run "$directory" "$WORK/logs" mpip-f08-static 60 "$ar_tool" \
        r libmpiP-f08.a f08.o stats.o lifecycle.o || return 1
    cp -p -- "$directory/libmpiP-f08.a" "$directory/libmpiP.so" "$STAGE/lib/" || return 1
    json_update "$RESULT_FILE" --arg abi "$selected" --arg subarrays "$subarrays" \
        --arg nm "$nm_tool" --arg nm_hash "$(hash_file "$nm_tool")" \
        --arg objcopy "$objcopy_tool" --arg objcopy_hash "$(hash_file "$objcopy_tool")" \
        --arg ar "$ar_tool" --arg ar_hash "$(hash_file "$ar_tool")" \
        --rawfile mapping "$directory/renames.txt" \
        '.fortran_f08={status:"BUILT",buffer_abi:$abi,mpi_subarrays_supported:($subarrays=="yes"),symbol_mapping:$mapping,build_tools:{nm:{path:$nm,sha256:$nm_hash},objcopy:{path:$objcopy,sha256:$objcopy_hash},ar:{path:$ar,sha256:$ar_hash}},static_library:"libmpiP-f08.a",static_linker:"configured MPI Fortran wrapper",runtime:"NOT_RUN",coverage:"listed native adapter routines plus scalar lifecycle/Pcontrol; other F08 APIs are not qualified"} | .optional_artifacts=["lib/libmpiP-f08.a"]' || return 1
    printf '[third-party] Built native mpi_f08 adapters (%s buffers; runtime NOT_RUN)\n' "$selected"
}
