import ast
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
INFRA = HERE.parents[2]/'release/production-build-alignment-20260928/r4-build'
sys.path.insert(0, str(INFRA))
import build_weknora as b
import stage_context as s
import verify_native_candidate as v
import package_offline as p


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_two_pinned_offline_contexts_and_no_engine_import(self):
        cmd = b.arguments('buildctl', 'unix:///opt/new/socket', Path('/opt/new/context'), '/pptrelayabc.slice',
            {'go-base':(Path('/opt/go'), 'sha256:'+'1'*64), 'runtime-base':(Path('/opt/base'),'sha256:'+'2'*64)},
            output=Path('/opt/new/out.tar'), image='local/candidate:1', metadata=Path('/opt/new/meta.json'))
        self.assertIn('force-network-mode=none', cmd)
        self.assertIn('context:weknora-glm-go-base=oci-layout://go-base@sha256:'+'1'*64, cmd)
        self.assertIn('context:weknora-glm-runtime-base=oci-layout://runtime-base@sha256:'+'2'*64, cmd)
        self.assertEqual(cmd.count('--oci-layout'), 2)
        self.assertNotIn('push=true', ' '.join(cmd))
        self.assertIn('type=oci,', ' '.join(cmd))

    def test_evidence_export_does_not_tag_image(self):
        cmd = b.arguments('buildctl', 'unix:///x', Path('/opt/c'), '/pptrelayabc.slice', {},
                          target='qualification-evidence', output=PurePosixPath('/opt/proof'))
        self.assertIn('type=local,dest=/opt/proof', cmd)
        self.assertNotIn('--metadata-file', cmd)

    def archive(self, rows, path=None, mode='w:'):
        path = path or self.root/'context.tar'
        with tarfile.open(path, mode) as t:
            for name, kind, body in rows:
                m = tarfile.TarInfo(name)
                m.type, m.mode = kind, 0o755
                m.linkname = 'outside' if kind == tarfile.SYMTYPE else ''
                m.size = len(body) if kind == tarfile.REGTYPE else 0
                t.addfile(m, io.BytesIO(body) if m.size else None)
        return path

    def test_context_link_and_traversal_fail(self):
        for i, (name, kind) in enumerate([('../x',tarfile.REGTYPE), ('x',tarfile.SYMTYPE), ('/x',tarfile.REGTYPE)]):
            arc = self.archive([(name,kind,b'x')])
            with patch.object(s,'safe_file'), self.assertRaisesRegex(RuntimeError,'unsafe_context'):
                s.unpack(arc,self.root/str(i),s.checksum(arc))

    def test_context_hash_and_reuse_blocked(self):
        arc = self.archive([('source/x',tarfile.REGTYPE,b'x')])
        out = self.root/'out'
        with patch.object(s,'safe_file'):
            with self.assertRaisesRegex(RuntimeError,'digest'):
                s.unpack(arc,out,'0'*64)
            self.assertFalse(out.exists())
            self.assertEqual(s.unpack(arc,out,s.checksum(arc))['files'],1)
            with self.assertRaises(ValueError):
                s.unpack(arc,out,s.checksum(arc))

    def test_binary_delta_hash_mode_and_paths(self):
        expected = {'app/WeKnora':hashlib.sha256(b'binary').hexdigest()}
        blob = self.archive([('app/WeKnora',tarfile.REGTYPE,b'binary')], self.root/'blob.gz','w:gz')
        seen = set()
        v.delta(blob, {'mediaType':'application/vnd.oci.image.layer.v1.tar+gzip'},expected,seen)
        self.assertEqual(seen,set(expected))
        with self.assertRaisesRegex(RuntimeError,'unexpected_binary'):
            v.delta(blob, {'mediaType':'tar+gzip'},expected,seen)
        with self.assertRaisesRegex(RuntimeError,'binary_delta_hash'):
            v.delta(blob, {'mediaType':'tar+gzip'}, {'app/WeKnora':'0'*64},set())

    def test_no_candidate_config_or_provider_mutation(self):
        docker = (HERE/'Dockerfile.offline').read_text()
        final = docker.split('AS candidate\n',1)[1].split('FROM scratch',1)[0]
        self.assertNotIn('LABEL', final)
        self.assertNotIn('ENV', final)
        self.assertNotIn('--probe-once', docker)
        self.assertEqual(final.count('COPY '),3)
        for line in docker.splitlines():
            if line.startswith('RUN '):
                self.assertIn('--network=none',line)
        guard = (HERE/'build_guard.py').read_text()
        self.assertNotIn("['patch'",guard)
        self.assertNotIn("['scale'",guard)

    def test_python37_server_syntax(self):
        for name in (*p.TOOLS, *p.FROZEN):
            if name.endswith('.py'):
                source = HERE/name if name in p.TOOLS else INFRA/name
                ast.parse(source.read_text(encoding='utf-8'), feature_version=(3,7))

    def test_generated_tar_uses_restricted_permissions(self):
        path = self.root/'package.tar'
        with tarfile.open(path,'w:') as t:
            p.add(t,'safe.py',raw=b'pass\n')
        with tarfile.open(path) as t:
            m = t.getmembers()[0]
            self.assertEqual((m.mode,m.uid,m.gid),(0o600,0,0))
        with tarfile.open(self.root/'bad.tar','w:') as t:
            with self.assertRaises(ValueError):
                p.add(t,'../escape',raw=b'x')

    def test_exact_baseline_not_slot_expansion(self):
        self.assertEqual(p.BASELINE['generation'],68)
        self.assertEqual(p.BASELINE['pod'],'weknora-app-f6fb87c6d-gqtf5')
        self.assertEqual(p.BASELINE['restarts'],0)
        self.assertEqual(set(p.BASELINE), {'deployment_uid','generation','spec_sha256','pod','pod_uid',
                         'container_id','image','image_id','node','restarts'})


if __name__ == '__main__':
    unittest.main()
