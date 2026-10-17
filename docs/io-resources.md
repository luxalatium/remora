# Selectable user-space I/O resources (v1)

The optional `io` module uses the existing Bash module functions and one compiled
C collector, `binary_data_collectors/data_collect_io`. It adds no Python runtime,
daemon, privileged operation, mount change or counter reset. Python is used only
by developer tests. The installer registers `io,IO`, default **off**, and builds
it through the same C compiler/ELF inspection path as the other native collectors.
The legacy Lustre/LNet/DVS modules retain their own availability requirements.

## Selection and use

Enable `--module io=on` in the existing `install.sh configure` command, then use
that plan for `build` and `install`. On HPC, configure/build and runtime tests
belong in an authorized compute allocation. Compilation confirms installation
capability; it does not qualify the running filesystem or hardware.

```bash
# List or describe the available providers; no sampling or scheduler operation.
"$REMORA_BIN/binary_data_collectors/data_collect_io" --describe

# Select providers for the existing REMORA collection loop.
export REMORA_RESOURCE_MODULES=mounts,capacity,nfs,block,tmp
export REMORA_RESOURCE_PATHS=$'/tmp\n/dev/shm'
export REMORA_RESOURCE_TIMEOUT=3        # integer seconds, per provider; 1..60
remora ./application

# Standalone read-only sample (JSONL on stdout).
"$REMORA_BIN/binary_data_collectors/data_collect_io" \
  --modules nfs --node "$(hostname)" --timeout-ms 3000

# Optional run-local rate state; use a NEW owned directory for each run.
mkdir -m 700 ./io-state
"$REMORA_BIN/binary_data_collectors/data_collect_io" \
  --modules block --device 259:0 --state-dir ./io-state
```

`--modules` is a strict comma-separated subset; duplicates and unknown providers
are errors. `--path ABS` and `--device MAJOR:MINOR` may be repeated. Environment
paths are newline-separated; environment devices (`REMORA_RESOURCE_DEVICES`)
are comma-separated. Device selection takes precedence over path mapping for
`block`. It never automatically selects or sums every disk. Paths are literal
arguments, resolved with `realpath`; no directory traversal or content scan runs.

`process` requires explicit `--pid PID:STARTTIME_TICKS`. Obtain field 22 of
`/proc/PID/stat` from the application launcher while tracking that process; the
comm field can contain spaces and `)`, so whitespace-only field counting is
unsafe. REMORA does not infer a process population from the username or job label.
For distributed collection, export `REMORA_RESOURCE_PROCESS_HANDLES` as
newline-separated `NODE PID:STARTTIME_TICKS` entries, using exactly the node name
passed by REMORA. Only handles for the current node are sampled. Remote PID
tracking/launcher integration is a separate producer of these handles.

