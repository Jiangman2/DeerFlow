import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import resume_glm_review as r


class NonSeekableTerminal(io.RawIOBase):
    def readable(self): return True
    def writable(self): return True
    def seekable(self): return False


class TerminalTests(unittest.TestCase):
    def test_reproduce_original_buffered_random_failure(self):
        with self.assertRaises(io.UnsupportedOperation):
            io.BufferedRandom(NonSeekableTerminal())

    def test_raw_descriptor_does_not_seek(self):
        termios = SimpleNamespace(tcgetattr=Mock(return_value=[0]*7))
        with patch.dict('sys.modules', {'termios': termios}), patch.object(r.os, 'open', return_value=5) as opened, \
             patch.object(r.os, 'isatty', return_value=True), patch.object(r.os, 'close') as closed, \
             patch.object(r.os, 'lseek') as seek:
            r.check_terminal()
        opened.assert_called_once_with('/dev/tty', r.os.O_RDWR | getattr(r.os, 'O_NOCTTY', 0))
        closed.assert_called_once_with(5)
        termios.tcgetattr.assert_called_once_with(5)
        seek.assert_not_called()

    def test_fd_closed_on_non_tty(self):
        termios = SimpleNamespace(tcgetattr=Mock())
        with patch.dict('sys.modules', {'termios': termios}), patch.object(r.os, 'open', return_value=5), \
             patch.object(r.os, 'isatty', return_value=False), patch.object(r.os, 'close') as closed:
            with self.assertRaisesRegex(r.CheckFailure, 'hidden_terminal_required'): r.check_terminal()
        closed.assert_called_once_with(5)
        termios.tcgetattr.assert_not_called()

    def test_fd_closed_on_termios_error(self):
        termios = SimpleNamespace(tcgetattr=Mock(side_effect=OSError('not exported')))
        with patch.dict('sys.modules', {'termios': termios}), patch.object(r.os, 'open', return_value=5), \
             patch.object(r.os, 'isatty', return_value=True), patch.object(r.os, 'close') as closed:
            with self.assertRaises(OSError): r.check_terminal()
        closed.assert_called_once_with(5)

    def test_getpass_fallback_is_fatal_before_echo(self):
        import warnings
        reached = []
        def fallback(*a):
            warnings.warn('cannot hide input', r.getpass.GetPassWarning)
            reached.append('unsafe stdin read')
            return 'test-only-token'
        with patch.object(r, 'check_terminal'), patch.object(r.getpass, 'getpass', side_effect=fallback):
            with self.assertRaises(r.getpass.GetPassWarning): r.hidden_token()
        self.assertEqual(reached, [])

    def test_token_shape_and_no_echo(self):
        for value in ('bad\nheader', '', 'with space', 'x'*4097):
            with patch.object(r, 'check_terminal'), patch.object(r.getpass, 'getpass', return_value=value):
                with self.assertRaises(r.CheckFailure): r.hidden_token()
        with patch.object(r, 'check_terminal'), patch.object(r.getpass, 'getpass', return_value='test-only-token'), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(r.hidden_token(), 'test-only-token')
        self.assertEqual(out.getvalue(), '')


