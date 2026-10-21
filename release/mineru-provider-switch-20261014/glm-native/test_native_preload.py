import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import preload_native_candidate as p
import qualify_runtime_candidate_fix1 as q


class PortablePreloadTests(unittest.TestCase):
    def test_exact_local_import_command(self):
        argv = p.import_args(Path('/opt/verified/image.oci.tar'))
        self.assertEqual(argv[:8], ['nice', '-n', '10', 'ionice', '-c', '2', '-n', '7'])
        self.assertEqual(argv[8:8+len(p.CTR)], p.CTR)
        self.assertIn('--local', argv)
        self.assertIn('linux/arm64', argv)
        self.assertIn('overlayfs', argv)
        self.assertIn(p.PIN, argv)
        self.assertNotIn('--all-platforms', argv)
        self.assertEqual(argv[-1], '/opt/verified/image.oci.tar')

    def test_full_cold_budget_not_delta_only(self):
        capacity = 316482899968
        reserve, need = p.required_bytes(capacity)
        self.assertEqual(need-reserve, 2*p.COMPRESSED+2*p.EXPANDED+2*p.GIB)
        self.assertLess(need/p.GIB, 67.35)
        self.assertGreater(need/p.GIB, 66)

    def test_imagefs_paths_and_invalid_responses(self):
        row = {'fsId': {'mountpoint': '/var/lib/containerd/overlayfs'}}
        doc = {'imageFilesystems': [row], 'containerFilesystems': [row]}
        expected = [Path('/var/lib/containerd/overlayfs')]
        self.assertEqual(p.image_fs_paths(doc), expected)
        self.assertEqual(p.image_fs_paths({'status': doc}), expected)
        for bad in ({}, {'status': doc, 'imageFilesystems': [row]},
                    {'imageFilesystems': [{'fsId': {'mountpoint': '/../private'}}]}):
            with self.subTest(doc=bad), self.assertRaises(p.PreloadFailure): p.image_fs_paths(bad)

    def test_reference_filter_and_conflict(self):
        with patch.object(p, 'command', return_value=b'') as call:
            self.assertFalse(p.reference_present())
            self.assertEqual(call.call_args[0][0][-1], 'name=='+p.IMAGE)
        with patch.object(p, 'command', return_value=(p.IMAGE+'\n').encode()):
            self.assertTrue(p.reference_present())
        with patch.object(p, 'command', return_value=b'another-image\n'):
            with self.assertRaisesRegex(p.PreloadFailure, 'unexpected_candidate_reference'): p.reference_present()

    def test_foreign_manifest_stops_before_any_mutation(self):
        output = (p.IMAGE+' media sha256:'+'0'*64+' 1MB linux/arm64 -\n').encode()
        with patch.object(p, 'command', return_value=output) as call:
            with self.assertRaisesRegex(p.PreloadFailure, 'candidate_reference_conflict'): p.image_check()
            self.assertEqual(call.call_count, 1)

    def test_command_no_raw_stderr(self):
        with self.assertRaisesRegex(p.PreloadFailure, 'command_failed_STATUS_ONLY'):
            p.command([sys.executable, '-c', 'import sys;sys.stderr.write("private credential");sys.exit(3)'])
        self.assertEqual(p.LAST['exit_code'], 3)
        self.assertNotIn('private', json.dumps(p.LAST))
        self.assertGreater(p.LAST['stderr_bytes'], 0)

    def test_command_output_bound(self):
        with self.assertRaisesRegex(p.PreloadFailure, 'command_output_bound'):
            p.command([sys.executable, '-c', 'print("x"*10000)'], limit=10)

    def test_timeout_stops_only_client(self):
        with patch.object(p.os, 'killpg', wraps=os.killpg) as killpg:
            with self.assertRaisesRegex(p.PreloadFailure, 'command_timeout_STATUS_ONLY'):
                p.command([sys.executable, '-c', 'import time;time.sleep(30)'], timeout=.1)
            self.assertEqual(killpg.call_count, 1)
            self.assertEqual(killpg.call_args[0][1], signal.SIGTERM)

    def test_image_identity_complete_and_pinned(self):
        sizes = [p.COMPRESSED//24]*24
        sizes[-1] += p.COMPRESSED-sum(sizes)
        manifest = json.dumps({'config': {'digest': p.CONFIG},
                               'layers': [{'size': n} for n in sizes]}).encode()
        digest = 'sha256:'+hashlib.sha256(manifest).hexdigest()
        answers = [(p.IMAGE+' media '+digest+' size platform labels\n').encode(),
                   manifest, (p.IMAGE+'\n').encode()]
        cri = {'status': {'id': p.CONFIG, 'repoTags': [p.IMAGE], 'pinned': True}}
        with patch.object(p, 'MANIFEST', digest), patch.object(p, 'command', side_effect=answers), \
             patch.object(p, 'cri', return_value=cri):
            value = p.image_check(require_pin=True)
        self.assertTrue(value['pinned'])
        self.assertEqual(value['content_and_unpack'], 'PASS')

    def test_storage_monitor_and_preflight_have_different_bounds(self):
        capacity = 316482899968
        reserve, need = p.required_bytes(capacity)
        stats = types.SimpleNamespace(f_blocks=capacity, f_frsize=1, f_bavail=need,
                                      f_flag=0, f_favail=100001)
        guard = types.SimpleNamespace(directory=Mock())
        device = types.SimpleNamespace(st_dev=42)
        with patch.object(p.os, 'statvfs', return_value=stats), patch.object(Path, 'stat', return_value=device):
            p.disk_budget(guard, [Path('/var/lib/containerd')], initial=True)
            stats.f_bavail = need-1
            with self.assertRaisesRegex(p.PreloadFailure, 'cold_import_space_insufficient'):
                p.disk_budget(guard, [Path('/var/lib/containerd')], initial=True)
            p.disk_budget(guard, [Path('/var/lib/containerd')], initial=False)
            stats.f_bavail = reserve+2*p.GIB-1
            with self.assertRaisesRegex(p.PreloadFailure, 'live_imagefs_reserve_reached'):
                p.disk_budget(guard, [Path('/var/lib/containerd')], initial=False)

    def test_python37_syntax_and_no_rollout_commands(self):
        source = Path(p.__file__).read_text()
        ast.parse(source, feature_version=(3, 7))
        for forbidden in ('shell=True', 'rollout restart', 'kubectl apply', 'scale deployment',
                          'images pull', 'images rm', 'images prune', 'imagegen', 'getpass'):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("'images', 'import'"), 1)


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Linux private paths')
class PrivatePreloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/root')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root/'state'
        self.source = self.root/'source'; self.source.mkdir(mode=0o700)
        self.out = io.StringIO()

    def write(self, path, value):
        path.write_bytes(value); path.chmod(0o600)

    def fixture(self):
        archive = self.source/'weknora.candidate.oci.tar'
        self.write(archive, b'opaque locked archive')
        c = dict(image=p.IMAGE, manifest_digest=p.MANIFEST, config_digest=p.CONFIG,
                 archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), layers_verified=24,
                 compressed_layer_bytes=p.COMPRESSED, uncompressed_layer_bytes=p.EXPANDED)
        value = dict(archive=str(archive), component=c, weknora_glm_candidate_build='PASS',
                     compile_reused=True, images_imported=False, ready_for_rollout=False,
                     production_workloads_mutated=False)
        report = self.source/'BUILD-RESULT.json'
        self.write(report, json.dumps(value).encode())
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(p, 'SOURCE', self.source))
        stack.enter_context(patch.object(p, 'REPORT_SHA', hashlib.sha256(report.read_bytes()).hexdigest()))
        stack.enter_context(patch.object(p, 'ARCHIVE_BYTES', archive.stat().st_size))
        stack.enter_context(patch.object(p, 'ARCHIVE_SHA', c['archive_sha256']))
        return stack, archive, report

    def test_source_exact_binding_no_mutation(self):
        stack, archive, report = self.fixture()
        before = {f: f.read_bytes() for f in self.source.iterdir()}
        with stack: self.assertEqual(p.source(q)[0], archive)
        self.assertEqual(before, {f: f.read_bytes() for f in self.source.iterdir()})

    def test_source_corruption_or_receipt_change_stops(self):
        stack, archive, report = self.fixture()
        with stack:
            self.write(archive, b'x'+archive.read_bytes()[1:])
            with self.assertRaisesRegex(p.PreloadFailure, 'candidate_archive_hash_changed'): p.source(q)
            self.write(report, b'{}')
            with self.assertRaisesRegex(p.PreloadFailure, 'candidate_receipt_changed'): p.source(q)

    def test_private_source_guard_rejects_world_writable_and_symlink(self):
        stack, archive, report = self.fixture()
        with stack:
            archive.chmod(0o666)
            with self.assertRaises(q.ReverifyFailure): p.source(q)
            archive.chmod(0o600)
            saved = self.source/'saved'; archive.rename(saved); archive.symlink_to(saved)
            with self.assertRaises(OSError): p.source(q)

    def test_helper_pinned_bytes_only(self):
        helper = self.root/'check.py'; self.write(helper, b'value = 42\n')
        with patch.object(p, 'HELPER', helper), patch.object(p, 'HELPER_SHA', hashlib.sha256(helper.read_bytes()).hexdigest()):
            self.assertEqual(p.load_helper().value, 42)
            self.write(helper, b'value = 99\n')
            with self.assertRaisesRegex(p.PreloadFailure, 'verified_helper_changed'): p.load_helper()

    def prepared(self, present=False):
        guard = types.SimpleNamespace(live=Mock())
        return {'archive': self.source/'archive', 'present': present, 'budget': {},
                'node_uid': 'node-uid', 'paths': [], 'guard': guard, 'plan': {}}

    def mocks(self, prepared):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(p, 'STATE', self.state))
        stack.enter_context(patch.object(q, 'lock', return_value=contextlib.nullcontext()))
        stack.enter_context(patch.object(q, 'processes_clear'))
        stack.enter_context(patch.object(p, 'preflight', return_value=prepared))
        stack.enter_context(patch.object(p, 'disk_budget', return_value={}))
        stack.enter_context(patch.object(p, 'reference_present', return_value=False))
        stack.enter_context(contextlib.redirect_stdout(self.out))
        return stack

    def test_import_boundary_once_and_success_receipts(self):
        prepared = self.prepared()
        def run(argv, **kwargs):
            self.assertTrue((self.state/'IMPORT-REQUESTED.json').is_file())
            self.assertFalse((self.state/'IMPORT-ACK.json').exists())
            self.assertEqual(argv, p.import_args(prepared['archive']))
            return b''
        with self.mocks(prepared), patch.object(p, 'command', side_effect=run) as call, \
             patch.object(p, 'pin', return_value={'pinned': True}):
            p.import_once(q)
            self.assertEqual(call.call_count, 1)
            self.assertTrue((self.state/'IMPORT-ACK.json').exists())
            result = q.document(self.state/'RESULT.json')
            self.assertEqual(result['weknora_candidate_preload'], 'PASS')
            self.assertFalse(result['ready_for_rollout'])
            self.assertFalse(result['production_workloads_mutated'])
            with self.assertRaisesRegex(p.PreloadFailure, 'previous_operation_exists'): p.import_once(q)
            self.assertEqual(call.call_count, 1)

    def test_uncertain_import_and_interrupt_no_retry(self):
        for error in (p.PreloadFailure('transport_failure'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.state = self.root/('state-'+type(error).__name__)
                with self.mocks(self.prepared()), patch.object(p, 'command', side_effect=error) as call:
                    with self.assertRaises(type(error)): p.import_once(q)
                    result = q.document(self.state/'RESULT.json')
                    self.assertEqual(result['weknora_candidate_preload'], 'UNCONFIRMED')
                    self.assertTrue(result['image_import_may_be_partial'])
                    self.assertFalse(result['automatic_retry'])
                    self.assertFalse((self.state/'IMPORT-ACK.json').exists())
                    self.assertEqual(call.call_count, 1)

    def test_complete_existing_image_is_not_reimported(self):
        with self.mocks(self.prepared(present=True)), patch.object(p, 'command') as call, \
             patch.object(p, 'pin', return_value={'pinned': True}):
            p.import_once(q)
            call.assert_not_called()
            self.assertFalse((self.state/'IMPORT-REQUESTED.json').exists())
            self.assertTrue(q.document(self.state/'RESULT.json')['reused_complete_image'])

    def test_preflight_failure_never_creates_state_or_imports(self):
        with self.mocks(self.prepared()), patch.object(p, 'preflight', side_effect=p.PreloadFailure('no_space')), \
             patch.object(p, 'command') as call:
            with self.assertRaisesRegex(p.PreloadFailure, 'no_space'): p.import_once(q)
            self.assertFalse(self.state.exists()); call.assert_not_called()

    def test_reference_appearing_before_import_is_never_overwritten(self):
        with self.mocks(self.prepared()), patch.object(p, 'reference_present', return_value=True), \
             patch.object(p, 'command') as call:
            with self.assertRaisesRegex(p.PreloadFailure, 'candidate_reference_appeared'):
                p.import_once(q)
            call.assert_not_called()
            self.assertFalse((self.state/'IMPORT-REQUESTED.json').exists())
            self.assertFalse(q.document(self.state/'RESULT.json')['image_import_may_be_partial'])

    def test_status_does_not_import_pin_or_write(self):
        self.state.mkdir(mode=0o700)
        q.record(self.state/'STARTED.json', {'candidate_image': p.IMAGE})
        before = {f: f.read_bytes() for f in self.state.iterdir()}
        with patch.object(p, 'STATE', self.state), patch.object(p, 'reference_present', return_value=True), \
             patch.object(p, 'image_check', return_value={'pinned': False}), \
             patch.object(p, 'pin') as pin, patch.object(p, 'import_once') as load, \
             contextlib.redirect_stdout(self.out): p.status(q)
        pin.assert_not_called(); load.assert_not_called()
        self.assertEqual(before, {f: f.read_bytes() for f in self.state.iterdir()})
        self.assertEqual(json.loads(self.out.getvalue())['weknora_preload_status'], 'READ_ONLY')

    def test_pinning_merges_only_candidate_label_after_receipt(self):
        self.state.mkdir(mode=0o700)
        def label(argv, **kwargs):
            self.assertTrue((self.state/'PIN-REQUESTED.json').exists())
            self.assertEqual(argv, p.CTR+['images', 'label', p.IMAGE, p.PIN])
            return b''
        with patch.object(p, 'STATE', self.state), \
             patch.object(p, 'image_check', side_effect=[{'pinned': False}, {'pinned': True}]), \
             patch.object(p, 'command', side_effect=label) as call, \
             patch.object(p, 'cri', return_value={'status': {'id': p.CONFIG, 'repoTags': [p.IMAGE], 'pinned': True}}):
            self.assertTrue(p.pin(q)['pinned'])
        self.assertEqual(call.call_count, 1)


if __name__ == '__main__':
    unittest.main()
