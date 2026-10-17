# Third-party installer boundary

REMORA owns configuration, module selection/dependencies, collector compilation,
scripts, plan integrity and final versioned deployment. `third_party_packages/`
owns dependency prerequisites, compiler-option capabilities, archive extraction,
upstream configure/make commands and dependency artifact staging. Both use generic
Bash helpers from `build_support/`; neither installer sources the other's CLI.
Bundled archives remain in `extra/` for legacy compatibility and retain their bytes.

## Contract (defined before implementation)

`third_party_packages/install.sh check|build --package NAME --request FILE
--build-dir NEW` consumes `remora-third-party-request/v1`: normalized `config`
(tool invocation strings and flag strings) and resolved `tools` metadata from the
caller. `check` compiles a complete dependency in a disposable probe stage; `build`
rebuilds in a different new directory. No test binary is executed. The standalone
CLI also accepts `--config FILE` using REMORA's existing config format; it resolves
only that dependency's tools. Work belongs under NEW/third-party source, logs and
stage, never in tracked source directories or an existing installation.

The result is `remora-third-party-result/v1`, containing package/version, phase,
status, reason on failure, requested/effective flags and per-option probe results,
archive SHA256, tool metadata, stage path and relative artifact SHA256 map. Runtime
qualification remains NOT_RUN. Exit0 requires a BUILT receipt and all expected
native artifacts; exit2 is failure, exit130 interruption. Logs and incomplete work
are retained. JSON failure cannot be success. No global process cleanup is used.

Optional artifact contract: a registry entry may declare `optional_artifacts`.
A result's `optional_artifacts` selects a unique subset of those paths. Its
`artifacts` keys must equal the required registry paths plus that selected
subset; undeclared extras and missing required/selected files are errors.
Absent optional lists mean empty lists, preserving earlier results. Configure
and build must agree on the selection and on the additive `fortran_f08`
capability record. Capability changes require reconfiguration. Both producer
and consumer verify this contract; hashes still cover every deployed file.

REMORA invokes the dependency installer only for an enabled third-party module.
It verifies the returned artifact contract/checksums and copies just those files
into its own stage. Required modules map failures to BLOCKED, auto to SKIPPED, off
to DISABLED without invoking the child. Configure receipts are sealed into the
main plan. Build must use the same dependency input/tool identities and effective
flag choices; changed capability results require reconfiguration. New source
manifests include dependency recipes and shared helpers, so older plans require
fresh configuration rather than silently adopting new behavior.

User flags are validated intact, not silently removed. Recipe defaults are probed
with the actual configured compiler or MPI wrapper. Unsupported defaults may be
omitted with a recorded reason; timeout, wrong architecture or an unusable base
compiler are failures. Required shared-library PIC capability must be present.
Upstream make defaults cannot reintroduce rejected options: the recipe explicitly
supplies the effective flags while preserving package feature definitions.

This is installation capability only; Linux/MPI/GPU/runtime qualification remains
node/toolchain/revision-specific and separate from local synthetic regressions.

## Standalone use

```bash
bash third_party_packages/install.sh check --package mpstat --config config.json --build-dir /scratch/OWNED/mpstat-check
bash third_party_packages/install.sh build --package mpip --config config.json --build-dir /scratch/OWNED/mpip-build
```

These commands stage only the dependency's `bin/mpstat` or `lib/libmpiP.a` and
`lib/libmpiP.so`, plus the declared optional `lib/libmpiP-f08.a` when native F08
adapters are built. The main REMORA installer remains the sole publisher of the
final package prefix. The child may be used without loading REMORA's CLI.
Package recipes and tool requirements are declared in `packages.json` and
`packages/*.sh`; adding or upgrading a dependency does not add its flags or
compatibility checks to REMORA's configuration orchestrator.

## mpiP 3.5 / Python 3

The active mpiP archive is the unmodified LLNL 3.5 release, pinned by SHA256
in packages.json and described in third_party_packages/mpiP-source.json. The
retained extra/mpiP-3.4.1.tar.gz is historical and is not selected by either
installer. Existing plans must be reconfigured after this source change.

The tool key mpip_python and --mpip-python option remain compatible; their
default is now python3. The recipe requires a genuine Python 3 interpreter,
MPI C/C++/Fortran wrappers and native compile/link/PIC capabilities. MPI vendor
names do not replace these capability probes. Open MPI installation checks are
allowed; this does not establish runtime/report-parser qualification.

Upstream 3.5 still searches for a command named python. The recipe supplies its
validated interpreter to that configure check and to both GNU make commands,
so no system python alias or Python 2 installation is needed. Generic REMORA
installation uses Bash; Python 3 is used only by the selected dependency's build.
Output remains lib/libmpiP.a and lib/libmpiP.so with runtime NOT_RUN. Address
translation/demangling/libunwind remain disabled in this initial recipe, as in
the previous recipe; callsite/source fidelity needs separate runtime validation.

Explicit legacy mode retains its historical MPI module selection but delegates
mpiP builds to this same recipe. Its unique dependency work/receipts are retained
under TMPDIR (default /tmp); on HPC set TMPDIR to a task-owned scratch directory
and include that directory in result backups. Logs and library destination paths
are quoted, including paths with spaces. No historical archives are glob-selected.

The recipe also repairs the release's Fortran-symbol C probe in its extracted
configure script: it declares `extern void FF(void)` and uses `int main(void)`.
The exact single expected upstream probe must be present; unexpected source
layout fails closed. This avoids implicit-int/function-declaration errors in
modern C compilers without weakening user compiler flags or disabling Fortran.
The retained release archive, generated library API and receipt schema are unchanged.

Native F08 compatibility is maintained in `compatibility/mpiP-f08/` and the
Bash `packages/mpip-f08.sh` recipe. The original C/legacy archive is retained;
the F08 static archive requires the matching MPI Fortran linker. The combined
shared library supplies qualified F08 preload entries. `fortran_f08.status`
distinguishes `BUILT` from `UNAVAILABLE`; runtime remains `NOT_RUN` during
installation. See that directory's README for exact coverage and ABI limits.