The selected handle exposes Linux process accounting, including I/O accumulated
from children after they are waited for. It does not isolate the parent's own
contribution. Selecting both parent and child handles can therefore overlap;
retain `aggregation: "forbidden"` and do not sum them into a rank or job total.
See the [Linux `/proc/PID/io` manual](https://man7.org/linux/man-pages/man5/proc_pid_io.5.html).

Both `collect_data_io NODE OUTDIR TMPDIR` and the existing native positional ABI
`data_collect_io NODE OUTDIR TMPDIR` append to `TMPDIR/io_NODE.jsonl`.
`init_module_io` validates configuration and creates that file with mode 0600,
refusing to truncate an existing run. REMORA's existing result transfer preserves
the JSONL; this provider does not generate legacy HTML charts. The new collector
has no global process signals or shared `/dev/shm` cleanup. Tests invoke the
module/native ABI directly rather than the legacy daemon's cleanup routines.

## Provider catalog and scope

| ID | Backend / default selection | Information | Actual statistical scope |
| --- | --- | --- | --- |
| `mounts` | `/proc/self/mountinfo`, all visible entries | mount ID, device, root, source, type, mount/super options | collector mount namespace; no usage counter |
| `capacity` | `statvfs`, `/tmp` by default | total/free/available bytes and inodes | filesystem capacity visible to caller; **not a user/job quota** |
| `nfs` | `/proc/self/mountstats`, visible NFS/NFS4 mounts | logical/direct/server bytes, pages, per-operation RPC counts/times, raw transports | node client mount; includes other traffic in that domain |
| `process` | `/proc/PID/stat` and `/proc/PID/io`, explicit handles only | rchar/wchar, syscr/syscw, storage read/write/cancelled bytes | Linux process accounting for that PID lifetime, including waited-for children; not a disjoint rank/job total |
| `block` | `/proc/diskstats`, explicit devices or selected paths (default `/tmp`) | read/write/discard/flush counts, sectors, time, in-flight gauge | node block device; other processes may contribute |
| `tmp` | mount mapping + `statvfs`, `/tmp` and `/dev/shm` | capacity, bind root/options, memory-backed tmpfs flag | filesystem; no claim of job-private storage or quota |

NFS byte counters are eight fields in kernel order: normal read/write, direct
read/write, server read/write (bytes), read/write pages. RPC operation records
accept the documented eight fields plus optional errors: ops, transmissions,
major timeouts, sent/received bytes, cumulative queue/RTT/execute milliseconds,
errors. Ops and transmissions remain distinct. Unknown statvers/layouts are
partial/invalid data. `xprt` TCP/RDMA rows are retained verbatim; no common numeric
transport layout is assumed. Version 1 supports statvers 1.0 and 1.1.

Diskstats accepts the 11, 15 and 17 counter layouts after major/minor/name.
Sectors are **512-byte accounting units**, independent of physical sector size.
`in_flight` is a gauge. Discard/flush fields absent in older layouts are null with
`OPTIONAL_FIELD_ABSENT`. A path whose filesystem device has neither a sysfs block entry nor a diskstats
row is SKIP with `NO_LOCAL_BLOCK_MAPPING` (for example BeeGFS). An explicitly
selected absent device remains MISSING; malformed/denied sources stay failures.
Node disk activity cannot be equated with application storage accounting. Tmpfs is memory-backed, and logical process I/O may include
pipes/terminals or cache hits; zero storage bytes can therefore be a valid sample.

## JSONL contract: `remora-resource-sample/v1`

One complete line represents one selected provider per invocation. The envelope
contains module/version/operation, status/reason, wall timestamp, monotonic read
start/end, node/kernel/boot/mount namespace, qualification, entities and raw
sources. Installed build provenance is referenced by `build_manifest`; its source
manifest and artifact hashes identify the build. Standalone users must retain
source/binary hashes themselves. `source_revision` and scheduler provenance stay
null when not established. No environment job ID is promoted into attribution.
`context.fixture` marks test replay; fixture-root substitution is compiled out of
production builds. Correctness and overhead remain `NOT_QUALIFIED` until separate
controlled experiments establish them for the exact revision/system/node class.

Each entity records its requested target, canonical path where relevant,
configuration, identity, actual scope, status/reason and typed metrics. Every
metric has `value`, `status`, `reason`, `kind` and `unit`. Available raw integers
and deltas are emitted as exact unsigned decimal integers, including values
above 2^53/2^63. Consumers must use an integer-capable JSON parser; floating-point
JSON tools can round them. Approximate per-second rates use a positive monotonic
interval; wall-clock adjustments do not enter the calculation. `statvfs` raw
fields, process IO/stat records, mountinfo, mountstats and diskstats evidence are
retained with the sample. Raw octets outside ASCII are JSON byte escapes.

Missing/denied/unsupported/parse/timeout data is never filled with zero. Envelope
`PARTIAL`, `UNSUPPORTED`, `TIMEOUT` or `INVALID` and detailed reasons distinguish
failures; `SKIP` explains no NFS mount/no explicit PID selection. Missing metrics
have null values. Optional unavailable fields can coexist with a PASS provider.
Zero available bytes with a nonzero filesystem size means full capacity. Zero
reported total blocks/fragment size or total inode count indicates unsupported
capacity/inode reporting, yielding null rather than a fictitious empty resource.

Every entity has `aggregation: "forbidden"`. Preserve NFS mount rows even when
major:minor matches: `shared_domain_candidate` identifies that possibility;
`identical_snapshot_counters` is additional evidence, **not proof of independent
or identical counter ownership**. Never sum candidate aliases. Likewise, do not
sum overlapping block-device layers (device mapper/RAID/partitions/backing disk),
multiple paths to one device, or process/NFS/block metrics across layers.
There is deliberately no fabricated job total or VAST server-health claim.

Counters optionally include an `interval` object: exact delta, elapsed_ns,
baseline_end_ns and per_second, or null with GAP/RESET/IDENTITY_CHANGED/
IDENTITY_UNKNOWN/INVALID_INTERVAL. Without state, first samples are GAP.
A failed/partial provider invalidates its baseline, so recovery requires a fresh
complete baseline. A reset rebaselines at the observed lower counter; it is not
interpreted as wraparound. Gauges have no interval. Consumers can verify the
explicit baseline timestamp against adjacent retained samples.

Mount identity includes namespace, ID, major:minor, root, source and type; the
mount table is checked again after sampling. A change invalidates the sample.
NFS mount age rollback, boot or namespace change suppresses rates. Process UID
and PID/starttime are checked before and after IO; races invalidate all fields.
Block identity requires boot plus sysfs device inode and diskseq for rates;
missing generation retains raw counters but yields IDENTITY_UNKNOWN intervals.
Remounts/restarts without a reliable generation cannot be proven absent merely
from unchanged counter values. Identity evidence is conservative, not exact
application attribution.

## Bounded reads, state and failure behavior

Providers run sequentially in isolated owned children, each with a deadline
(default 3 seconds). Each source is limited to 2 MiB, provider output to 16 MiB,
128 entities, 768 metrics per entity and 64 explicit targets/handles. Exceeding a
limit is explicit failure, not silent truncation. Nonregular input files are
rejected using nonblocking opens; partial line-oriented raw sources are rejected.
A deadline discards incomplete JSON, emits a TIMEOUT record and invalidates that
provider's state. TERM/INT/HUP terminate and reap only the owned provider child,
returning 128+signal; a signal arriving with the last provider EOF/reap also
discards pending output/state rather than reporting a provider failure or a
successful completion. No wildcard process killing is used. Kernel-uninterruptible
filesystem calls can delay child reaping despite SIGKILL; local tests validate
interruptible owned-worker cleanup, not a guarantee about every kernel failure.

Run state and locks are local to the specified owned directory and node/provider.
A nonblocking lock prevents concurrent writers. State has version, size and
checksum checks; corrupt/truncated state cannot become a baseline. Output/state
symlinks, nonregular files and multiply-linked files are rejected. Parent-owned
pending files are committed only after a complete output write and flush, or
removed by exact inode identity; raw JSONL is never rewritten by derivation.
State/output is not a cross-filesystem crash transaction: retain the explicit
baseline timestamps and use a new run directory after controller/run restart.
Native provider failures emit records and return 2; malformed configuration
returns 64; output/lock failures return 74. `--allow-partial` and the legacy
positional ABI return success for recorded missing/unsupported providers so
available providers can continue. Hard output/configuration/signal failures
still propagate. The surrounding historical REMORA loop has its own exit-status
handling and cleanup limitations; this new module does not qualify that daemon.

## Validation and extension

`test_resource_modules.py` compiles production and test-only C builds and checks
independent fixture expectations, aliases, scope, missing data, precision,
identity changes, reset/gaps, optional layouts, deadline/signal cleanup and
state ownership. `test_resource_adapter.py` checks Bash/native ABI, quoting,
selection and no-clobber output. Installer tests verify the selected binary and
absence of Python resource payloads. Set `REMORA_IO_TEST_CC` when the developer
compiler needs explicit flags. These local fixtures are not Linux/HPC runtime,
controlled accuracy or repeated overhead qualification.

Future providers must declare an exact scope, identity/reset boundary, raw
backend, units/kind, missing semantics, timeout/output bounds and independent
fixtures before joining this catalog. Keep production readers in Bash/C. A
readable ancestor cgroup is not sufficient for a job cgroup provider. Lustre,
quota and other backends require current unprivileged interfaces on the target
system; earlier survey observations alone do not establish future availability.
