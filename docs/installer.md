# Configurable Bash installer

Bash 3.2+, jq 1.6+, and SHA-256 utilities (`sha256sum` or `shasum`) are required.
Native Linux checks additionally require `setsid` and GNU `timeout`; publication
uses GNU `mv -T -n` to preserve an unexpectedly created destination. Binutils
`readelf` is checked when a binary feature needs it. The installer is entirely
Bash and does not require Python. This interface checks **installation
capability** on native Linux x86_64/aarch64. It does not launch REMORA, MPI
applications, GPU probes, sensor access or monitoring workloads. Dependency
configure scripts may run their own ordinary compiler conftests.

On HPC, both `configure` and `build` compile code: run them in an allocated
compute session. The installer never requests or reuses an allocation itself.
`--help` and `show` are metadata-only operations.

## Configure, inspect, build, install

Use a new task-owned build directory and a new versioned installation prefix.
The source tree and any existing installation are preserved. All four commands
can be invoked from another working directory using an absolute script path.

```bash
# Select a unique task root in storage you own before running these commands.
task_dir="$SCRATCH/remora-installer/my-unique-task"
source_dir=/path/to/remora

bash "$source_dir/install.sh" configure \
  --build-dir "$task_dir/build-state" \
  --prefix "$task_dir/install-v1" \
  --cc gcc --cflags=-O2 \
  --module mpstat=on --module cpu=on --module memory=on \
  --module mpip=off --module gpu=off

bash "$source_dir/install.sh" show --plan "$task_dir/build-state/plan.json"
bash "$source_dir/install.sh" build --plan "$task_dir/build-state/plan.json"
bash "$source_dir/install.sh" install --plan "$task_dir/build-state/plan.json"
```

`install.sh` invokes `installer/cli.sh` and `installer/common.sh` through Bash.
Commands must succeed in order;
`configure` with BLOCKED features exits 2, and build refuses a blocked plan.
There is no automatic fallback to the legacy installer.

Configure, build and install print important progress immediately to stdout,
including module checks/results, dependency configure/make steps, artifact
verification, destination copying and installation publication. Lines use the
`[configure]`, `[build]` or `[install]` prefix and include log/receipt paths.
Failed commands and interrupted operations report progress without claiming
completion; diagnostic errors also remain on stderr. Full command stdout/stderr
stay in the recorded log files. The `show` command retains its plan-only output.

Installer Bash source uses four-space indentation, separate lines for statements
and expanded functions, conditionals, loops and case arms. Quoted probe source,
JSON expressions and heredocs remain data; formatting must not change them.
Development formatting uses `shfmt -ln bash -i 4 -ci -sr` on `install.sh`,
`installer/cli.sh` and `installer/common.sh`; shfmt is not an installer runtime
dependency.

## Toolchain configuration

`--cc`, `--cxx`, `--fc`, `--mpicc`, `--mpicxx`, `--mpifc`, `--nvcc`,
`--make`, `--bash`, `--readelf` and `--mpip-python` accept a single executable
name or path, not a shell command such as `ccache gcc`.

`--cflags`, `--cxxflags`, `--fflags`, `--cppflags`, `--ldflags` accept shell-like
quoted argument strings, without executing them as shell commands in the direct
probes. Use `--cflags=-O2` for values starting with `-`. Upstream dependency
configure/make systems consume these flags through their own normal mechanisms.

The corresponding compiler/flag environment variables supply initial defaults.
Command-line settings override saved config and environment defaults. Optional
C++/Fortran/MPI/CUDA compilers are checked when explicitly selected, or when a
selected feature needs them. Missing unused languages do not block installation.
MPI wrapper backend commands are displayed and, when resolved, their compiler
executable is checksum-pinned separately. The ordinary C compiler and MPI
backend may differ; each is checked for its own stages.

For CUDA and NVML:

```bash
--cuda /path/to/cuda \
--nvml-include /path/to/nvml/include \
--nvml-lib /path/to/nvml/lib \
--module gpu=on
```

`--cuda` selects its `bin/nvcc` unless explicitly overridden, checks CUDA
compile/link, and supplies a default NVML include directory. NVML can instead
be configured independently: `gpu=on` with NVML flags does **not** require
nvcc. The GPU collector's sanity check compiles and links NVML without executing
the resulting program. Device visibility and telemetry permissions are not
installation requirements. An explicit NVML library path is embedded as an
rpath in the GPU binary; retain that dependency installation.

If C++ is explicitly configured, the CUDA sanity probe uses that compiler as
nvcc's host compiler via `-ccbin`; otherwise nvcc chooses its normal host default.

## Modules and modes

The declarative registry is `installer/modules.json`.

| Module | Default | Installation requirement |
| --- | --- | --- |
| mpstat | on | Bundled sysstat 11.5.3 configure and actual mpstat build |
| cpu | on | C collector build and READY mpstat dependency |
| memory | on | C collector build |
| numa, eth, ib, power, temperature | auto | Each collector compile/link |
| opa | off | C collector build |
| gpu | auto | NVML header/link probe and GPU collector compile/link |
| mpip | auto | Intel MPI/MVAPICH2 legacy backend, MPI C/Fortran compile-link, Python 2, mpiP 3.4.1 configure and static/shared build |
| network | auto | Shell module syntax |
| lustre, lnet, dvs | off | Shell module syntax |

