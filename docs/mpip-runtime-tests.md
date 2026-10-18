# mpiP runtime qualification contract, version 1

The suite qualifies one source/library hash, MPI wrapper/backend, language
binding, system and node class at a time. C and C++ use the portable MPI C API;
C++ additionally exercises the C++ runtime. Fortran uses `use mpi`, with a
separate `mpi_f08` capability test. A successful executable is not sufficient:
its deterministic results, placement, cleanup and mpiP statistics must agree.

## Cases and independent expectations

All normal cases use MPI_Init_thread(FUNNELED), check the provided level and
MPI integer size, and finish with one validation Allreduce. Iterations L=4.
Application validation reports errors independently of mpiP. Runtime receipts
record source/binary/library hashes, commands, modules, ranks, physical CPU
binding, return code, timing, stdout/stderr and all profiler output.

| Case | Operations and data oracle | Required profiler checks |
| --- | --- | --- |
| core | Paired Send/Recv, ring Sendrecv, zero count, Isend/Irecv/Waitall; Bcast, in-place/normal Allreduce, Allgather, Alltoall, Iallreduce/Wait; Comm_split; strided datatype with unchanged holes | Per-rank exact call counts; sent bytes for Send, Isend and Sendrecv; all expected ranks |
| io | Disjoint rank offsets, collective write/read of 16 integers, sync and value checks | Exact write/read counts and I/O bytes |
| rma | Ring Put with fence completion and expected remote values | Exact Put and fence counts |
| errors | MPI_ERRORS_RETURN; invalid destination must yield MPI_ERR_RANK | Same application/error outcome as baseline; profiler remains usable |
| pcontrol | Disabled region, reset, four enabled Sendrecv calls, verbose snapshot, disabled region | Each verbose report counts only four Sendrecv per rank; no validation Allreduce leakage |
| inplace diagnostic | Compare normal and Fortran MPI_IN_PLACE Allreduce against the closed-form sum; print both results | Three Allreduce calls; deterministic values must match baseline |
| abort | Explicit marker then MPI_Abort(17) | Nonzero launcher exit excluding timeout; no surviving recorded worker; report not required |

Core per-rank counts: Send/Recv=L for paired ranks (zero for an unpaired last
rank); Sendrecv=3L, Isend=Irecv=Waitall=L, Bcast=Allgather=Alltoall=Iallreduce=
Wait=L, Allreduce=2L+1 and Barrier=1. Sent bytes are L*4*integer_size for Send,
L*64*integer_size for Isend and L*(8+4)*integer_size for Sendrecv. Datatype extent
is deliberately larger than its payload. IO counts are L for each write/read,
with L*16*integer_size bytes each. RMA counts are Put=L and Win_fence=2L.

Expected counts come from the independent analysis contract, not mpiP itself.
Use detailed all-callsite tables, summing sites for each operation/rank; the
top-twenty summaries are not complete evidence. Require mpiP release 3.5 (report version 3.5.0),
complete report termination, finite nonnegative times/bytes, consistent extrema
and exact task assignments. Address-to-source fidelity remains unqualified
because this installer disables BFD/libunwind/demangling.

## Controls and execution matrix

For each language run baseline, shared preload and static link. Preload only
the application inside the rank wrapper; do not preload the launcher. Use native
TACC ibrun and explicit Linux taskset binding to distinct allocated physical CPUs.
Never use the legacy full REMORA wrapper or global process-pattern cleanup.

Core: ranks 1 and 4, two repetitions per mode; rank 3, one repetition. Additional
cases: four ranks, one repetition per mode/language. mpi_f08: core, four ranks,
one repetition per mode if it compiles. A missing binding is SKIP with a compiler
receipt; missing profiler interception is a separate coverage FAIL, not zero.

