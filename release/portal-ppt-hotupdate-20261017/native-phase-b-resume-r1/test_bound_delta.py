"""Actual inventory bindings plus the reviewed pre-proof UNKNOWN reporting delta."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


G = load(HERE / 'resume_phase_b_bound.py', 'bound_guard')
B = load(HERE.parent / 'native-preload-r2/preload_native_r2.py', 'helpers')


class BindingDelta(unittest.TestCase):
    def test_actual_inventory_24_pins_and_reconstructable_receipts(self):
        raw = Path('/inventory.json').read_bytes()
        self.assertEqual((len(raw), G.sha(raw)), (89968, G.INVENTORY_SHA256))
        inventory = json.loads(raw)
        self.assertEqual(inventory['collected_at_utc'], G.INVENTORY_COLLECTED_AT_UTC)
        self.assertIn('NOT_A_FRESH_STORAGE_RESERVATION', G.INVENTORY_SCOPE)
        rows = {row['name']: row for row in inventory['checkpoint']['files'] if row['present']}
        self.assertEqual(len(rows), 24)
        self.assertEqual(G.CHECKPOINT_SHA256, {name: {'bytes': row['bytes'], 'sha256': row['sha256']} for name, row in rows.items()})
        checked = 0
        for name, row in rows.items():
            if name.endswith('.SEALED.json') or name.endswith('.PHASE-A-REQUESTED.json') or name == 'PHASE-A-PASS.json':
                body = G.encoded(row['safe'])
            elif name == 'PHASE-A-REQUESTED.json':
                body = G.encoded(dict(mode='FULL_OCI_CONTENT_ONLY', automatic_retry=False))
            elif name == 'FAIL.json':
                body = (HERE.parent / 'native-preload-attempt-r3-local-resume/TARGET-FAIL.json').read_bytes()
            elif name == 'PREPARED.json':
                body = (HERE.parent / 'native-preload-attempt-r3/PREPARED.json').read_bytes()
            else:
                continue
            self.assertEqual((len(body), G.sha(body)), (row['bytes'], row['sha256']), name)
            checked += 1
        self.assertEqual(checked, 16)

    @unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Linux private state fixture')
    def test_busy_lock_changed_checkpoint_and_existing_once_keep_unknown(self):
        for rejection in ('busy', 'checkpoint', 'once'):
            with self.subTest(rejection=rejection), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); once = root / 'once.json'
                if rejection == 'once':
                    once.write_bytes(b'original successor marker\n')
                results = []
                module = types.SimpleNamespace(B=B, REPORT=None, IMPORT_REQUESTED=False)
                module.handoff_report = lambda raw: results.append(json.loads(raw)) or '/fixture/REPORT.json'
                def lock():
                    if rejection == 'busy':
                        raise BlockingIOError()
                    import fcntl
                    fd = os.open(root / 'lock', os.O_RDWR | os.O_CREAT, 0o600)
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return fd
                module.common_lock = lock
                handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
                try:
                    with mock.patch.multiple(G, ROOT=root, ONCE=once), mock.patch.object(G, 'host_identity', return_value={}), \
                            mock.patch.object(G, 'load_target', return_value=(module, {})), \
                            mock.patch.object(G, 'inspect_checkpoint', side_effect=ValueError('original_checkpoint_advanced_or_changed') if rejection == 'checkpoint' else None, return_value=({}, {})), \
                            mock.patch.object(G, 'open_sealed') as open_sealed, \
                            mock.patch.object(G, 'continue_phase_b') as continuation, contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(G.run(), 1)
                    result = results[-1]
                    self.assertIsNone(result['target_import_requested']); self.assertIsNone(result['phase_b'])
                    self.assertIsNone(result['image_import_may_be_partial'])
                    self.assertEqual(result['prior_phase_a'], 'UNKNOWN_UNTIL_LOCKED_CHECKPOINT_CHECK')
                    self.assertEqual(result['state_scope'], 'THIS_PHASE_B_CONTINUATION')
                    open_sealed.assert_not_called(); continuation.assert_not_called()
                    if rejection == 'once':
                        self.assertEqual(once.read_bytes(), b'original successor marker\n')
                finally:
                    for sig, handler in handlers.items():
                        signal.signal(sig, handler)


if __name__ == '__main__':
    unittest.main(verbosity=2)
