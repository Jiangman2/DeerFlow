"""Exact stored transport message, fixed Pod event scope, zero task execution."""
import ast
import asyncio
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]/'deployment/recovery-gate-20261112-r2'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
sealer=load('transport_sealer',HERE/'seal_transport_probe.py')
child=load('transport_child',HERE/'inspect_transport_child.py')
common={'__name__':'fixed_common_fixture'};exec(compile(sealer.common_source(),'<common-fixture>','exec'),common)
host=types.ModuleType('transport_host');exec(compile(sealer.source_host(),'<transport-host>','exec'),host.__dict__)
MESSAGE='Error: Unexpected error initializing sandbox: RuntimeError: Sandbox 0123abcd failed to become ready within 180s at http://user:private-password@sandbox-0123abcd-svc.deer-flow.svc.cluster.local:8080?api_token=private-token'

class MessageTests(unittest.TestCase):
    def test_bare_id_template_and_all_endpoint_credentials_are_removed(self):
        result=child.transport(MESSAGE,common)
        self.assertEqual(result['sandbox_pod_name'],'sandbox-0123abcd');self.assertEqual(result['startup_wait_seconds'],180)
        self.assertTrue(result['message_template_recognized']);self.assertIn('failed to become ready within 180s',result['sanitized_message'])
        self.assertTrue(result['receipt']['starts_with_error_prefix'])
        for value in ('private-password','private-token','cluster.local','http://','?api_token'):
            self.assertNotIn(value,json.dumps(result))

    def test_unknown_error_detail_is_not_generically_echoed(self):
        result=child.transport('Error: RuntimeError: secret-password-unknown-context',common)
        self.assertFalse(result['message_template_recognized']);self.assertNotIn('secret-password',json.dumps(result))
        self.assertLessEqual(len(result['sanitized_message']),512)

    def test_no_valid_error_template_means_no_pod_query_candidate(self):
        result=child.transport('Sandbox 0123abcd failed to become ready within 180s at http://example.test',common)
        self.assertNotIn('sandbox_pod_name',result)
        result=child.transport(MESSAGE.replace('0123abcd','arbitrary-id'),common)
        self.assertNotIn('sandbox_pod_name',result)

    def test_attempts_export_only_categories_and_hashes(self):
        calls={'tool_calls':[{'name':'bash','id':'fixed-call','args':{'command':"ls /private; python3 -c 'import os; print(os.environ)' ; cat /private-secret"}}]}
        result=child.attempted_call(calls,common)
        self.assertEqual(set(result[0]['categories']),{'list','python','environment','read'})
        self.assertNotIn('/private',json.dumps(result));self.assertTrue(result[0]['ai_call_is_not_execution_proof'])

