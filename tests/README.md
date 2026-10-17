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
`python3 -B -m unittest discover -s tests -p 'test_network*.py'` (42 cases).
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
