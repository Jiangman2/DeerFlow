import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import stat
import types
import unittest
from unittest.mock import patch

import inspect_build_input as p

MD = 'sha256:' + 'a' * 64
LD = 'sha256:' + 'b' * 64


def fixture(index=False):
    manifest = {'schemaVersion': 2, 'mediaType': p.MANIFESTS[0],
                'config': {'digest': p.CONFIG, 'size': 200},
                'layers': [{'digest': LD, 'size': 1234}]}
    config = {'os': 'linux', 'architecture': 'arm64',
              'rootfs': {'type': 'layers', 'diff_ids': [LD]},
              'config': {'Env': ['FIXTURE_SECRET=must-not-print']}}
    target = {'mediaType': p.MANIFESTS[0], 'digest': MD}
    blobs = {MD: manifest, p.CONFIG: config}
    if index:
        target = {'mediaType': p.INDEXES[0], 'digest': 'sha256:' + 'c' * 64}
        blobs[target['digest']] = {'schemaVersion': 2, 'manifests': [
            dict(target, digest=MD, mediaType=p.MANIFESTS[0], size=123,
                 platform={'os': 'linux', 'architecture': 'arm64', 'variant': 'v8'})]}
    return target, blobs


class MetadataTests(unittest.TestCase):
    def test_python37_grammar(self):
        ast.parse(Path(p.__file__).read_text(), feature_version=(3, 7))

    def test_manifest_and_no_config_env_output(self):
        target, blobs = fixture()
        out = p.inspect_identity(target, lambda d: blobs[d['digest']])
        self.assertEqual(out['config_digest'], p.CONFIG)
        self.assertEqual(out['declared_layer_bytes'], 1234)
        self.assertFalse(out['layer_bytes_rehashed'])
        self.assertNotIn('must-not-print', json.dumps(out))

    def test_index_selects_linux_arm64(self):
        target, blobs = fixture(True)
        out = p.inspect_identity(target, lambda d: blobs[d['digest']])
        self.assertEqual(out['manifest_digest'], MD)
        self.assertNotEqual(out['manifest_digest'], out['target_digest'])

    def test_ambiguous_platform_rejected(self):
        target, blobs = fixture(True)
        rows = blobs[target['digest']]['manifests']
        rows.append(dict(rows[0]))
        with self.assertRaisesRegex(p.CheckFailure, 'arm64_manifest_not_unique'):
            p.inspect_identity(target, lambda d: blobs[d['digest']])

    def test_wrong_platform_rejected(self):
        target, blobs = fixture()
        blobs[p.CONFIG]['architecture'] = 'amd64'
        with self.assertRaisesRegex(p.CheckFailure, 'production_platform_mismatch'):
            p.inspect_identity(target, lambda d: blobs[d['digest']])

    def test_wrong_config_rejected(self):
        target, blobs = fixture()
        blobs[MD]['config']['digest'] = LD
        with self.assertRaisesRegex(p.CheckFailure, 'production_config_identity_changed'):
            p.inspect_identity(target, lambda d: blobs[d['digest']])

    def test_external_layer_rejected(self):
        target, blobs = fixture()
        blobs[MD]['layers'][0]['urls'] = ['https://unapproved.invalid/layer']
        with self.assertRaisesRegex(p.CheckFailure, 'external_descriptor_not_accepted'):
            p.inspect_identity(target, lambda d: blobs[d['digest']])

    def test_diffid_count_rejected(self):
        target, blobs = fixture()
        blobs[p.CONFIG]['rootfs']['diff_ids'] = []
        with self.assertRaisesRegex(p.CheckFailure, 'diffid_count_mismatch'):
            p.inspect_identity(target, lambda d: blobs[d['digest']])

    def test_read_metadata_hash_and_size(self):
        raw = b'{"schemaVersion":2}'
        desc = {'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        with patch.object(p, 'capture', return_value=raw):
            self.assertEqual(p.read_json(desc), {'schemaVersion': 2})
            with self.assertRaisesRegex(p.CheckFailure, 'metadata_hash_mismatch'):
                p.read_json(dict(desc, digest=MD))
            with self.assertRaisesRegex(p.CheckFailure, 'metadata_size_mismatch'):
                p.read_json(dict(desc, size=1))

    def test_exact_image_name_and_duplicate_rejection(self):
        row = (p.IMAGE + ' ' + p.MANIFESTS[0] + ' ' + MD + ' 20GiB linux/arm64 -\n').encode()
        self.assertIsNone(p.target_from_list(b'REF TYPE DIGEST\n'))
        self.assertEqual(p.target_from_list(row)['digest'], MD)
        self.assertIsNone(p.target_from_list(row.replace(p.IMAGE.encode(), (p.IMAGE + '-different').encode())))
        with self.assertRaisesRegex(p.CheckFailure, 'duplicate_image_records'):
            p.target_from_list(row + row)

    def test_mutating_command_rejected(self):
        for cmd in (p.CTR + ['images', 'pull', p.IMAGE], p.CRI + ['pull', p.IMAGE],
                    ['kubectl', 'get', 'secrets'], p.CTR + ['images', 'export', '/tmp/a.tar', p.IMAGE]):
            with self.subTest(cmd=cmd), self.assertRaisesRegex(p.CheckFailure, 'non_readonly_command_rejected'):
                p.capture(cmd, 'test')

    def test_command_failure_redacts_stderr(self):
        reply = types.SimpleNamespace(returncode=1, stdout=b'', stderr=b'FIXTURE_SECRET')
        with patch.object(p.subprocess, 'run', return_value=reply):
            with self.assertRaisesRegex(p.CheckFailure, '^readonly_command_failed$'):
                p.capture(p.CTR + ['images', 'ls'], 'test')

    def test_unexpected_failure_redacted(self):
        buf = io.StringIO()
        with patch.object(p, 'inventory', side_effect=ValueError('FIXTURE_SECRET')), contextlib.redirect_stdout(buf):
            self.assertEqual(p.main(), 1)
        self.assertNotIn('FIXTURE_SECRET', buf.getvalue())
        self.assertFalse(json.loads(buf.getvalue())['automatic_retry'])


class InventoryTests(unittest.TestCase):
    def run_case(self, missing=False, changed=False):
        target, blobs = fixture()
        row = (p.IMAGE + ' ' + p.MANIFESTS[0] + ' ' + MD).encode()
        answers = iter([b'' if missing else row, json.dumps({'status': {
            'id': p.CONFIG, 'repoTags': [p.IMAGE], 'pinned': True, 'size': '1234'}}).encode(),
            json.dumps({'imageFilesystems': [{'fsId': {'mountpoint': '/var/lib/containerd'}}]}).encode(),
            row.replace(MD.encode(), LD.encode()) if changed else row])
        with patch.object(p.socket, 'gethostname', return_value='datax-01'), \
             patch.object(p.os, 'geteuid', return_value=0, create=True), \
             patch.object(p.platform, 'system', return_value='Linux'), \
             patch.object(p.Path, 'lstat', return_value=types.SimpleNamespace(st_mode=stat.S_IFSOCK)), \
             patch.object(p, 'filesystem', side_effect=lambda path: {'path': path}), \
             patch.object(p, 'capture', side_effect=lambda *a: next(answers)), \
             patch.object(p, 'inspect_identity', return_value={'config_digest': p.CONFIG}), \
             patch.object(p, 'emit'):
            return p.inventory()

    def test_local_present_summary(self):
        r = self.run_case()
        self.assertEqual(r['local_base_image'], 'METADATA_PASS')
        self.assertFalse(r['ready_for_build'])
        self.assertFalse(r['files_modified'])
        self.assertFalse(r['images_imported_or_pulled'])

    def test_local_absent_never_pulls(self):
        r = self.run_case(missing=True)
        self.assertEqual(r['local_base_image'], 'NOT_PRESENT')
        self.assertFalse(r['production_workloads_mutated'])

    def test_retag_during_read_rejected(self):
        with self.assertRaisesRegex(p.CheckFailure, 'image_target_changed_during_audit'):
            self.run_case(changed=True)


if __name__ == '__main__':
    unittest.main()
