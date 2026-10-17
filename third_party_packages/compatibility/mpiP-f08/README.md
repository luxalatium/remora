# Native mpi_f08 compatibility

These adapters are compiled by the configured MPI Fortran wrapper, using its
own `mpi_f08` module and `PMPI` procedures. Buffer descriptors, derived handles,
status layouts and CHARACTER arguments remain in native Fortran. No F08 choice
buffer is cast to a legacy Fortran or C buffer layout.

The Bash recipe probes default INTEGER interoperability and lifecycle symbols,
then compiles assumed-rank or assumed-size buffers against the actual module.
It maps the compiled native definitions to the corresponding compiler-selected
MPI symbols. Ambiguous or unknown mappings fail rather than guessing a vendor
ABI. Lack of compiler/module support is explicitly `UNAVAILABLE`; `BUILT` alone
keeps runtime `NOT_RUN`. Build logs retain rejected capability probes.

The C boundary uses mpiP's existing per-thread nesting depth to avoid counting
both a native F08 call and any internally invoked C wrapper. Timing, call IDs,
requested payload bytes and reports use the same mpiP statistics machinery.
Scalar Init, Init_thread, Finalize and Pcontrol adapters handle optional ierror
and enter the same lifecycle as C callers. Native procedures are recursive so
their local profiling tokens do not acquire compiler-specific static storage.
The shared library and the separate F08 static library each
contain one combined set of mpiP globals; a separate dependent shared sidecar
can resolve initialization to the MPI vendor and statistics to mpiP incorrectly.

`libmpiP.a` retains the original C/legacy Fortran archive. F08 applications use
`libmpiP-f08.a` and the configured MPI Fortran wrapper for the final link. Do not
force all F08 objects into a C link with `--whole-archive` without the matching
Fortran MPI/runtime dependencies. The combined `libmpiP.so` supports preload for
the qualified bindings with their matching compiler/MPI runtime environment.

Coverage is limited to the 30 procedures in `routines.txt` plus the scalar
lifecycle/Pcontrol procedures. Other F08 routines and callbacks are not claimed
as profiled. Applications must use the same MPI/Fortran ABI as the build.
Requested I/O bytes are count times datatype size, not physical filesystem
traffic or actual bytes from short/error transfers. Ignored send-count/type
semantics for in-place collectives beyond the qualified Allreduce cases retain
the separately documented qualification gap. No overhead claim is made.

The original mpiP 3.5 release archive remains unchanged. These compatibility
sources are maintained separately from its existing sentinel/I/O patch.

The qualification fixture provides an explicit NVHPC24.7 workaround for the
site module's rejected scalar MPI_IN_PLACE overload: a rank-one Fortran pointer
aliases the actual sentinel through C_LOC/C_F_POINTER. Its value is never copied.
This is a fixture/application workaround, not a replacement for the site's MPI
module or a claim that its original scalar overload compiles.
