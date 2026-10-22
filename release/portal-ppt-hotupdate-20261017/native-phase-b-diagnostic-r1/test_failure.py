import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


G = load(HERE / 'collect_failure.py', 'diagnostic')
M = load(HERE.parent / 'native-preload-attempt-r3/derived_target_code.py', 'frozen_target')
B = load(HERE.parent / 'native-preload-r2/preload_native_r2.py', 'frozen_helpers')
M.B = B


class DiagnosticFixtures(unittest.TestCase):
    def test_fixed_real_report_binding_and_read_only_checkpoint(self):
        real = HERE.parent / 'native-phase-b-attempt-r1'
        raw, fail = (real / 'REPORT.json').read_bytes(), (real / 'TARGET-FAIL.json').read_bytes()
        self.assertEqual((len(raw), G.sha(raw)), (12001, G.REPORT_SHA))
        self.assertEqual((len(fail), G.sha(fail)), (6578, G.FAIL_SHA))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            data = {'REPORT.json': raw, 'FAIL.json': fail,
                    'gateway-forward.PHASE-B-REQUESTED.json': B.encoded(dict(role='gateway-forward', phase='PHASE-B', automatic_retry=False)),
                    'frontend.PHASE-B-PASS.json': B.encoded({'returncode': 0})}
            for name in G.FILES:
                p = root / name; p.write_bytes(data.get(name, b'{}\n')); p.chmod(0o600)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with mock.patch.object(G, 'EVIDENCE', root):
                receipts = G.checkpoint(M)
                self.assertEqual(len(receipts), 7)
                (root / 'gateway-forward.PHASE-B-PASS.json').symlink_to(root / 'missing')
                with self.assertRaisesRegex(ValueError, 'failure_evidence_advanced_or_changed'):
                    G.checkpoint(M)
            self.assertEqual({name: (root / name).read_bytes() for name in before}, before)

    def test_receipt_time_bounds(self):
        rows = {'gateway-forward.PHASE-B-REQUESTED.json': {'mtime_ns': 1000000000000}, 'FAIL.json': {'mtime_ns': 1001000000000}}
        self.assertEqual(G.window(rows), (995, 1006))
        rows['FAIL.json']['mtime_ns'] = 1610000000000
        with self.assertRaisesRegex(ValueError, 'receipt_time_window_exceeds_10_minutes'): G.window(rows)
        rows['FAIL.json']['mtime_ns'] = 990000000000
        with self.assertRaisesRegex(ValueError, 'receipt_time_order'): G.window(rows)

    def test_journal_projection_omits_secrets_unrelated_logs_and_preserves_gaps(self):
        digest = 'sha256:' + 'a' * 64
        image = {'outer_blobs': {digest: 10}, 'layers': [{'chain_id': 'sha256:' + 'b' * 64}]}
        tag = 'deerflow.local/gateway:fixed'
        def row(message, unit='containerd.service'):
            return json.dumps({'__REALTIME_TIMESTAMP': '1001000000', '_SYSTEMD_UNIT': unit, 'MESSAGE': message, 'PRIORITY': '3'}).encode()
        messages = [row('failed ' + digest + ' no space left on device password=SECRET token=HIDDEN path=/private/key'),
                    row('failed ' + digest + ' novel failure credential=SENSITIVE'),
                    row('failed unrelated-image permission denied PASSWORD=DO_NOT_EXPORT'),
                    row('failed ' + tag + ' rpc error: code = ResourceExhausted Errno 28 API_KEY=HUSH'),
                    row('failed ' + digest + ' secret root', 'unrelated.service')]
        result = G.project_journal(b'\n'.join(messages), 1000, 1010, image, tag)
        self.assertEqual(len(result['matched_error_projection']), 3)
        rendered = json.dumps(result)
        for secret in ('SECRET', 'HIDDEN', '/private/key', 'SENSITIVE', 'DO_NOT_EXPORT', 'HUSH', 'unrelated-image'):
            self.assertNotIn(secret, rendered)
        self.assertIn('NO_SPACE', rendered); self.assertIn('UNCLASSIFIED_TARGET_ERROR', rendered)
        self.assertIn('ResourceExhausted', rendered)
        self.assertFalse(result['raw_log_text_exported']); self.assertFalse(result['original_cli_stderr_recovered'])
        tail = G.project_journal(b'\n'.join([row('normal unrelated')]*1000), 1000, 1010, image, tag)
        self.assertTrue(tail['tail_may_be_truncated']); self.assertEqual(tail['no_matches_mean'], 'UNDETERMINED_NOT_NO_ERROR')

    def test_command_scope_is_bounded_and_nonzero_journal_has_no_raw_output(self):
        helper = types.SimpleNamespace(run=mock.Mock(return_value=(1, b'PASSWORD=NEVER_EXPORT')))
        module = types.SimpleNamespace(B=helper)
        receipts = {'gateway-forward.PHASE-B-REQUESTED.json': {'mtime_ns': 1000000000000}, 'FAIL.json': {'mtime_ns': 1001000000000}}
        result = G.collect_journal(module, receipts, {'outer_blobs': {}, 'layers': []}, 'fixed')
        self.assertEqual(result['status'], 'NOT_COLLECTED'); self.assertNotIn('PASSWORD', json.dumps(result))
        call = helper.run.call_args
        self.assertEqual(call.args[0][0:2], ['journalctl', '--unit=containerd.service'])
        self.assertIn('--lines=1000', call.args[0]); self.assertEqual(call.kwargs['timeout'], 20)
        self.assertEqual(call.kwargs['limit'], 2 * 1024 ** 2)

    def test_sigterm_uses_frozen_process_cleanup(self):
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
        original = B.subprocess.Popen
        children = []
        def launch(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child); return child
        timer = threading.Timer(.2, lambda: os.kill(os.getpid(), signal.SIGTERM))
        try:
            G.install_signals(); timer.start()
            with mock.patch.object(B.subprocess, 'Popen', side_effect=launch), mock.patch.object(B, 'progress'):
                with self.assertRaisesRegex(ValueError, 'interrupted_no_retry'):
                    B.run([sys.executable, '-I', '-B', '-c', 'import time;time.sleep(30)'], 'FIXTURE', timeout=2, limit=1024)
            self.assertEqual(len(children), 1); self.assertIsNotNone(children[0].poll())
        finally:
            timer.cancel(); timer.join()
            for sig, handler in handlers.items(): signal.signal(sig, handler)

    def test_first_action_interruption_does_not_run_second_action(self):
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
        wire = b' ' * 122305 + b'{}'
        reports = []
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            module = types.SimpleNamespace(B=B, private_bytes=lambda *a: wire,
                state_paths=lambda op: {'control': root}, unwire_plan=lambda v: {'images': [{'role': 'gateway-forward'}]},
                validate_plan=lambda *a: None, handoff_report=lambda raw: reports.append(json.loads(raw)) or '/fixture/REPORT.json')
            profile = {'images': [{'role': 'gateway-forward', 'tag': 'fixed'}]}
            try:
                with mock.patch.object(G, 'load', return_value=(None, module, profile, {})), \
                        mock.patch.object(G, 'locked', side_effect=lambda m: os.open(root, os.O_RDONLY | os.O_DIRECTORY)), \
                        mock.patch.object(G, 'checkpoint', return_value={}), mock.patch.object(G, 'WIRE_SHA', G.sha(wire)), \
                        mock.patch.object(G, 'collect_journal', side_effect=ValueError('interrupted_no_retry')), \
                        mock.patch.object(G, 'collect_snapshots') as second, mock.patch.object(B, 'open_anchored') as later, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(G.run(), 1)
                second.assert_not_called(); later.assert_not_called()
                self.assertEqual(reports[-1]['error_code'], 'interrupted_no_retry')
                self.assertEqual(reports[-1]['status'], 'INCOMPLETE')
            finally:
                for sig, handler in handlers.items(): signal.signal(sig, handler)


if __name__ == '__main__': unittest.main(verbosity=2)
