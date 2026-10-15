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
`lib/libmpiP.so`. The main REMORA installer remains the sole publisher of the
final package prefix. The child may be used without loading REMORA's CLI.
Package recipes and tool requirements are declared in `packages.json` and
`packages/*.sh`; adding or upgrading a dependency does not add its flags or
compatibility checks to REMORA's configuration orchestrator.
