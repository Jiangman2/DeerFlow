import ast
import contextlib
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import export_base as e
import verify_base_export as v


def raw_json(value):
    return json.dumps(value, separators=(',', ':')).encode()


def sha(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def make_fixture(path, case=None):
    unpacked = b'this is a fixture layer; no extraction or image execution'
    blob = gzip.compress(unpacked)
    layer = {'digest': sha(blob), 'size': len(blob), 'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip'}
    config = {'os': 'linux', 'architecture': 'arm64',
              'rootfs': {'type': 'layers', 'diff_ids': [sha(unpacked)]},
              'config': {'Env': ['TEST_SECRET=not-for-output']}}
    if case == 'platform': config['architecture'] = 'amd64'
    if case == 'diffid': config['rootfs']['diff_ids'] = ['sha256:' + 'e'*64]
    if case == 'urls': layer['urls'] = ['https://unapproved.invalid/layer']
    if case == 'size': layer['size'] += 1
    cfg = raw_json(config)
    manifest = raw_json({'schemaVersion': 2, 'mediaType': e.inv.MANIFESTS[0],
                        'config': {'digest': sha(cfg), 'size': len(cfg)}, 'layers': [layer]})
    index = {'schemaVersion': 2, 'manifests': [{'digest': sha(manifest), 'size': len(manifest),
                                              'mediaType': e.inv.MANIFESTS[0]}]}
    if case == 'wrong_root': index['manifests'][0]['digest'] = 'sha256:'+'f'*64
    files = {'oci-layout': raw_json({'imageLayoutVersion': '1.0.0'}), 'index.json': raw_json(index),
             'blobs/sha256/'+sha(cfg)[7:]: cfg,
             'blobs/sha256/'+sha(manifest)[7:]: manifest,
             'blobs/sha256/'+sha(blob)[7:]: blob}
    if case == 'corrupt': files['blobs/sha256/'+sha(blob)[7:]] = b'x' + blob[1:]
    if case == 'unreferenced': files['blobs/sha256/'+'d'*64] = b'extra'
    if case == 'traversal': files['../escape'] = b'no'
    with tarfile.open(str(path), 'w') as out:
        for name, raw in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            if case == 'link' and name == 'oci-layout':
                info.type = tarfile.SYMTYPE
                info.linkname = '/etc/shadow'
                info.size = 0
                out.addfile(info)
            else:
                out.addfile(info, io.BytesIO(raw))
            if case == 'duplicate' and name == 'index.json':
                out.addfile(info, io.BytesIO(raw))
    return dict(manifest_digest=sha(manifest), config_digest=sha(cfg),
                expected_layers=1, expected_bytes=layer['size']), len(unpacked)


class VerifyTests(unittest.TestCase):
    def test_python37_syntax(self):
        for module in (e, v):
            ast.parse(Path(module.__file__).read_text(), feature_version=(3, 7))

    def test_all_hashes_diffids_and_no_secret_output(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'base.tar'
            params, size = make_fixture(path)
            events = []
            result = v.verify(path, events.append, **params)
            self.assertEqual(result['uncompressed_layer_bytes'], size)
            self.assertEqual(result['layers_verified'], 1)
            self.assertEqual(result['uncompressed_diffids'], 'PASS')
            self.assertFalse(result['archive_extracted'])
            self.assertNotIn('TEST_SECRET', json.dumps([result, events]))
            self.assertEqual([p.name for p in Path(d).iterdir()], ['base.tar'])

    def test_invalid_archives_fail_closed(self):
        cases = {'platform': 'platform_mismatch', 'diffid': 'layer_diffid_mismatch',
                 'urls': 'invalid_layer_descriptor', 'size': 'layer_missing_or_size_mismatch',
                 'wrong_root': 'export_manifest_identity_mismatch', 'corrupt': 'layer_digest_mismatch',
                 'unreferenced': 'unreferenced_archive_blob', 'traversal': 'invalid_archive_path',
                 'duplicate': 'duplicate_or_oversized_archive_member', 'link': 'linked_or_special_archive_member'}
        for case, message in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                path = Path(d)/'base.tar'
                params, _ = make_fixture(path, case)
                with self.assertRaisesRegex(v.CheckFailure, '^'+message+'$'):
                    v.verify(path, lambda row: None, **params)

    def test_stream_limit(self):
        with self.assertRaisesRegex(v.CheckFailure, 'content_size_bound_exceeded'):
            v.hashed(io.BytesIO(b'123456'), 5, lambda row: None, 'test', rate=0)

    def test_duplicate_json_key(self):
        with self.assertRaisesRegex(v.CheckFailure, 'duplicate_json_key'):
            json.loads('{"a":1,"a":2}', object_pairs_hook=v.unique)


class ControllerTests(unittest.TestCase):
    def test_export_is_local_offline_and_single_image(self):
        cmd = e.export_command()
        self.assertEqual(cmd[-2:], ['-', e.inv.IMAGE])
        self.assertIn('--local', cmd)
        self.assertIn('--skip-manifest-json', cmd)
        self.assertIn('linux/arm64', cmd)
        self.assertNotIn('pull', cmd)
        self.assertNotIn('import', cmd)

    def test_metadata_identity_changed(self):
        with patch.object(e.inv, 'capture', return_value=b''):
            with self.assertRaisesRegex(v.CheckFailure, 'base_tag_identity_changed'):
                e.current_identity()

    @unittest.skipUnless(os.name == 'posix', 'Linux file descriptors and selectors')
    def test_export_stream_hash_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)/'archive.partial'
            cmd = [sys.executable, '-c', 'import sys;sys.stdout.buffer.write(b"a"*100000)']
            with patch.object(e, 'emit'):
                result = e.stream_export(cmd, target, lambda: None, rate=0)
            self.assertEqual(result['bytes'], 100000)
            self.assertEqual(result['archive_sha256'], hashlib.sha256(b'a'*100000).hexdigest())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == 'posix', 'Linux file descriptors and selectors')
    def test_export_nonzero_retains_partial_no_retry(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)/'archive.partial'
            cmd = [sys.executable, '-c', 'import sys;sys.stdout.buffer.write(b"partial");sys.exit(1)']
            with self.assertRaisesRegex(v.CheckFailure, 'ctr_export_failed_no_automatic_retry'):
                e.stream_export(cmd, target, lambda: None, rate=0)
            self.assertEqual(target.read_bytes(), b'partial')

    @unittest.skipUnless(os.name == 'posix', 'Linux file descriptors and selectors')
    def test_export_bound_retains_partial(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)/'archive.partial'
            cmd = [sys.executable, '-c', 'import sys;sys.stdout.buffer.write(b"x"*2048)']
            with self.assertRaisesRegex(v.CheckFailure, 'export_archive_size_bound'):
                e.stream_export(cmd, target, lambda: None, maximum=1024, rate=0)
            self.assertTrue(target.is_file())
            self.assertLessEqual(target.stat().st_size, 1024)

    @unittest.skipUnless(os.name == 'posix', 'Linux file descriptors and selectors')
    def test_export_deadline_stops_only_client(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)/'archive.partial'
            cmd = [sys.executable, '-c', 'import time;time.sleep(30)']
            start = time.monotonic()
            with self.assertRaisesRegex(v.CheckFailure, 'export_deadline_exceeded'):
                e.stream_export(cmd, target, lambda: None, deadline=0.1, rate=0)
            self.assertLess(time.monotonic()-start, 5)

    def test_export_refuses_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)/'keep'
            target.write_bytes(b'keep')
            with self.assertRaisesRegex(v.CheckFailure, 'export_target_already_exists'):
                e.stream_export([], target, lambda: None)
            self.assertEqual(target.read_bytes(), b'keep')

    def test_space_not_guessed_from_previous_report(self):
        fake = types.SimpleNamespace(f_blocks=1000, f_frsize=e.GIB, f_bavail=202,
                                     f_favail=100000, f_flag=0)
        with patch.object(e, 'safe_dir'), patch.object(e.os, 'statvfs', return_value=fake), \
             patch.object(e.Path, 'stat', autospec=True, side_effect=lambda path: types.SimpleNamespace(
                 st_dev=1 if str(path)=='/' else 2)):
            with self.assertRaisesRegex(v.CheckFailure, 'export_space_reserve_insufficient'):
                e.check_space(Path('/opt'))
            fake.f_bavail = 204
            result = e.check_space(Path('/opt'))
            self.assertEqual(result['required_free_GiB'], 203)


if __name__ == '__main__':
    unittest.main()
