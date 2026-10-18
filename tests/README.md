# Local regression tests

From the repository root, run development tests with Python 3.8+ and Bash 3.2+:

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py'
```

The lifecycle tests run the real `src/remora` entrypoint in temporary directories with
mock initialization, collection, node lists and external operations. Tests
cover both a configurable finalizer stub and the real finalizer with mocked
SSH, file cleanup and reporting. No installer, actual collector, scheduler,
remote connection or privileged operation runs.

Coverage includes application success/failure, verbose on/off, finalizer
failure diagnostics, missing configuration, command-not-found, non-executable
commands, a child terminated by SIGTERM and the unmonitored fallback.
Application status is authoritative after the child completes; a reported
finalizer failure is a separate stderr diagnostic.

These are local control-flow regressions, not TACC runtime qualification.
Signals sent directly to the wrapper, distributed MPI cancellation, readiness
deadlines and isolation of concurrent collectors require separate lifecycle
work. Individual cleanup commands still use the legacy error handling; this
change does not make every internal finalizer error propagate to its return
status. The SIGTERM test targets only the child launched by the test.

To test a saved source revision without changing this checkout, set
`REMORA_TEST_SOURCE_ROOT` to an extracted source tree containing `src/remora`
and `src/scripts/remora_finalize.sh`. Keep the tests from the current checkout.

`test_installer.py` exercises the actual Bash CLI as a subprocess, with synthetic compilers,
readelf/make tools and dependency archives. Coverage includes required/optional
features, MPI static/shared build stages, missing headers, dependency configure
failures, compiler/source/environment/plan drift, native architecture checks,
artifact tampering, interruption, timeout, archive traversal and installation
without overwriting an existing prefix. Python is the development test harness,
not an installer runtime dependency. These tests never run linked probe or
collector binaries and are not native dependency or HPC qualification.

`test_third_party.py` checks the standalone dependency boundary, selective
invocation, compiler-default rejection/ignored warnings versus required user
flags, upstream make overrides, capability drift, recipe sealing, required PIC,
optional failure receipts and interruption. Tests use synthetic compiler/artifact
fixtures and do not establish NVHPC, native dependency or HPC qualification.

## Network module standard

The versioned contract is [network-modules.md](../docs/network-modules.md).
`test_network.py` compiles the real eth/ib/opa collectors and uses deterministic
sysfs fixtures with independent expected integer results. A test-only wrapper
injects EINVAL/EACCES or pauses a read for SIGTERM; production has no fault hooks.
`test_network_environment.py` additionally exercises real folder initialization
and a clean worker shell, unset versus empty selectors, and literal serialization
of malformed values without command evaluation. Run both network files with
`python3 -B -m unittest discover -s tests -p 'test_network*.py'`. The glob also
includes optional snapshot/proc/NIC resource tests described below; qualification
counts are recorded against the tested source revision.
Set `REMORA_NETWORK_TEST_CC` to a compiler command when needed (separate from
the installer's CC). Preserve compiler failures; missing compiler is not PASS.

| Acceptance group | Required cases |
| --- | --- |
| Discovery | multiple Ethernet netdevs; no default loopback/IPoIB; all ACTIVE ports; mixed link layers; port 2; named subset; renamed hfi driver; OPA rejects mlx |
| Backend and units | extended/classic/per-metric hw fallback; EINVAL; packet/event scale1, IB data4, hfi PMA data8, hfi DWORD4; no fallback masking malformed/denied reads |
| Arithmetic | unsigned counters above 2^53/2^63; exact packet/byte deltas; sum/conversion overflow; reset/saturation; valid zero versus missing |
| Continuity | first baseline; idempotent init; interface/HCA replacement; selection/source change; read failure and recovery; separate run directories |
| Failure and cleanup | invalid selectors; truncated/corrupt state; legacy data preservation; output failure; pending transaction; nonblocking lock; symlink/FIFO; SIGTERM and owned-handle cleanup |
| Compatibility | native/module exact fixture parity; old/new producer layout × old/new plot consumer; original raw checksums unchanged |
| Linux hardware | clean isolated install; native GCC strict warnings; same fixture suite; three complete live intervals per default/explicit/module case; source-aware independent read brackets |
| Traffic | three bounded 1MiB localhost TCP transfers for explicit lo with proc/net/dev as a separate kernel interface; record payload and raw before/after |

`network_live.py --prefix PREFIX --output NEW_DIRECTORY --legacy-modules DIR`
executes the last three groups on allocated Linux compute. DIR contains the
three unchanged baseline module scripts for the compatibility cross-product.
The runner never selects or submits an allocation, modifies sysfs or invokes
MPI/UMAD. It preserves command status/stdout/stderr, inventory, per-call oracles,
raw source/unit sidecars, legacy outputs and a machine-readable summary.
PASS is scoped to package revision, compiler, system and observed node class.
Unsupported modules are SKIP with observed exit69; denied/broken backends FAIL.
A source-level unit mapping plus sysfs oracle is not an end-to-end fabric payload
or overhead measurement. Multi-node payload and formal repeated overhead tests
remain separate qualification levels, requiring their own allocation envelope.

## Optional I/O resources

The Bash/C provider contract and selection catalog are in
[io-resources.md](../docs/io-resources.md). Run the focused developer suite with:

```bash
python3 -B -m unittest discover -s tests -p 'test_resource*.py' -v
python3 -B -m unittest discover -s tests -p test_installer.py -k io -v
```

The first suite compiles native collectors (`REMORA_IO_TEST_CC` overrides the
compiler command) and uses synthetic proc/sysfs data, test-only syscall doubles,
owned worker processes and temporary state. Production rejects fixture-root
options and has no Python resource runtime. The installer suite uses synthetic
ELF artifacts and does not execute them. These checks do not claim HPC runtime,
filesystem accuracy, full legacy-daemon cleanup or monitoring overhead acceptance.

Optional network-resource standard: `test_network_resources.py` extends the
legacy network suite with snapshots, field availability, unsigned values,
family/port2/discovery/selection, raw preservation, locks, malformed/oversized
sources, FIFO/symlink/hardlink output and interruption cases.
`network_resources_live.py` independently enumerates expected entities and
source attributes, brackets all readable counters and stable metadata across
three repetitions of each native/Bash module path, verifies exact explicit
selection and finalizer preservation. Run only on allocated compute; preserve
raw JSONL plus before/after readings, commands, return codes and checksums.
See `docs/network-resources.md`. Unsupported families remain SKIP.

`test_network_proc.py` extends the standard network fixture suite for the
Bash/C netprotocol and softnet modules. On allocated Linux compute,
`network_proc_live.py --prefix PREFIX --output NEW_DIR` preserves three
repetitions per Bash/native path, independent before/after proc sources,
units/CPU/header mapping checks and raw finalization hashes. Development Python
is not installed in the collection runtime.

### NIC/link-health standard cases

`test_network_nic.py` adds40 deterministic Bash/C fixture cases at collector
revision fb3f89f. The fixture backend is compiled only with `REMORA_NIC_TEST`;
installed collectors have no fixture input, fault hooks or Python dependency.
The full network suite at this revision has163 cases.

| Acceptance group | Required checks |
| --- | --- |
| Interface/driver selection | sorted Ethernet/IPoIB and DOWN; default lo exclusion; exact comma selectors; duplicate/invalid/absent selection; unknown/plain IPoIB driver explicit unsupported |
| Units/scope | all five driver families; queue/priority/CoS bounded indices; uint64 strings; frames versus microseconds/bits/blocks/bytes/events/native_count; IPoIB Ethernet interpretations unsupported even when raw zero |
| Raw/schema | full32-byte padded names plus ordered values; name reordering; duplicate names; schema/identity change rejection with raw retention; absent versus zero; reset remains absolute snapshot |
| Failure/limits | denied/unsupported/malformed/overflow; interface/stat caps; lock; nofollow FIFO/symlink/hardlink; pending/incomplete files; SIGTERM preserves pending and only owned child cleanup |
| Bash/runtime | init discovery does not truncate or promise readability; six hooks preserve raw; native/Bash fixture parity; actual runtime worker JSONL validation |
| Allocated Linux | strict GCC clean install with only selected modules; entire standard suite; three native and three Bash repetitions per module independently bracketed by ethtool-S/i; source index/name/value and semantic scope checks; installed prefix has no.py |

Run `python3 -B tests/network_nic_live.py --prefix PREFIX --output NEW_DIR`
on an authorized allocated compute node. The ethtool CLI and Python are
independent development oracles, not collection runtime dependencies. Retain
all before/after stdout/stderr, command statuses, raw API snapshots, revisions
and binary hashes. Record SKIP reasons per field/interface; one readable
interface does not establish support for every interface or every feature.
Hardlink fixture creation denial is a filesystem SKIP; never weaken the guard.
Formal overhead, full legacy wrapper and multinode qualification remain separate.

## DCB configuration resource standard

`test_network_dcb.py` runs portable C wire fixtures and Bash/native lifecycle
checks for the opt-in `netdcb` resource. Include it with the existing standard
`python3 -B -m unittest discover -s tests -p 'test_network*.py'` command.

| Case group | Required result |
| --- | --- |
| Nonzero mask, disabled PFC, nonidentity and shared TC mappings | Exact 19 configuration strings, correct units/scope; zero stays readable |
| PFC-only / ETS-only / neither exposed | Independent group availability; absent values null, never fabricated zero |
| Malformed/truncated/duplicate attributes, wrong interface/sequence/kernel peer, invalid cap/TC | Exit74; received raw retained; all affected interpreted values null |
| Kernel denial / unsupported / absent / timeout | Distinct errno/status;69 only optional unavailable,74 hard errors |
| UP/DOWN discovery, exact selectors, IPoIB, limits | Stable sorted selection; explicit inapplicable transport SKIP; no name inference |
| Symlink/hardlink/FIFO/lock/incomplete/pending, SIGTERM, finalize | Safe fail-closed output;75 lock contention; no raw cleanup or unrelated process signals |
| Native / Bash / real runtime worker, default-off registry | Same field contract and availability; no fixture backend in installed C binary |

`network_dcb_live.py` is a development-only oracle: three independent samples
per native/Bash path per interface, raw netlink decode plus before/after
`dcb -j pfc/ets show` when supported, source/config identity and finalizer hashes.
Save command/stdout/stderr/rc for CLI failures too. API/attribute/transport SKIP
is a data support result, separate from test harness PASS. Never enable PFC,
change mapping or infer per-priority traffic from shared TC/PB counters to test
this read-only module. See [contract](../docs/network-dcb-resources.md).

## Standard ethtool pause / FEC cases

`test_network_ethtool.py` is included in the standard `test_network*.py` suite.
It tests separate configured/active FEC meanings, legacy ioctl-vs-linkmode masks,
FEC_NONE-vs-Off, nonzero boolean normalization with raw preservation, uint64
bits/blocks/frames, port total at array0 and lane0 at array1, aggregate-only/empty/
missing and unequal lane arrays, exact selectors/DOWN/IPoIB, malformed framing/
bitsets/attributes/identity, unsupported/denied/timeout, safe outputs/signals and
native/Bash/real worker hooks. Denial/malformed/timeout must not invoke fallback.
No per-lane link-up/down field is invented where standard FEC GET has none.

`network_ethtool_live.py` runs three C/Bash samples per module/interface, records
independent `ethtool -a`, `--show-fec` and `-I` before/after oracles, checks raw
netlink/ioctl values and meanings, per-field missingness, rootless capabilities,
and finalizer hashes. Preserve GET failures and explicit oracle/per-lane SKIP too.
Fixtures are not hardware support; per-lane breakdown needs actual driver arrays.
See [resource contract](../docs/network-ethtool-resources.md). All Python here is
development-only; installed collection uses Bash/C without ethtool CLI/library.
