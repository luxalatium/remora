# Local lifecycle regression tests

From the repository root, run with Python 3.6+ and Bash:

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py'
```

The tests run the real `src/remora` entrypoint in temporary directories with
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
