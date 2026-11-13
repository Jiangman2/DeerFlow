"""Offline fixtures for bounded fixed-thread recovery evidence, never a real DB."""
import ast
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]/'deployment/recovery-gate-20261112-r1'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
child=load('recovery_child',HERE/'inspect_recovery_child.py')
sealer=load('recovery_sealer',HERE/'seal_recovery_probe.py')
host=types.ModuleType('recovery_host');exec(compile(sealer.source_host(),'<fixed-recovery-host>','exec'),host.__dict__)

def event(seq,kind,value):
    raw=json.dumps(value);return {'seq':seq,'run_id':'fixed-run','event_type':kind,'created_at':'2026-11-12T03:00:00Z','bytes':len(raw.encode()),'content':raw}

COMMAND="python3 -I -B /mnt/skills/public/energy-market-daily/scripts/daily.py doctor --workspace '/mnt/user-data/workspace/energy-daily-$(date +%Y%m%d)'"

class ClassificationTests(unittest.TestCase):
    def test_dynamic_workspace_is_shape_only_not_an_executed_date(self):
        value=child.command_summary('bash',{'command':COMMAND})
        self.assertEqual(value['daily_actions'],['doctor']);self.assertTrue(value['workspace']['contains_shell_substitution'])
        self.assertEqual(value['workspace']['quote_style'],'single');self.assertNotIn(COMMAND,json.dumps(value))

    def test_real_doctor_receipt_is_correlated_to_owning_call(self):
        receipt='Exit code: 0\n'+json.dumps({'status':'ENVIRONMENT_OBSERVED','ready_for_report':True,'credentials_configured':True,'next_command':COMMAND.replace('doctor','prepare')})
        ai=child.event_summary(event(2,'llm.ai.response',{'content':'I believe it failed','tool_calls':[{'id':'doctor-call','name':'bash','args':{'command':COMMAND}}]}))
        tool=child.event_summary(event(3,'llm.tool.result',{'tool_call_id':'doctor-call','name':'bash','status':'success','content':receipt}))
        pairs=child.correlate_doctors([ai,tool]);self.assertEqual(len(pairs),1)
        result=pairs[0]['results'][0]['receipt'];self.assertEqual(result['exit_codes'],[0])
        self.assertTrue(result['structured_receipts'][0]['ready_for_report']);self.assertTrue(ai['model_text_is_not_execution_proof'])
        self.assertEqual(result['structured_receipts'][0]['next_command']['daily_actions'],['prepare'])

    def test_wrapper_usage_import_permission_and_timeout_fingerprints(self):
        value={'exitcode':2,'stdout':'usage: daily.py doctor\n','stderr':'unrecognized arguments: --secret private-token\nPermissionError: [Errno 13] Permission denied\nModuleNotFoundError\nTimeoutError'}
        row=child.result_summary(value)
        self.assertEqual(row['exit_codes'],[2]);self.assertTrue(all(row['error_fingerprint'][key] for key in ('usage','unrecognized_arguments','import_error','permission','timeout')))
        self.assertEqual(row['errno_values'],[13]);self.assertNotIn('private-token',json.dumps(row))

    def test_ready_false_is_preserved_even_when_exit_zero(self):
        row=child.result_summary('Exit code: 0\n'+json.dumps({'status':'ENVIRONMENT_OBSERVED','ready_for_report':False,'readiness_error_codes':['BROWSER_RUNTIME_UNAVAILABLE'],'retry_allowed':False}))
        self.assertEqual(row['exit_codes'],[0]);self.assertFalse(row['structured_receipts'][0]['ready_for_report'])
        self.assertEqual(row['structured_receipts'][0]['readiness_error_codes'],['BROWSER_RUNTIME_UNAVAILABLE'])

    def test_transport_error_prefix_and_structured_name_error(self):
        row=child.result_summary('  Error: '+json.dumps({'status':'FAILED','error_code':'UNEXPECTED_FAILURE','error_class':'NameError'}))
        self.assertTrue(row['starts_with_error_prefix']);self.assertIn('NameError',row['exception_classes'])
        self.assertEqual(row['structured_receipts'][0]['error_class'],'NameError')
        row=child.result_summary({'status':'FAILED','error_code':'UNEXPECTED_FAILURE','error_class':'NameError'})
        self.assertEqual(row['structured_receipts'][0]['error_class'],'NameError')

    def test_unknown_error_and_schema_do_not_export_strings(self):
        row=child.result_summary({'status':'private-token','error_code':'api-password-private-token','schema':'https://example.test?token=private-token'})
        self.assertNotIn('private-token',json.dumps(row));self.assertEqual(row['structured_receipts'][0]['error_code'],'OTHER_REDACTED')

    def test_oversized_event_is_not_parsed(self):
        row=event(1,'llm.tool.result',{});row['content']=None
        self.assertTrue(child.event_summary(row)['oversized_content_skipped'])

    def test_recovery_stop_signal_does_not_export_prose(self):
        row=child.event_summary(event(9,'llm.ai.response',{'content':'Skill 工作流连续 3 次未遵循当前恢复契约 secret-body','tool_calls':[]}))
        self.assertTrue(row['text_signals']['recovery_stop_mention']);self.assertNotIn('secret-body',json.dumps(row))

