import ast
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import repair_sqlite_build as r

HERE = Path(__file__).resolve().parent


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old = self.root/'old'
        self.old.mkdir()
        self.patch = self.root/'patch'
        self.patch.mkdir()
        self.original_script = b'set -e\nold\n'
        self.rows = {
            'build_native.sh':self.original_script,
            'Dockerfile.weknora':b'unchanged runtime and candidate stages',
            'source/cmd/server/main.go':b'original source',
            'source/SOURCE.SHA256':b'unchanged source manifest',
            'offline/go-proxy/sample.zip':b'original go zip',
            'offline/INPUTS.SHA256':b'0'*64+b'  go-proxy/sample.zip\n',
            'CONTEXT.json':json.dumps({'input_files':1}).encode()}
        with tarfile.open(self.old/'context.tar','w:') as t:
            for name, raw in self.rows.items(): r.add(t,name,raw=raw)
        self.deb = b'locked deb bytes'
        (self.patch/'a+test.deb').write_bytes(self.deb)
        (self.patch/'SQLITE-BUILD-LOCK.json').write_text(json.dumps({'packages':[
            {'filename':'a+test.deb','bytes':len(self.deb),'sha256':hashlib.sha256(self.deb).hexdigest()}]}))
        (self.patch/'build_native.sh').write_bytes(b'new build preflight\n')
        (self.patch/'verify_native_candidate.py').write_bytes(b'new qualification\n')
        (self.patch/'LOCAL-VERIFICATION.json').write_bytes(b'{"local_tool_tests":"PASS"}\n')
        (self.old/'BUILD-PLAN.json').write_text(json.dumps({'context_sha256':r.digest(self.old/'context.tar'),
            'production_baseline':{'generation':68}, 'runtime_base':{'unchanged':True}}))
        (self.old/'builder.oci.tar').write_bytes(b'original builder')
        (self.old/'build_weknora.py').write_bytes(b'original driver')
        (self.old/'verify_native_candidate.py').write_bytes(b'old verification')
        (self.old/'LOCAL-VERIFICATION.json').write_bytes(b'old tests')
        (self.old/'PACKAGE.SHA256').write_text(''.join(r.digest(f)+'  '+f.name+'\n' for f in sorted(self.old.iterdir())))
        self.plan = {'old_context_sha256':r.digest(self.old/'context.tar'),
            'old_build_native_sha256':hashlib.sha256(self.original_script).hexdigest(),
            'old_package_manifest_sha256':r.digest(self.old/'PACKAGE.SHA256')}

    def delta(self):
        return r.context_delta(self.old/'context.tar',self.root/'new.tar',self.patch,self.plan)

    def test_only_declared_context_deltas(self):
        self.delta()
        with tarfile.open(self.root/'new.tar') as t:
            actual = {m.name:t.extractfile(m).read() for m in t}
        expected_changes = {'build_native.sh','offline/INPUTS.SHA256','CONTEXT.json'}
        for name, raw in self.rows.items():
            if name not in expected_changes: self.assertEqual(actual[name],raw)
        self.assertEqual(set(actual)-set(self.rows), {'offline/debian/a+test.deb','offline/debian/SQLITE-BUILD-LOCK.json'})
        self.assertTrue(actual['offline/INPUTS.SHA256'].startswith(self.rows['offline/INPUTS.SHA256']))
        self.assertIn(hashlib.sha256(self.deb).hexdigest().encode()+b'  debian/a+test.deb',actual['offline/INPUTS.SHA256'])
        self.assertEqual(json.loads(actual['CONTEXT.json'])['input_files'],3)

    def test_original_context_hash_required_before_output(self):
        self.plan['old_context_sha256'] = '0'*64
        with self.assertRaisesRegex(r.CheckFailure,'original_context_changed'): self.delta()
        self.assertFalse((self.root/'new.tar').exists())

    def test_old_script_hash_required(self):
        self.plan['old_build_native_sha256'] = '0'*64
        with self.assertRaisesRegex(r.CheckFailure,'original_build_script_changed'): self.delta()

    def test_download_corruption_blocks_delta(self):
        (self.patch/'a+test.deb').write_bytes(b'corrupt')
        with self.assertRaisesRegex(r.CheckFailure,'sqlite_package_hash'): self.delta()

    def test_reuse_rejected(self):
        (self.root/'new.tar').write_bytes(b'keep')
        with self.assertRaisesRegex(r.CheckFailure,'fresh_context_required'): self.delta()
        self.assertEqual((self.root/'new.tar').read_bytes(),b'keep')

    def test_links_traversal_duplicates_rejected(self):
        for case in ('../escape','/escape','x\\escape','x:escape','x\nescape','link','duplicate'):
            with self.subTest(case=case), tarfile.open(self.old/'context.tar','w:') as t:
                m = tarfile.TarInfo(case)
                m.size = 1
                if case == 'link': m.type, m.linkname = tarfile.SYMTYPE, '/outside'
                t.addfile(m,io.BytesIO(b'x'))
                if case == 'duplicate': t.addfile(m,io.BytesIO(b'x'))
            self.plan['old_context_sha256'] = r.digest(self.old/'context.tar')
            output = self.root/('result-'+str(len(list(self.root.iterdir())))+'.tar')
            with self.assertRaisesRegex(r.CheckFailure,'unsafe_context_member'):
                r.context_delta(self.old/'context.tar',output,self.patch,self.plan)

    def test_fresh_package_preserves_original_and_live_bindings(self):
        before = {p.name:p.read_bytes() for p in self.old.iterdir()}
        with patch.object(r,'private'):
            result = r.prepare(self.old,self.patch,self.root/'fresh',self.plan)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.old.iterdir()})
        new = self.root/'fresh/package'
        for name in ('builder.oci.tar','build_weknora.py'):
            self.assertEqual((new/name).read_bytes(),before[name])
        plan = json.loads((new/'BUILD-PLAN.json').read_bytes())
        self.assertEqual(plan['production_baseline'],{'generation':68})
        self.assertEqual(plan['runtime_base'],{'unchanged':True})
        self.assertFalse(result['build_started'])
        self.assertFalse(result['database_changed'])
        self.assertFalse(result['host_system_packages_modified'])

    def test_original_manifest_untrusted_change_fails_closed(self):
        (self.old/'PACKAGE.SHA256').write_bytes(b'changed')
        with patch.object(r,'private'), self.assertRaisesRegex(r.CheckFailure,'original_manifest_changed'):
            r.prepare(self.old,self.patch,self.root/'fresh',self.plan)
        self.assertFalse((self.root/'fresh').exists())

    def test_flat_manifest_allows_debian_version_but_not_paths(self):
        self.assertIn('libsqlite3-dev_3.40.1-2+deb12u2_arm64.deb',r.manifest(
            (('a'*64)+'  libsqlite3-dev_3.40.1-2+deb12u2_arm64.deb\n').encode()))
        for name in ('../x','/x','x/y','x\\y'):
            with self.assertRaises(r.CheckFailure): r.manifest((('a'*64)+'  '+name+'\n').encode())

    def test_early_preflight_is_before_heavy_build_and_never_uses_apt_network(self):
        text = (HERE/'build_native.sh').read_text()
        self.assertLess(text.index('go build -p 2 github.com/asg017/sqlite-vec'),text.index('for component in rustc'))
        self.assertIn('dpkg -i /inputs/debian/',text)
        self.assertNotIn('apt-get',text)
        final = (HERE/'Dockerfile.offline').read_text().split('AS candidate\n',1)[1].split('FROM scratch',1)[0]
        self.assertEqual(final.count('COPY '),3)
        self.assertNotIn('sqlite',final)

    def test_server_python37_syntax(self):
        ast.parse((HERE/'repair_sqlite_build.py').read_text(),feature_version=(3,7))

    @unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0,'Linux root permission check')
    def test_world_writable_rejected(self):
        (self.old/'PACKAGE.SHA256').chmod(0o777)
        with self.assertRaises(r.CheckFailure): r.private(self.old/'PACKAGE.SHA256')


if __name__ == '__main__': unittest.main()