`on` means required: failure is BLOCKED and prevents build. `auto` failures
are SKIPPED with a reason. `off` is DISABLED and its probes are not run. A
dependency explicitly off is not silently enabled; for example cpu=on with
mpstat=off is BLOCKED. Explicit optional compiler selections also block if their
checks fail, even if no enabled module consumes that compiler.

The bundled mpiP version requires Fortran symbol configuration and a Python 2
generator. This version does not pretend those requirements disappeared or
claim Open MPI statistics support. Unsupported auto mpiP is clearly skipped;
required mpiP blocks. Upgrading mpiP or adding an Open MPI backend is separate
work. No Python installation or dependency download occurs automatically.

Shell readiness and collector compile success do not establish runtime backend
availability. In particular, Intel power/temperature collectors may compile on
AMD or ARM while their live sensor interfaces remain unavailable. The installer
does not choose OPA by inspecting the current node's loaded kernel modules, and
does not disable filesystem backends by hostname.

## Saved configuration and evidence

`config.json` preserves user choices; edit/copy it and run `configure --config
old/config.json --build-dir NEW --prefix NEW_PREFIX` to reconfigure. Optional
compiler choices remain optional when a generated config is reused.

`plan.json` contains schema `remora-build-plan/v2`: resolved tools and hashes,
wrapper backend, source/dependency archive hashes, explicit NVML dependency
hashes, platform, relevant toolchain environment, module statuses, selected
features, expected artifacts and a plan checksum. This checksum detects
accidental changes; it is not an authorization/signature mechanism.

V2 seals canonical sorted UTF-8 JSON using jq without a trailing newline.
Python-generated v1 plans are retained historical evidence and are rejected with
a reconfigure instruction, because the installer implementation/source and seal
encoding changed. Their editable `config.json` choices remain reusable through
`--config` in a new build directory; original plans and evidence are not changed.
Flags use a Bash quote/backslash tokenizer without `eval`, variable expansion
or command substitution. JSON config/plan files are never sourced as shell code.

Build/install reject source, recorded environment, tools, explicit dependency
or platform changes. Implicit system library/header search paths and the full
transitive toolchain are not completely hermetic in this implementation; for reproducible GPU
builds supply explicit NVML paths and retain the module environment. Runtime
ABI compatibility remains separate qualification.

`checks/logs/` retains commands, cwd, both output streams, exit codes and timeout
limits. Configure builds bundled dependencies in isolated check directories so
F77/configure/shared-library failures are found before the selected full build.
The full build uses a fresh directory and reproduces the selected recipe;
check products are not silently reused as installation artifacts.

`build/result.json` records overall and per-module build state, artifact hashes
and `runtime_qualification: NOT_RUN`. ELF architecture and readelf metadata are
checked without running collector binaries. `build/stage/build-manifest.json`
travels with the installation. Only selected config entries and collector
binaries are enabled/built; legacy shell scripts remain distributed for manual
inspection and compatibility. Legacy runtime BASIC/MONITOR/custom selection
semantics have not been redesigned by this change.

Install verifies staged hashes and provenance, copies to the destination
filesystem, verifies the copy and renames it into an absent prefix under an
exclusive installation lock. Existing prefixes are never replaced. A failed
candidate is retained for diagnosis. Timeout/interrupt terminates the owned
subprocess group and preserves logs. After SIGKILL or machine failure, inspect
any retained staging/lock before retrying; no automatic recovery/deletion runs.

## Compatibility and validation scope

`install.sh` now requires a subcommand. The old no-argument installer is retained
as an explicit `bash install.sh --legacy` escape hatch and retains its historical
failure-masking risks; run that only from the source root. Existing automation
must migrate to the four-step interface to obtain the new checks.

Run `python3 -B -m unittest discover -s tests -p 'test_*.py'`. Installer tests
use synthetic toolchains/dependency fixtures and failure injection; they do not
establish native sysstat/mpiP/NVML compilation, HPC node-class compatibility,
monitor correctness, overhead or full wrapper lifecycle qualification.

## Third-party packages

mpstat and mpiP use a separate Bash installer in `third_party_packages/`. Only
enabled dependency modules invoke it. The main configure/build/install CLI and
config format remain unchanged; required/auto/off decisions stay in REMORA.
Dependency source/build/logs/results now live in
`BUILD_DIR/checks/third_party_packages/NAME/` and
`BUILD_DIR/build/third_party_packages/NAME/`; outer invocation logs remain under
`checks/logs/` and `build/logs/`. Detailed progress is forwarded to stdout.

The child tests actual compiler flags and records accepted/rejected defaults.
Upstream make receives explicit effective flags, preventing sysstat from adding
unsupported `-pipe` again. User flags are never silently discarded. Configure
receipts are sealed in the plan; build checks the same archive/tool/flag identity
before upstream make. This source layout change requires fresh configuration.
See [boundary and standalone usage](third-party-installer.md).
