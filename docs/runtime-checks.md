# Stage-two runtime capability checks

Installation checks establish compiler/link capability. Runtime checks operate
on an installed prefix inside the user's existing compute allocation; they never
request an allocation or run the full legacy wrapper. Run one bounded check per
module and record shell-module and collector-binary results independently.

## Data contract (remora-runtime-check/v1)

Each case records module, implementation (shell/collector), executable/module
hash, node/architecture, commands, exit status, timeout, raw files, prerequisites,
independent OS/device observations, availability and semantic validation. Status
is PASS, FAIL, SKIP, BLOCKED or NOT_RUN with a reason. Absent/unsupported hardware
is SKIP; missing tools/artifacts or inaccessible prerequisites are BLOCKED;
nonzero exit, signal, timeout, malformed or empty samples are FAIL. Unsupported
data are never fabricated as zero. A PASS is a bounded runtime smoke result for
this node/toolchain/revision; it does not establish monitoring accuracy, overhead,
other node classes or multi-node behavior. Correctness is recorded separately;
known unit defects remain FAIL/OPEN even when collection is available.
Source-derived unit verdicts carry DERIVED rather than OBSERVED attribution.

```bash
bash "$prefix/bin/runtime/check.sh" --prefix "$prefix" \
    --output "$task_dir/runtime-check" --module all

# Each module also has an independent small-script entry point.
bash "$prefix/bin/runtime/modules/cpu.sh" --prefix "$prefix" \
    --output "$task_dir/cpu-check"
```

Output directories must be new, outside the prefix, and contain no whitespace
because the legacy module functions split unquoted paths. The runner returns 1
if any case FAILs, 2 for BLOCKED without FAIL, otherwise 0. Read summary.json for
each implementation's individual status; a zero exit with only SKIP cases is
not a qualified collector. The runner's samples/interval/timeout are bounded.

Collector CLI remains `data_collect_NAME NODE OUTDIR TMPDIR`. Stateful collectors
now store `remora_NAME.tmp` beneath TMPDIR, rather than global /dev/shm filenames.
Use a unique empty TMPDIR for every run and distinct case directories. Raw output
names/numeric formats remain unchanged. Old collector binaries containing the
global state paths are blocked by the runtime harness. No automatic deletion of
existing state or unrelated processes is allowed.
The shell memory module now obtains allocated shared-memory filesystem bytes
through nonrecursive `df -B1` and converts them to GiB, matching the C collector's
filesystem-usage interpretation rather than recursively scanning /dev/shm.

The runtime runner owns only its subprocess group and new output directory.
It calls module init/collect functions, preserves init/sample return codes and
logs, and waits for CPU module background sampling before validation. Plotting,
finalization, global wrapper cleanup and privilege changes are not run. Network
topology is postprocessing-only and requires independent trace qualification;
mpiP requires an explicitly supplied, precompiled MPI smoke executable and the
native TACC launcher in the preserved allocation environment.

At least three observations with increasing timestamps and finite numeric data
are required for time-series collectors. Header-only output and exit-zero/no-data
are failures. Backend prerequisites and schema checks are module-specific. Saved
OS/device observations are independent evidence, not REMORA self-validation.

HPC follow-up: Bash `bc` legitimately emits leading-decimal numbers such as `.069`.
The raw validator accepts those; the initial bbfbda7 SKX power schema failure is
superseded by replay validation, with original results retained. Legacy shell RAPL
power nevertheless has a separate derived unit defect: microjoules divided by
1000 and seconds produces milliwatts while the output says watts, and its TIME(ms)
column holds seconds. Stage two reports availability PASS and correctness FAIL
when that RAPL branch was actually selected. No producer unit conversion is changed.
