import ast
import contextlib
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import export_weknora_base as e
import verify_weknora_base as v


def raw(obj): return json.dumps(obj, separators=(',', ':')).encode()
def digest(data): return 'sha256:'+hashlib.sha256(data).hexdigest()


def fixture(path, case=None):
    data = b'fixture layer: never extracted or executed'
    layer = gzip.compress(data)
    ld = {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip',
          'digest': digest(layer), 'size': len(layer)}
    cfg = {'os': 'linux', 'architecture': 'arm64', 'config': {'Env': ['SECRET=not-for-output']},
           'rootfs': {'type': 'layers', 'diff_ids': [digest(data)]}}
    if case == 'platform': cfg['architecture'] = 'amd64'
    if case == 'diffid': cfg['rootfs']['diff_ids'] = ['sha256:'+'e'*64]
    if case == 'urls': ld['urls'] = ['https://not-approved.invalid']
    if case == 'data': ld['data'] = 'embedded'
    config = raw(cfg)
    cd = {'digest': digest(config), 'size': len(config)}
    if case == 'config_urls': cd['urls'] = ['https://not-approved.invalid']
    manifest = raw({'schemaVersion': 2, 'mediaType': v.MANIFESTS[0], 'config': cd, 'layers': [ld]})
    md = {'digest': digest(manifest), 'size': len(manifest), 'mediaType': v.MANIFESTS[0]}
    blobs = {'blobs/sha256/'+digest(config)[7:]: config,
             'blobs/sha256/'+digest(manifest)[7:]: manifest,
             'blobs/sha256/'+digest(layer)[7:]: layer}
    root = md
    if case in ('index', 'duplicate_arm64', 'wrong_index'):
        md = dict(md, platform={'os': 'linux', 'architecture': 'arm64'})
        items = [md, md] if case == 'duplicate_arm64' else [md]
        index = raw({'schemaVersion': 2, 'manifests': items})
        root = {'digest': digest(index), 'size': len(index), 'mediaType': v.INDEXES[0]}
        blobs['blobs/sha256/'+digest(index)[7:]] = index
    idx = raw({'schemaVersion': 2, 'manifests': [root]})
    files = dict(blobs, **{'oci-layout': raw({'imageLayoutVersion': '1.0.0'}), 'index.json': idx})
    if case == 'corrupt': files['blobs/sha256/'+digest(layer)[7:]] = b'x'+layer[1:]
    if case == 'unreferenced': files['blobs/sha256/'+'f'*64] = b'extra'
    if case == 'traversal': files['../escape'] = b'no'
    with tarfile.open(str(path), 'w') as out:
        for name, content in files.items():
            info = tarfile.TarInfo(name); info.size = len(content)
            if case == 'link' and name == 'oci-layout':
                info.type = tarfile.SYMTYPE; info.linkname = '/etc/shadow'; info.size = 0
                out.addfile(info)
            else: out.addfile(info, io.BytesIO(content))
            if case == 'duplicate' and name == 'index.json': out.addfile(info, io.BytesIO(content))
    params = dict(manifest_digest=digest(manifest), config_digest=digest(config),
                  expected_layers=1, expected_bytes=len(layer), root_digest=root['digest'])
    if case == 'wrong_index': params['root_digest'] = 'sha256:'+'a'*64
    return params, len(data), blobs, root


