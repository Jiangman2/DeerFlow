import ast
import contextlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import time
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('host_audit', Path(__file__).with_name('verify_host_base.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def encoded(doc):
    return json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()


def layer(rows):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for row in rows:
            name, data = row[:2]
            member = tarfile.TarInfo(name)
            if len(row) > 2:
                member.type = row[2]
            if member.issym() or member.islnk():
                member.linkname = data.decode()
                data = b''
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def oci(path, layers, wrong_diff=False, bad_blob=False):
    compressed = [gzip.compress(raw, mtime=0) for raw in layers]
    diffs = ['sha256:' + m.sha(raw) for raw in layers]
    if wrong_diff:
        diffs[-1] = 'sha256:' + '0' * 64
    config = encoded({'os': 'linux', 'architecture': 'arm64', 'rootfs': {'type': 'layers', 'diff_ids': diffs},
                      'config': {'Env': ['PASSWORD=PRIVATE_SYNTHETIC_VALUE'], 'Cmd': ['PRIVATE_SYNTHETIC_CMD']}})
    cd = 'sha256:' + m.sha(config)
    manifest = encoded({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                        'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': cd, 'size': len(config)},
                        'layers': [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip',
                                    'digest': 'sha256:' + m.sha(raw), 'size': len(raw)} for raw in compressed]})
    md = 'sha256:' + m.sha(manifest)
    files = [('oci-layout', encoded({'imageLayoutVersion': '1.0.0'})),
             ('index.json', encoded({'schemaVersion': 2, 'manifests': [{'mediaType': 'application/vnd.oci.image.manifest.v1+json',
               'digest': md, 'size': len(manifest), 'annotations': {'io.containerd.image.name': m.TAG}}]})),
             ('blobs/sha256/' + md[7:], manifest), ('blobs/sha256/' + cd[7:], config)]
    files += [('blobs/sha256/' + m.sha(raw), raw) for raw in dict.fromkeys(compressed)]
    if bad_blob:
        files[-1] = (files[-1][0], b'X' * len(files[-1][1]))
    path.write_bytes(layer(files))
    path.chmod(0o600)
    return {'ARCHIVE_SHA': m.sha(path.read_bytes()), 'MANIFEST': md, 'CONFIG': cd,
            'LAYERS': len(layers), 'COMPRESSED_LAYER_BYTES': sum(map(len, compressed))}


class HostAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api, cls.outer, cls.expected = m.load_inputs()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = 'opt/ppt-master-agent-host/agent_host.py'
        self.new = 'opt/ppt-master-agent-host/ppt_task_receipts.py'
        self.report = {'diagnostic_headers': []}

    def replay(self, *rows):
        replay = m.make_replay(self.api, {self.target, self.new}, self.report)
        for number, items in enumerate(rows):
            raw = layer(items)
            replay.current_layer = 'sha256:' + m.sha(raw)
            reader = self.api.DigestReader(io.BytesIO(raw), time.monotonic() + 10)
            replay.layer(reader, time.monotonic() + 10)
            while reader.read(m.CHUNK):
                pass
            self.assertEqual(reader.hash.hexdigest(), m.sha(raw))
        return replay

    def audit(self, layers, wrong_diff=False, bad_blob=False):
        path = self.root / 'fixture.oci.tar'
        changes = oci(path, layers, wrong_diff, bad_blob)
        expected = {'targets': [{'target': self.target, 'expected_before': {'status': 'PRESENT', 'sha256': m.sha(b'old'), 'bytes': 3}},
                                {'target': self.new, 'expected_before': {'status': 'REQUIRE_PHYSICAL_ABSENCE'}}]}
        with mock.patch.multiple(m, **changes), contextlib.redirect_stdout(io.StringIO()):
            m.audit(path, self.api, self.outer, expected, self.report, time.monotonic() + 20)
        return self.report

    def test_frozen_core_and_exact_thirteen_path_profile(self):
        self.assertEqual(len(self.expected['targets']), 13)
        self.assertEqual(sum(x['expected_before']['status'] == 'REQUIRE_PHYSICAL_ABSENCE' for x in self.expected['targets']), 8)
        replay = m.make_replay(self.api, {x['target'] for x in self.expected['targets']}, self.report)
        self.assertFalse(replay.selected('app/backend/packages/harness/deerflow/persistence/migrations/versions/0020.py'))
        self.assertFalse(replay.selected('root/.codex/auth.json'))
        self.assertFalse(replay.selected('mnt/user-data/workspace/secret.txt'))

    def test_whole_tiny_oci_diffids_and_no_env_projection(self):
        result = self.audit([layer([(self.target, b'old')]), layer([('tmp/unselected', b'ignored')])])
        self.assertEqual(result['status'], 'DIAGNOSTIC_COMPLETE_REVIEW_REQUIRED')
        self.assertTrue(result['all_source_preconditions_match'])
        self.assertEqual(len(result['layers']), 2)
        text = json.dumps(result)
        self.assertNotIn('PRIVATE_SYNTHETIC', text)
        self.assertNotIn('ignored', text)
        self.assertFalse(result['outer_archive']['extracted'])

    def test_corrupt_compressed_blob_and_diffid_rejected(self):
        for wrong_diff, bad_blob in [(True, False), (False, True)]:
            with self.subTest(wrong_diff=wrong_diff, bad_blob=bad_blob):
                self.report = {'diagnostic_headers': []}
                with self.assertRaises((self.api.Refused, self.outer.Failure)):
                    self.audit([layer([(self.target, b'old')])], wrong_diff, bad_blob)

    def test_new_file_conflict_and_old_source_drift(self):
        for rows in [[(self.target, b'old'), (self.new, b'existing')], [(self.target, b'drift')]]:
            with self.subTest(rows=rows):
                self.report = {'diagnostic_headers': []}
                with self.assertRaisesRegex(m.Refused, 'physical_source_preconditions_differ'):
                    self.audit([layer(rows)])
                self.assertTrue(any(not x['pass'] for x in self.report['source_preconditions']))

    def test_whiteout_preserves_same_layer_write_and_opaque_erases_lower(self):
        parent = self.target.rsplit('/', 1)[0]
        replay = self.replay([(self.target, b'old'), (self.new, b'old helper')],
                             [(self.target, b'new'), (parent + '/.wh..wh..opq', b'')])
        self.assertEqual(replay.observe(self.target)['sha256'], m.sha(b'new'))
        self.assertIsNone(replay.observe(self.new))
        replay = self.replay([(self.target, b'old')], [(parent + '/.wh.agent_host.py', b'')])
        self.assertIsNone(replay.observe(self.target))

    def test_target_and_ancestor_links_never_followed(self):
        for rows in [[(self.target, b'/secret', tarfile.SYMTYPE)],
                     [('opt/ppt-master-agent-host', b'/secret', tarfile.SYMTYPE), (self.target, b'old')]]:
            replay = self.replay(rows)
            with self.assertRaises(self.api.Refused):
                replay.observe(self.target)
            self.assertNotIn('/secret', json.dumps(replay.state))

    def test_dependency_dynamic_ancestor_link_is_recorded(self):
        parent = 'usr/local/lib/python3.10/dist-packages/flask'
        leaf = parent + '/__init__.py'
        replay = self.replay([(parent, b'/private', tarfile.SYMTYPE), (leaf, b'code')])
        result = m.dependency_checks(replay)
        row = next(x for x in result['files'] if x['path'] == '/' + leaf)
        self.assertTrue(row['ancestor_link_or_non_directory'])
        self.assertFalse(result['links_followed'])

    def test_dependency_hash_only_and_no_dynamic_scope_expansion(self):
        leaf = 'usr/local/lib/python3.10/dist-packages/flask/__init__.py'
        replay = self.replay([(leaf, b'private hardcoded fixture'), ('opt/ms-playwright/chromium-1234/chrome-linux64/chrome', b'ELF')])
        self.assertEqual(replay.state[leaf]['sha256'], m.sha(b'private hardcoded fixture'))
        self.assertNotIn('private hardcoded', json.dumps(m.dependency_checks(replay)))
        for bad in ['usr/local/lib/python3.10/dist-packages/flask/auth.json', 'opt/ms-playwright/auth.json',
                    'usr/local/lib/python3.10/dist-packages/other-1.dist-info/METADATA']:
            self.assertFalse(replay.selected(bad))

    def test_known_diagnostic_header_complete_replay_still_review_required(self):
        unusual = 'usr/share/doc/fixture\\name.txt'
        with mock.patch.object(m, 'KNOWN_HEADER', m.sha(unusual.encode())):
            result = self.audit([layer([(unusual, b'header fixture'), (self.target, b'old')])])
        self.assertEqual(result['status'], 'DIAGNOSTIC_COMPLETE_REVIEW_REQUIRED')
        note = result['diagnostic_headers'][0]
        self.assertEqual(note['type_hex'], tarfile.REGTYPE.hex())
        self.assertTrue(note['strict_posix_disjoint_from_targets_and_ancestors'])
        self.assertTrue(note['layer_digest'].startswith('sha256:'))
        self.assertNotIn(unusual, json.dumps(result))

    def test_known_name_cannot_affect_targets_ancestors_or_dependencies(self):
        for unusual in ['opt\\ppt-master-agent-host/agent_host.py', 'opt\\ppt-master-agent-host',
                        'usr\\local/lib/python3.10/dist-packages/flask/x', 'opt\\.wh.ppt-master-agent-host',
                        'usr/share/doc/..\\..\\opt/ppt-master-agent-host/agent_host.py']:
            with self.subTest(unusual=unusual), mock.patch.object(m, 'KNOWN_HEADER', m.sha(unusual.encode())):
                with self.assertRaises(m.Refused):
                    self.replay([(unusual, b'x')])

    def test_unknown_second_or_link_unsafe_header_refused(self):
        unusual = 'usr/share/doc/fixture\\name.txt'
        with self.assertRaisesRegex(m.Refused, 'unknown_or_second'):
            self.replay([(unusual, b'x')])
        for rows in [[(unusual, b'x'), (unusual, b'x')], [(unusual, b'/private', tarfile.SYMTYPE)]]:
            self.report = {'diagnostic_headers': []}
            with mock.patch.object(m, 'KNOWN_HEADER', m.sha(unusual.encode())):
                with self.assertRaises(m.Refused):
                    self.replay(rows)

    def test_paths_duplicates_sparse_and_pax_bound(self):
        for name in ['../escape', '/absolute', 'a/../escape', 'a//b']:
            with self.subTest(name=name), self.assertRaises((m.Refused, self.api.Refused)):
                self.replay([(name, b'x')])
        with self.assertRaisesRegex(m.Refused, 'duplicate_layer_path'):
            self.replay([(self.target, b'one'), (self.target, b'two')])
        for kind in [tarfile.GNUTYPE_SPARSE, tarfile.XHDTYPE]:
            raw = layer([('unknown', b'X' * 65537, kind)])
            replay = m.make_replay(self.api, {self.target}, self.report)
            with self.assertRaises((self.api.Refused, tarfile.TarError)):
                replay.layer(io.BytesIO(raw), time.monotonic() + 10)

    def test_selected_expansion_and_deadline_bounds(self):
        with mock.patch.object(m, 'MAX_DEPENDENCY', 1):
            with self.assertRaisesRegex(m.Refused, 'selected_file_limit'):
                self.replay([('usr/bin/python3.10', b'large')])
        with mock.patch.object(m, 'MAX_HASHED_SELECTION', 1):
            with self.assertRaisesRegex(m.Refused, 'selected_hash_byte_limit'):
                self.replay([(self.target, b'large')])
        reader = self.api.DigestReader(io.BytesIO(b'123'), time.monotonic() + 5, limit=2)
        with self.assertRaisesRegex(self.api.Refused, 'expanded_layer_limit'):
            reader.read(3)
        replay = m.make_replay(self.api, {self.target}, self.report)
        with self.assertRaisesRegex(m.Refused, 'deadline_exceeded'):
            replay.layer(io.BytesIO(layer([(self.target, b'old')])), time.monotonic() - 1)

    def test_missing_unexpected_blobs_and_trailer_rejected(self):
        path = self.root / 'outer.tar'
        changes = oci(path, [layer([(self.target, b'old')])])
        raw = path.read_bytes()
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            files = [(x.name, tf.extractfile(x).read()) for x in tf]
        manifest = dict(files)['blobs/sha256/' + changes['MANIFEST'][7:]]
        config = dict(files)['blobs/sha256/' + changes['CONFIG'][7:]]
        blobs, ordered = self.outer.expected_blobs(manifest, config, changes['MANIFEST'], changes['CONFIG'], 1)
        for changed in [layer(files[:-1]), layer(files + [('secret.txt', b'x')]), raw + b'hidden']:
            path.write_bytes(changed)
            with self.assertRaises(self.outer.Failure):
                self.outer.validate_archive(path, blobs, ordered, time.monotonic() + 5, changes['MANIFEST'], changes['CONFIG'], m.TAG)

    def test_python37_and_no_application_import_or_execution_path(self):
        tree = ast.parse(Path(m.__file__).read_text(encoding='utf-8'), feature_version=(3, 7))
        imports = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
        self.assertFalse(imports & {'subprocess', 'socket', 'requests', 'docker'})
        self.assertNotIn('extractall', Path(m.__file__).read_text(encoding='utf-8'))
        self.assertNotIn('eval(', Path(m.__file__).read_text(encoding='utf-8'))

    def test_fresh_fixed_output_and_exclusive_report(self):
        base = self.root / 'fixed-output'
        dest = base / 'host-base-0123456789abcdef'
        directory = m.fresh_output(dest, base)
        path, digest = m.write_report(directory, {'ready_for_build': False})
        self.assertEqual(digest, m.sha(path.read_bytes()))
        with self.assertRaises(FileExistsError):
            m.write_report(directory, {})
        with self.assertRaises(FileExistsError):
            m.fresh_output(dest, base)
        for outside in [base / '../escape', base / 'anything', self.root / 'host-base-0123456789abcdef']:
            with self.assertRaises(m.Refused):
                m.fresh_output(outside, base)
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink/private ownership semantics require Linux')
    def test_output_link_and_nonprivate_base_refused(self):
        base = self.root / 'base'
        base.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(m.Refused):
            m.fresh_output(base / 'host-base-0123456789abcdef', base)
        base.unlink()
        base.mkdir(mode=0o755)
        with self.assertRaisesRegex(m.Refused, 'output_base_not_private'):
            m.fresh_output(base / 'host-base-0123456789abcdef', base)

    def test_cli_refuses_without_explicit_verify_and_wrong_host(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            m.main(['--out', 'ignored'])
        wrong_host = mock.patch.object(m.os, 'uname', return_value=types.SimpleNamespace(nodename='fixture-wrong-host')) if os.name == 'posix' else contextlib.nullcontext()
        with contextlib.redirect_stdout(io.StringIO()) as log, wrong_host:
            self.assertEqual(m.main(['--verify', '--out', str(self.root / 'uncreated')]), 2)
        self.assertIn('root_datax_gate', log.getvalue())
        self.assertFalse((self.root / 'uncreated').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
