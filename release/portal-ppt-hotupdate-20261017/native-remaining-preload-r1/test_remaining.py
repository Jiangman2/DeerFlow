import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import types
import unittest
from unittest import mock
import zlib

HERE = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


N = load(HERE / 'resume_remaining.py', 'remaining_preload')
G = N.load_supervisor()
B = load(HERE.parent / 'native-preload-r2/preload_native_r2.py', 'helpers')
F = load(HERE.parent / 'native-preload-attempt-r3/derived_target_code.py', 'frozen_target')
F.B = B
PROFILE = json.loads((HERE.parent / 'native-preload-r1/PROFILE.json').read_bytes())
SUCCESS = json.loads((HERE.parent / 'native-gateway-recovery-evidence-r1/REPORT.json').read_bytes())


class RemainingFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.input = self.root / 'metadata.tar'; self.input.write_bytes(b'held metadata fixture'); self.input.chmod(0o600)
        self.fd = os.open(self.input, os.O_RDONLY)
        self.handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}

    def tearDown(self):
        for sig, handler in self.handlers.items(): signal.signal(sig, handler)
        os.close(self.fd); self.temp.cleanup()

    def test_exact_supervisor_and_actual_success_binding(self):
        self.assertEqual(zlib.decompress(base64.b64decode(N.SUPERVISOR_Z64)), (HERE.parent / 'native-gateway-recovery-r1/recover_gateway.py').read_bytes())
        report = (HERE.parent / 'native-gateway-recovery-evidence-r1/REPORT.json').read_bytes()
        self.assertEqual((len(report), N.sha(report)), (N.SUCCESS_REPORT_BYTES, N.SUCCESS_REPORT_SHA))
        self.assertEqual(N.SUCCESS_EVIDENCE.as_posix(), SUCCESS['evidence'])
        self.assertEqual(N.ROLES, ('gateway-rollback-compatible', 'ppt-master-runner-forward', 'ppt-master-runner-rollback-compatible', 'ppt-master-agent-host-forward'))

    def boundary_fixture(self):
        state = self.root / 'success'; state.mkdir(mode=0o700)
        report = json.loads(json.dumps(SUCCESS)); report['evidence'] = str(state)
        for name, record in report['import_receipt']['logs'].items():
            raw = ('fixture ' + name).encode(); B.write_new(state / name, raw)
            record.update(path=str(state / name), saved_bytes=len(raw), observed_bytes=len(raw), sha256=N.sha(raw))
        raw = B.encoded(report); B.write_new(state / 'REPORT.json', raw)
        B.write_new(state / 'GATEWAY-IMPORT-RESULT.json', B.encoded(report['import_receipt']))
        B.write_new(state / 'GATEWAY-IMPORT-REQUESTED.json', B.encoded(dict(role='gateway-forward', phase='PHASE-B', automatic_retry=False)))
        started = {key: report[key] for key in ('schema', 'operation', 'evidence', 'host_identity', 'prior_receipts')}
        started.update(status='INCOMPLETE', gateway_import_requested=False)
        B.write_new(state / 'STARTED.json', B.encoded(started))
        once = self.root / 'gateway.once'; B.write_new(once, B.encoded(dict(operation=N.OP, evidence=str(state), automatic_retry=False)))
        return state, report, raw, once

    def test_success_boundary_accepts_linkage_rejects_report_log_and_once_changes(self):
        state, report, raw, once = self.boundary_fixture()
        with mock.patch.multiple(N, SUCCESS_EVIDENCE=state, SUCCESS_REPORT_SHA=N.sha(raw), SUCCESS_REPORT_BYTES=len(raw)), mock.patch.object(G, 'ONCE', once):
            accepted = N.success_boundary(G, F, PROFILE, report['host_identity'])
            self.assertTrue(accepted['linked_request_result_and_logs_verified'])
            path = state / 'REPORT.json'; path.write_bytes(raw + b' ')
            with self.assertRaisesRegex(ValueError, 'gateway_success_report_changed'): N.success_boundary(G, F, PROFILE, report['host_identity'])
            path.write_bytes(raw)
            path = state / 'stderr.private.log'; original = path.read_bytes(); path.write_bytes(original + b'x')
            with self.assertRaisesRegex(ValueError, 'gateway_success_log_changed'): N.success_boundary(G, F, PROFILE, report['host_identity'])
            path.write_bytes(original)
            once.write_bytes(B.encoded(dict(operation=N.OP, evidence='/different', automatic_retry=False)))
            with self.assertRaisesRegex(ValueError, 'gateway_success_once_changed'): N.success_boundary(G, F, PROFILE, report['host_identity'])

    def test_success_boundary_rejects_symlink_extra_entry_and_identity_drift(self):
        state, report, raw, once = self.boundary_fixture()
        with mock.patch.multiple(N, SUCCESS_EVIDENCE=state, SUCCESS_REPORT_SHA=N.sha(raw), SUCCESS_REPORT_BYTES=len(raw)), mock.patch.object(G, 'ONCE', once):
            with self.assertRaisesRegex(ValueError, 'gateway_success_semantics'): N.success_boundary(G, F, PROFILE, {})
            (state / 'extra').symlink_to(self.root / 'missing')
            with self.assertRaisesRegex(ValueError, 'gateway_success_directory_changed'): N.success_boundary(G, F, PROFILE, report['host_identity'])

    @contextlib.contextmanager
    def main_fixture(self, fail_role=None, final_failure=False, previous_failure=False, already=()):
        import fcntl
        plan = {'images': PROFILE['images'], 'allocation': {'maximum_metadata_import_bytes': 291167}}
        receipts = {row['role'] + '.SEALED.json': {} for row in PROFILE['images']}
        reports, launches, checks = [], [], []
        lock_path = self.root / 'shared.lock'; committed = set(N.PREVIOUS) | set(already)
        def locked():
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600); fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); return fd
        def exact_lock():
            fd = os.open(lock_path, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError): fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally: os.close(fd)
        def command(row, fd, no_unpack):
            exact_lock(); self.assertFalse(no_unpack); launches.append(row['role'])
            code = "import sys;assert open(sys.argv[-1],'rb').read()==b'held metadata fixture';sys.stdout.write('ROLE=" + row['role'] + "');"
            code += "sys.stderr.write('permission denied secret=PRIVATE');sys.exit(23)" if row['role'] == fail_role else 'sys.exit(0)'
            return [sys.executable, '-I', '-B', '-c', code, '/proc/self/fd/' + str(fd)]
        def complete(row):
            exact_lock(); checks.append(row['role'])
            if previous_failure and row['role'] == 'gateway-forward': raise ValueError('previous_incomplete')
            if final_failure and len(checks) > 6: raise ValueError('final_reference_changed')
            committed.add(row['role']); return N.expected_completion(row), {}
        helper = types.SimpleNamespace(**B.__dict__)
        helper.complete_row = complete; helper.collision_check = mock.Mock(); helper.progress = mock.Mock()
        helper.host_projection = mock.Mock(return_value={'state': 'SAFE_IMAGE_PATHS_BOUND'})
        helper.postgres_cache = mock.Mock(return_value={'state': 'CACHED_LINUX_ARM64_IDENTITY_BOUND'})
        module = types.SimpleNamespace(B=helper, common_lock=locked, REPORT=None, fd_regular=F.fd_regular, private_bytes=F.private_bytes,
            all_content_present=mock.Mock(), verify_references=mock.Mock(), storage_check=mock.Mock(), base_report=lambda op: {}, import_argv=command,
            handoff_report=lambda raw: reports.append(json.loads(raw)) or '/fixture/REPORT.json')
        module.write_receipt = lambda name, value: B.write_new(module.STATE / name, B.encoded(value))
        guard = types.SimpleNamespace(inspect_checkpoint=lambda *args: (plan, receipts), open_sealed=lambda *args: os.open(self.root, os.O_RDONLY | os.O_DIRECTORY),
                                     metadata_fd=lambda *args: os.dup(self.fd))
        def snapshot(supervisor, module, image):
            return dict(already_committed=image['role'] in committed, committed_prefix=1 if image['role'] in committed else 0)
        with mock.patch.multiple(N, ROOT=self.root, ONCE=self.root / 'new-once'), mock.patch.object(N, 'load_supervisor', return_value=G), \
                mock.patch.object(G, 'load', return_value=(guard, module, PROFILE, {})), mock.patch.object(G, 'old_boundary', return_value={}), \
                mock.patch.object(N, 'success_boundary', return_value={}), mock.patch.object(N, 'snapshot_gate', side_effect=snapshot), \
                contextlib.redirect_stdout(io.StringIO()):
            yield reports, launches, checks, module

    def test_four_real_children_in_order_private_directories_common_lock_and_once(self):
        with self.main_fixture() as (reports, launches, checks, module):
            self.assertEqual(N.run(), 0); self.assertEqual(N.run(), 1)
        self.assertEqual(launches, list(N.ROLES)); self.assertNotIn('frontend', launches); self.assertNotIn('gateway-forward', launches)
        self.assertEqual(checks[:2], list(N.PREVIOUS)); self.assertEqual(checks[-6:], [row['role'] for row in PROFILE['images']])
        report = reports[0]; self.assertEqual(report['status'], 'COMPLETE_PRELOAD_REVIEW_REQUIRED'); self.assertEqual(report['phase_b'], 'PASS')
        self.assertFalse(report['ready_for_rollout']); self.assertFalse(report['image_import_may_be_partial']); self.assertEqual(len(report['final_six_images']), 6)
        for row in report['role_results']:
            self.assertEqual(row['status'], 'COMPLETE'); self.assertEqual(row['import_returncode'], 0)
            self.assertTrue(row['import_receipt']['capture_complete']); self.assertEqual(row['import_receipt']['role'], row['role'])
            state = Path(row['evidence']); self.assertEqual(state.name, row['role'])
            self.assertEqual((state / 'stdout.private.log').read_text(), 'ROLE=' + row['role'])
            self.assertEqual((state.stat().st_mode & 0o777), 0o700)
        self.assertEqual(reports[1]['error_code'], 'remaining_preload_already_started')
        self.assertEqual(module.storage_check.call_count, 4)

    def test_second_role_real_nonzero_preserves_first_and_stops_later_roles(self):
        with self.main_fixture(fail_role=N.ROLES[1]) as (reports, launches, checks, module): self.assertEqual(N.run(), 1)
        self.assertEqual(launches, list(N.ROLES[:2])); report = reports[0]
        self.assertEqual(report['status'], 'INCOMPLETE'); self.assertEqual(report['error_code'], 'import_nonzero')
        self.assertEqual(report['role_results'][0]['status'], 'COMPLETE'); failed = report['role_results'][1]
        self.assertEqual(failed['import_returncode'], 23); self.assertTrue(failed['import_receipt']['capture_complete'])
        self.assertIn('permission denied', failed['safe_error_phrases']); self.assertNotIn('secret=PRIVATE', json.dumps(report))
        self.assertTrue(report['image_import_may_be_partial']); self.assertEqual(report['final_six_images'], [])

    def test_final_verification_failure_cannot_report_complete(self):
        with self.main_fixture(final_failure=True) as (reports, launches, checks, module): self.assertEqual(N.run(), 1)
        self.assertEqual(launches, list(N.ROLES)); report = reports[0]
        self.assertEqual(report['status'], 'INCOMPLETE'); self.assertEqual(report['error_code'], 'final_reference_changed')
        self.assertNotEqual(report['phase_b'], 'PASS'); self.assertEqual([row['status'] for row in report['role_results']], ['COMPLETE'] * 4)

    def test_previous_verification_failure_stops_before_any_import(self):
        with self.main_fixture(previous_failure=True) as (reports, launches, checks, module): self.assertEqual(N.run(), 1)
        self.assertEqual(launches, []); self.assertEqual(reports[0]['role_results'], [])
        self.assertEqual(reports[0]['error_code'], 'previous_incomplete'); self.assertFalse(reports[0]['target_import_requested'])

    def test_already_committed_remaining_role_is_verified_without_reimport(self):
        with self.main_fixture(already=(N.ROLES[0],)) as (reports, launches, checks, module): self.assertEqual(N.run(), 0)
        self.assertEqual(launches, list(N.ROLES[1:])); first = reports[0]['role_results'][0]
        self.assertFalse(first['import_requested']); self.assertIsNone(first['import_returncode']); self.assertEqual(first['status'], 'COMPLETE')

    def test_postgres_projection_cancellation_cannot_become_a_successful_gap(self):
        with self.main_fixture(already=N.ROLES) as (reports, launches, checks, module):
            module.B.postgres_cache.side_effect = ValueError('interrupted_no_retry')
            self.assertEqual(N.run(), 1)
        self.assertEqual(launches, []); self.assertEqual(reports[0]['status'], 'INCOMPLETE')
        self.assertEqual(reports[0]['error_code'], 'interrupted_no_retry'); self.assertNotEqual(reports[0]['phase_b'], 'PASS')

    def test_host_projection_frozen_interruption_is_not_swallowed(self):
        with self.main_fixture(already=N.ROLES) as (reports, launches, checks, module):
            module.B.host_projection.side_effect = B.Failure('interrupted_no_automatic_retry')
            self.assertEqual(N.run(), 1)
        self.assertEqual(launches, []); self.assertEqual(reports[0]['status'], 'INCOMPLETE')
        self.assertEqual(reports[0]['error_code'], 'interrupted_no_automatic_retry'); self.assertNotEqual(reports[0]['phase_b'], 'PASS')


if __name__ == '__main__': unittest.main(verbosity=2)
