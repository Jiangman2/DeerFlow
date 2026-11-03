"""Offline package integrity and node admission checks; no production commands."""
import ast
import contextlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('mobile_fit_package', HERE / 'package-dashboard-navigation-frontend-20261103.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
TEMPLATES = HERE.parents[1] / 'release/dashboard-navigation-20261103/importer-templates'


def tar_bytes(files):
    import tarfile
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, raw in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(raw), 0o600
            archive.addfile(entry, io.BytesIO(raw))
    return output.getvalue()


def fixture(folder):
    layer_raw = tar_bytes({'fixture.txt': b'local synthetic image\n'})
    layer = gzip.compress(layer_raw, mtime=0)
    dg = lambda raw: 'sha256:' + P.pin(raw)['sha256']
    encode = lambda value: json.dumps(value, sort_keys=True).encode()
    config = encode({'os': 'linux', 'architecture': 'arm64',
                     'rootfs': {'type': 'layers', 'diff_ids': [dg(layer_raw)] * 11}})
    manifest = encode({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                      'config': {'digest': dg(config), 'size': len(config),
                                 'mediaType': 'application/vnd.oci.image.config.v1+json'},
                      'layers': [{'digest': dg(layer), 'size': len(layer),
                                  'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip'}] * 11})
    archive = tar_bytes({'oci-layout': encode({'imageLayoutVersion': '1.0.0'}),
                         'index.json': encode({'schemaVersion': 2, 'manifests': [
                             {'digest': dg(manifest), 'size': len(manifest),
                              'mediaType': 'application/vnd.oci.image.manifest.v1+json'}]}),
                         'blobs/sha256/' + dg(manifest)[7:]: manifest,
                         'blobs/sha256/' + dg(config)[7:]: config,
                         'blobs/sha256/' + dg(layer)[7:]: layer})
    (folder / 'frontend.oci.tar').write_bytes(archive)
    (folder / 'SOURCE.json').write_text('{}', encoding='utf-8')
    record = {'frontend_slim': 'LOCAL_PASS', 'platform': 'linux/arm64',
              'image': {'tag': P.TAG, 'id': dg(manifest)},
              'fresh_runtime': {'pinned_base_prefix': 'PASS'},
              'runtime': {'http': {'local_http': 'PASS'}},
              'export': dict(P.pin(archive), layers=11, exact_qualified_layers='PASS',
                             blob_and_diffid_hashes='PASS')}
    save(folder, record)
    return record


def save(folder, record):
    (folder / 'RESULT.json').write_text(json.dumps(record), encoding='utf-8')


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.qualified = self.root / 'qualified'
        self.qualified.mkdir()
        self.record = fixture(self.qualified)

    def build(self):
        return P.build(self.qualified, TEMPLATES, self.root / 'delivery')

    def test_unqualified_and_wrong_platform_refused_before_delivery(self):
        for field, value in (('frontend_slim', 'FAIL'), ('platform', 'linux/amd64')):
            with self.subTest(field=field):
                changed = dict(self.record, **{field: value})
                save(self.qualified, changed)
                with self.assertRaisesRegex(ValueError, 'LOCAL_QUALIFICATION_REQUIRED'):
                    self.build()
                self.assertFalse((self.root / 'delivery').exists())

    def test_runtime_or_export_rejection_is_not_packageable(self):
        for section, key in (('fresh_runtime', 'pinned_base_prefix'), ('export', 'exact_qualified_layers'),
                             ('export', 'blob_and_diffid_hashes')):
            changed = json.loads(json.dumps(self.record))
            changed[section][key] = 'FAIL'
            save(self.qualified, changed)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'QUALIFICATION_SCOPE'):
                self.build()
        self.assertFalse((self.root / 'delivery').exists())

    def test_changed_archive_refused_before_output(self):
        path = self.qualified / 'frontend.oci.tar'
        raw = bytearray(path.read_bytes())
        raw[0] ^= 1
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'EXPORT_CHANGED'):
            self.build()
        self.assertFalse((self.root / 'delivery').exists())

    def test_changed_template_refused_before_output(self):
        with patch.dict(P.TEMPLATES, {'0015': (P.TEMPLATES['0015'][0], '0' * 64)}):
            with self.assertRaisesRegex(ValueError, 'IMPORTER_CHANGED_0015'):
                self.build()
        self.assertFalse((self.root / 'delivery').exists())

    def test_full_oci_closure_and_protected_images_preserved(self):
        receipt = self.build()
        self.assertFalse(receipt['ready_for_rollout'])
        output = self.root / 'delivery'
        self.assertEqual((output / P.ARCHIVE).read_bytes(), (self.qualified / 'frontend.oci.tar').read_bytes())
        for role, record in receipt['scripts'].items():
            source = (output / record['file']).read_text(encoding='utf-8')
            ast.parse(source, feature_version=(3, 7))
            m = P.base.load_source(source, record['hostname'])
            self.assertEqual(m.NODES, {record['hostname']: record['expected_node_uid']})
            self.assertTrue(m.EARLY_BUDGET['layer_stats'])
            self.assertEqual(m.CACHED_DESCRIPTORS, [])
            self.assertEqual(m.PROTECTED[m.REPO + '@' + P.RECENT['20261102-composer-controls-r4']],
                             P.RECENT['20261102-composer-controls-r4'])
            self.assertEqual(m.PROTECTED[m.REPO + '@' + P.RECENT['20261102-usage-monitor-r2']],
                             P.RECENT['20261102-usage-monitor-r2'])
            self.assertEqual(m.PROTECTED[m.REPO + '@' + P.RECENT['20261102-usage-heatmap-r2']], P.RECENT['20261102-usage-heatmap-r2'])
            self.assertIn('portal-frontend-dashboard-navigation-preload-20261103-r1', source)
            self.assertNotIn("20261102-usage-heatmap-r1", repr(m.PROTECTED))
            self.assertTrue(m.CONTROL.startswith("/mnt/data/" if role == "0015" else "/var/lib/"))
            m.transport_guard()
            with patch.object(m.sys, 'argv', ['script', '--preload-frontend-dashboard-recovery-once']), \
                    patch.object(m.os, 'name', 'nt'), \
                    patch.object(m.os, 'mkdir', side_effect=AssertionError('unexpected mutation')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(), 1)

    def test_early_capacity_refusal_cannot_claim_or_import(self):
        receipt = self.build()
        for record in receipt['scripts'].values():
            source = (self.root / 'delivery' / record['file']).read_text(encoding='utf-8')
            m = P.base.load_source(source, record['hostname'])
            with patch.object(m, 'worker_budget', side_effect=m.Refused('root_nodefs_headroom')), \
                    patch.object(m.os, 'open', side_effect=AssertionError('input should not be opened')), \
                    patch.object(m.os, 'mkdir', side_effect=AssertionError('must not claim')):
                with self.assertRaisesRegex(m.Refused, 'root_nodefs_headroom'):
                    m.early_preload_gate(None, None, None, ('ctr', 'cri'), None, P.time.monotonic())


if __name__ == '__main__':
    unittest.main(verbosity=2)
