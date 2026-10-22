"""Small POSIX source-seal fixtures; no candidate payloads, SSH or container runtime."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


c = module('source_scan_under_test', HERE / 'collect_cache.py')
b = module('frozen_source_scan_helpers', HERE.parent / 'native-preload-r2/preload_native_r2.py')


def dig(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def js(value):
    return json.dumps(value, separators=(',', ':')).encode()


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'requires Linux root in isolated local fixture')
class SourceScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp')
        self.parent = Path(self.temp.name)
        # /tmp is intentionally writable: anchor tests use a safe_dir override
        # only for this fixture. The production helper stays unchanged.
        self.original_safe = b.safe_dir
        b.safe_dir = lambda path: None
        c.B = b
        c.SEALED = self.parent

    def tearDown(self):
        b.safe_dir = self.original_safe
        self.temp.cleanup()

    def make_archive(self):
        layer = b'fixture layer payload never interpreted'
        config = js(dict(architecture='arm64', os='linux', rootfs=dict(diff_ids=[dig(b'diff')]),
                         config=dict(Env=['SECRET=fixture-must-not-export'], Cmd=['private-fixture-arg'])))
        manifest = js(dict(schemaVersion=2, config=dict(digest=dig(config), size=len(config)),
                           layers=[dict(digest=dig(layer), size=len(layer))]))
        index = js(dict(schemaVersion=2, manifests=[dict(digest=dig(manifest), size=len(manifest))]))
        path = self.parent / 'fixture.tar'
        with tarfile.open(path, 'w') as archive:
            for directory in ('blobs', 'blobs/sha256'):
                member = tarfile.TarInfo(directory)
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            for name, raw in [('index.json', index), ('blobs/sha256/' + dig(config)[7:], config),
                              ('blobs/sha256/' + dig(manifest)[7:], manifest),
                              ('blobs/sha256/' + dig(layer)[7:], layer)]:
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
        path.chmod(0o600)
        row = dict(role='fixture', basename=path.name, archive_bytes=path.stat().st_size,
                   archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   manifest_digest=dig(manifest), config_digest=dig(config))
        known = [dict(blob_digest=dig(layer), compressed_bytes=len(layer), diff_id=dig(b'diff'),
                      uncompressed_bytes_upper_bound=1024)]
        return path, row, known

    def test_real_tar_headers_metadata_chain_and_secret_projection(self):
        path, row, known = self.make_archive()
        out = c.scan_archive(row, row, known)
        self.assertEqual(out['layers'][0]['chain_id'], known[0]['diff_id'])
        self.assertEqual(len(out['outer_blobs']), 3)
        self.assertNotIn('fixture-must-not-export', json.dumps(out))
        self.assertNotIn('private-fixture-arg', json.dumps(out))
        self.assertEqual(out['archive_sha256'], row['archive_sha256'])

    def test_wrong_layer_binding_is_rejected(self):
        path, row, known = self.make_archive()
        known[0]['diff_id'] = dig(b'changed')
        with self.assertRaisesRegex(ValueError, 'catalog_layer_binding'):
            c.scan_archive(row, row, known)

    def test_symlink_and_group_writable_seal_rejected(self):
        path, row, known = self.make_archive()
        path.chmod(0o660)
        with self.assertRaisesRegex(ValueError, 'source_seal_metadata_changed'):
            c.scan_archive(row, row, known)
        path.chmod(0o600)
        link = self.parent / 'link.tar'
        link.symlink_to(path)
        row['basename'] = link.name
        with self.assertRaises(OSError):
            c.scan_archive(row, row, known)

    def test_private_helper_reader_rejects_untrusted_ancestor(self):
        path = self.parent / 'private.py'
        path.write_bytes(b'not executed')
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'private_parent_changed'):
            c.private_read(path, 128)

    def test_generated_catalog_hash_and_role_binding(self):
        catalog = c.unpack(c.CATALOG_Z64)
        self.assertEqual(c.sha(json.dumps(catalog, sort_keys=True, separators=(',', ':')).encode()), c.CATALOG_SHA)
        rows = c.catalog_rows(catalog)
        self.assertEqual(len(rows), 6)
        self.assertEqual(len(rows['ppt-master-agent-host-forward'][1]), 128)


if __name__ == '__main__':
    unittest.main()