Each launch has a finite timeout and private output directory. The Bash runner
changes to that case directory and passes relative `-f profile` to mpiP. mpiP3.5
formats report paths through a fixed 256-byte buffer; a long absolute directory
can truncate the extension or report counter and prevent finalization. Preserve
valid statistics from truncated report names, but fail report naming separately.
Three-system same-node A/B evidence supports this relative-directory workaround.
The abort fixture synchronizes ranks after printing worker identity, before rank0
invokes MPI_Abort, so cleanup does not depend on a print-versus-abort race. Record exact worker
PID/start identity before exec, then inspect only those identities after launch.
A timeout, surviving owned worker, affinity failure or allocation failure stops
that path. Two equivalent failures require diagnosis before more attempts. Abort
is expected to omit MPI_Finalize and therefore need not produce a profiler report.

All normal profiled runs must preserve baseline deterministic results and have
matching statistics. IO/RMA baseline failures identify a platform/MPI capability
gap; retain them and do not blame mpiP without a successful control. Timings are
diagnostic measurements including startup; they do not establish a formal
monitoring-overhead percentage. Multi-node/network, MPI_THREAD_MULTIPLE concurrency,
GPU-aware MPI and numerical overhead qualification remain separate matrix rows.

Research evidence stays per-system and immutable. Create/hash archives on compute,
then retrieve through the owned checked-fetch entry. No test implicitly grants a
new allocation, budget, publication or source/pin change.

## Native F08 and timeout diagnostics

When native adapters are built, preload uses `libmpiP.so`; F08 static tests use
`libmpiP-f08.a` with the matching MPI Fortran linker. C/legacy static tests retain
`libmpiP.a`. Test normal and optional-ierror Init/Init_thread/Finalize separately
with `mpi-f08-lifecycle.F90`. MPI_Init may legitimately provide THREAD_SINGLE;
its control contract differs from the FUNNELED Init_thread cases above.

For NVHPC24.7's rejected scalar sentinel overload, the fixture flag
`-DREMORA_F08_INPLACE_ARRAY_ALIAS` aliases the actual MPI_IN_PLACE address through
C_LOC/C_F_POINTER as a rank-one pointer. Do not construct an array containing a
copy of the sentinel value. Preserve the original compiler rejection separately;
the workaround does not repair the site's MPI module.

The case runner retains its 25-second timeout. At 20 seconds it records only
matching rank PID/start/node/UID identities from that case's WORKER lines,
including Linux process state, wchan/status and optional syscall/stack snapshots.
The additional proc reads recheck owner and start identity; access denial or
a vanished process is retained as stderr, never treated as a zero measurement.
Each proc read has a one-second timeout and the snapshot loop has a three-second
budget, retaining partial files if the budget expires. These files are diagnosis
evidence, not proof of MPI correctness. It never enumerates or signals
unrelated processes. The caller remains responsible for exact-worker cleanup.
An intentional sleep timeout validates this diagnostic path; it is not a
reproduction or root-cause proof of an earlier Horizon shutdown failure.

For static finalize diagnosis, compile `fixtures/mpi-finalize-trace.c` and link
its object with `-Wl,--wrap=MPI_Finalize,--wrap=PMPI_Finalize`. The stderr markers
separate entry/return from mpiP's finalize wrapper and its PMPI finalize call.
Calls internal to a vendor shared object are not redirected by those static
link flags, so an unprofiled control can have only the outer MPI markers.
Absence of the inner markers in that control is not itself a failure.

For a site native F08 Pcontrol failure, the explicit fixture option
`-DREMORA_PCONTROL_C_REFERENCE` with `mpi-pcontrol-reference.c` routes only
Pcontrol through the MPI C binding in the unprofiled reference. Keep profiler
preload/static cases on the original F08 binding. Label this comparison with
its binding variance; it does not turn the original native baseline into PASS
or establish that the vendor F08 entry is repaired.

A native F08 Pcontrol ABI defect can also be isolated without changing the
application's F08 call: link `libmpiP-f08-pcontrol.a` for the unprofiled compatibility
reference. Keep the original unmodified native result separately. Validate
levels 0–3, correct application results and cleanup, then compare original-F08
preload/full-static statistics. The compatibility archive itself contains no
profiler; an explicit compatibility reference must not be labeled native PASS.
Application-only preload on that linked reference must still control mpiP's C
Pcontrol path. Do not use the compatibility archive together with a whole-archive
full F08 static library.
