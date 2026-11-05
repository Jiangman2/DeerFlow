import ast
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import qualify_runtime_candidate_fix1 as q
import verify_runtime_candidate as original

LAYER = 'application/vnd.oci.image.layer.v1.tar+gzip'
OCI = 'application/vnd.oci.image.'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def member(name='app', directory=True, uid=1000, gid=1000, mode=0o755, data=b''):
    m = tarfile.TarInfo(name)
    m.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    m.uid, m.gid, m.mode, m.size = uid, gid, mode, len(data)
    return m, data


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Linux root-private fixtures')
class RuntimeReverificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build = self.root/'build1'
        self.build.mkdir(mode=0o700)
        self.layout = self.root/'layout'
        self.expected = {'app/WeKnora': {'mode': 0o755, 'sha256': sha(b'executable')}}
        self.inherited = {'metadata': dict(q.APP), 'attributes': {}}

    def write(self, path, raw):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o600)

    def doc(self, path, value):
        self.write(path, json.dumps(value).encode())

    def blob(self, layout, raw, kind):
        self.write(layout/'blobs/sha256'/sha(raw), raw)
        return {'mediaType': kind, 'digest': 'sha256:'+sha(raw), 'size': len(raw)}

    def layer(self, entries, layout=None):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w', format=tarfile.PAX_FORMAT) as t:
            for m, data in entries:
                t.addfile(m, io.BytesIO(data))
        raw = buf.getvalue()
        return self.blob(layout or self.layout, gzip.compress(raw), LAYER), 'sha256:'+sha(raw)

    def delta(self, entries, expected=None, seen=None, inherited=None):
        row, _ = self.layer(entries)
        seen = set() if seen is None else seen
        q.delta(self.layout/'blobs/sha256'/row['digest'][7:], row,
                self.expected if expected is None else expected, seen,
                self.inherited if inherited is None else inherited)
        return seen

    def executable(self, **kw):
        return member('app/WeKnora', False, 0, 0, data=b'executable', **kw)

    def test_preserved_nonroot_app_only(self):
        self.assertEqual(self.delta([member(), self.executable()]), {'app/WeKnora'})

    def test_new_app_owner_must_match_locked_base(self):
        for uid, gid in ((0, 0), (1000, 0), (0, 1000), (1001, 1001)):
            with self.subTest(uid=uid, gid=gid), self.assertRaisesRegex(q.ReverifyFailure, 'app_directory_differs'):
                self.delta([member(uid=uid, gid=gid)])

    def test_app_modes_remain_fail_closed(self):
        for mode in (0o777, 0o775, 0o700, 0o4755, 0o2755):
            with self.subTest(mode=mode), self.assertRaises(q.ReverifyFailure):
                self.delta([member(mode=mode)])

    def test_executable_must_remain_root_owned(self):
        for uid, gid in ((1000, 0), (0, 1000), (1000, 1000)):
            m, data = self.executable(); m.uid, m.gid = uid, gid
            with self.subTest(uid=uid, gid=gid), self.assertRaisesRegex(q.ReverifyFailure, 'file_or_owner'):
                self.delta([(m, data)])

    def test_other_parent_cannot_be_nonroot(self):
        expected = {'usr/local/bin/probe': {'mode': 0o755, 'sha256': sha(b'probe')}}
        with self.assertRaisesRegex(q.ReverifyFailure, 'nonroot_new_runtime_directory'):
            self.delta([member('usr')], expected)

    def test_links_devices_sparse_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            m, data = member(); m.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE): m.linkname = '/private'
            with self.subTest(kind=kind), self.assertRaises(q.ReverifyFailure):
                self.delta([(m, data)])
        m, _ = member(); m.sparse = [(0, 1)]
        with patch.object(q, 'layer_members', return_value=iter([('app', m, None)])):
            with self.assertRaisesRegex(q.ReverifyFailure, 'unsafe_runtime_delta'):
                q.delta(None, None, self.expected, set(), self.inherited)

    def test_paths_duplicate_headers_and_whiteouts_rejected(self):
        for name in ('../app', '/app', 'app\\child', 'other', '.wh.app', 'app/.wh.WeKnora'):
            with self.subTest(name=name), self.assertRaises(q.ReverifyFailure):
                self.delta([member(name)])
        with self.assertRaisesRegex(q.ReverifyFailure, 'unsafe_runtime_delta'):
            self.delta([member(), member()])

    def test_duplicate_binary_across_layers_rejected(self):
        seen = self.delta([self.executable()])
        with self.assertRaisesRegex(q.ReverifyFailure, 'file_or_owner'):
            self.delta([self.executable()], seen=seen)

    def test_hash_and_mode_must_match(self):
        with self.assertRaisesRegex(q.ReverifyFailure, 'runtime_file_hash'):
            self.delta([member('app/WeKnora', False, 0, 0, data=b'changed')])
        with self.assertRaisesRegex(q.ReverifyFailure, 'runtime_file_mode_or_size'):
            self.delta([self.executable(mode=0o644)])

    def test_extra_permissions_pax_rejected_but_time_ignored(self):
        m, data = member(); m.pax_headers = {'SCHILY.xattr.user.test': 'different'}
        with self.assertRaisesRegex(q.ReverifyFailure, 'app_directory_differs'):
            self.delta([(m, data)])
        m.pax_headers = {'mtime': '123.456'}
        self.delta([(m, data)])

    def test_base_effective_directory_resolved(self):
        empty, _ = self.layer([])
        app, _ = self.layer([member()])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(q.base_app(self.layout, [empty, app, empty]), self.inherited)

    def test_base_wrong_owner_missing_or_whiteout_rejected(self):
        for entries in ([], [member(uid=0, gid=0)], [member('.wh.app')],
                        [member('.wh..wh..opq')], [member(), member()]):
            row, _ = self.layer(entries)
            with self.subTest(entries=entries), self.assertRaises(q.ReverifyFailure):
                q.base_app(self.layout, [row])

    def test_private_path_and_qualification_symlink_protection(self):
        proof = self.root/'proof'; proof.mkdir(mode=0o700)
        self.write(proof/'safe', b'receipt')
        self.write(proof/'empty-success-stderr.txt', b'')
        q.proof_safety(proof)
        (proof/'alias').symlink_to(proof/'safe')
        with self.assertRaises(OSError): q.proof_safety(proof)
        (proof/'alias').unlink(); proof.chmod(0o777)
        with self.assertRaisesRegex(q.ReverifyFailure, 'unsafe_private_directory'):
            q.proof_safety(proof)

    def test_copy_preserves_original_and_rejects_repeat(self):
        source, output = self.root/'old.partial', self.root/'output'
        self.write(source, b'sealed candidate'); output.mkdir(mode=0o700)
        before = source.stat()
        final = q.copy_sealed(source, output, sha(source.read_bytes()))
        self.assertEqual(final.read_bytes(), source.read_bytes())
        self.assertEqual(q.identity(before), q.identity(source.stat()))
        self.assertEqual(final.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(q.ReverifyFailure, 'fresh_seal_required'):
            q.copy_sealed(source, output, sha(source.read_bytes()))

    def test_bad_seal_hash_never_publishes_final(self):
        source, output = self.root/'old.partial', self.root/'output'
        self.write(source, b'candidate'); output.mkdir(mode=0o700)
        with self.assertRaisesRegex(q.ReverifyFailure, 'source_archive_changed'):
            q.copy_sealed(source, output, '0'*64)
        self.assertFalse((output/'weknora.candidate.oci.tar').exists())
        self.assertEqual(source.read_bytes(), b'candidate')

    def test_previous_failure_exact_boundary_and_no_repetition(self):
        self.doc(self.build/'BUILD-FAILED.json', {'stage': 'verify-runtime-candidate',
                  'images_imported': False, 'production_writes': 0})
        self.write(self.build/'06-verify-runtime-candidate.log',
                   b'{"total_layers": 24}\nCheckFailure: unsafe_runtime_delta')
        with patch.object(q, 'ROOT', self.root):
            self.assertIn('failed_log_sha256', q.previous())
            self.doc(self.build/'BUILD-RESULT.json', {'PASS': True})
            with self.assertRaisesRegex(q.ReverifyFailure, 'old_success_exists'): q.previous()
        with patch.object(q, 'STATE', self.root):
            with self.assertRaisesRegex(q.ReverifyFailure, 'previous_reverification_exists'): q.run()

    def test_python37_no_build_or_image_execution_commands(self):
        source = Path(q.__file__).read_text()
        ast.parse(source, feature_version=(3, 7))
        calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == 'subprocess']
        self.assertEqual(len(calls), 1)
        self.assertEqual(ast.literal_eval(calls[0].args[0]), ['ps', '-eo', 'pid=,comm=,args='])

    def test_package_complete_hash_binding_before_any_import(self):
        package = self.root/'package'
        files = {'verify_runtime_candidate.py': b'original frozen verifier',
                 'build_guard.py': b'original frozen guard', 'context.tar': b'locked payload',
                 'BUILD-PLAN.json': b'{"fixture": true}'}
        for name, data in files.items(): self.write(package/name, data)
        manifest = ''.join(sha(data)+'  '+name+'\n' for name, data in sorted(files.items())).encode()
        self.write(package/'PACKAGE.SHA256', manifest)
        imported = []
        def load(name):
            imported.append(name)
            return types.SimpleNamespace(__file__=str(package/(name+'.py')))
        with patch.object(q, 'ROOT', self.root), patch.object(q, 'PACKAGE_SHA', sha(manifest)), \
             patch.object(q, 'VERIFIER_SHA', sha(files['verify_runtime_candidate.py'])), \
             patch.object(q.importlib, 'import_module', side_effect=load), \
             patch.object(q.sys, 'path', list(q.sys.path)), redirect_stdout(io.StringIO()):
            self.assertTrue(q.package()[2]['fixture'])
            self.assertEqual(imported, ['verify_runtime_candidate', 'build_guard'])
            imported.clear(); self.write(package/'context.tar', b'corrupted payload')
            with self.assertRaisesRegex(q.ReverifyFailure, 'package_digest:context.tar'): q.package()
            self.assertEqual(imported, [])

    def test_status_reads_without_writes_or_process_calls(self):
        state = self.root/'state'; state.mkdir(mode=0o700)
        self.doc(state/'STARTED.json', {'output': '/opt/fixture', 'automatic_retry': False})
        before = {p: p.read_bytes() for p in state.iterdir()}
        output = io.StringIO()
        with patch.object(q, 'STATE', state), patch.object(q.subprocess, 'run') as called, \
             redirect_stdout(output): q.status()
        called.assert_not_called()
        self.assertEqual(before, {p: p.read_bytes() for p in state.iterdir()})
        self.assertFalse(json.loads(output.getvalue())['files_modified'])

    def make_candidate(self, bad_app=False):
        base = self.build/'runtime-base'
        layers, diffids = [], []
        for i in range(20):
            row, diff = self.layer([member()] if i == 18 else [], base)
            layers.append(row); diffids.append(diff)
        base_config = {'os': 'linux', 'architecture': 'arm64', 'config':
                       {'Env': ['PATH=/usr/local/bin:/usr/bin:/bin'], 'User': '1000:1000',
                        'WorkingDir': '/app', 'Cmd': ['/app/WeKnora']},
                       'rootfs': {'type': 'layers', 'diff_ids': diffids}}
        cfg = self.blob(base, json.dumps(base_config).encode(), OCI+'config.v1+json')
        manifest = self.blob(base, json.dumps({'schemaVersion': 2, 'config': cfg, 'layers': layers}).encode(),
                             OCI+'manifest.v1+json')
        self.doc(self.build/'runtime-base-receipt.json', {'manifest_digest': manifest['digest']})
        candidate = self.root/'fixture-image'
        for row in layers:
            path = Path('blobs/sha256')/row['digest'][7:]
            self.write(candidate/path, (base/path).read_bytes())
        binaries = {n: sha(n.encode()) for n in q.BINARIES}
        dictionaries = {n: sha(n.encode()) for n in q.DICT_NAMES}
        extra, extra_ids = [], []
        for n in ('app/WeKnora', 'usr/local/bin/weknora-anydoc-probe', 'usr/local/bin/weknora-glm-probe'):
            entries = [member(uid=1001 if bad_app else 1000)] if n == 'app/WeKnora' else []
            entries.append(member(n, False, 0, 0, data=n.encode()))
            row, diff = self.layer(entries, candidate); extra.append(row); extra_ids.append(diff)
        row, diff = self.layer([member(q.DICT+n, False, 0, 0, 0o644, n.encode())
                                for n in sorted(q.DICT_NAMES)], candidate)
        extra.append(row); extra_ids.append(diff)
        candidate_config = copy.deepcopy(base_config)
        candidate_config['config']['Env'].append(original.ENV)
        candidate_config['rootfs']['diff_ids'] += extra_ids
        candidate_cfg = self.blob(candidate, json.dumps(candidate_config).encode(), OCI+'config.v1+json')
        candidate_manifest = self.blob(candidate, json.dumps({'schemaVersion': 2, 'config': candidate_cfg,
                                 'layers': layers+extra}).encode(), OCI+'manifest.v1+json')
        image = 'deerflow.local/weknora-app:20261016-native-glm-runtimefix-fc44d25744194ece'
        descriptor = dict(candidate_manifest, annotations={'io.containerd.image.name': image})
        self.doc(candidate/'index.json', {'schemaVersion': 2, 'manifests': [descriptor]})
        self.doc(candidate/'oci-layout', {'imageLayoutVersion': '1.0.0'})
        archive = self.build/'weknora.candidate.oci.tar.partial'
        with tarfile.open(str(archive), 'w') as t:
            for p in sorted(candidate.rglob('*')):
                if p.is_file(): t.add(str(p), arcname=p.relative_to(candidate).as_posix(), recursive=False)
        archive.chmod(0o600)
        self.doc(self.build/'build-metadata.json', {'containerimage.digest': candidate_manifest['digest']})
        proof = self.build/'qualification'
        self.write(proof/'source.sha256', b'locked source manifest')
        self.write(proof/'binaries.sha256', ''.join(h+'  /out/'+n.split('/')[-1]+'\n'
                                                    for n, h in binaries.items()).encode())
        self.write(proof/'sqlite-build-preflight.txt',
            b'sqlite_header_link_and_cgo=PASS builder_only=true database_configuration_changed=false')
        for name in ('vlm-tests.jsonl', 'anydoc-tests.jsonl', 'ssrf-regression.jsonl'):
            rows = [{'Action': 'start', 'Package': 'test-package'},
                    {'Action': 'pass', 'Package': 'test-package', 'Test': 'TestEnterpriseGLM'},
                    {'Action': 'pass', 'Package': 'test-package'}]
            self.write(proof/name, b'\n'.join(json.dumps(x).encode() for x in rows))
        for name in ('anydoc-self-test.json', 'runtime/anydoc.json'):
            self.doc(proof/name, {'available': True, 'self_test': True, 'version': '0.1.9',
                                'markdown_bytes': 10, 'ocr_required': False})
        self.write(proof/'runtime/PASS.txt', b'runtime_loader_and_anydoc=PASS provider_requests=0')
        for name in ('libc-error.txt', 'anydoc-error.txt', 'server-ldd-error.txt'):
            self.write(proof/'runtime'/name, b'')
        self.write(proof/'go-mod-verify.txt', b'all modules verified')
        self.write(proof/'server-elf.txt', b'AArch64')
        plan = {'scope': 'WEKNORA_NATIVE_GLM_BUILD_ONLY',
                'context_sha256': 'fc44d25744194ece953249237305856c1c4e7e13fbed739dadc35fb9bb76ca10',
                'runtime_base': {'manifest_digest': manifest['digest'], 'config_digest': cfg['digest']},
                'runtime_finish': {'binaries': binaries, 'dictionaries': dictionaries,
                                   'source_manifest_sha256': sha(b'locked source manifest')},
                'production_baseline': {'fixture': True}}
        guard = types.SimpleNamespace(live=Mock(), available_memory=lambda: 8*q.GIB,
                                     SpaceGuard=Mock(return_value=Mock()))
        return plan, guard

    def test_complete_existing_oci_qualification_and_old_evidence_unchanged(self):
        plan, guard = self.make_candidate()
        before = {p: sha(p.read_bytes()) for p in self.build.rglob('*') if p.is_file()}
        output = self.root/'new'; output.mkdir(mode=0o700)
        with patch.object(q, 'ROOT', self.root), redirect_stdout(io.StringIO()):
            result = q.qualify(output, original, guard, plan)
        self.assertEqual(result['weknora_glm_candidate_build'], 'PASS')
        self.assertFalse(result['ready_for_rollout'])
        self.assertEqual(result['component']['layers_verified'], 24)
        self.assertEqual(len(result['component']['runtime_files']), 8)
        self.assertEqual(guard.live.call_count, 2)
        self.assertEqual(before, {p: sha(p.read_bytes()) for p in self.build.rglob('*') if p.is_file()})
        self.assertEqual(result['component']['archive_sha256'], sha(Path(result['archive']).read_bytes()))

    def test_complete_oci_bad_app_cannot_be_sealed(self):
        plan, guard = self.make_candidate(bad_app=True)
        output = self.root/'new'; output.mkdir(mode=0o700)
        with patch.object(q, 'ROOT', self.root), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(q.ReverifyFailure, 'app_directory_differs'):
                q.qualify(output, original, guard, plan)
        self.assertFalse((output/'weknora.candidate.oci.tar').exists())


if __name__ == '__main__':
    unittest.main()
