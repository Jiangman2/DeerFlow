import asyncio
import base64
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('two_task_seal',HERE/'seal.py');seal=importlib.util.module_from_spec(s);s.loader.exec_module(seal)
HOST,CHILD=seal.build()
c={'__name__':'test_child'};exec(compile(CHILD,'<child>','exec'),c)
h={'__name__':'test_host'};exec(compile(HOST,'<host>','exec'),h)
NOW=datetime.datetime(2026,11,14,4,0,0,tzinfo=datetime.timezone.utc)

def event(seq,kind,obj):
    raw=json.dumps(obj);return {'seq':seq,'event_type':kind,'created_at':NOW+datetime.timedelta(seconds=seq),'bytes':len(raw.encode()),'content':raw,'head_b64':None,'tail_b64':None}

class Transaction:
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False

class Database:
    def __init__(self):self.calls=[]
    def transaction(self,**kwargs):
        assert kwargs.get('readonly') is True;self.calls.append(('TRANSACTION',kwargs));return Transaction()
    async def execute(self,query):
        assert query.startswith('SET LOCAL ');self.calls.append((query,()))
    async def fetchval(self,query):
        assert query=='SHOW transaction_read_only';return 'on'
    async def fetch(self,query,*args):
        self.calls.append((query,args))
        if query==c['RUN_SQL']:
            return [{'run_id':'run_1','status':'success','model_name':'DeepSeek-V4.1-Flash','created_at':NOW,'updated_at':NOW+datetime.timedelta(seconds=100),'stop_reason':'loop_capped','recursion_limit':'10000','message_count':'38'}]
        if query==c['SCHEDULE_RUN_SQL']:
            return [] if args[0]==c['THREADS'][0] else [{'id':'occurrence_1','task_id':'task_1','run_id':'run_1','status':'failed','trigger':'scheduled','created_at':NOW}]
        if query==c['COUNT_SQL']:return [{'event_type':'llm.ai.response','count':1,'first_seq':1,'last_seq':1,'first_at':NOW,'last_at':NOW}]
        if query==c['EVENT_SQL']:return [event(1,'llm.ai.response',{'content':'done','tool_calls':[]})]
        raise AssertionError('Unexpected query')
    async def fetchrow(self,query,*args):
        self.calls.append((query,args))
        if query==c['CHECKPOINT_SQL']:return {'checkpoint_id':'checkpoint_1','parent_checkpoint_id':None,'step':'299','source':'loop','timestamp':NOW.isoformat(),'workflow_version':'1','inline_workflow':json.dumps({'skill_name':'energy-market-daily','gate_status':'failed','resume_pointer':'python daily.py doctor --workspace /private/project','evidence':[]})}
        if query==c['SCHEDULE_SQL']:return {'id':'task_1','status':'enabled','created_at':NOW,'updated_at':NOW}
        raise AssertionError('Unexpected row query')

