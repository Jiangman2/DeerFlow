import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import preload_enrichment_images as p
import resume_enrichment_preload as r
import test_export_weknora_base as fixtures


class ReadChecksTests(unittest.TestCase):
    def test_transient_reads_are_bounded_but_import_and_label_are_rejected(self):
        responses = [p.cli.PreloadFailure('command_failed_STATUS_ONLY'), b'accepted']
        with patch.object(p.cli, 'command', side_effect=responses) as original, \
             patch.object(p.inventory, 'capture'), patch.object(r.time, 'sleep'), patch.object(r, 'emit'):
            mutation = r.install_read_checks(p)
            self.assertIs(mutation, original)
            self.assertEqual(p.inventory.capture(p.io.CTR+['images', 'ls']), b'accepted')
            self.assertEqual(original.call_count, 2)
            for argv in (p.cli.import_args(Path('/private/file')), p.io.CTR+['images', 'label', p.ITEMS[1]['image'], p.cli.PIN]):
                with self.assertRaisesRegex(RuntimeError, 'non_readonly'): p.cli.command(argv)
            self.assertEqual(original.call_count, 2)

    def test_read_retries_stop_at_three_and_nontransient_checks_never_retry(self):
        for message, calls in (('command_failed_STATUS_ONLY', 3), ('command_timeout_STATUS_ONLY', 3), ('command_output_bound', 1)):
            with self.subTest(message=message), patch.object(p.cli, 'command', side_effect=p.cli.PreloadFailure(message)) as read, \
                 patch.object(p.inventory, 'capture'), patch.object(r.time, 'sleep'), patch.object(r, 'emit'):
                r.install_read_checks(p)
                with self.assertRaises(p.cli.PreloadFailure): p.inventory.capture(p.io.CTR+['images', 'ls'])
                self.assertEqual(read.call_count, calls)

    def test_exact_operator_failure_pin_and_python37(self):
        self.assertEqual(r.RECEIPTS['FAILED.json'], '469696342b6304d3145f5340b78e6bdcadcaeb22358cfd28781b6d03e87ee362')
        ast.parse(Path(r.__file__).read_text(), feature_version=(3, 7))
        self.assertNotIn('getpass', Path(r.__file__).read_text())

    def test_wrong_host_does_not_load_or_mutate(self):
        with patch.object(r.socket, 'gethostname', return_value='wrong'), patch.object(r.diagnostic, 'load') as load, patch.object(r, 'emit'):
            self.assertEqual(r.main(['--recover-once']), 1); load.assert_not_called()


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Linux private files')
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/root'); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.old = self.root/'old'; self.state = self.root/'recovery'
        self.old.mkdir(mode=0o700)
        self.items = tuple(dict(i, archive_bytes=8, archive_sha256=hashlib.sha256(b'12345678').hexdigest()) for i in p.ITEMS)
        self.addCleanup(patch.stopall)
        patch.object(p, 'ITEMS', self.items).start()
        patch.object(r, 'OLD', self.old).start(); patch.object(r, 'STATE', self.state).start()
        patch.object(r, 'IMPORT_ATTEMPTED', False).start(); patch.object(r, 'emit').start()
        for item in self.items:
            directory = self.old/item['role']; directory.mkdir(mode=0o700)
            self.write(directory/'store.oci.tar', b'12345678')
        values = {name: {} for name in r.RECEIPTS}
        values['FAILED.json'] = dict(enrichment_store_preload='UNCONFIRMED', failed_stage='VERIFY_LOCAL_CONTENT_UNPACK_CRI_PIN',
                                    failed_check='local_command_failed_no_retry', imports_attempted=['postgres'], production_writes=0)
        values['postgres/IMPORT-ACK.json'] = {'client_exit_code': 0}
        for item in self.items:
            values[item['role']+'/TRANSFER-PASS.json'] = dict(item, private_transfer='PASS', archive=str(self.old/item['role']/'store.oci.tar'))
        hashes = {}
        for name, value in values.items():
            raw = json.dumps(value).encode(); self.write(self.old/name, raw); hashes[name] = hashlib.sha256(raw).hexdigest()
        patch.object(r, 'RECEIPTS', hashes).start()

    def write(self, path, data): path.write_bytes(data); path.chmod(0o600)

    def snapshot(self): return {str(f): f.read_bytes() for f in self.old.rglob('*') if f.is_file()}

    def helpers(self):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(p, 'image_check', side_effect=lambda item, **k: dict(role=item['role'], pinned=True)))
        stack.enter_context(patch.object(p, 'reference_present', return_value=False))
        stack.enter_context(patch.object(r, 'redis_archive', return_value=(self.old/'redis/store.oci.tar', p.io.private_meta(self.old/'redis/store.oci.tar'))))
        stack.enter_context(patch.object(p.cli, 'cri', return_value={}))
        stack.enter_context(patch.object(p.cli, 'image_fs_paths', return_value=[]))
        stack.enter_context(patch.object(p, 'disk_budget', return_value=[]))
        stack.enter_context(patch.object(p.io, 'check_space'))
        stack.enter_context(patch.object(p, 'processes_clear'))
        original = Path.read_text
        stack.enter_context(patch.object(Path, 'read_text', lambda path, *a, **k: 'MemAvailable: 9000000 kB\n' if str(path) == '/proc/meminfo' else original(path, *a, **k)))
        return stack

    def test_history_binding_unchanged_exact(self):
        before = self.snapshot()
        self.assertEqual(r.bind_history(p), r.RECEIPTS)
        self.assertEqual(before, self.snapshot())

    def test_corrupt_historical_receipt_blocks(self):
        self.write(self.old/'FAILED.json', b'{}')
        with self.assertRaisesRegex(p.verify.CheckFailure, 'historical_receipt_hash_changed'): r.bind_history(p)

    def test_any_prior_redis_import_marker_or_later_success_blocks(self):
        for name in ('redis/IMPORT-REQUESTED.json', 'RESULT.json', 'postgres/PRELOAD-PASS.json'):
            with self.subTest(name=name):
                path = self.old/name; self.write(path, b'{}')
                with self.assertRaisesRegex(p.verify.CheckFailure, 'previous_operation_advanced'): r.bind_history(p)
                path.unlink()  # Test fixture only, never a server recovery action.

    def test_full_recovery_imports_only_redis_preserves_all_original_evidence(self):
        before = self.snapshot()
        def mutate(argv, **options):
            self.assertTrue((self.state/'IMPORT-REQUESTED.json').exists())
            self.assertFalse((self.state/'IMPORT-ACK.json').exists())
            self.assertEqual(argv, p.cli.import_args(self.old/'redis/store.oci.tar')); return b''
        with self.helpers(), patch.object(p.cli, 'command') as no_command:
            once = Mock(side_effect=mutate)
            self.assertEqual(r.recover(p, once), 0)
            once.assert_called_once(); no_command.assert_not_called()
        result = json.loads((self.state/'RESULT.json').read_bytes())
        self.assertEqual(result['enrichment_store_preload_recovery'], 'PASS')
        self.assertFalse(result['postgres_reimported']); self.assertFalse(result['ready_for_rollout'])
        self.assertEqual(before, self.snapshot())
        with self.assertRaisesRegex(p.verify.CheckFailure, 'recovery_already_started'): r.recover(p, once)
        once.assert_called_once()

    def test_uncertain_mutation_and_interrupt_no_retry_no_old_changes(self):
        before = self.snapshot()
        with self.helpers():
            once = Mock(side_effect=KeyboardInterrupt())
            self.assertEqual(r.recover(p, once), 1)
            once.assert_called_once()
        self.assertTrue((self.state/'IMPORT-REQUESTED.json').exists())
        self.assertFalse((self.state/'IMPORT-ACK.json').exists())
        self.assertTrue(json.loads((self.state/'FAILED.json').read_bytes())['redis_import_attempted'])
        self.assertEqual(before, self.snapshot())

    def test_postgres_failure_or_redis_already_present_is_prewrite_failure(self):
        for scenario in ('postgres', 'redis'):
            with self.subTest(scenario=scenario), self.helpers():
                context = patch.object(p, 'image_check', side_effect=p.verify.CheckFailure('pg_not_ready')) if scenario == 'postgres' else patch.object(p, 'reference_present', return_value=True)
                with context:
                    once = Mock()
                    with self.assertRaises(p.verify.CheckFailure): r.recover(p, once)
                    once.assert_not_called(); self.assertFalse(self.state.exists())

    def test_redis_reference_race_never_overwrites(self):
        with self.helpers(), patch.object(p, 'reference_present', side_effect=[False, True]):
            once = Mock(); self.assertEqual(r.recover(p, once), 1)
            once.assert_not_called()
        self.assertFalse((self.state/'IMPORT-REQUESTED.json').exists())

    def test_status_does_not_prompt_import_label_or_write(self):
        self.state.mkdir(mode=0o700); p.record(self.state/'STARTED.json', {'safe': True})
        before = {f.name: f.read_bytes() for f in self.state.iterdir()}
        with self.helpers(), patch.object(p, 'record') as record, patch.object(p.cli, 'command') as command:
            self.assertEqual(r.status(p), 0); record.assert_not_called(); command.assert_not_called()
        self.assertEqual(before, {f.name: f.read_bytes() for f in self.state.iterdir()})

    def test_actual_redis_archive_layer_checks_and_hash_corruption(self):
        archive = self.old/'redis/store.oci.tar'
        args, _, _, root = fixtures.fixture(archive, 'index'); archive.chmod(0o600)
        item = dict(self.items[1], archive_bytes=archive.stat().st_size,
                    archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                    manifest_digest=args['manifest_digest'], config_digest=args['config_digest'],
                    layers=args['expected_layers'], compressed_layer_bytes=args['expected_bytes'], root_digest=root['digest'])
        with patch.object(p, 'archive_reference'), patch.object(p.verify, 'verify', wraps=p.verify.verify) as verify:
            # Obtain the synthetic expanded bytes from the same verifier, independently.
            value = p.verify.verify(archive, lambda row: None, **args)
            item['uncompressed_layer_bytes'] = value['uncompressed_layer_bytes']
            self.assertEqual(r.redis_archive(p, item)[0], archive)
            self.assertEqual(verify.call_count, 2)
            self.write(archive, b'x'+archive.read_bytes()[1:])
            with self.assertRaisesRegex(p.verify.CheckFailure, 'redis_archive_hash_changed'): r.redis_archive(p, item)


if __name__ == '__main__': unittest.main()