class RecoveryTests(unittest.TestCase):
    def old(self):
        return {'audit': 'COMPLETE', 'failure': {'class': 'UnsupportedOperation'},
                'provider_submissions_maximum': 0, 'production_writes': 0, 'signals_sent': 0,
                'next88': {'source_pod_uid': 'new-pod', 'proposal_sha256': r.OLD_PROPOSAL_SHA}}

    def fake(self, old=None, requested=False):
        old = self.old() if old is None else old
        return SimpleNamespace(ROOT=Path('/old'), APP_UID='new-pod',
                  e=SimpleNamespace(locked_receipt=Mock(side_effect=[old, {'apply_authorized_by_this_file': False}])),
                  recovery=SimpleNamespace(exists=lambda p: requested))

    def test_prior_zero_requests_accepted(self):
        with patch.object(r, 'review', self.fake()):
            old, proposal = r.previous_zero_submission()
        self.assertEqual(old['provider_submissions_maximum'], 0)
        self.assertFalse(proposal['apply_authorized_by_this_file'])

    def test_prior_requested_even_if_no_result_is_not_repeated(self):
        with patch.object(r, 'review', self.fake(requested=True)):
            with self.assertRaisesRegex(r.CheckFailure, 'previous_submission_possible'): r.previous_zero_submission()

    def test_prior_possible_send_counter_is_not_repeated(self):
        old = self.old(); old['provider_submissions_maximum'] = 2
        with patch.object(r, 'review', self.fake(old)):
            with self.assertRaisesRegex(r.CheckFailure, 'previous_not_known_pre_submission'): r.previous_zero_submission()

    def test_prior_glm_receipt_is_not_repeated(self):
        old = self.old(); old['glm'] = {'wire_probe': 'UNCONFIRMED'}
        with patch.object(r, 'review', self.fake(old)):
            with self.assertRaises(r.CheckFailure): r.previous_zero_submission()

    def test_prior_proposal_hash_mismatch_refused(self):
        old = self.old(); old['next88']['proposal_sha256'] = 'changed'
        with patch.object(r, 'review', self.fake(old)):
            with self.assertRaisesRegex(r.CheckFailure, 'previous_scope_or_proposal'): r.previous_zero_submission()

    def test_live_process_change_blocks(self):
        fake = SimpleNamespace(bind=lambda: ({}, {}, {}, {}),
                 recovery=SimpleNamespace(same_json=lambda *a: False), obs=SimpleNamespace(identity=lambda *a: []))
        with patch.object(r, 'review', fake):
            with self.assertRaisesRegex(r.CheckFailure, 'app_process_changed'):
                r.verify_live({'app': {'identity': []}}, {})

    def test_no_request_record_before_credential_success(self):
        fake = SimpleNamespace(obs=SimpleNamespace(health=lambda *a: {}, DISPATCHER='dispatch', write_new=Mock()),
                               APP_UID='pod')
        with patch.object(r, 'review', fake), patch.object(r, 'previous_zero_submission', return_value=({}, {})), \
             patch.object(r, 'verify_live', return_value=({}, {'dispatch': [{}]})), \
             patch.object(r, 'hidden_token', side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
            report = {}
            with self.assertRaises(KeyboardInterrupt): r.run(report)
        self.assertEqual(report['phase'], 'READ_HIDDEN_CREDENTIAL')
        fake.obs.write_new.assert_not_called()

    def test_requested_boundary_precedes_exec(self):
        events = []
        fake = SimpleNamespace(obs=SimpleNamespace(health=lambda *a: {}, DISPATCHER='dispatch', utc=lambda: 'UTC',
                 write_new=lambda path, data: events.append(path.name), identity=lambda *a: []),
                 APP_UID='pod', recovery=SimpleNamespace(same_json=lambda *a: True),
                 remote_probe=lambda *a: events.append('remote_probe') or {'wire_probe': 'COMPLETE'})
        with patch.object(r, 'review', fake), patch.object(r, 'previous_zero_submission', return_value=({}, {})), \
             patch.object(r, 'verify_live', return_value=({}, {'dispatch': [{}]})), \
             patch.object(r, 'hidden_token', return_value='test-only-token'), contextlib.redirect_stdout(io.StringIO()):
            report = {}; r.run(report)
        self.assertLess(events.index('GLM-REQUESTED.json'), events.index('remote_probe'))
        self.assertEqual(events.count('remote_probe'), 1)
        self.assertEqual(report['collection'], 'COMPLETE')
        self.assertEqual(report['native_weknora_acceptance'], 'NOT_TESTED')

    def test_uncertain_transport_retains_requested_boundary_and_never_retries(self):
        events = []
        remote = Mock(side_effect=RuntimeError('private transport detail'))
        fake = SimpleNamespace(obs=SimpleNamespace(health=lambda *a: {}, DISPATCHER='dispatch', utc=lambda: 'UTC',
                 write_new=lambda path, data: events.append(path.name), identity=lambda *a: []),
                 APP_UID='pod', recovery=SimpleNamespace(same_json=lambda *a: True), remote_probe=remote)
        with patch.object(r, 'review', fake), patch.object(r, 'previous_zero_submission', return_value=({}, {})), \
             patch.object(r, 'verify_live', return_value=({}, {'dispatch': [{}]})), \
             patch.object(r, 'hidden_token', return_value='test-only-token'), contextlib.redirect_stdout(io.StringIO()):
            report = {}
            with self.assertRaises(RuntimeError): r.run(report)
        self.assertEqual(events, ['GLM-REQUESTED.json'])
        self.assertEqual(report['provider_submissions_maximum'], 2)
        self.assertEqual(report['phase'], 'GLM_WIRE_COMPARISON')
        remote.assert_called_once()

    def test_baseline_drift_after_input_blocks_submission(self):
        fake = SimpleNamespace(obs=SimpleNamespace(health=lambda *a: {}, DISPATCHER='dispatch', write_new=Mock()),
                               APP_UID='pod', remote_probe=Mock())
        with patch.object(r, 'review', fake), patch.object(r, 'previous_zero_submission', return_value=({}, {})), \
             patch.object(r, 'verify_live', side_effect=[({}, {'dispatch': [{}]}), r.CheckFailure('drift')]), \
             patch.object(r, 'hidden_token', return_value='test-only-token'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(r.CheckFailure): r.run({})
        fake.obs.write_new.assert_not_called()
        fake.remote_probe.assert_not_called()

    def test_status_never_runs_probe_or_writes(self):
        stored = {'collection': 'COMPLETE', 'production_writes': 0}
        fake = SimpleNamespace(recovery=SimpleNamespace(exists=lambda p: True), e=SimpleNamespace(load=lambda p: stored))
        with patch.object(r, 'review', fake), patch.object(r, 'initialize'), patch.object(r.sys, 'platform', 'linux'), \
             patch.object(r.os, 'geteuid', return_value=0, create=True), patch.object(r.os, 'umask'), \
             patch.object(r.socket, 'gethostname', return_value='datax-01'), \
             patch.dict('sys.modules', {'fcntl': SimpleNamespace(), 'resource': SimpleNamespace(RLIMIT_CORE=0, setrlimit=Mock())}), \
             patch.object(r, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(r.main(['--status']), 0)
        self.assertEqual(json.loads(out.getvalue()), stored)
        run.assert_not_called()

    def test_existing_operation_refuses_reexecution_and_releases_locks(self):
        fake = SimpleNamespace(recovery=SimpleNamespace(exists=lambda p: True),
                 e=SimpleNamespace(ROOT=Path('/old88'), PREVIOUS_ROOT=Path('/old56')),
                 helper=SimpleNamespace(open_safe=Mock(side_effect=[4, 5])))
        with patch.object(r, 'review', fake), patch.object(r, 'initialize'), patch.object(r.sys, 'platform', 'linux'), \
             patch.object(r.os, 'geteuid', return_value=0, create=True), patch.object(r.os, 'umask'), \
             patch.object(r.os, 'close') as close, patch.object(r.socket, 'gethostname', return_value='datax-01'), \
             patch.dict('sys.modules', {'fcntl': SimpleNamespace(LOCK_SH=1, LOCK_NB=2, flock=Mock()),
                        'resource': SimpleNamespace(RLIMIT_CORE=0, setrlimit=Mock())}), \
             patch.object(r, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(r.main(['--probe-once']), 1)
        self.assertEqual(json.loads(out.getvalue())['failure']['check'], 'operation_exists_use_status_no_repeat')
        self.assertEqual([c.args[0] for c in close.call_args_list], [4, 5])
        run.assert_not_called()

    def test_wrong_host_no_initialization(self):
        with patch.object(r.socket, 'gethostname', return_value='not-datax'), patch.object(r, 'initialize') as init, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(r.main(['--probe-once']), 1)
        init.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['failure']['check'], 'run_on_datax01_as_root')

    def test_python37_and_no_mutation_entrypoints(self):
        tree = ast.parse(Path(r.__file__).read_text(encoding='utf-8'), feature_version=(3, 7))
        attrs = {x.func.attr for x in ast.walk(tree) if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
        self.assertFalse(attrs & {'audit', 'mutate', 'patch_call', 'recover', 'send_signal', 'killpg'})

    def test_immutable_old_delivery_inputs(self):
        pins = {'review_after_resume56.py': '0c11e2a7b354805b547b8b9be0880411e2bbe76313695d0d93bec90f4878b289',
                'glm_vlm_wire_probe.py': '106cd8544de37419e1b2e050f4b64c4186d2255fdc2323b3d9caf285a96014d9'}
        for name, sha in pins.items():
            self.assertEqual(hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest(), sha)


if __name__ == '__main__':
    unittest.main()