class HostTests(unittest.TestCase):
    def test_python37_source_syntax_and_query_bound(self):
        ast.parse(sealer.source_host().decode(),feature_version=(3,7));self.assertIn('self.queries<9',sealer.source_host().decode())

    def test_only_fixed_extracted_pod_is_allowed(self):
        op=host.Operation('child');op.pod='gateway-abcd-xyz';op.sandbox='sandbox-0123abcd'
        for args in (['delete','pod',op.sandbox],['get','pod','sandbox-ffffffff','-o','json'],['get','events','-o','json'],['get','secret','credential']):
            with self.assertRaises(host.Refused):op.command(args)

    def test_missing_pod_does_not_claim_why_it_is_absent(self):
        result=host.sandbox_summary(None,'sandbox-0123abcd')
        self.assertFalse(result['present']);self.assertTrue(result['current_absence_does_not_prove_historical_cause'])

    def test_pending_pod_exports_status_not_messages_or_environment(self):
        pod={'metadata':{'name':'sandbox-0123abcd','uid':'fixed-uid'},'spec':{'containers':[{'env':[{'name':'PASSWORD','value':'private-password'}]}]},
             'status':{'phase':'Pending','conditions':[{'type':'PodScheduled','status':'False','reason':'Unschedulable','message':'private-body'}],
                       'containerStatuses':[{'name':'sandbox','ready':False,'state':{'waiting':{'reason':'CreateContainerConfigError','message':'private-secret-name'}}}]}}
        result=host.sandbox_summary(pod,'sandbox-0123abcd')
        self.assertEqual(result['containers'][0]['reason'],'CreateContainerConfigError');self.assertNotIn('private-',json.dumps(result))

    def test_event_reason_and_flags_hide_all_free_text(self):
        events={'items':[{'involvedObject':{'kind':'Pod','name':'sandbox-0123abcd','uid':'fixed-uid'},'type':'Warning','reason':'FailedScheduling','count':2,
            'message':"0/5 nodes available: Insufficient cpu, Insufficient memory, Too many pods; couldn't find key private-password in Secret credential-name"}]}
        result=host.sandbox_events(events,'sandbox-0123abcd');flags=result['events'][0]['flags']
        self.assertEqual(set(flags),{'insufficient_cpu','insufficient_memory','too_many_pods','secret_key_missing'})
        self.assertNotIn('private-password',json.dumps(result));self.assertNotIn('credential-name',json.dumps(result))

    def test_event_scope_count_and_empty_history(self):
        self.assertTrue(host.sandbox_events({'items':[]},'sandbox-0123abcd')['historical_events_not_present'])
        with self.assertRaises(host.Refused):host.sandbox_events({'items':[{}]*129},'sandbox-0123abcd')
        with self.assertRaises(host.Refused):host.sandbox_events({'items':[{'involvedObject':{'kind':'Pod','name':'sandbox-ffffffff'}}]},'sandbox-0123abcd')

    def test_sql_scope_is_only_four_fixed_events_not_last80(self):
        self.assertEqual(child.SEQS,[7,8,9,10]);self.assertIn('LIMIT 4',child.EVENT_SQL);self.assertIn('content)<=8192',child.EVENT_SQL)
        self.assertIn('thread_id=$1 AND run_id=$2 AND seq=ANY($3::bigint[])',child.EVENT_SQL)
        self.assertNotIn('FROM runs',child.EVENT_SQL);self.assertNotIn('SELECT',sealer.common_source().decode())

class FakeDatabaseTests(unittest.TestCase):
    def test_exact_query_owner_stability_and_transport_diagnosis(self):
        log=[]
        class Tx:
            async def __aenter__(self):pass
            async def __aexit__(self,*args):return False
        class Conn:
            def transaction(self,**kwargs):log.append(('transaction',kwargs));return Tx()
            async def execute(self,sql):log.append(('execute',sql))
            async def fetchval(self,sql,*args):log.append(('fetchval',sql,args));return 'on' if sql.startswith('SHOW') else 'trusted-owner'
            async def fetch(self,sql,*args):
                log.append(('fetch',sql,args));obj={'tool_call_id':'doctor-call','content':MESSAGE,'name':'bash','status':'success'}
                return [{'seq':7,'run_id':child.RUN,'event_type':'llm.tool.result','created_at':None,'bytes':375,'content':json.dumps(obj)}]
            async def close(self,timeout):pass
        async def connect(url,**kwargs):log.append(('connect',kwargs));return Conn()
        cfg=lambda:types.SimpleNamespace(database=types.SimpleNamespace(backend='postgres',postgres_url='postgresql://db-user:private-password@db.internal/fake'),sandbox=types.SimpleNamespace(startup_timeout=180))
        safe={'runtime_modules':lambda:(types.SimpleNamespace(connect=connect),cfg,None)}
        helper=dict(common);helper['fixed_hash']=lambda path:{'present':True,'sha256':'a'*64}
        with patch.object(child.socket,'getaddrinfo',return_value=[(2,1,6,'',('192.0.2.1',5432))]),patch.object(child,'CALL_SHA',child.sha(b'doctor-call')):
            result=asyncio.run(child.observe(helper,safe))
        self.assertEqual(result['status'],'FIXED_DOCTOR_TRANSPORT_EVIDENCE_OBSERVED_NO_CHANGE');self.assertTrue(result['database_credentials_used'])
        queries=[row for row in log if row[0]=='fetch'];self.assertEqual(len(queries),1);self.assertEqual(queries[0][2],(child.THREAD,child.RUN,[7,8,9,10]))
        self.assertEqual(len([row for row in log if row[0]=='fetchval' and row[1]==child.OWNER_SQL]),2)
        self.assertEqual(result['missing_event_seqs'],[8,9,10]);self.assertEqual(result['sandbox_pod_name'],'sandbox-0123abcd')
        self.assertEqual(result['configured_startup_timeout_seconds'],180);self.assertNotIn('private-password',json.dumps(result))

if __name__=='__main__':unittest.main()
