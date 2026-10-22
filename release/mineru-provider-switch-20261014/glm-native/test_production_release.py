import ast
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import production_release_review as r
import production_node_preload as n
import prepare_production_release as p


def row():
    return dict(read_only='on',schema=[dict(version=90,dirty=False)],legacy_inline_kbs=0,unresolved_model_refs=0,
        models=[dict(id='deerflow-dashscope-vlm',type='VLLM',is_builtin=True,managed_by='',name_class='QWEN3_VL_PLUS',
            glm_profile=False,max_concurrency=None,kb_count=5,enabled_count=4)])


class ScopeTests(unittest.TestCase):
    def test_clean_projection(self):
        got=r.clean_models(row()); self.assertEqual(got['models'][0]['kb_count'],5)
        self.assertFalse(got['credentials_selected']); self.assertFalse(got['business_content_selected'])

    def test_never_export_other_ids_extra_fields_or_keys(self):
        raw=row(); raw['models'][0].update(id='secret-customer-name',api_key='SECRET'); raw['api_key']='SECRET'
        text=json.dumps(r.clean_models(raw)); self.assertNotIn('secret-customer-name',text); self.assertNotIn('SECRET',text)

    def test_malformed_projection_rejected(self):
        for k,v in [('read_only','off'),('schema',[]),('legacy_inline_kbs',-1),('models',[row()['models'][0]]*257)]:
            x=row(); x[k]=v
            with self.subTest(k=k),self.assertRaises(RuntimeError): r.clean_models(x)

    def test_invalid_model_values_rejected(self):
        for k,v in [('kb_count',True),('max_concurrency',-1),('glm_profile',1),('name_class','secret')]:
            x=row(); x['models'][0][k]=v
            with self.subTest(k=k),self.assertRaises(RuntimeError): r.clean_models(x)

    def test_readonly_bounded_sql(self):
        self.assertTrue(r.SQL.startswith('BEGIN READ ONLY;')); self.assertTrue(r.SQL.endswith('ROLLBACK;'))
        self.assertIn("statement_timeout='5s'",r.SQL); self.assertIn("lock_timeout='1s'",r.SQL)
        for x in ('api_key','app_secret','SELECT *','UPDATE ','DELETE ','INSERT ','public.knowledges','public.chunks'):
            self.assertNotIn(x,r.SQL)
        self.assertIn('default_transaction_read_only=on',r.DB_SHELL)
        self.assertNotIn('echo',r.DB_SHELL)

    def test_exact_receipts_all_gates_required(self):
        ok={k:'PASS' for k in ('native_enrichment_acceptance','native_schema_migration','real_asynq_glm_enrichment',
            'real_embedding_1024d_index','vector_only_retrieval')}
        ok.update(cleanup='NAMESPACE_ABSENT_VERIFIED',production_baseline='UNCHANGED',production_writes=0,document_submission_attempted=True)
        wrapper=dict(fixture_config_recovery='PASS',acceptance_exit_code=0,fresh_acceptance_state=str(Path(r.PASS_PATH).parent))
        a=SimpleNamespace(audit=SimpleNamespace(read=Mock(side_effect=[json.dumps(ok),json.dumps(wrapper)])))
        self.assertEqual(r.accepted(a)['full_acceptance_sha256'],r.PASS_SHA)
        for k in ('real_asynq_glm_enrichment','real_embedding_1024d_index','vector_only_retrieval'):
            bad=dict(ok); bad[k]='NOT_TESTED'; a.audit.read.side_effect=[json.dumps(bad),json.dumps(wrapper)]
            with self.assertRaises(RuntimeError): r.accepted(a)

    def test_duplicate_env_and_literal_credentials_rejected(self):
        with self.assertRaises(RuntimeError): r.literals({'env':[{'name':'X','value':'1'},{'name':'X','value':'2'}]})
        with self.assertRaises(RuntimeError): r.credential_ref({'env':[{'name':'DB_PASSWORD','value':'SECRET'}]},'DB_PASSWORD')

    def test_gateway_probe_does_not_import_application_or_read_keys(self):
        ast.parse(r.VISION_PROBE)
        for token in ('api_key','API_KEY','PASSWORD','asyncpg','requests','urlopen','subprocess'):
            self.assertNotIn(token,r.VISION_PROBE)
        self.assertIn('ast.parse',r.VISION_PROBE)


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/root'); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'attempt'
        self.patch=patch.object(n,'ROOT',self.root); self.patch.start(); self.addCleanup(self.patch.stop)

    def test_no_repeat_after_existing_state(self):
        self.root.mkdir()
        with patch.object(n,'rollback') as old,self.assertRaisesRegex(n.verify.CheckFailure,'previous_remote_attempt'):
            n.accept()
        old.assert_not_called()

    def test_record_exclusive_private_and_no_overwrite(self):
        self.root.mkdir(mode=0o700); n.record('a.json',{'v':1})
        self.assertEqual((self.root/'a.json').stat().st_mode&0o777,0o600)
        with self.assertRaises(FileExistsError): n.record('a.json',{'v':2})
        with self.assertRaises(n.verify.CheckFailure): n.write('../bad',b'x')

    def test_unsafe_directory_blocked(self):
        self.root.mkdir(mode=0o777); self.root.chmod(0o777)
        with self.assertRaises(n.verify.CheckFailure): n.record('bad.json',{})

    def test_other_build_not_killed(self):
        with patch.object(n.cli,'command',return_value=b'buildctl buildctl build\n') as call:
            with self.assertRaisesRegex(n.verify.CheckFailure,'other_image'): n.clear_operations()
            self.assertEqual(call.call_count,1)

    def test_no_false_match_program_arguments(self):
        with patch.object(n.cli,'command',return_value=b'python3 python3 buildctl\nctr ctr -n k8s.io images ls\n'):
            n.clear_operations()

    def test_bad_archive_cannot_reach_layer_check(self):
        self.root.mkdir(mode=0o700)
        with patch.object(n.cli,'ARCHIVE_BYTES',3),patch.object(n.cli,'ARCHIVE_SHA','0'*64),patch.object(n.verify,'verify') as check:
            with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(n.verify.CheckFailure,'archive_hash'):
                n.receive_archive(io.BytesIO(b'abc'))
            check.assert_not_called()

    def test_truncated_and_extra_archive_rejected(self):
        for data in (b'ab',b'abcd'):
            with self.subTest(data=data):
                self.root.mkdir(mode=0o700,exist_ok=True)
                old=self.root/'candidate.oci.tar.partial'
                if old.exists(): old.unlink()
                with patch.object(n.cli,'ARCHIVE_BYTES',3),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(n.verify.CheckFailure):
                    n.receive_archive(io.BytesIO(data))

    def test_failure_after_import_never_retries(self):
        old=dict(config='old'); image=dict(pinned=True)
        with patch.object(n,'clear_operations'),patch.object(n,'rollback',return_value=old),patch.object(n,'storage',return_value={}), \
             patch.object(n.cli,'reference_present',return_value=False),patch.object(n,'status_program',return_value=b'status'), \
             patch.object(n,'receive_archive',return_value=Path('/private/archive')), \
             patch.object(n.cli,'command',side_effect=n.cli.PreloadFailure('command_failed_STATUS_ONLY')) as command, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(n.accept(),1)
        self.assertTrue((self.root/'IMPORT-REQUESTED.json').exists()); self.assertFalse((self.root/'IMPORT-ACK.json').exists())
        self.assertEqual(command.call_count,1)
        self.assertEqual(json.loads((self.root/'RESULT.json').read_bytes())['imports_attempted'],1)

    def test_existing_image_reused_without_transfer_or_import(self):
        old=dict(config='old'); image=dict(pinned=True)
        with patch.object(n,'clear_operations'),patch.object(n,'rollback',return_value=old),patch.object(n,'storage',return_value={}), \
             patch.object(n.cli,'reference_present',return_value=True),patch.object(n,'status_program',return_value=b'status'), \
             patch.object(n.cli,'image_check',return_value=image),patch.object(n,'receive_archive') as receive, \
             patch.object(n.cli,'command') as command,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(n.accept(),0)
        receive.assert_not_called(); command.assert_not_called()

    def test_status_no_mutation_or_import(self):
        self.root.mkdir(mode=0o700)
        with patch.object(n.cli,'reference_present',return_value=False),patch.object(n,'record') as record,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(n.status(),0)
        record.assert_not_called()

    def test_error_redaction(self):
        self.assertEqual(n.safe_error(ValueError('SECRET')),'SAFE_DIAGNOSTIC_REQUIRED')


