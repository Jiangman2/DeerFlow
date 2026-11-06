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
SPEC = importlib.util.spec_from_file_location('mobile_fit_package', HERE / 'package-sidechat-security-frontend-20261105.py')
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


ARCHIVED_PACKAGER = HERE.parents[1] / 'release/sidechat-skillhub-security-20261105-r1/packager-as-used.py'
ARCHIVED_PACKAGER_SHA256 = 'eba1902d5c63d8e5a73739296c6a8f0163f3b69d7242b37c325c39adf6dc1be9'


def historical_packager():
    raw = ARCHIVED_PACKAGER.read_bytes()
    if P.pin(raw)['sha256'] != ARCHIVED_PACKAGER_SHA256:
        raise AssertionError('immutable historical packager changed')
    module = types.ModuleType('historical_sidechat_packager')
    # The archived bytes retain their original helper imports; resolve them from
    # the original deploy/enterprise location without editing the archive.
    module.__file__ = str(HERE / 'package-sidechat-security-frontend-20261105.py')
    exec(compile(raw, str(ARCHIVED_PACKAGER), 'exec'), module.__dict__)
    return module


def simulate_import(source, host, archive, after=None):
    """Run the real controller, parser, budget and execute_import against fake IO.

    Do not replace call/import_phase/preserved/verify: their true command sequence
    is the regression. Only the external transport, filesystem observations and
    private receipt writer are substituted; any subprocess is an assertion.
    """
    m = P.base.load_source(source, host)
    importer, resource, imagefs = m.load_inputs()
    controller = m.controller_class(importer, resource, imagefs)(
        'offline-ctr', 'offline-crictl', None, '1' * 32, None,
        {'bundle_sha256': m.TRANSPORT_PIN['sha256'], 'append_layer': {}}, P.time.monotonic())
    refs = dict(m.PROTECTED)
    state = {'imported': False, 'unpacked': False}
    snapshot_path = ('/var/lib/containerd' if host == 'datax-02' else '/mnt/data/containerd') + '/io.containerd.snapshotter.v1.overlayfs'

    def directory(path):
        return dict(path=path, device=1, capacity_bytes=100 * m.GIB,
                    available_bytes=90 * m.GIB, total_inodes=2000000,
                    available_inodes=1900000, fragment_size=4096)

    def snapshots():
        rows = ['KEY PARENT KIND']
        if state['unpacked']:
            rows += ['%s %s Committed' % (r['chain_id'], r['parent']) for r in controller.p['chains']]
        return ('\n'.join(rows) + '\n').encode()

    def transport(argv, timeout, limit, pass_fds=()):
        args = argv[5:]
        phase = controller.report['commands'][-1]['phase']
        if phase in ('containerd_server_version', 'extra_read'):
            out = b'Server:\n  Version: 2.3.3\n'
        elif phase == 'actual_imagefs':
            out = json.dumps({'imageFilesystems': [{'fsId': {'mountpoint': snapshot_path}}]}).encode()
        elif phase == 'image_reference':
            name = args[-1][len('name=='):]
            out = ('REF TYPE DIGEST\n' + ('%s type %s\n' % (name, refs[name]) if name in refs else '')).encode()
        elif phase in ('phase_a', 'phase_b'):
            state['imported'] = True
            state['unpacked'] = phase == 'phase_b'
            refs[controller.temp] = m.MANIFEST
            out = b''
        elif phase == 'publish_canonical_reference':
            refs[args[-1]] = m.MANIFEST
            out = b''
        elif phase in ('budget_content_presence', 'content_closure'):
            out = ('\n'.join(sorted(controller.p['blob_sizes'])) + '\n').encode() if state['imported'] else b''
        elif phase in ('budget_committed_chains', 'snapshot_closure'):
            out = snapshots()
        elif phase == 'manifest_readback':
            out = controller.p['manifest_raw']
        elif phase == 'config_readback':
            out = controller.p['config_raw']
        elif phase == 'content_gc_labels':
            digest = args[-1][len('digest=='):]
            if digest == m.CONFIG:
                labels = {'containerd.io/gc.ref.snapshot.overlayfs': m.FINAL_CHAIN}
            else:
                labels = {'containerd.io/gc.ref.content.config': m.CONFIG}
                labels.update({'containerd.io/gc.ref.content.l.' + str(i): row['digest']
                               for i, row in enumerate(controller.p['manifest']['layers'])})
            out = ('DIGEST SIZE AGE LABELS\n%s 1B now %s\n' %
                   (digest, ','.join(key + '=' + value for key, value in labels.items()))).encode()
        elif phase == 'image_content_and_unpack':
            out = (args[-1][len('name=='):] + '\n').encode()
        elif phase == 'cri_visibility':
            out = json.dumps({'status': {'id': m.CONFIG, 'repoTags': [m.TAG],
                                         'repoDigests': [m.REPO + '@' + m.MANIFEST]}}).encode()
        else:
            raise AssertionError('Unexpected command phase: ' + phase)
        if len(out) > limit:
            raise AssertionError('fake output exceeds real bound')
        return out, b'', {'returncode': 0, 'error_code': None}

    controller.runner = transport
    controller.progress = lambda phase: None
    error = None
    with patch.object(importer, 'write_new', return_value=None), \
            patch.object(resource, 'observed_directory', side_effect=directory), \
            patch.object(m.os, 'sysconf', create=True, return_value=4096), \
            patch.object(importer.subprocess, 'Popen', side_effect=AssertionError('external command forbidden')), \
            archive.open('rb') as stream:
        # These five controller commands occur in main before execute_import.
        m.validate_version(controller.call('containerd_server_version', ['version']))
        controller.fresh_budget()
        controller.p = m.inspect_archive(stream.fileno(), importer, P.time.monotonic())
        controller.fresh_budget()
        controller.archive_fd = stream.fileno()
        try:
            controller.execute_import()
        except m.Refused as exc:
            error = str(exc)
        if after is not None:
            after(m, controller, refs)
    return m, controller.report, error


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

    def test_fixed_new_release_identity(self):
        self.assertEqual(P.TAG, 'deerflow.local/deer-flow-frontend:20261105-sidechat-security-r1')
        self.assertEqual(P.ARCHIVE, 'frontend.sidechat-security-r1.full.oci.tar')
        self.assertEqual(P.REVISION, '20261106-r2')

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
            self.assertEqual(m.PROTECTED[m.REPO + '@' + P.RECENT['20261103-dashboard-navigation-r2']], P.RECENT['20261103-dashboard-navigation-r2'])
            self.assertEqual(m.PROTECTED[m.REPO + '@' + P.RECENT['20261105-new-chat-v41-r1']], P.RECENT['20261105-new-chat-v41-r1'])
            self.assertIn('portal-frontend-sidechat-security-preload-20261106-r2', source)
            self.assertEqual(m.COMMAND_BUDGET, record['command_budget'])
            self.assertEqual(m.COMMAND_BUDGET['planned_calls'], 5 * len(m.PROTECTED) + 52)
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


    def test_historical_r1_real_path_exhausts_datax02_at_200(self):
        historical = historical_packager()
        receipt = historical.build(self.qualified, TEMPLATES, self.root / 'historical')
        for role, expected_calls, error in (('0015', 197, None), ('datax02', 200, 'command_limit')):
            record = receipt['scripts'][role]
            source = (self.root / 'historical' / record['file']).read_text(encoding='utf-8')
            with self.subTest(role=role):
                m, report, actual_error = simulate_import(source, record['hostname'], self.qualified / 'frontend.oci.tar')
                self.assertEqual(actual_error, error)
                self.assertEqual(len(report['commands']), expected_calls)
                self.assertEqual(report['phase_a'], 'PASS')
                self.assertEqual(report['phase_b'], 'PASS')
                if role == 'datax02':
                    self.assertEqual(5 * len(m.PROTECTED) + 52, 207)
                    # verify(), preservation and the final resource check already
                    # ran; update arguments fail during references_after evaluation.
                    self.assertIsNone(report.get('content_and_unpack'))
                    self.assertEqual(report['commands'][-1]['phase'], 'image_reference')
                    self.assertEqual(sum(r['phase'] == 'cri_visibility' for r in report['commands']), 2)

    def test_real_import_path_completes_with_derived_finite_budget(self):
        receipt = self.build()
        self.assertEqual(receipt['tool_revision'], '20261106-r2')
        for role, expected_calls in (('0015', 197), ('datax02', 207)):
            record = receipt['scripts'][role]
            source = (self.root / 'delivery' / record['file']).read_text(encoding='utf-8')
            with self.subTest(role=role):
                m, report, error = simulate_import(source, record['hostname'], self.qualified / 'frontend.oci.tar')
                self.assertIsNone(error)
                self.assertEqual(len(report['commands']), expected_calls)
                self.assertEqual(m.COMMAND_BUDGET['planned_calls'], expected_calls)
                self.assertEqual(m.COMMAND_BUDGET['max_calls'], expected_calls + 16)
                self.assertEqual(report['content_and_unpack'], 'PASS')
                self.assertEqual(report['cri_visibility'], 'PASS')
                self.assertTrue(report['old_references_preserved'])
                self.assertFalse(report['image_import_may_be_partial'])
                self.assertEqual(len(report['references_after']), len(m.PROTECTED) + 2)
                self.assertEqual(sum(r['containerd_mutation_requested'] for r in report['commands']), 4)

    def test_additional_reads_remain_bounded_and_margin_cannot_mutate(self):
        receipt = self.build()
        record = receipt['scripts']['datax02']
        source = (self.root / 'delivery' / record['file']).read_text(encoding='utf-8')

        def exhaust(m, controller, refs):
            before = dict(refs)
            # Reaching the margin cannot authorize another import/tag mutation.
            with self.assertRaisesRegex(m.Refused, 'command_readonly_margin'):
                controller.call('unexpected_import', ['images', 'import', '--local'], mutates=True)
            for _ in range(m.COMMAND_BUDGET['additional_readonly_calls']):
                controller.call('extra_read', ['version'])
            self.assertEqual(len(controller.report['commands']), m.COMMAND_BUDGET['max_calls'])
            for _ in range(3):
                with self.assertRaisesRegex(m.Refused, 'command_limit'):
                    controller.call('extra_read', ['version'])
            self.assertEqual(refs, before)

        _, report, error = simulate_import(source, record['hostname'], self.qualified / 'frontend.oci.tar', exhaust)
        self.assertIsNone(error)
        self.assertEqual(len(report['commands']), 223)

    def test_reference_growth_cannot_exceed_global_budget(self):
        with self.assertRaisesRegex(ValueError, 'COMMAND_BUDGET_REQUIRES_REVIEW'):
            P.command_budget(dict.fromkeys(range(63)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
