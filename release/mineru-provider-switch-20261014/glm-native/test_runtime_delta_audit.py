import ast
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import audit_runtime_delta as a


class ClassificationTests(unittest.TestCase):
    def test_exact_failure_reasons(self):
        m = tarfile.TarInfo('app'); m.type = tarfile.DIRTYPE; m.mode = 0o755
        self.assertEqual(a.metadata(m, set())[1]['failed_conditions'], [])
        m.uid = 1000
        self.assertEqual(a.metadata(m, set())[1]['failed_conditions'], ['nonroot_owner_or_group'])
        m.uid = 0; m.mode = 0o2775
        self.assertIn('mode_has_setid_or_group_world_write', a.metadata(m, set())[1]['failed_conditions'])

    def test_duplicate_and_link_are_not_hidden(self):
        m = tarfile.TarInfo('app'); m.type = tarfile.SYMTYPE; m.linkname = '/secret'; m.mode = 0o755
        value = a.metadata(m, {'app'})[1]
        self.assertIn('duplicate_path_in_layer', value['failed_conditions'])
        self.assertIn('link_target_present', value['failed_conditions'])
        self.assertNotIn('/secret', json.dumps(value))

    def test_unexpected_names_are_redacted(self):
        m = tarfile.TarInfo('private/config-secret'); m.mode = 0o644
        value = a.metadata(m, set())[1]
        self.assertEqual(value['name'], 'UNEXPECTED_PATH_REDACTED')
        self.assertNotIn('config-secret', json.dumps(value))

    def test_python37_and_read_only_transport(self):
        source = Path(a.__file__).read_text()
        ast.parse(source, feature_version=(3, 7))
        for text in ('subprocess', 'extractall', '.unlink(', 'chmod(', 'kubectl', 'importlib'):
            self.assertNotIn(text, source)


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Linux private-path fixture')
class AuditFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/root'); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root/'package'; self.package.mkdir(mode=0o700)
        self.build = self.root/'build1'; self.build.mkdir(mode=0o700)

    def write(self, path, raw):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(raw); path.chmod(0o600)

    def document(self, path, value):
        self.write(path, json.dumps(value).encode())

    def blob(self, layout, raw, kind):
        digest = hashlib.sha256(raw).hexdigest()
        self.write(layout/'blobs/sha256'/digest, raw)
        return {'mediaType': kind, 'digest': 'sha256:'+digest, 'size': len(raw)}

    def layer(self, layout, entries):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as t:
            for m in entries: t.addfile(m, io.BytesIO(b'x'*m.size))
        return self.blob(layout, gzip.compress(raw.getvalue()), 'application/vnd.oci.image.layer.v1.tar+gzip')

    def fixture(self):
        base, candidate = self.build/'runtime-base', self.build/'candidate-check'
        empty = self.layer(base, [])
        m = tarfile.TarInfo('app'); m.type = tarfile.DIRTYPE; m.uid = 1000; m.mode = 0o755
        inherited = self.layer(base, [m])
        layers = [empty]*19+[inherited]
        before = self.blob(base, json.dumps({'layers': layers}).encode(), 'application/vnd.oci.image.manifest.v1+json')
        self.document(self.build/'runtime-base-receipt.json', {'manifest_digest': before['digest']})
        extra = []
        for name in ('app/WeKnora', 'usr/local/bin/weknora-anydoc-probe',
                     'usr/local/bin/weknora-glm-probe', 'usr/local/share/weknora/jieba/idf.utf8'):
            f = tarfile.TarInfo(name); f.mode = 0o755; f.size = 1
            extra.append(self.layer(candidate, [m, f] if name == 'app/WeKnora' else [f]))
        after = self.blob(candidate, json.dumps({'layers': layers+extra}).encode(), 'application/vnd.oci.image.manifest.v1+json')
        self.document(candidate/'index.json', {'manifests': [after]})
        self.document(self.build/'build-metadata.json', {'containerimage.digest': after['digest']})
        self.document(self.build/'BUILD-FAILED.json', {'stage': 'verify-runtime-candidate',
                      'images_imported': False, 'production_writes': 0})
        self.write(self.package/'PACKAGE.SHA256', b'fixture-manifest')
        self.write(self.package/'verify_runtime_candidate.py', b'not executed')
        return (hashlib.sha256(b'fixture-manifest').hexdigest(), hashlib.sha256(b'not executed').hexdigest())

    def test_complete_read_only_delta_and_base_comparison(self):
        manifest, verifier = self.fixture()
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        output = io.StringIO()
        with patch.object(a, 'ROOT', self.root), patch.object(a, 'PACKAGE_SHA', manifest), \
             patch.object(a, 'VERIFIER_SHA', verifier), redirect_stdout(output): a.audit()
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        rows = [json.loads(x) for x in output.getvalue().splitlines()]
        self.assertEqual(rows[-1]['delta_audit'], 'COMPLETE')
        inherited = [x for x in rows if 'base_parent_metadata' in x]
        self.assertEqual(inherited[0]['base_parent_metadata']['uid'], 1000)
        self.assertEqual(rows[-1]['members_with_failed_conditions'], 1)

    def test_corrupt_blob_rejected(self):
        layout = self.root/'layout'
        d = self.blob(layout, b'original', 'test')
        self.write(layout/'blobs/sha256'/d['digest'][7:], b'modified')
        with self.assertRaisesRegex(RuntimeError, 'digest_or_size_changed'): a.blob(layout, d)

    def test_symlink_rejected(self):
        self.write(self.root/'target', b'read')
        (self.root/'alias').symlink_to(self.root/'target')
        with self.assertRaises(OSError): a.read(self.root/'alias')

    def test_writable_ancestor_rejected(self):
        self.write(self.root/'x', b'read'); self.root.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, 'unsafe_private_ancestor'): a.read(self.root/'x')


if __name__ == '__main__':
    unittest.main()
