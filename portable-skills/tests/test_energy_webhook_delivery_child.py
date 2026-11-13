"""Offline fixed-thread Webhook timeline checks; no actual HTTP or database."""
import asyncio
import base64
import datetime
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'deployment/webhook-budget-20261112-r1/inspect_delivery_child.py'
spec=importlib.util.spec_from_file_location('energy_delivery_probe',SOURCE)
child=importlib.util.module_from_spec(spec);spec.loader.exec_module(child)
NOW=datetime.datetime(2026,11,12,6,30,tzinfo=datetime.timezone.utc)
URL='https://cnoocclaw.cnooc.com.cn/workspace/chats/'+child.THREAD
HOOK='https://work.cnooc.com.cn/open-apis/bot/v2/hook/private-secret-token'

def event(seq,kind,obj):
    raw=json.dumps(obj,ensure_ascii=False)
    return {'seq':seq,'event_type':kind,'created_at':NOW,'bytes':len(raw.encode()),'content':raw,'head_b64':None,'tail_b64':None}

def python_command(body):
    return "python3 -I -B - <<'PY'\n"+body+"\nPY\n"

class StaticPayloadTests(unittest.TestCase):
    def test_literal_python_payload_proves_only_posted_literal_signals(self):
        command=python_command('requests.post('+repr(HOOK)+', json={"msg_type":"text","content":{"text":'+repr('[点击查看完整日报]('+URL+') 数据不完整 未用旧价 成品见附件')+'}})')
        result=child.post_candidates(command)[0]
        self.assertTrue(result['payload']['literal_payload_proven'])
        self.assertTrue(result['payload']['current_thread_url_present'])
        self.assertTrue(result['payload']['full_daily_link_label_present'])
        self.assertEqual(result['target_path_kind'],'CUSTOM_ROBOT_WEBHOOK')
        self.assertEqual(result['payload']['message_type'],'text')
        self.assertEqual(result['payload']['request_argument_kind'],'json');self.assertTrue(result['payload']['wire_payload_bytes_unknown'])
        self.assertIn('literal_payload_representation_bytes',result['payload']);self.assertNotIn('payload_bytes',result['payload'])
        self.assertNotIn('private-secret-token',json.dumps(result));self.assertNotIn(URL,json.dumps(result));self.assertNotIn(HOOK,json.dumps(result))

    def test_variable_payload_does_not_infer_missing_link(self):
        command=python_command('payload={"text":'+repr(URL)+'}\nrequests.post('+repr(HOOK)+', json=payload)')
        result=child.post_candidates(command)[0]['payload']
        self.assertFalse(result['literal_payload_proven']);self.assertIsNone(result['current_thread_url_present'])
        self.assertEqual(result['request_argument_kind'],'json')

    def test_fstrings_jsondumps_and_unquoted_heredocs_are_unknown(self):
        for command in (python_command('requests.post(url,json={"text":f"{text}"})'),python_command('requests.post(url,data=json.dumps({"text":"x"}))'),"python3 - <<PY\nrequests.post('"+HOOK+"',json={'msg_type':'text','content':{'text':'x'}})\nPY\n"):
            result=child.post_candidates(command)[0]
            self.assertFalse(result['payload']['literal_payload_proven']);self.assertIsNone(result['payload']['current_thread_url_present'])

    def test_python_with_script_stdin_is_not_literal_program(self):
        command="python3 different_script.py <<'PY'\nrequests.post('"+HOOK+"',json={'msg_type':'text','content':{'text':'x'}})\nPY\n"
        result=child.post_candidates(command)[0]
        self.assertEqual(result['client_kind'],'python_unknown_shell_context');self.assertFalse(result['payload']['literal_payload_proven'])

    def test_curl_literal_json_and_external_file_distinguished(self):
        value=json.dumps({'msg_type':'text','content':{'text':'plain text'}})
        result=child.post_candidates("curl -sS -d '"+value+"' "+HOOK)[0]
        self.assertTrue(result['payload']['literal_payload_proven']);self.assertFalse(result['payload']['current_thread_url_present'])
        for command in ('curl -d @/tmp/private.json '+HOOK,'curl --config config -d \''+value+'\' '+HOOK,'curl -d "$PAYLOAD" '+HOOK,"curl --get -d '"+value+"' "+HOOK,"curl -X GET -d '"+value+"' "+HOOK):
            result=child.post_candidates(command)[0]
            self.assertFalse(result['payload']['literal_payload_proven']);self.assertIsNone(result['payload']['current_thread_url_present'])

    def test_json_string_is_not_treated_as_json_object_and_form_dict_not_json(self):
        for key,value in (('json',repr('{"msg_type":"text"}')),('data','{"msg_type":"text"}')):
            result=child.post_candidates(python_command('requests.post(url,'+key+'='+value+')'))[0]['payload']
            self.assertEqual(result['message_type'],'OTHER_OR_NOT_JSON');self.assertTrue(result['wire_payload_bytes_unknown'])

    def test_wrong_origin_same_thread_path_is_not_current_conversation_url(self):
        self.assertFalse(child.signals('https://attacker.invalid/workspace/chats/'+child.THREAD)['current_thread_url_present'])
        self.assertFalse(child.signals('http://cnoocclaw.cnooc.com.cn/workspace/chats/'+child.THREAD)['current_thread_url_present'])

    def test_pure_literal_reader_rejects_call_names_and_too_deep_values(self):
        for body in ('payload','danger()','{"x": f"{secret}"}','{"x":{"x":{"x":{"x":{"x":"deep"}}}}}'):
            node=child.ast.parse(body,mode='eval').body
            with self.assertRaises(child.Refused):child.literal_node(node)

