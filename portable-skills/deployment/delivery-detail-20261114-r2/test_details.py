"""Offline fixtures exercise sender format without reading any payload file."""
import asyncio
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('detail_seal',HERE/'seal.py')
seal=importlib.util.module_from_spec(spec);spec.loader.exec_module(seal)
child=types.ModuleType('detail_child');exec(compile(seal.child_source(),'<detail-child>','exec'),child.__dict__)


def classify(source):
    return child.sender_static_summary("python3 -I -B - <<'PY'\n"+source+"\nPY\n")


class DetailsTests(unittest.TestCase):
    def test_requests_dynamic_text_preserves_format_not_content(self):
        result=classify("import requests\ntext=open('/tmp/never-read').read()\npayload={'msgtype':'text','text':{'content':text}}\nrequests.post('https://private.example/hook/PRIVATE_SECRET',json=payload)")
        entry=result['send_candidates'][0]
        self.assertEqual(entry['argument_kind'],'json')
        self.assertEqual(entry['payload']['message_type_key'],'msgtype')
        self.assertEqual(entry['payload']['message_type'],'text')
        self.assertEqual(entry['payload']['known_field_presence']['text.content']['type'],'unknown')
        self.assertFalse(entry['payload']['payload_static_known'])
        self.assertNotIn('PRIVATE_SECRET',json.dumps(result))
        self.assertNotIn('never-read',json.dumps(result))

    def test_requests_json_serialized_data_bytes(self):
        result=classify("import requests,json\nbody={'msg_type':'text','content':{'text':open('/tmp/never').read()}}\nraw=json.dumps(body,ensure_ascii=False).encode('utf-8')\nrequests.post('https://private.example',data=raw)")
        entry=result['send_candidates'][0]
        self.assertEqual(entry['payload']['body_representation'],'bytes')
        self.assertEqual(entry['payload']['message_type_key'],'msg_type')
        self.assertEqual(entry['payload']['known_field_presence']['content.text']['type'],'unknown')
        self.assertFalse(entry['payload']['payload_static_known'])

    def test_http_client_post_body(self):
        result=classify("import http.client,json\nc=http.client.HTTPSConnection('private.example')\ndata=json.dumps({'msgtype':'markdown','markdown':{'content':'private words','title':'headline'}}).encode()\nc.request('POST','/private-hook',body=data)")
        entry=result['send_candidates'][0]
        self.assertEqual((entry['client'],entry['method'],entry['argument_kind']),('http.client','POST','body'))
        self.assertEqual(entry['payload']['message_type'],'markdown')
        self.assertNotIn('private',json.dumps(result))

    def test_urllib_request_and_urlopen_are_separate(self):
        result=classify("from urllib.request import Request,urlopen\nimport json\npayload={'msgtype':'text','text':{'content':'private text'}}\nr=Request('https://private.example',data=json.dumps(payload).encode('utf-8'),method='POST')\nurlopen(r)")
        entries=result['send_candidates']
        self.assertTrue(entries[0]['construction_only'])
        self.assertFalse(entries[1]['construction_only'])
        self.assertEqual(entries[1]['payload']['message_type_key'],'msgtype')
        self.assertNotIn('private text',json.dumps(result))

    def test_requests_session_alias(self):
        result=classify("import requests as r\ns=r.Session()\ns.post('https://private.example',json={'msg_type':'post','content':{'post':{}}})")
        self.assertEqual(result['send_candidates'][0]['client'],'requests')
        self.assertEqual(result['send_candidates'][0]['payload']['message_type'],'post')

    def test_unquoted_heredoc_never_claims_values(self):
        result=child.sender_static_summary("python3 - <<PY\nimport requests\nrequests.post('https://a.example',json={'msgtype':'text'})\nPY\n")
        self.assertFalse(result['send_candidates'][0]['shell_literal_source'])
        self.assertFalse(result['send_candidates'][0]['payload']['payload_static_known'])

    def test_external_script_stays_unknown(self):
        result=child.sender_static_summary('python /tmp/send-private-script.py')
        self.assertTrue(result['external_python_program_referenced'])
        self.assertFalse(result['external_program_contents_read'])
        self.assertEqual(result['send_candidates'],[])

    def test_loop_has_no_proven_literal_binding(self):
        result=classify("import requests\nbody={'msgtype':'text'}\nfor item in items:\n body=item\n requests.post(url,json=body)")
        self.assertTrue(result['send_candidates'][0]['conditional_or_function_context'])
        self.assertFalse(result['send_candidates'][0]['payload']['payload_static_known'])

    def test_enums_only_errors(self):
        output=child.receipt_summary('{"code":19002,"msg":"invalid JSON; PRIVATE_SECRET https://private.example/hook/secret"}')
        self.assertIn('JSON_PARSE_OR_SERIALIZE',output['known_error_marker_classes'])
        self.assertEqual(output['provider_business_codes'],[19002])
        self.assertNotIn('PRIVATE_SECRET',json.dumps(output))
        self.assertNotIn('private.example',json.dumps(output))

    def test_projection_missing_call_id_is_explicit(self):
        payload={'name':'present_files','status':'success','content':'Successfully presented files\nconversation_url: https://cnoocclaw.cnooc.com.cn/workspace/chats/'+child.THREAD,'requested_files':['a','b','c','d']}
        output=child.command_summary({'id':'c','name':'write_file','args':{'path':'/tmp/p/delivery/host-publication.json','content':json.dumps(payload)}})
        self.assertFalse(output['projection_parser_prerequisites']['tool_call_id_valid'])
        self.assertEqual(output['projection_parser_prerequisites']['tool_call_id_type'],'missing')

    def test_exact_sql_bounded_and_no_secret_query(self):
        self.assertIn('seq=ANY($4::integer[])',child.EVENT_SQL)
        self.assertIn('LIMIT 26',child.EVENT_SQL)
        self.assertEqual(len(child.FIXED_SEQUENCES),26)
        self.assertNotIn('UPDATE',child.EVENT_SQL)
        self.assertIn('32768',child.EVENT_SQL)
        self.assertIn('524288',child.EVENT_SQL)

    def test_wrong_run_never_reads_events(self):
        class Transaction:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
        class Database:
            def transaction(self,**kwargs):return Transaction()
            async def fetchval(self,*args):return 'on'
            async def execute(self,*args):return None
            async def fetch(self,sql,*args):
                if sql==child.RUN_SQL:return [{'run_id':'wrong-run'}]
                raise AssertionError('EVENT_READ_NOT_ALLOWED')
        with self.assertRaisesRegex(child.Refused,'FIXED_OPERATOR_RUN_NOT_IN_LATEST_TWO'):
            asyncio.run(child.diagnose(Database()))

    def test_wrong_event_sequence_refuses(self):
        with self.assertRaisesRegex(child.Refused,'FIXED_EVENT_SEQUENCE_REQUIRED'):
            child.summarize_events([{'seq':1}])

    def test_sealed_mutation_commands_rejected(self):
        sealed,_=seal.build();scope={'__name__':'sealed_test'};exec(compile(sealed,'<sealed>','exec'),scope)
        operation=scope['Operation'](scope['assets']())
        for args in (['get','secret','private','-o','json'],['delete','pod','gateway'],['patch','deployment','gateway']):
            with self.assertRaises(scope['Refused']):operation.command(args)
        self.assertEqual(operation.queries,0)

    def test_child_write_process_and_lock_audit(self):
        for event,args in [('open',('/tmp/x','w',child.os.O_WRONLY)),('open',('/tmp/x',None,child.os.O_RDWR)),('fcntl.flock',(1,2)),('subprocess.Popen',('anything',)),('os.mkdir',('/tmp/x',))]:
            with self.assertRaises(child.Refused):child.audit(event,args)

    def test_only_database_network_allowed(self):
        with patch.object(child,'DB_ADDRESSES',{'192.0.2.1'}),patch.object(child,'DB_PORT',5432):
            child.audit('socket.connect',(None,('192.0.2.1',5432)))
            with self.assertRaises(child.Refused):child.audit('socket.connect',(None,('192.0.2.2',80)))

    def test_static_calls_are_not_evaluated(self):
        result=classify("import requests\nrequests.post('https://private.example',json={'msgtype':'text','text':{'content':__import__('os').system('false')}})")
        self.assertFalse(result['send_candidates'][0]['payload']['payload_static_known'])
        self.assertEqual(result['send_candidates'][0]['payload']['known_field_presence']['text.content']['type'],'unknown')

    def test_exact_run_and_sequences_inside_readonly_transaction(self):
        log=[]
        class Transaction:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
        class Database:
            def transaction(self,**kwargs):log.append(('transaction',kwargs));return Transaction()
            async def fetchval(self,*args):return 'on'
            async def execute(self,sql):log.append(('execute',sql))
            async def fetch(self,sql,*args):
                log.append(('fetch',sql,args))
                return [{'run_id':'unit-target-run','status':'success'}] if sql==child.RUN_SQL else []
        with patch.object(child,'FIXED_RUN_SHA256',child.sha('unit-target-run')):
            result=asyncio.run(child.diagnose(Database()))
        self.assertEqual(result['run_selection_basis'],'EXACT_OPERATOR_RUN_SHA256_BOUND')
        self.assertEqual(log[0][1],{'readonly':True,'isolation':'repeatable_read'})
        event=[item for item in log if item[0]=='fetch' and item[1]==child.EVENT_SQL][0]
        self.assertEqual(event[2],(child.THREAD,'unit-target-run',child.EVENT_KINDS,child.FIXED_SEQUENCES))


if __name__=='__main__':unittest.main()