class ArchiveTests(unittest.TestCase):
    def test_pass_single_manifest_and_index_no_secret_or_extraction(self):
        for case in (None, 'index'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'base.tar'; args, size, _, _ = fixture(path, case)
                events = []
                result = v.verify(path, events.append, **args)
                self.assertEqual(result['uncompressed_layer_bytes'], size)
                self.assertEqual(result['uncompressed_diffids'], 'PASS')
                self.assertFalse(result['image_code_executed'])
                self.assertNotIn('SECRET', json.dumps([events, result]))
                self.assertEqual([p.name for p in Path(tmp).iterdir()], ['base.tar'])

    def test_reject_invalid_archives(self):
        for case in ('platform', 'diffid', 'urls', 'data', 'config_urls', 'corrupt',
                     'unreferenced', 'traversal', 'link', 'duplicate', 'duplicate_arm64', 'wrong_index'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'base.tar'; args, _, _, _ = fixture(path, case)
                with self.assertRaises(v.CheckFailure): v.verify(path, lambda x: None, **args)

    def test_duplicate_json_and_stream_bound(self):
        with self.assertRaises(v.CheckFailure):
            json.loads('{"a":1,"a":2}', object_pairs_hook=v.unique)
        with self.assertRaises(v.CheckFailure):
            v.hashed(io.BytesIO(b'123456'), 5, lambda x: None, 'test', rate=0)


class PipeTests(unittest.TestCase):
    def run_child(self, code, **kw):
        chunks = []
        size = e.pipe_command([sys.executable, '-I', '-B', '-c', code], chunks.append,
                              kw.get('maximum', 1048576), kw.get('timeout', 5), rate=0)
        return size, b''.join(chunks)

    def test_real_subprocess_success_and_stderr_redaction(self):
        size, data = self.run_child('import sys;sys.stderr.write("SECRET");sys.stdout.buffer.write(b"a"*100000)')
        self.assertEqual(size, 100000); self.assertEqual(data, b'a'*100000)

    def test_nonzero_is_not_success_or_retry(self):
        with self.assertRaisesRegex(v.CheckFailure, 'local_command_failed_no_retry'):
            self.run_child('import sys;sys.stdout.write("partial");sys.exit(1)')

    def test_oversize_terminates_client(self):
        with self.assertRaisesRegex(v.CheckFailure, 'local_output_size_bound'):
            self.run_child('import sys;sys.stdout.buffer.write(b"x"*1000000)', maximum=1024)

    def test_deadline_terminates_only_started_client(self):
        start = time.monotonic()
        with self.assertRaisesRegex(v.CheckFailure, 'local_command_deadline'):
            self.run_child('import time;time.sleep(20)', timeout=.1)
        self.assertLess(time.monotonic()-start, 5)


class IdentityTests(unittest.TestCase):
    def container(self):
        return {'id': e.CONTAINER, 'state': 'CONTAINER_RUNNING', 'imageRef': e.CONFIG,
                'metadata': {'name': 'app', 'attempt': 0}, 'labels': {
                    'io.kubernetes.pod.name': e.POD, 'io.kubernetes.pod.uid': e.POD_UID,
                    'io.kubernetes.pod.namespace': 'deer-flow'}}

    def test_container_identity_supported_cri_shapes(self):
        identity = {'manifest_digest': 'sha256:'+'a'*64, 'root_digest': 'sha256:'+'b'*64}
        for ref in (e.CONFIG, identity['manifest_digest'], 'deerflow.local/weknora-app@'+identity['root_digest']):
            obj = self.container(); obj['imageRef'] = ref
            e.bind_container(obj, identity)

    def test_reject_different_or_stopped_container(self):
        identity = {'manifest_digest': 'sha256:'+'a'*64, 'root_digest': 'sha256:'+'b'*64}
        for key, value in (('id', 'changed'), ('state', 'CONTAINER_EXITED'), ('imageRef', 'sha256:'+'f'*64),
                           ('metadata', {'name': 'app', 'attempt': 1}), ('labels', {})):
            with self.subTest(key=key):
                obj = self.container(); obj[key] = value
                with self.assertRaises(v.CheckFailure): e.bind_container(obj, identity)

    def test_exact_local_image_config_and_redaction(self):
        for case in (None, 'index'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                params, _, blobs, root = fixture(Path(tmp)/'base.tar', case)
                def capture(argv):
                    if argv == e.CTR+['images', 'ls']:
                        return (e.IMAGE+' '+root['mediaType']+' '+root['digest']+'\n').encode()
                    if argv[:len(e.CTR)+2] == e.CTR+['content', 'get']:
                        return blobs['blobs/sha256/'+argv[-1][7:]]
                    if argv == e.CRI+['inspecti', e.IMAGE]:
                        return raw({'status': {'id': params['config_digest'], 'repoTags': [e.IMAGE]}})
                    if argv == e.CRI+['inspect', e.CONTAINER]: return raw({'status': self.container()})
                    raise AssertionError('unexpected command')
                with patch.object(e, 'CONFIG', params['config_digest']), patch.object(e, 'capture', side_effect=capture):
                    result = e.current_identity()
                self.assertEqual(result['manifest_digest'], params['manifest_digest'])
                self.assertNotIn('SECRET', json.dumps(result))

    def test_metadata_hash_drift(self):
        with patch.object(e, 'capture', return_value=b'{}'):
            with self.assertRaisesRegex(v.CheckFailure, 'local_metadata_hash_mismatch'):
                e.read_json({'digest': 'sha256:'+'f'*64})

    def test_unsafe_descriptors(self):
        base = {'digest': 'sha256:'+'a'*64, 'size': 1}
        for k, value in (('urls', ['https://unapproved.invalid']), ('data', 'x'), ('size', True), ('size', 0)):
            with self.subTest(key=k):
                with self.assertRaises(v.CheckFailure): e.descriptor(dict(base, **{k: value}))

    def test_command_allowlist_and_export(self):
        with patch.object(e, 'pipe_command') as command:
            for verb in ('pull', 'import', 'tag', 'delete', 'prune'):
                with self.assertRaises(v.CheckFailure): e.capture(e.CTR+['images', verb, e.IMAGE])
            with self.assertRaises(v.CheckFailure): e.capture(['kubectl', 'patch'])
            with self.assertRaises(v.CheckFailure): e.capture(e.CRI+['exec', e.CONTAINER, 'sh'])
            command.assert_not_called()
        self.assertEqual(e.export_command()[-2:], ['-', e.IMAGE])
        self.assertIn('--local', e.export_command())


class ControllerTests(unittest.TestCase):
    def test_space_budget_reserves_twenty_percent_and_remaining_only(self):
        fs = types.SimpleNamespace(f_blocks=300, f_frsize=e.GIB, f_bavail=63, f_favail=100000, f_flag=0)
        self.assertEqual(e.space_budget(fs, 3*e.GIB)['reserve_bytes'], 60*e.GIB)
        with self.assertRaises(v.CheckFailure): e.space_budget(fs, 4*e.GIB)
        fs.f_bavail = 60
        e.space_budget(fs, 0)
        fs.f_favail = 5
        with self.assertRaises(v.CheckFailure): e.space_budget(fs, 0)

    def test_wrong_host_does_not_invoke_command(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(e, 'current_identity') as identity, \
             patch.object(e.socket, 'gethostname', return_value='datax-01'):
            self.assertEqual(e.main(['--export-once']), 1)
            identity.assert_not_called()
        self.assertIn('requires_root_linux_arm64_datax03', output.getvalue())

    def test_existing_operation_rejected_before_export(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(e, 'ROOT', Path(tmp)), \
             patch.object(e, 'current_identity') as identity:
            with self.assertRaisesRegex(v.CheckFailure, 'operation_exists_use_status_no_repeat'): e.do_export()
            identity.assert_not_called()

    def test_full_controller_offline_archive_and_receipt(self):
        # Exercise the real exporter/verifier with only host metadata and the
        # containerd byte source replaced. No live services or Linux claims.
        with tempfile.TemporaryDirectory() as tmp:
            root, archive = Path(tmp)/'operation', Path(tmp)/'fixture.tar'
            params, _, _, _ = fixture(archive, 'index')
            source = archive.read_bytes()
            identity = {'manifest_digest': params['manifest_digest'], 'root_digest': params['root_digest'],
                        'layers': 1, 'compressed_layer_bytes': params['expected_bytes']}
            saved = {}
            def snapshot(path):
                s = path.stat()
                return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            def write(name, value): saved[name] = value
            def read(name): return raw(saved[name]), saved[name]
            def stream(argv, consume, maximum, timeout, progress, rate):
                consume(source); return len(source)
            with patch.object(e, 'ROOT', root), patch.object(e, 'CONFIG', params['config_digest']), \
                 patch.object(e, 'current_identity', return_value=identity), \
                 patch.object(e, 'check_space', return_value={'reserve': 'fixture'}), \
                 patch.object(e, 'safe_dir'), patch.object(e, 'private_meta', side_effect=snapshot), \
                 patch.object(e, 'write_new', side_effect=write), patch.object(e, 'read_receipt', side_effect=read), \
                 patch.object(e, 'capture', return_value=b'--local --skip-manifest-json'), \
                 patch.object(e.os, 'O_NOFOLLOW', 0, create=True), \
                 patch.object(e.os, 'fchmod', create=True), \
                 patch.object(e, 'pipe_command', side_effect=stream), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(e.do_export(), 0)
            self.assertEqual((root/'weknora.base.oci.tar').read_bytes(), source)
            result = saved['BASE-EXPORT.json']
            self.assertEqual(result['archive_sha256'], hashlib.sha256(source).hexdigest())
            self.assertEqual(result['weknora_base_export'], 'PASS')
            self.assertFalse(result['build_started'])
            self.assertFalse(result['ready_for_rollout'])

    def test_unsafe_owner_permissions_or_symlink_are_rejected(self):
        base = dict(st_mode=0o100600, st_uid=0, st_nlink=1, st_dev=1, st_ino=2,
                    st_size=10, st_mtime_ns=3, st_ctime_ns=4)
        for changes in ({'st_uid': 1000}, {'st_mode': 0o100666}, {'st_mode': 0o120777}, {'st_nlink': 2}):
            with self.subTest(changes=changes), patch.object(e.Path, 'lstat',
                    return_value=types.SimpleNamespace(**dict(base, **changes))):
                with self.assertRaises(v.CheckFailure): e.private_meta(Path('/private/file'))

    def test_python37_grammar(self):
        for module in (e, v):
            ast.parse(Path(module.__file__).read_text(encoding='utf-8'), feature_version=(3, 7))

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                         'requires actual Linux root filesystem semantics')
    def test_private_permissions_and_symlink_rejection_linux(self):
        # The production CRI/ctr is never invoked by this test.
        with tempfile.TemporaryDirectory(prefix='weknora-export-test-', dir='/var/lib') as tmp:
            with patch.object(e, 'ROOT', Path(tmp)):
                e.write_new('record.json', {'test': True})
                _, record = e.read_receipt('record.json')
                self.assertTrue(record['test'])
                os.symlink(str(Path(tmp)/'record.json'), str(Path(tmp)/'link.json'))
                with self.assertRaises(v.CheckFailure): e.read_receipt('link.json')


if __name__ == '__main__': unittest.main()