class SenderTests(unittest.TestCase):
    def test_remote_ssh_pinned_no_password_argv(self):
        with patch.object(p,'REMOTE_CODE','pass'):
            args=p.remote_command()
        self.assertIn('StrictHostKeyChecking=yes',args); self.assertIn('HostKeyAlias=datax-03',args)
        self.assertIn('sudo -S',args[-1]); self.assertNotIn('StrictHostKeyChecking=no',args)

    def test_sender_real_pipe_handshake(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'image'; path.write_bytes(b'abc')
            q=SimpleNamespace(opened=lambda p,m:open(str(p),'rb'))
            remote='''import sys,json
assert sys.stdin.buffer.readline()==b"secret\\n"
print(json.dumps({"receiver":"READY","bytes":3}),flush=True)
assert sys.stdin.buffer.read()==b"abc"
print(json.dumps({"production_node_preload":"PASS"}),flush=True)
'''
            secret=bytearray(b'secret\n')
            with open(os.devnull,'w') as tty,patch.object(p,'remote_command',return_value=[sys.executable,'-c',remote]), \
                 patch.object(p.trust_cli,'ARCHIVE_BYTES',3),patch.object(p.trust_cli,'ARCHIVE_SHA',hashlib.sha256(b'abc').hexdigest()), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(p.send(path,q,secret,tty)['production_node_preload'],'PASS')
            self.assertEqual(secret,bytearray(len(secret)))

    def test_rejected_handshake_never_streams_archive(self):
        q=SimpleNamespace(opened=Mock())
        remote='import json; print(json.dumps({"receiver":"READY","bytes":1}),flush=True)'
        with open(os.devnull,'w') as tty,patch.object(p,'remote_command',return_value=[sys.executable,'-c',remote]),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'receiver_handshake'): p.send(Path('/bad'),q,bytearray(b'secret\n'),tty)
        q.opened.assert_not_called()

    def test_unexpected_errors_are_redacted(self):
        self.assertEqual(p.safe_error(ValueError('SECRET')),'SAFE_DIAGNOSTIC_REQUIRED')

    def test_sources_python37_and_no_rollout(self):
        for module in (r,n,p):
            source=Path(module.__file__).read_text(); ast.parse(source,feature_version=(3,7))
            for token in ('kubectl apply','rollout restart','images prune','images rm','rmtree(','shell=True','SIGTSTP'):
                self.assertNotIn(token,source)


if __name__=='__main__': unittest.main()
