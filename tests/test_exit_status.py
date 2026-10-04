"""Local lifecycle regressions; no real collector, SSH, or scheduler is run."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest

SOURCE = Path(os.environ.get('REMORA_TEST_SOURCE_ROOT', Path(__file__).resolve().parents[1]))
BASH = shutil.which('bash') or '/bin/bash'


class ExitStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='remora-status-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'install'
        self.out = self.root / 'output'
        self.out.mkdir()
        self.commands = self.root / 'commands'
        self.entry = self.root / 'remora'
        self.write(self.entry, (SOURCE / 'src/remora').read_text())
        self.write(self.bin / 'scripts/remora_init.sh', '''remora_init() {
    export REMORA_OUTDIR="$TEST_OUTPUT" REMORA_TMPDIR="$TEST_OUTPUT"
    export REMORA_PLOT_RESULTS=0 REMORA_MODE=BASIC REMORA_PARALLEL=0
    printf 'mocknode\\n' > "$REMORA_OUTDIR/remora_nodes.txt"
    printf 'export REMORA_ACTIVE_MODULES="" REMORA_ACTIVE_MODULES_OUTDIRS=""\\n' > "$REMORA_OUTDIR/remora_env.txt"
}
''')
        self.write(self.bin / 'scripts/remora_collect.sh', '''remora_collect() {
    : > "$REMORA_OUTDIR/.remora_out_mocknode"
}
''')
        self.write(self.bin / 'aux/extra', ':\n')
        self.write(self.bin / 'aux/report', 'show_summary_report() { :; }\n')
        self.write(self.bin / 'modules/modules_utils', ':\n')
        for name in ('ssh', 'scp', 'pkill', 'rm', 'mv', 'scontrol'):
            self.write(self.commands / name, '#!/bin/bash\nprintf "%s\\n" "$0 $*" >> "$TEST_CALLS"\n')
        self.write(self.commands / 'sleep', '#!/bin/bash\nexit 0\n')
        self.write(self.commands / 'pgrep', '#!/bin/bash\nexit 1\n')
        self.write(self.commands / 'date', '#!/bin/bash\nprintf "1700000000000000000\\n"\n')
        # An allowlist prevents the caller's REMORA/Slurm/LD_PRELOAD settings
        # from enabling external collection or changing the fixture behavior.
        self.env = {'PATH': str(self.commands) + os.pathsep + os.defpath,
                    'HOME': str(self.root), 'LC_ALL': 'C',
                    'REMORA_BIN': str(self.bin), 'REMORA_VERBOSE': '0',
                    'TEST_OUTPUT': str(self.out), 'TEST_CALLS': str(self.root / 'calls'),
                    'TEST_FINALIZED': str(self.root / 'finalized')}
        self.stub_finalize(0)

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o700)

    def stub_finalize(self, status):
        self.write(self.bin / 'scripts/remora_finalize.sh',
                   'remora_finalize() {\n'
                   '    printf "done\\n" >> "$TEST_FINALIZED"\n'
                   '    return %d\n}\n' % status)

    def invoke(self, app, env=None):
        proc = subprocess.Popen([BASH, str(self.entry), *app], cwd=self.root,
                                env=self.env if env is None else env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            # Only terminate the isolated process group created by this test.
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            self.fail('local fixture timed out')
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)

    def finalized_count(self):
        path = self.root / 'finalized'
        return len(path.read_text().splitlines()) if path.exists() else 0

    def test_application_status_survives_finalize_and_verbose(self):
        for verbose in ('0', '1'):
            self.env['REMORA_VERBOSE'] = verbose
            for cleanup_status in (0, 23):
                self.stub_finalize(cleanup_status)
                for app_status in (0, 17, 42, 255):
                    with self.subTest(verbose=verbose, cleanup=cleanup_status, app=app_status):
                        count = self.finalized_count()
                        result = self.invoke([BASH, '-c', 'exit %d' % app_status])
                        self.assertEqual(result.returncode, app_status, result.stderr)
                        self.assertEqual(self.finalized_count(), count + 1)

    def test_application_status_with_real_finalize(self):
        self.write(self.bin / 'scripts/remora_finalize.sh',
                   (SOURCE / 'src/scripts/remora_finalize.sh').read_text())
        for verbose in ('0', '1'):
            self.env['REMORA_VERBOSE'] = verbose
            for app_status in (0, 17):
                with self.subTest(verbose=verbose, app=app_status):
                    result = self.invoke([BASH, '-c', 'exit %d' % app_status])
                    self.assertEqual(result.returncode, app_status, result.stderr)
                    self.assertNotIn('WARNING: REMORA finalization failed', result.stderr)

    def test_finalize_failure_is_visible_without_replacing_app_status(self):
        self.stub_finalize(23)
        result = self.invoke([BASH, '-c', 'exit 17'])
        self.assertEqual(result.returncode, 17)
        self.assertIn('WARNING: REMORA finalization failed (status 23)', result.stderr)
        self.assertIn('17', result.stderr)

    def test_missing_installation_is_failure(self):
        env = dict(self.env)
        del env['REMORA_BIN']
        result = self.invoke([BASH, '-c', 'echo APP_MUST_NOT_RUN'], env)
        self.assertEqual(result.returncode, 1)
        self.assertIn('REMORA_BIN undefined', result.stderr)
        self.assertNotIn('APP_MUST_NOT_RUN', result.stdout)
        self.assertEqual(self.finalized_count(), 0)

    def test_missing_command_preserves_127_and_finalizes(self):
        result = self.invoke([str(self.root / 'does-not-exist')])
        self.assertEqual(result.returncode, 127)
        self.assertEqual(self.finalized_count(), 1)

    def test_non_executable_command_preserves_126_and_finalizes(self):
        app = self.root / 'not-executable'
        app.write_text('#!/bin/bash\nexit 0\n')
        app.chmod(0o600)
        result = self.invoke([str(app)])
        self.assertEqual(result.returncode, 126)
        self.assertEqual(self.finalized_count(), 1)

    def test_child_sigterm_status_is_preserved(self):
        result = self.invoke([BASH, '-c', 'kill -TERM $$'])
        self.assertEqual(result.returncode, 128 + signal.SIGTERM)
        self.assertEqual(self.finalized_count(), 1)

    def test_unmonitored_fallback_still_preserves_application_status(self):
        self.write(self.commands / 'pgrep', '#!/bin/bash\nprintf "99999999\\n"\n')
        for app_status in (0, 17):
            with self.subTest(app=app_status):
                result = self.invoke([BASH, '-c', 'exit %d' % app_status])
                self.assertEqual(result.returncode, app_status)
                self.assertIn('without REMORA monitoring', result.stdout)
        self.assertEqual(self.finalized_count(), 0)


if __name__ == '__main__':
    unittest.main()