class HostBoundaryTests(unittest.TestCase):
    def test_python37_transport_syntax(self):ast.parse(sealer.source_host().decode(),feature_version=(3,7))
    def test_only_the_new_thread_is_embedded(self):
        self.assertEqual(host.THREAD,child.THREAD);self.assertNotIn('201fb3d5',sealer.source_host().decode())
    def test_mutations_arbitrary_exec_and_secrets_refused(self):
        op=host.Operation('fixed-child');op.pod='gateway-abcd-xyz'
        for args in (['get','secret','deer-flow-env'],['patch','deployment','gateway'],['delete','pod',op.pod],['exec',op.pod,'--','sh']):
            with self.assertRaises(host.Refused):op.command(args)
    def test_only_real_skill_pvc_roots_are_sent(self):
        pod={'spec':{'volumes':[{'name':'skills','persistentVolumeClaim':{'claimName':'deer-flow-skills-rwx-pvc'}}],
            'containers':[{'name':'gateway','volumeMounts':[{'name':'skills','mountPath':'/mnt/skills/public','subPath':'public'}]}]}}
        self.assertEqual(host.skill_roots(pod),['/mnt/skills/public/energy-market-daily'])
        pod['spec']['containers'][0]['volumeMounts'].append({'name':'another','mountPath':'/mnt/skills/public/energy-market-daily/scripts'})
        with self.assertRaises(host.Refused):host.skill_roots(pod)
    def test_sql_is_bounded_and_does_not_select_human_or_final_content(self):
        self.assertIn('LIMIT 2',child.RUN_SQL);self.assertNotIn('first_human_message',child.RUN_SQL);self.assertNotIn('last_ai_message',child.RUN_SQL)
        self.assertIn('LIMIT 64',child.EVENT_SQL);self.assertEqual(child.EVENT_SQL.count('LIMIT 32'),2)
        self.assertNotIn('human_message',child.KINDS);self.assertIn('thread_id=$1',child.EVENT_SQL)
        self.assertEqual(64*65536,4194304)
    def test_embedded_safety_has_no_other_thread_or_storage_queries(self):
        source=sealer.source_safety().decode();self.assertNotIn('201fb3d5',source);self.assertNotIn('SELECT',source);self.assertNotIn('inspect_stored',source)

class FakeDatabaseTests(unittest.TestCase):
    def test_readonly_snapshot_limits_scoped_params_owner_recheck_and_no_task_execution(self):
        log=[];owner='trusted-owner'
        class Tx:
            async def __aenter__(self):pass
            async def __aexit__(self,*args):return False
        class Conn:
            def transaction(self,**kwargs):log.append(('transaction',kwargs));return Tx()
            async def execute(self,sql):log.append(('execute',sql))
            async def fetchval(self,sql,*args):
                log.append(('fetchval',sql,args));return 'on' if sql.startswith('SHOW') else owner if sql==child.OWNER_SQL else 3
            async def fetch(self,sql,*args):
                log.append(('fetch',sql,args))
                if sql==child.RUN_SQL:return [{'run_id':'fixed-run','status':'error','created_at':None,'updated_at':None,'error':'recovery-contract private-text'}]
                return [event(2,'llm.ai.response',{'tool_calls':[{'id':'doctor-call','name':'bash','args':{'command':COMMAND}}]}),event(3,'llm.tool.result',{'name':'bash','tool_call_id':'doctor-call','status':'success','content':'Exit code: 0\n{"status":"ENVIRONMENT_OBSERVED","ready_for_report":true}'})]
            async def close(self,timeout):log.append(('close',timeout))
        async def connect(url,**kwargs):log.append(('connect_settings',kwargs));return Conn()
        cfg=lambda:types.SimpleNamespace(database=types.SimpleNamespace(backend='postgres',postgres_url='postgresql://db-user:db-private-password@db.internal/fake'))
        safe={'runtime_modules':lambda:(types.SimpleNamespace(connect=connect),cfg,None)}
        with patch.object(child.socket,'getaddrinfo',return_value=[(2,1,6,'',('192.0.2.1',5432))]),patch.object(child,'source_hashes',return_value={'middleware':{'sha256':'a'*64}}):
            result=asyncio.run(child.observe({'skill_roots':[]},safe))
        self.assertEqual(result['status'],'NAMED_THREAD_RECOVERY_EVIDENCE_OBSERVED_NO_CHANGE');self.assertTrue(result['database_credentials_used'])
        self.assertEqual(len([row for row in log if row[0]=='fetchval' and row[1]==child.OWNER_SQL]),2)
        self.assertEqual([row[1] for row in log if row[0]=='transaction'],[{'readonly':True,'isolation':'repeatable_read'}]*2)
        self.assertIn(('execute',"SET LOCAL lock_timeout='500ms'"),log)
        for row in log:
            if row[0] in ('fetch','fetchval') and row[1].startswith(('SELECT','WITH')):self.assertEqual(row[2][0],child.THREAD)
        self.assertNotIn('db-private-password',json.dumps(result));self.assertFalse(result['task_retry_requested']);self.assertFalse(result['gate_modified'])

@unittest.skipUnless(os.name=='posix','POSIX bounded regular file reads')
class SourceTests(unittest.TestCase):
    def test_fixed_source_hash_is_readonly_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source.py';source.write_bytes(b'fixed source')
            self.assertEqual(child.fixed_hash(source)['sha256'],hashlib.sha256(b'fixed source').hexdigest())
            link=root/'link.py';link.symlink_to(source)
            with self.assertRaises(child.Refused):child.fixed_hash(link)
            self.assertEqual(source.read_bytes(),b'fixed source')

if __name__=='__main__':unittest.main()