class TimelineTests(unittest.TestCase):
    def records(self):
        payload=json.dumps({'msg_type':'text','content':{'text':'正文没有尾段'}})
        return [event(2,'llm.ai.response',{'tool_calls':[{'id':'post-1','name':'bash','args':{'command':"curl -d '"+payload+"' "+HOOK}}],'content':'private model prose'}),
          event(3,'llm.tool.result',{'name':'bash','tool_call_id':'post-1','status':'success','content':'HTTP/1.1 200 OK\n{"code":0,"msg":"private response"}\nExit code: 0'}),
          event(4,'llm.ai.response',{'tool_calls':[{'id':'pub-1','name':'present_files','args':{'file_paths':['private-file']}}]}),
          event(5,'llm.tool.result',{'name':'present_files','tool_call_id':'pub-1','status':'success','content':'Successfully presented files. '+URL,'artifact':{'publication_status':'success'}}),
          event(6,'llm.ai.response',{'tool_calls':[],'content':'数据不完整，未用旧价。成品见附件。[点击查看完整日报]('+URL+')'})]

    def test_post_final_and_publication_sequence_is_not_conflated(self):
        result=child.summarize_events(self.records());post=result['post_candidate_timeline'][0]
        self.assertTrue(post['actual_result_present_in_selected_window']);self.assertTrue(post['result_before_latest_ai_candidate'])
        self.assertEqual(post['prior_present_files_receipt_count'],0);self.assertEqual(post['receipt']['http_status_codes'],[200]);self.assertEqual(post['receipt']['provider_business_codes'],[0])
        self.assertFalse(post['post_candidates'][0]['payload']['current_thread_url_present'])
        self.assertTrue(result['latest_ai_without_toolcalls_candidate']['signals']['current_thread_url_present'])
        output=json.dumps(result,ensure_ascii=False)
        for secret in ('private-secret-token','private response','private model prose','private-file',HOOK,URL,'正文没有尾段'):
            self.assertNotIn(secret,output)

    def test_no_tool_result_is_never_reported_as_execution(self):
        result=child.summarize_events(self.records()[:1])
        self.assertFalse(result['post_candidate_timeline'][0]['actual_result_present_in_selected_window'])
        self.assertIsNone(result['post_candidate_timeline'][0]['result_before_latest_ai_candidate'])

    def test_oversized_fragments_only_signals_and_no_false_absence(self):
        body=('x'*19000+URL+' private-secret-token').encode()
        record={'seq':9,'event_type':'llm.ai.response','created_at':NOW,'bytes':len(body),'content':None,'head_b64':base64.b64encode(body[:8192]).decode(),'tail_b64':base64.b64encode(body[-8192:]).decode()}
        result=child.summarize_events([record]);self.assertEqual(result['event_content_bytes_read'],16384)
        self.assertTrue(result['events'][0]['partial_fragments_cannot_prove_payload_absence_or_tool_execution'])
        self.assertEqual(result['post_candidate_timeline'],[]);self.assertNotIn('private-secret-token',json.dumps(result))

    def test_receipt_non_scalar_status_and_error_json_are_safe(self):
        result=child.receipt_summary({'status':{'secret':'private-secret-token'},'stdout':'Error: {"status":"FAILED","error_code":"UNEXPECTED_FAILURE","error_class":"NameError"}'})
        self.assertEqual(result['structured_receipts'][0]['error_class'],'NameError');self.assertNotIn('private-secret-token',json.dumps(result))

    def test_sql_bounds_apply_before_returning_event_contents(self):
        for required in ('LIMIT 8','LIMIT 32','LIMIT 40','b.bytes<=16384 AND b.cumulative_bytes<=131072','b.bytes>16384 AND b.cumulative_bytes<=131072','FOR 8192'):
            self.assertIn(required,child.EVENT_SQL)
        self.assertNotIn('checkpoint',child.EVENT_SQL);self.assertNotIn('llm.human',child.EVENT_KINDS)

