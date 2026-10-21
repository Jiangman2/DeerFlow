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
from unittest.mock import patch

import audit_enrichment_runtime as a


def sha(raw): return hashlib.sha256(raw).hexdigest()


def tar_bytes(rows):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as t:
        for name,value in rows:
            m=tarfile.TarInfo(name)
            if isinstance(value,tuple): m.type=tarfile.SYMTYPE; m.linkname=value[0]; t.addfile(m)
            elif value is None: m.type=tarfile.DIRTYPE; t.addfile(m)
            else: m.size=len(value); t.addfile(m,io.BytesIO(value))
    return out.getvalue()


class OverlayTests(unittest.TestCase):
    def test_layer_overwrite_and_opaque_whiteouts(self):
        effective={}
        a.layer_assets(io.BytesIO(tar_bytes([('app/config/a.yaml',b'old'),('app/config/gone',b'x'),
                                          ('app/migrations/versioned/1.up.sql',b'sql')])),effective)
        a.layer_assets(io.BytesIO(tar_bytes([('app/config/a.yaml',b'new'),('app/config/.wh..wh..opq',b'')])),effective)
        self.assertEqual(effective['app/config/a.yaml']['sha256'],sha(b'new'))
        self.assertNotIn('app/config/gone',effective)
        self.assertIn('app/migrations/versioned/1.up.sql',effective)

    def test_ancestor_whiteout_and_symlink_are_not_followed(self):
        effective={}
        a.layer_assets(io.BytesIO(tar_bytes([('app/config/a',b'x')])),effective)
        a.layer_assets(io.BytesIO(tar_bytes([('.wh.app',b'')])),effective)
        self.assertEqual(effective,{})
        a.layer_assets(io.BytesIO(tar_bytes([('app',('/private/source',)),('app/config/a',b'x')])),effective)
        report=a.comparison({'app/config/a':sha(b'x')},effective)
        self.assertEqual(report['different'],['app/config/a'])

    def test_duplicates_and_unsafe_paths_rejected(self):
        for rows in ([('app/config/a',b'1'),('app/config/a',b'2')],[('../app/config/a',b'x')],
                     [('/app/config/a',b'x')],[('app\\config\\a',b'x')]):
            with self.subTest(rows=rows),self.assertRaises(a.AuditFailure): a.layer_assets(io.BytesIO(tar_bytes(rows)),{})

    def test_file_replacing_directory_hides_lower_children(self):
        effective={}
        a.layer_assets(io.BytesIO(tar_bytes([('app/config/a',b'x')])),effective)
        a.layer_assets(io.BytesIO(tar_bytes([('app/config',b'not-directory')])),effective)
        self.assertNotIn('app/config/a',effective)

    def test_unrelated_image_files_are_not_hashed_or_returned(self):
        effective={}
        a.layer_assets(io.BytesIO(tar_bytes([('private.env',b'SECRET_VALUE'),('app/config/a',b'body')])),effective)
        self.assertEqual(list(effective),['app/config/a'])
        self.assertNotIn('body',json.dumps(effective)); self.assertNotIn('SECRET_VALUE',json.dumps(effective))

    def test_comparison_reports_each_group_without_false_pass(self):
        actual={'app/config/changed':dict(kind='file',sha256=sha(b'other')),
                'app/migrations/versioned/2.up.sql':dict(kind='file',sha256=sha(b'new'))}
        r=a.comparison({'app/config/changed':sha(b'original'),'app/migrations/versioned/1.up.sql':sha(b'old')},actual)
        self.assertFalse(r['all_runtime_assets_match'])
        self.assertFalse(r['groups']['postgres_migrations']['matches_compiled_source'])
        self.assertEqual(r['missing'],['app/migrations/versioned/1.up.sql'])

    def test_bounded_decompression(self):
        with io.BufferedReader(a.Bounded(io.BytesIO(b'123456789'),4)) as stream:
            with self.assertRaisesRegex(a.AuditFailure,'expanded_layer_bound'): stream.read()

    def test_checksums_reject_duplicates_and_unsafe_names(self):
        for raw in ((sha(b'x')+'  ../escape\n').encode(),(sha(b'x')+'  a\n') .encode()*2,b'bad'):
            with self.assertRaises(a.AuditFailure): a.checksums(raw)
        ast.parse(Path(a.__file__).read_text(),feature_version=(3,7))

    def test_unicode_source_names_and_bounded_console_lists(self):
        name='testdata/扫描件 (1).txt'
        self.assertEqual(a.checksums((sha(b'x')+'  '+name+'\n').encode()),{name:sha(b'x')})
        missing=['app/config/%s.yaml'%i for i in range(100)]
        assets=a.comparison({k:sha(b'x') for k in missing},{})
        result={'runtime_assets':assets,'images':[],'production_writes':0}
        compact=a.compact(result)
        group=compact['runtime_assets']['groups']['config_assets']
        self.assertEqual(group['missing_count'],100); self.assertEqual(len(group['missing_first20']),20)
        self.assertEqual(len(result['runtime_assets']['missing']),100)


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Linux root private files')
class PrivateAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/root'); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)

    def write(self,path,raw):
        path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw); path.chmod(0o600)

    def test_source_manifest_bound_to_prior_package_not_live_source_tree(self):
        manifest=(sha(b'init')+'  migrations/versioned/000001.up.sql\n'+sha(b'program')+'  cmd/server/main.go\n').encode()
        plan=json.dumps({'runtime_finish':{'compile_reused':True,'source_manifest_sha256':sha(manifest)}}).encode()
        package=(sha(plan)+'  BUILD-PLAN.json\n').encode()
        self.write(self.root/'package/PACKAGE.SHA256',package); self.write(self.root/'package/BUILD-PLAN.json',plan)
        self.write(self.root/'build1/qualification/source.sha256',manifest)
        with patch.object(a,'COMPILED',self.root),patch.object(a,'PACKAGE_SHA',sha(package)):
            rows,digest=a.source_assets()
            self.assertEqual(rows,{'app/migrations/versioned/000001.up.sql':sha(b'init')})
            self.assertEqual(digest,sha(manifest))
            self.write(self.root/'build1/qualification/source.sha256',b'tampered')
            with self.assertRaisesRegex(a.AuditFailure,'pinned_file_hash_changed'): a.source_assets()

    def test_private_file_symlink_and_writable_permissions_rejected(self):
        self.write(self.root/'target',b'x'); (self.root/'link').symlink_to(self.root/'target')
        with self.assertRaises(OSError): a.read(self.root/'link')
        (self.root/'target').chmod(0o666)
        with self.assertRaises(a.AuditFailure): a.read(self.root/'target')

    def test_complete_archive_and_changed_bytes_fail_before_code_execution(self):
        payload=gzip.compress(tar_bytes([('app/config/item',b'INTERNAL_CONFIG'),('app/migrations/versioned/000001.up.sql',b'CREATE TABLE x();')]))
        cfg=json.dumps({'os':'linux','architecture':'arm64','config':{'Env':['PRIVATE_TOKEN=not_exposed']}}).encode()
        desc=lambda raw:dict(digest='sha256:'+sha(raw),size=len(raw))
        manifest=json.dumps({'schemaVersion':2,'config':desc(cfg),'layers':[dict(desc(payload),mediaType='application/vnd.oci.image.layer.v1.tar+gzip')]}).encode()
        raw=tar_bytes([('oci-layout',b'{"imageLayoutVersion":"1.0.0"}'),('index.json',b'{}'),
                       ('blobs/sha256/'+sha(cfg),cfg),('blobs/sha256/'+sha(manifest),manifest),('blobs/sha256/'+sha(payload),payload)])
        path=self.root/'image.tar'; self.write(path,raw)
        with patch.object(a,'emit'),patch.object(a.subprocess,'run',side_effect=AssertionError('no process allowed')):
            actual=a.inspect_archive(path,sha(raw),len(raw),'sha256:'+sha(manifest),'sha256:'+sha(cfg))
            self.assertEqual(actual['app/config/item']['sha256'],sha(b'INTERNAL_CONFIG'))
            self.assertNotIn('PRIVATE_TOKEN',json.dumps(actual)); self.assertNotIn('INTERNAL_CONFIG',json.dumps(actual))
            self.write(path,b'x'+raw[1:])
            with self.assertRaisesRegex(a.AuditFailure,'candidate_archive_hash_changed'):
                a.inspect_archive(path,sha(raw),len(raw),'sha256:'+sha(manifest),'sha256:'+sha(cfg))


if __name__=='__main__': unittest.main()
