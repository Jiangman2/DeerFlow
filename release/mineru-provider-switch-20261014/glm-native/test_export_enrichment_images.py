import ast
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import export_enrichment_images as e
import test_export_weknora_base as oldtests
import verify_weknora_base as v


class IdentityTests(unittest.TestCase):
    def setUp(self): e.configure('datax-02')

    def inventory(self, case=None):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        args, _, blobs, root = oldtests.fixture(Path(tmp.name)/'image.tar', case)
        role = dict(e.ROLE, config_digest=args['config_digest'])
        calls = []
        def capture(argv):
            calls.append(argv)
            if argv == e.io.CTR+['images', 'ls']:
                return ('REF TYPE DIGEST\n'+role['image']+' '+root['mediaType']+' '+root['digest']+'\n').encode()
            if argv == e.io.CRI+['inspecti', role['image']]:
                return json.dumps({'status': {'id': args['config_digest']},
                                   'info': {'imageSpec': {'config': {'Env': ['PASSWORD=private']}}}}).encode()
            return blobs['blobs/sha256/'+argv[-1][7:]]
        return role, calls, capture, args, root

    def test_exact_operator_roles_and_pins(self):
        self.assertEqual(e.TARGETS['datax-02']['config_digest'],
                         'sha256:5656e1d666225b9ee09c83684b3f1363d6b216bab498d11e0a186a21e175b85b')
        self.assertEqual(e.TARGETS['datax-03']['image'].split('@')[1], e.TARGETS['datax-03']['root_digest'])
        self.assertEqual(set(e.TARGETS), {'datax-02', 'datax-03'})

    def test_wrong_node_rejected(self):
        with self.assertRaises(v.CheckFailure): e.configure('datax-01')

    def test_manifest_and_index_verified_no_env_output(self):
        for case in (None, 'index'):
            role, calls, capture, args, root = self.inventory(case)
            with patch.object(e, 'ROLE', role), patch.object(e, 'capture', capture): got = e.identity()
            self.assertEqual(got['config_digest'], args['config_digest'])
            self.assertEqual(got['root_digest'], root['digest'])
            self.assertEqual(got['platform'], 'linux/arm64')
            self.assertNotIn('private', json.dumps(got))
            self.assertNotIn('PASSWORD', json.dumps(got))
            self.assertFalse(any('exec' in c or 'inspect' in c for c in calls))

    def test_redis_root_bound_then_arm64_manifest(self):
        e.configure('datax-03')
        role, _, capture, args, root = self.inventory('index')
        role.update(root_digest=root['digest'], config_digest=None)
        with patch.object(e, 'ROLE', role), patch.object(e, 'capture', capture):
            self.assertEqual(e.identity()['manifest_digest'], args['manifest_digest'])

    def test_config_pin_change_stops(self):
        role, _, capture, _, _ = self.inventory()
        role['config_digest'] = 'sha256:'+'e'*64
        with patch.object(e, 'ROLE', role), patch.object(e, 'capture', capture):
            with self.assertRaisesRegex(v.CheckFailure, 'reviewed_config_changed'): e.identity()

    def test_root_pin_change_stops(self):
        role, _, capture, _, _ = self.inventory('index')
        role['root_digest'] = 'sha256:'+'e'*64
        with patch.object(e, 'ROLE', role), patch.object(e, 'capture', capture):
            with self.assertRaisesRegex(v.CheckFailure, 'reviewed_root_digest_changed'): e.identity()

    def test_bad_platform_and_duplicate_arm64_rejected(self):
        for case in ('platform', 'duplicate_arm64'):
            role, _, capture, _, _ = self.inventory(case)
            with patch.object(e, 'ROLE', role), patch.object(e, 'capture', capture):
                with self.assertRaises(v.CheckFailure): e.identity()

    def test_cri_mismatch_rejected(self):
        role, _, capture, _, _ = self.inventory()
        def bad(argv):
            if 'inspecti' in argv: return b'{"status":{"id":"sha256:wrong"}}'
            return capture(argv)
        with patch.object(e, 'ROLE', role), patch.object(e, 'capture', bad):
            with self.assertRaisesRegex(v.CheckFailure, 'cri_config_identity_mismatch'): e.identity()

    def test_metadata_corruption_rejected(self):
        role, _, capture, _, _ = self.inventory()
        def bad(argv): return b'{}' if 'content' in argv else capture(argv)
        with patch.object(e, 'ROLE', role), patch.object(e, 'capture', bad):
            with self.assertRaisesRegex(v.CheckFailure, 'image_metadata_hash_mismatch'): e.identity()

    def test_capture_forbids_store_access_and_image_mutations(self):
        for command in (['psql'], ['redis-cli'], ['kubectl', 'exec'],
                        e.io.CTR+['images', 'pull', e.ROLE['image']],
                        e.io.CTR+['images', 'import', 'x.tar'], e.io.CRI+['inspect', 'some-container']):
            with self.assertRaisesRegex(v.CheckFailure, 'non_image_read_command_rejected'): e.capture(command)

    def test_export_is_local_single_platform_content_not_runtime(self):
        cmd = e.export_command()
        self.assertIn('--local', cmd); self.assertIn('--skip-manifest-json', cmd)
        self.assertEqual(cmd[-2:], ['-', e.ROLE['image']])
        self.assertEqual(cmd[cmd.index('--platform')+1], 'linux/arm64')
        self.assertNotIn('containers', cmd); self.assertNotIn('snapshots', cmd)