class DatabaseTests(unittest.TestCase):
    def diagnostic(self,found=True):
        log=[]
        class Tx:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
        class Conn:
            def transaction(self,**kwargs):log.append(('transaction',kwargs));return Tx()
            async def fetchval(self,sql):log.append(('fetchval',sql));return 'on'
            async def execute(self,sql):log.append(('execute',sql))
            async def fetch(self,sql,*args):
                log.append(('fetch',sql,args))
                if sql==child.RUN_SQL:
                    return [{'run_id':'synthetic-run','status':'success','created_at':NOW,'updated_at':NOW,'stop_reason':None,'message_count':20,'configured_recursion_limit':'300'}] if found else []
                if sql==child.EVENT_SQL:return TimelineTests().records()
                raise AssertionError('unknown query')
        return asyncio.run(child.diagnose(Conn())),log

    def test_fixed_thread_readonly_transaction_and_latest_mapping(self):
        result,log=self.diagnostic();self.assertEqual(result['status'],'DELIVERY_TIMELINE_OBSERVED_READONLY')
        self.assertEqual(log[0],('transaction',{'readonly':True,'isolation':'repeatable_read'}));self.assertTrue(any('statement_timeout' in str(row) for row in log));self.assertTrue(any('lock_timeout' in str(row) for row in log))
        self.assertTrue(result['latest_run_not_proof_of_specific_user_attempt']);self.assertEqual(result['runs'][0]['projected_config_recursion_limit'],300)
        self.assertTrue(all(row[2][0]==child.THREAD for row in log if row[0]=='fetch'))
        self.assertNotIn('synthetic-run',json.dumps(result))

    def test_missing_thread_does_not_scan_events(self):
        result,log=self.diagnostic(False);self.assertEqual(result['status'],'NAMED_THREAD_RUN_NOT_FOUND')
        self.assertFalse(any(row[0]=='fetch' and row[1]==child.EVENT_SQL for row in log))

    def test_observe_fixed_input_rejected_before_config(self):
        with patch.object(child,'runtime_modules',side_effect=AssertionError('must not load')):
            result=asyncio.run(child.observe({'thread_id':'different-thread'}))
        self.assertEqual(result['error_code'],'FIXED_THREAD_REQUIRED');self.assertFalse(result['database_credentials_used'])

    def test_runtime_summary_survives_later_database_failure(self):
        config=SimpleNamespace(database=SimpleNamespace(backend='unsupported'))
        with patch.object(child,'runtime_modules',return_value=(None,lambda:config)),patch.object(child,'RUNTIME_OBSERVER',lambda cfg:{'status':'OBSERVED','budget':300}):
            result=asyncio.run(child.observe({'thread_id':child.THREAD}))
        self.assertEqual(result['runtime_summary']['budget'],300);self.assertEqual(result['error_code'],'POSTGRES_REQUIRED')

class AuditTests(unittest.TestCase):
    def test_mutations_and_locks_blocked(self):
        for event,args in [('open',('/tmp/x','w',child.os.O_WRONLY)),('open',('/tmp/x',None,child.os.O_RDWR)),('fcntl.flock',(1,2)),('subprocess.Popen',('anything',)),('os.mkdir',('/tmp/x',))]:
            with self.assertRaises(child.Refused):child.audit(event,args)

    def test_only_approved_database_connect_allowed(self):
        with patch.object(child,'DB_ADDRESSES',{'192.0.2.1'}),patch.object(child,'DB_PORT',5432):
            child.audit('socket.connect',(None,('192.0.2.1',5432)))
            with self.assertRaises(child.Refused):child.audit('socket.connect',(None,('192.0.2.2',80)))

if __name__=='__main__':unittest.main()
