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

Each launch has a finite timeout and private output directory. Record exact worker
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