class ExportTests(unittest.TestCase):
    def setUp(self):
        e.configure('datax-02')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'export'
        archive = Path(self.tmp.name)/'fixture.tar'
        args, _, _, root = oldtests.fixture(archive)
        self.payload = archive.read_bytes()
        self.ident = dict(role='postgres', source_node='datax-02', image=e.ROLE['image'],
                          manifest_digest=args['manifest_digest'], config_digest=args['config_digest'],
                          root_digest=root['digest'], layers=args['expected_layers'],
                          compressed_layer_bytes=args['expected_bytes'])
        for item in (patch.object(e, 'ROOT', self.root), patch.object(e.io, 'ROOT', self.root),
                     patch.object(e.io, 'safe_dir'), patch.object(e.io, 'check_space', return_value={'fixture': True}),
                     patch.object(e, 'capture', return_value=b'--local --skip-manifest-json')):
            item.start(); self.addCleanup(item.stop)
        self.events = []
        p = patch.object(e, 'emit', self.events.append); p.start(); self.addCleanup(p.stop)

    def pipe(self, argv, consume, maximum, timeout, progress=None, rate=0):
        self.assertEqual(argv, e.export_command()); self.assertLessEqual(len(self.payload), maximum)
        consume(self.payload)
        if progress: progress(len(self.payload), 1)
        return len(self.payload)

    def test_actual_tar_reverified_and_source_unchanged(self):
        with patch.object(e, 'identity', return_value=self.ident) as identity, patch.object(e.io, 'pipe_command', self.pipe):
            self.assertEqual(e.export_once(), 0)
        self.assertEqual(identity.call_count, 2)
        r = json.loads((self.root/'RESULT.json').read_text())
        self.assertEqual(r['store_image_export'], 'PASS')
        self.assertEqual(r['archive_sha256'], hashlib.sha256(self.payload).hexdigest())
        self.assertTrue(r['archive_must_remain_private'])
        self.assertFalse(r['runtime_volumes_exported'])
        self.assertEqual(r['production_database_connections'], 0)
        self.assertFalse((self.root/'store.oci.tar.partial').exists())
        self.assertNotIn('SECRET', json.dumps(self.events))

    def test_repeat_rejected_before_read(self):
        self.root.mkdir()
        with patch.object(e, 'identity') as identity:
            with self.assertRaisesRegex(v.CheckFailure, 'operation_exists'): e.export_once()
        identity.assert_not_called()

    def test_failed_export_keeps_marker_no_automatic_repeat(self):
        def failed(*args): raise RuntimeError('credential-not-for-output')
        with patch.object(e, 'identity', return_value=self.ident), patch.object(e.io, 'pipe_command', failed):
            self.assertEqual(e.export_once(), 1)
        self.assertTrue((self.root/'STARTED.json').exists())
        self.assertTrue((self.root/'FAILED.json').exists())
        self.assertFalse((self.root/'RESULT.json').exists())
        self.assertNotIn('credential-not-for-output', json.dumps(self.events))

    def test_mid_export_image_change_preserves_partial(self):
        with patch.object(e, 'identity', side_effect=[self.ident, dict(self.ident, layers=2)]), patch.object(e.io, 'pipe_command', self.pipe):
            self.assertEqual(e.export_once(), 1)
        self.assertTrue((self.root/'store.oci.tar.partial').exists())
        self.assertFalse((self.root/'store.oci.tar').exists())

    def test_status_no_image_command_no_write(self):
        self.root.mkdir()
        (self.root/'STARTED.json').write_text('{"store_image_export":"STARTED"}')
        before = {x.name: x.read_bytes() for x in self.root.iterdir()}
        # Private file mode is platform-dependent on Windows; test only the
        # status control flow here. Linux privacy helpers are separately tested.
        with patch.object(e.io, 'read_receipt', return_value=(b'{}', {'store_image_export': 'STARTED'})), patch.object(e, 'capture') as capture:
            self.assertEqual(e.status(), 0)
        capture.assert_not_called()
        self.assertEqual(before, {x.name: x.read_bytes() for x in self.root.iterdir()})

    def test_no_root_created_if_space_preflight_fails(self):
        with patch.object(e, 'identity', return_value=self.ident), patch.object(e.io, 'check_space', side_effect=v.CheckFailure('no_space')):
            with self.assertRaises(v.CheckFailure): e.export_once()
        self.assertFalse(self.root.exists())

    def test_python37_grammar(self):
        ast.parse(Path(e.__file__).read_text(encoding='utf-8'), feature_version=(3, 7))


if __name__ == '__main__': unittest.main()
