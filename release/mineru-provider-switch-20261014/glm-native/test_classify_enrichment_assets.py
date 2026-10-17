import ast
import gzip
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import audit_enrichment_runtime as a
import classify_enrichment_assets as c
from test_audit_enrichment_runtime import tar_bytes, sha


NAME = 'app/migrations/versioned/000001.up.sql'


def actual(raw, name=NAME):
    view={}
    a.layer_assets(io.BytesIO(tar_bytes([(name,raw)])),view,c.newline_hashes)
    return view


class ClassificationTests(unittest.TestCase):
    def check(self, candidate, source, classification):
        report=c.classify({NAME:sha(source)},actual(candidate))
        self.assertEqual(report['files'][0]['classification'],classification)
        self.assertFalse(report['semantic_equivalence_proven'])
        return report

    def test_exact_including_empty_and_crlf(self):
        for value in (b'',b'select 1;',b'select 1;\r\n',b'\xff'):
            with self.subTest(value=value): self.check(value,value,'EXACT')

    def test_lf_candidate_crlf_source(self):
        r=self.check(b'-- a\nselect 1;\n',b'-- a\r\nselect 1;\r\n','LINE_ENDINGS_ONLY')
        self.assertEqual(r['files'][0]['candidate_to_source_transform'],'CANONICAL_CRLF')

    def test_crlf_candidate_lf_source(self):
        r=self.check(b'-- a\r\nselect 1;\r\n',b'-- a\nselect 1;\n','LINE_ENDINGS_ONLY')
        self.assertEqual(r['files'][0]['candidate_to_source_transform'],'CRLF_TO_LF')

    def test_mixed_candidate_preserves_standalone_cr(self):
        self.check(b'a\r\nb\nc\rd',b'a\nb\nc\rd','LINE_ENDINGS_ONLY')
        self.check(b'a\r\nb\nc\rd',b'a\r\nb\r\nc\rd','LINE_ENDINGS_ONLY')
        self.check(b'a\rb\n',b'a\nb\n','UNCLASSIFIED_BYTE_DIFFERENCE')

    def test_mixed_source_is_not_guessed(self):
        self.check(b'a\nb\n',b'a\r\nb\n','UNCLASSIFIED_BYTE_DIFFERENCE')

    def test_never_ignore_bom_spaces_comments_or_final_newline(self):
        for source in (b'\xef\xbb\xbfselect 1;\n',b'select  1;\n',b'select 1;',
                       b'-- different\nselect 1;\n',b'select 2;\n',b'select 1;\n\n'):
            with self.subTest(source=source): self.check(b'select 1;\n',source,'UNCLASSIFIED_BYTE_DIFFERENCE')
        self.check(b'\xef\xbb\xbfselect 1;\n',b'\xef\xbb\xbfselect 1;\r\n','LINE_ENDINGS_ONLY')

    def test_binary_not_normalized(self):
        for raw in (b'\xff\n',b'a\x00\n'):
            self.check(raw,raw.replace(b'\n',b'\r\n'),'UNCLASSIFIED_BYTE_DIFFERENCE')

    def test_no_config_values_retained(self):
        row=c.newline_hashes(b'PASSWORD=PRIVATE_TEST_VALUE\n')
        self.assertNotIn('PASSWORD',json.dumps(row)); self.assertNotIn('PRIVATE_TEST_VALUE',json.dumps(row))

    def test_missing_link_and_file_parent_rejected(self):
        r=c.classify({NAME:sha(b'x')},{})
        self.assertEqual(r['files'][0]['classification'],'MISSING')
        for kind in ('link','file'):
            view=actual(b'x'); view['app']=dict(kind=kind)
            self.assertEqual(c.classify({NAME:sha(b'x')},view)['files'][0]['classification'],'NONREGULAR_PATH')
        view={NAME:dict(kind='link')}
        self.assertEqual(c.classify({NAME:sha(b'x')},view)['files'][0]['classification'],'NONREGULAR_PATH')

    def test_operator_config_separate_from_unexpected_extras(self):
        view=actual(b'sql'); view.update(actual(b'PRIVATE_TEST_VALUE','app/config/config.yaml'))
        r=c.classify({NAME:sha(b'sql')},view)
        self.assertTrue(r['byte_differences_explained']); self.assertFalse(r['operator_config_reuse_allowed'])
        self.assertEqual(r['operator_config_not_source_asset'],['app/config/config.yaml'])
        view.update(actual(b'x','app/config/unexpected.yaml'))
        self.assertFalse(c.classify({NAME:sha(b'sql')},view)['byte_differences_explained'])
        view.pop('app/config/unexpected.yaml'); view['app/config/config.yaml']['kind']='link'
        self.assertFalse(c.classify({NAME:sha(b'sql')},view)['byte_differences_explained'])

    def test_overlay_only_retains_current_file_variants(self):
        view=actual(b'old\n')
        a.layer_assets(io.BytesIO(tar_bytes([('app/migrations/versioned/.wh..wh..opq',b''),
            (NAME,b'new\n')])),view,c.newline_hashes)
        self.assertEqual(c.classify({NAME:sha(b'new\r\n')},view)['files'][0]['classification'],'LINE_ENDINGS_ONLY')
        self.assertEqual(c.classify({NAME:sha(b'old\r\n')},view)['files'][0]['classification'],'UNCLASSIFIED_BYTE_DIFFERENCE')
        a.layer_assets(io.BytesIO(tar_bytes([('.wh.app',b'')])),view,c.newline_hashes)
        self.assertEqual(view,{})

    def test_prior_raw_difference_receipt_must_match(self):
        expected={NAME:sha(b'x\r\n')}; view=actual(b'x\n')
        prior=dict(enrichment_runtime_audit='COMPLETE',production_writes=0,
            candidate_archive_sha256=a.ARCHIVE_SHA,source_manifest_sha256=c.SOURCE_SHA,
            runtime_assets=a.comparison(expected,view))
        c.reconcile(prior,expected,view,c.SOURCE_SHA)
        prior['runtime_assets']['different']=[]
        with self.assertRaisesRegex(a.AuditFailure,'prior_asset_view_changed'): c.reconcile(prior,expected,view,c.SOURCE_SHA)

    def test_compact_bounds_lists_and_preserves_status(self):
        report=c.classify({'app/config/%s'%i:sha(b'x') for i in range(100)},{})
        compact=c.compact(dict(classification=report,ready_for_rollout=False))
        self.assertEqual(compact['classification']['unresolved_count'],100)
        self.assertEqual(len(compact['classification']['unresolved_first20']),20)
        self.assertNotIn('files',compact['classification']); self.assertFalse(compact['ready_for_rollout'])

    def test_python37_grammar(self):
        ast.parse(Path(c.__file__).read_text(),feature_version=(3,7))


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Linux root private files')
class ArchiveIntegrationTests(unittest.TestCase):
    def test_hash_pinned_archive_classification_no_subprocess(self):
        with tempfile.TemporaryDirectory(dir='/root') as folder:
            root=Path(folder)
            payload=gzip.compress(tar_bytes([(NAME,b'CREATE TABLE x();\n')]))
            cfg=json.dumps(dict(os='linux',architecture='arm64')).encode()
            desc=lambda raw:dict(digest='sha256:'+sha(raw),size=len(raw))
            manifest=json.dumps(dict(schemaVersion=2,config=desc(cfg),layers=[dict(desc(payload),
                mediaType='application/vnd.oci.image.layer.v1.tar+gzip')])).encode()
            raw=tar_bytes([('blobs/sha256/'+sha(cfg),cfg),('blobs/sha256/'+sha(manifest),manifest),
                ('blobs/sha256/'+sha(payload),payload)])
            path=root/'image.tar'; path.write_bytes(raw); path.chmod(0o600)
            with patch.object(a,'emit'),patch.object(a.subprocess,'run',side_effect=AssertionError('no command')):
                view=a.inspect_archive(path,sha(raw),len(raw),'sha256:'+sha(manifest),'sha256:'+sha(cfg),c.newline_hashes)
                r=c.classify({NAME:sha(b'CREATE TABLE x();\r\n')},view)
                self.assertTrue(r['byte_differences_explained'])
                self.assertEqual(r['groups']['postgres_migrations']['line_endings_only'],1)
                prior=root/'RESULT.json'; digest=c.record(prior,r)
                self.assertEqual(sha(prior.read_bytes()),digest)
                self.assertEqual(prior.stat().st_mode&0o777,0o600)
                with self.assertRaises(FileExistsError): c.record(prior,r)
                with self.assertRaisesRegex(a.AuditFailure,'candidate_archive_hash_changed'):
                    a.inspect_archive(path,'0'*64,len(raw),'sha256:'+sha(manifest),'sha256:'+sha(cfg),c.newline_hashes)


if __name__=='__main__': unittest.main()
