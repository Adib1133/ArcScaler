from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from core.safety import CrashWatchdog, SafetyManager


class WatchdogTests(unittest.TestCase):
    def test_reporter_pid_exact_match(self):
        self.assertTrue(CrashWatchdog.reporter_targets(['WerFault.exe', '-p', '123'], [123]))
        self.assertFalse(CrashWatchdog.reporter_targets(['WerFault.exe', '-p', '1234'], [123]))
        self.assertFalse(CrashWatchdog.reporter_targets(['WerFault.exe', '123'], [123]))

    def test_normal_early_exit_no_recovery(self):
        safety = Mock()
        watchdog = CrashWatchdog('x.exe', '.', safety, surveillance_duration=0.01)
        proc = Mock()
        proc.poll.return_value = 0
        with patch('core.safety.psutil.process_iter', return_value=[]):
            watchdog._surveillance_loop(proc, 98765)
        safety.rollback.assert_not_called()
        proc.kill.assert_not_called()

    def test_crash_while_process_active_retains_backup(self):
        safety = Mock()
        callback = Mock()
        wd = CrashWatchdog('x.exe', '.', safety, on_crash_detected=callback, surveillance_duration=1)
        proc = Mock()
        proc.poll.return_value = None
        reporter = Mock(info={'name': 'WerFault.exe'})
        reporter.cmdline.return_value = ['WerFault.exe', '-p', '123']
        with patch('core.safety.psutil.process_iter', return_value=[reporter]), patch('core.safety.psutil.Process'):
            wd._surveillance_loop(proc, 123)
        safety.rollback.assert_not_called()
        proc.kill.assert_not_called()
        self.assertFalse(callback.call_args.args[1]['success'])

    def test_stop_prevents_callbacks_and_rollback(self):
        safety, callback = Mock(), Mock()
        wd = CrashWatchdog('x.exe', '.', safety, on_launch_success=callback)
        wd.stop()
        wd._surveillance_loop(Mock(), 123)
        callback.assert_not_called()
        safety.rollback.assert_not_called()

    def test_real_nonzero_process_exit_restores_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            original = folder / 'dxgi.dll'
            original.write_bytes(b'original')
            SafetyManager.create_pre_injection_snapshot(tmp, ['dxgi.dll'], {})
            original.write_bytes(b'proxy')
            from core.files import sha256
            manifest=SafetyManager.load_manifest(tmp)
            SafetyManager.save_stage(manifest,'deploying',expected={tmp:{'dxgi.dll':sha256(original)}})
            script = folder / 'exit.py'
            script.write_text('raise SystemExit(7)')
            callback = Mock()
            wd = CrashWatchdog([sys.executable, str(script)], tmp, SafetyManager(), on_crash_detected=callback, surveillance_duration=3)
            self.assertTrue(wd.launch_and_monitor())
            wd._monitor_thread.join(5)
            self.assertFalse(wd._monitor_thread.is_alive())
            self.assertTrue(callback.call_args.args[1]['success'])
            self.assertEqual(original.read_bytes(), b'original')