class Checks(unittest.TestCase):
    def test_sealed_child_hash(self):self.assertEqual(h['assets']().encode(),CHILD)
    def test_host_supports_only_inspection(self):
        self.assertNotIn('create',' '.join(h['KUBE']));self.assertIn('--inspect-readonly',HOST.decode());self.assertNotIn('--activate',HOST.decode())
    def test_mutation_commands_refused_before_subprocess(self):
        op=h['Operation']('child')
        for args in (['delete','pod','x'],['patch','deployment','gateway'],['apply','-f','-'],['get','secret','x']):
            with self.assertRaises(h['Refused']):op.command(args)
        self.assertEqual(op.queries,0)
    def test_fixed_thread_scope(self):
        result=asyncio.run(c['observe']({'thread_ids':['other']}));self.assertEqual(result['error_code'],'EXACT_TWO_THREADS_REQUIRED')
    def test_all_sql_parameter_bound(self):
        for name in ('RUN_SQL','SCHEDULE_RUN_SQL','EVENT_SQL','COUNT_SQL','CHECKPOINT_SQL','BLOB_SQL'):
            self.assertIn('thread_id=$1',c[name]);self.assertNotIn(c['THREADS'][0],c[name])
    def test_schema_tolerant_scalar_fields(self):
        self.assertIn("to_jsonb(r)->>'llm_call_count'",c['RUN_SQL']);self.assertIn("to_jsonb(r)->>'attempt_count'",c['SCHEDULE_RUN_SQL'])
    def test_event_limits_before_read(self):
        self.assertIn('LIMIT 808',c['EVENT_SQL']);self.assertIn('8388608',c['EVENT_SQL']);self.assertIn('bytes<=16384',c['EVENT_SQL']);self.assertIn('LIMIT 10001',c['COUNT_SQL'])
    def test_thread_kind_from_schedule_not_order(self):
        db=Database();a=asyncio.run(c['diagnose_thread'](db,c['THREADS'][0]));b=asyncio.run(c['diagnose_thread'](db,c['THREADS'][1]));self.assertEqual(a['source'],'NO_SCHEDULE_OCCURRENCE_FOR_THREAD');self.assertEqual(b['source'],'SCHEDULED_OCCURRENCE_PRESENT')
        self.assertEqual(b['runs'][0]['stop_reason'],'loop_capped');self.assertEqual(b['runs'][0]['recursion_limit'],10000)
        self.assertEqual(b['checkpoint']['step'],299)
    def test_derived_schedule_task_id_only(self):
        db=Database();asyncio.run(c['diagnose_thread'](db,c['THREADS'][1]));self.assertEqual([args for q,args in db.calls if q==c['SCHEDULE_SQL']],[('task_1',)])
    def test_ocr_and_build_paired_timing(self):
        rows=[event(1,'llm.ai.response',{'tool_calls':[{'id':'c1','name':'bash','args':{'command':'python daily.py build --workspace /x'}}]}),event(4,'llm.tool.result',{'tool_call_id':'c1','name':'bash','status':'success','content':json.dumps({'status':'REPORT_GENERATED_VISUAL_REVIEW_REQUIRED'})}),event(5,'llm.ai.response',{'tool_calls':[{'id':'c2','name':'bash','args':{'command':'python -c "import pytesseract; secret = 123"'}}]})]
        result=c['event_summary'](rows,c['THREADS'][0]);self.assertEqual(result['timeline'][1]['tool_seconds'],3);self.assertEqual(result['proposed_action_counts']['OCR_CODE_OR_COMMAND'],1);self.assertEqual(result['proposed_action_counts']['build'],1)
    def test_unknown_enum_error_retained(self):
        result=c['errors'](json.dumps({'error_code':'HOST_CONVERSATION_IDENTITY_INCOMPLETE','error_class':'SomeNewError'}));self.assertEqual(result['error_codes'],['HOST_CONVERSATION_IDENTITY_INCOMPLETE']);self.assertIn('SomeNewError',result['exception_classes'])
    def test_error_credentials_and_urls_not_exported(self):
        secret='pAsSw0rd_DONT_EXPORT';url='https://example.invalid/hook/'+secret
        result=c['errors'](json.dumps({'error_code':url,'error':secret,'message':'TimeoutError '+url}));body=json.dumps(result);self.assertNotIn(secret,body);self.assertNotIn(url,body);self.assertIn('TimeoutError',body)
    def test_workflow_task_and_raw_paths_not_exported(self):
        result=c['workflow']({'task_text':'SECRET_NEWS','resume_pointer':'python daily.py accept --workspace /PRIVATE_PATH','gate_detail':'token=DO_NOT_EXPORT','evidence':[]});body=json.dumps(result);self.assertNotIn('SECRET_NEWS',body);self.assertNotIn('PRIVATE_PATH',body);self.assertNotIn('DO_NOT_EXPORT',body)
    def test_hard_failure_detected_with_exitcode(self):
        rows=[event(2,'llm.tool.result',{'name':'bash','status':'success','content':'Process exited with code 1\n{"error_code":"BROWSER_RUNTIME_UNAVAILABLE"}'})];r=c['event_summary'](rows,c['THREADS'][0]);self.assertEqual(r['first_observed_error']['seq'],2)
    def test_fragments_never_prove_absence(self):
        r=event(1,'llm.tool.result',{});r.update(content=None,head_b64=base64.b64encode(b'{"content":"OCR').decode(),tail_b64=base64.b64encode(b'END').decode());out=c['event_summary']([r],c['THREADS'][0]);self.assertFalse(out['content_coverage_complete']);self.assertTrue(out['timeline'][0]['fragment_is_not_full_message'])
    def test_current_thread_url_exact_marker(self):
        other=c['THREADS'][1];r=event(1,'llm.ai.response',{'content':'https://cnoocclaw.cnooc.com.cn/workspace/chats/'+other});out=c['event_summary']([r],c['THREADS'][0]);self.assertFalse(out['last_ai_candidates'][0]['current_thread_url_present'])
    def test_audit_prohibits_files_and_remote_network(self):
        for event_name,args in (('open',('/tmp/x','w',1)),('subprocess.Popen',('cmd',)),('socket.connect',(None,('1.2.3.4',80))),('socket.bind',(None,('0.0.0.0',80)))):
            with self.assertRaises(c['Refused']):c['audit'](event_name,args)
    def test_checkpoint_has_no_pickle_or_application_import(self):
        self.assertNotIn('loads_typed',CHILD.decode());self.assertNotIn('import pickle',CHILD.decode());self.assertIn('ormsgpack.unpackb(bytes(raw))',CHILD.decode())
    def test_fixed_sources_include_scheduler_and_guard(self):
        self.assertIn("'app/scheduler/service.py'",CHILD.decode());self.assertIn('skill_workflow_middleware.py',CHILD.decode())
    def test_real_run_no_replay_code(self):
        for text in ('ainvoke(','astream(','instantiate_provider(','create_sandbox(','set_status('):self.assertNotIn(text,CHILD.decode())

if __name__=='__main__':unittest.main()
