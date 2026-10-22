import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
import zlib

HERE = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


G = load(HERE / 'recover_gateway.py', 'gateway_recovery')
F = load(HERE.parent / 'native-preload-attempt-r3/derived_target_code.py', 'frozen_target')
B = load(HERE.parent / 'native-preload-r2/preload_native_r2.py', 'helpers')
F.B = B
A = types.ModuleType('budget')
exec(compile(zlib.decompress(base64.b64decode(F.ALLOCATION_Z64)), 'budget.py', 'exec'), A.__dict__)


class RecoveryFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.input = self.root / 'metadata.tar'; self.input.write_bytes(b'fixture'); self.input.chmod(0o600)
        self.fd = os.open(self.input, os.O_RDONLY)

    def tearDown(self):
        os.close(self.fd); self.temp.cleanup()

    def module(self, code):
        module = types.SimpleNamespace(B=B, STATE=self.root, fd_regular=F.fd_regular, private_bytes=F.private_bytes,
            storage_check=mock.Mock(), import_argv=lambda row, fd, no_unpack: [sys.executable, '-I', '-B', '-c', code, '/proc/self/fd/' + str(fd)])
        module.write_receipt = lambda name, value: B.write_new(module.STATE / name, B.encoded(value))
        return module

    def test_exact_diagnostic_pins_and_embedded_guard(self):
        raw = (HERE.parent / 'native-phase-b-diagnostic-evidence-r1/REPORT.json').read_bytes()
        self.assertEqual((len(raw), G.sha(raw)), (13787, G.DIAGNOSTIC_SHA))
        data = json.loads(raw)
        self.assertEqual(G.PINS, {name: {'bytes': row['bytes'], 'sha256': row['sha256']} for name, row in data['fixed_receipts'].items()})
        guard = zlib.decompress(base64.b64decode(G.GUARD_Z64))
        self.assertEqual(G.sha(guard), G.GUARD_SHA)
        self.assertEqual(guard, (HERE.parent / 'native-phase-b-resume-r1/resume_phase_b_bound.py').read_bytes())

    def test_real_nonzero_retains_code_private_stderr_and_safe_projection(self):
        module = self.module("import sys;sys.stdout.write('normal output');sys.stderr.write('failed to extract layer: permission denied token=SECRET');sys.exit(7)")
        result = {}
        with mock.patch.object(B, 'progress'):
            with self.assertRaisesRegex(ValueError, 'import_nonzero'):
                G.captured_import(module, {'role': 'gateway-forward'}, self.fd, {}, {}, None, result)
        self.assertEqual(result['import_returncode'], 7)
        self.assertEqual((self.root / 'stderr.private.log').read_bytes(), b'failed to extract layer: permission denied token=SECRET')
        self.assertEqual(stat.S_IMODE((self.root / 'stderr.private.log').stat().st_mode), 0o600)
        self.assertIn('permission denied', result['safe_error_phrases'])
        self.assertNotIn('SECRET', json.dumps(result))
        receipt = json.loads((self.root / 'GATEWAY-IMPORT-RESULT.json').read_bytes())
        self.assertEqual(receipt['returncode'], 7); self.assertTrue(receipt['capture_complete'])

    def test_output_limit_is_bounded_and_true_child_code_retained(self):
        module = self.module("import sys;sys.stderr.write('X'*8192)"); result = {}
        with mock.patch.object(G, 'LIMIT', 1024), mock.patch.object(B, 'progress'):
            with self.assertRaisesRegex(ValueError, 'import_pipe_failure|import_output_limit_or_capture_failed'):
                G.captured_import(module, {'role': 'gateway-forward'}, self.fd, {}, {}, None, result)
        self.assertLessEqual((self.root / 'stderr.private.log').stat().st_size, 1024)
        self.assertTrue(result['import_receipt']['output_limit_exceeded'])
        self.assertIsInstance(result['import_returncode'], int)

    def test_interruption_cleans_owned_process_and_retains_actual_termination(self):
        module = self.module('import time;time.sleep(30)'); result = {}
        old = signal.getsignal(signal.SIGTERM)
        def interrupt(signum, frame): raise ValueError('interrupted_no_retry')
        timer = threading.Timer(.2, lambda: os.kill(os.getpid(), signal.SIGTERM))
        try:
            signal.signal(signal.SIGTERM, interrupt); timer.start()
            with mock.patch.object(B, 'progress'):
                with self.assertRaisesRegex(ValueError, 'interrupted_no_retry'):
                    G.captured_import(module, {'role': 'gateway-forward'}, self.fd, {}, {}, None, result)
            self.assertIn(result['import_returncode'], (-15, -9))
            self.assertTrue((self.root / 'GATEWAY-IMPORT-RESULT.json').is_file())
        finally:
            timer.cancel(); timer.join(); signal.signal(signal.SIGTERM, old)

    def test_snapshot_prefix_allowed_and_active_extract_conflict_rejected(self):
        one, two = 'sha256:' + 'a'*64, 'sha256:' + 'b'*64
        image = {'layers': [{'chain_id': one, 'parent': ''}, {'chain_id': two, 'parent': one}]}
        module = types.SimpleNamespace(A=A, B=types.SimpleNamespace(CTR=['ctr'], capture=mock.Mock()))
        for raw, prefix in ((f'KEY PARENT KIND\n{one} Committed\n', 1), (f'KEY PARENT KIND\n{one} Committed\n{two} {one} Committed\n', 2)):
            module.B.capture.return_value = raw.encode()
            observed = G.gateway_snapshot_gate(module, image)
            self.assertEqual(observed['committed_prefix'], prefix)
            self.assertEqual(observed['already_committed'], prefix == 2)
        module.B.capture.return_value = f'KEY PARENT KIND\nextract-123-random {two} {one} Active\n'.encode()
        with self.assertRaisesRegex(ValueError, 'gateway_active_or_view_snapshot_requires_review'):
            G.gateway_snapshot_gate(module, image)

    def test_thread_start_failure_still_stops_child_and_records_true_returncode(self):
        module = self.module('import time;time.sleep(30)'); result = {}
        with mock.patch.object(G.threading.Thread, 'start', side_effect=RuntimeError('fixture start failure')), mock.patch.object(B, 'progress'):
            with self.assertRaisesRegex(RuntimeError, 'fixture start failure'):
                G.captured_import(module, {'role': 'gateway-forward'}, self.fd, {}, {}, None, result)
        self.assertIn(result['import_returncode'], (-15, -9))
        self.assertFalse(result['import_receipt']['capture_complete'])
        self.assertTrue((self.root / 'GATEWAY-IMPORT-RESULT.json').is_file())

    def test_prior_seven_receipts_reject_byte_change_and_extra_file(self):
        data = {'REPORT.json': (HERE.parent / 'native-phase-b-attempt-r1/REPORT.json').read_bytes(),
                'FAIL.json': (HERE.parent / 'native-phase-b-attempt-r1/TARGET-FAIL.json').read_bytes()}
        state = self.root / 'prior'; state.mkdir(mode=0o700)
        pins = {}
        for name in G.PINS:
            raw = data.get(name, b'{}\n'); path = state / name; path.write_bytes(raw); path.chmod(0o600)
            pins[name] = {'bytes': len(raw), 'sha256': G.sha(raw)}
        with mock.patch.multiple(G, PRIOR=state, PINS=pins):
            self.assertEqual(len(G.old_boundary(F)), 7)
            changed = state / 'frontend.PHASE-B-PASS.json'; original = changed.read_bytes(); changed.write_bytes(original + b' ')
            with self.assertRaisesRegex(ValueError, 'prior_phase_b_receipt_changed'): G.old_boundary(F)
            changed.write_bytes(original)
            (state / 'later.json').symlink_to(state / 'missing')
            with self.assertRaisesRegex(ValueError, 'prior_phase_b_state_changed'): G.old_boundary(F)

    def test_success_spawns_one_child_and_uses_held_readonly_input(self):
        module = self.module("import sys;assert open(sys.argv[-1],'rb').read()==b'fixture';sys.stdout.write('success')")
        result = {}; original = G.subprocess.Popen
        with mock.patch.object(G.subprocess, 'Popen', wraps=original) as launch, mock.patch.object(B, 'progress'):
            G.captured_import(module, {'role': 'gateway-forward'}, self.fd, {}, {}, None, result)
        self.assertEqual(launch.call_count, 1); self.assertEqual(result['import_returncode'], 0)
        self.assertEqual((self.root / 'stdout.private.log').read_bytes(), b'success')
        self.assertTrue(result['import_receipt']['capture_complete'])

    def test_final_snapshot_failure_keeps_incomplete_status(self):
        profile = json.loads((HERE.parent / 'native-preload-r1/PROFILE.json').read_bytes())
        plan = {'images': profile['images'], 'allocation': {'maximum_metadata_import_bytes': 291167}}
        receipts = {row['role'] + '.SEALED.json': {} for row in profile['images']}
        reports = []
        helper = types.SimpleNamespace(**B.__dict__)
        helper.complete_row = mock.Mock(side_effect=lambda row: ({'role': row['role']}, {}))
        helper.collision_check = mock.Mock(); helper.progress = mock.Mock()
        module = types.SimpleNamespace(B=helper, common_lock=lambda: os.open(self.input, os.O_RDONLY),
            REPORT=None, all_content_present=mock.Mock(), verify_references=mock.Mock(),
            storage_check=mock.Mock(), base_report=lambda op: {},
            handoff_report=lambda raw: reports.append(json.loads(raw)) or '/fixture/REPORT.json')
        module.write_receipt = lambda name, value: B.write_new(module.STATE / name, B.encoded(value))
        guard = types.SimpleNamespace(inspect_checkpoint=lambda *a: (plan, receipts),
            open_sealed=lambda m: os.open(self.root, os.O_RDONLY | os.O_DIRECTORY),
            metadata_fd=lambda *a: os.open(self.input, os.O_RDONLY))
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
        try:
            with mock.patch.multiple(G, ROOT=self.root, ONCE=self.root / 'new-once.json'), \
                    mock.patch.object(G, 'load', return_value=(guard, module, profile, {})), \
                    mock.patch.object(G, 'old_boundary', return_value={}), \
                    mock.patch.object(G, 'gateway_snapshot_gate', side_effect=[{'already_committed': False}, ValueError('final_snapshot_failed')]), \
                    mock.patch.object(G, 'captured_import') as importer, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(G.run(), 1)
                self.assertEqual(importer.call_count, 1)
            self.assertEqual(reports[0]['status'], 'INCOMPLETE')
            self.assertEqual(reports[0]['error_code'], 'final_snapshot_failed')
            self.assertFalse(reports[0]['ready_for_rollout'])
        finally:
            for sig, handler in handlers.items(): signal.signal(sig, handler)

    def test_main_imports_only_gateway_holds_lock_and_stops_after_success(self):
        import fcntl
        profile = json.loads((HERE.parent / 'native-preload-r1/PROFILE.json').read_bytes())
        plan = {'images': profile['images'], 'allocation': {'maximum_metadata_import_bytes': 291167}}
        receipts = {row['role'] + '.SEALED.json': {} for row in profile['images']}
        lockpath = self.root / 'lock'; once = self.root / 'new-once.json'; reports = []
        def locked():
            fd = os.open(lockpath, os.O_RDWR | os.O_CREAT, 0o600); fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); return fd
        helper = types.SimpleNamespace(**B.__dict__)
        helper.complete_row = mock.Mock(side_effect=lambda row: ({'role': row['role']}, {}))
        helper.collision_check = mock.Mock(); helper.progress = mock.Mock()
        module = types.SimpleNamespace(B=helper, common_lock=locked, REPORT=None, all_content_present=mock.Mock(),
            verify_references=mock.Mock(), storage_check=mock.Mock(), base_report=lambda op: {},
            handoff_report=lambda raw: reports.append(json.loads(raw)) or '/fixture/REPORT.json')
        module.write_receipt = lambda name, value: B.write_new(module.STATE / name, B.encoded(value))
        guard = types.SimpleNamespace(inspect_checkpoint=lambda *a: (plan, receipts),
            open_sealed=lambda m: os.open(self.root, os.O_RDONLY | os.O_DIRECTORY),
            metadata_fd=lambda *a: os.open(self.input, os.O_RDONLY))
        def capture(m, row, *args):
            self.assertEqual(row['role'], 'gateway-forward')
            fd = os.open(lockpath, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError): fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally: os.close(fd)
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
        try:
            with mock.patch.multiple(G, ROOT=self.root, ONCE=once), mock.patch.object(G, 'load', return_value=(guard, module, profile, {})), \
                    mock.patch.object(G, 'old_boundary', return_value={}), \
                    mock.patch.object(G, 'gateway_snapshot_gate', return_value={'already_committed': False}), \
                    mock.patch.object(G, 'captured_import', side_effect=capture) as importer, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(G.run(), 0); self.assertEqual(G.run(), 1)
                self.assertEqual(importer.call_count, 1)
            self.assertEqual([call.args[0]['role'] for call in helper.complete_row.call_args_list], ['frontend', 'gateway-forward'])
            self.assertEqual(reports[0]['status'], 'GATEWAY_COMPLETE_REVIEW_REQUIRED')
            self.assertFalse(reports[0]['ready_for_rollout']); self.assertFalse(reports[0]['other_four_roles_imported'])
            self.assertEqual(reports[1]['error_code'], 'gateway_recovery_already_started')
        finally:
            for sig, handler in handlers.items(): signal.signal(sig, handler)


if __name__ == '__main__': unittest.main(verbosity=2)
