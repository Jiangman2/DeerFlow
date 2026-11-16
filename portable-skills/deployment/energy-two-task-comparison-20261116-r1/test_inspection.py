"""Reuse execution privacy tests and cover combined notification/stage evidence."""
import ast
import asyncio
import base64
import datetime
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('comparison_seal',HERE/'seal.py')
seal=importlib.util.module_from_spec(spec);spec.loader.exec_module(seal)
HOST,CHILD=seal.build()
c={'__name__':'comparison_child'};exec(compile(CHILD,'<child>','exec'),c)
h={'__name__':'comparison_host'};exec(compile(HOST,'<host>','exec'),h)
THREAD=c['THREADS'][0]
ORIGIN='https://cnoocclaw.cnooc.com.cn'
READER=ORIGIN+'/reports/energy-daily/'+THREAD+'/2026-11-16.html'


class CompatOperation(h['Inspection']):
    pass


# Retain the earlier two-chat evidence/privacy regressions; only adapt their
# tightened event bounds and the new host interface. Archived tests stay intact.
source=(HERE.parent/'energy-two-task-inspection-20261114-r1/test_inspection.py').read_text(encoding='utf-8')
source=source.replace("h['assets']().encode()","base64.b64decode(h['ASSET']['base64'])")
source=source.replace("h['Operation']('child')","h['Inspection']('child')")
source=source.replace('LIMIT 808','LIMIT 168').replace('8388608','2097152')
tree=ast.parse(source)
selected=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.ClassDef))]
NOW=datetime.datetime(2026,11,16,1,tzinfo=datetime.timezone.utc)
exec(compile(ast.fix_missing_locations(ast.Module(body=selected,type_ignores=[])),'<retained-execution-evidence-tests>','exec'),globals())


def formatter():
    from urllib.parse import urlsplit
    from zoneinfo import ZoneInfo
    namespace={'re':__import__('re'),'urlsplit':urlsplit,'date':datetime.date,'datetime':datetime.datetime,
               'UTC':datetime.timezone.utc,'ZoneInfo':ZoneInfo,'validate_thread_id':lambda value:value}
    root=HERE.parent/'energy-reader-delivery-repair-20261115-r2/backend/app/scheduler'
    c['extracted']((root/'work_notifications.py').read_text(encoding='utf-8'),{'_notification_summary'},namespace)
    c['extracted']((root/'work_notification_delivery.py').read_text(encoding='utf-8'),{'_message','_daily_reader_url'},namespace)
    return namespace


class CombinedChecks(unittest.TestCase):
    def test_only_exact_operator_two_chats_are_bound(self):
        self.assertEqual(h['THREADS'],seal.THREADS)
        self.assertEqual(list(c['THREADS']),seal.THREADS)
        with patch.object(h['subprocess'],'Popen') as process:
            op=h['Inspection']('child');op.pod='gateway-test'
            with self.assertRaises(h['Refused']):
                op.command(['exec','-i','gateway-test','-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c','child'],b'{"thread_ids":["other"]}')
        process.assert_not_called()

    def test_host_entry_and_child_compile_on_python37(self):
        for raw in (HOST,CHILD):ast.parse(raw.decode(),feature_version=(3,7))

    def test_stage_facts_keep_dates_counts_and_artifact_types_without_body(self):
        value={'status':'REPORT_ACCEPTED','version':'2.1.6-portable','publication_date':'2026-11-16','data_date':'2026-11-15',
               'company_news_candidates':5,'market_news_candidates':3,'programmatic_checks_passed':True,
               'files':[{'path':'/PRIVATE/日报.html'},{'path':'/PRIVATE/日报.png'},{'path':'/PRIVATE/日报.json'},{'path':'/PRIVATE/日报.txt'}],
               'news':'PRIVATE_NEWS','JKX_API_PASSWORD':'DO_NOT_EXPORT','conversation_url':'https://private.invalid/SECRET'}
        result=c['stage_facts'](json.dumps(value))
        self.assertEqual(result[0]['file_extensions_in_receipt'],['.html','.png','.json','.txt'])
        self.assertEqual(result[0]['version'],'2.1.6-portable')
        output=json.dumps(result)
        for forbidden in ('PRIVATE','DO_NOT_EXPORT','private.invalid','SECRET'):self.assertNotIn(forbidden,output)

    def test_exact_owning_tool_receipt_contributes_stage_facts(self):
        rows=[event(1,'llm.ai.response',{'tool_calls':[{'id':'build','name':'bash','args':{'command':'python daily.py build --workspace /PRIVATE'}}]}),
              event(2,'llm.tool.result',{'tool_call_id':'build','name':'bash','status':'success','content':json.dumps({'status':'REPORT_ACCEPTED','version':'2.1.6-portable','render_seconds':3})})]
        result=c['event_summary'](rows,THREAD)
        self.assertEqual(result['timeline'][1]['stage_facts'][0]['status'],'REPORT_ACCEPTED')
        self.assertNotIn('/PRIVATE',json.dumps(result))

    def test_restart_and_expired_lease_are_separate_signals(self):
        restart=c['errors']('interrupted: gateway restarted before the run reached a terminal state')
        lease=c['errors']('interrupted: the owning gateway stopped renewing its run lease')
        self.assertIn('gateway_restart_sweep',restart['flags'])
        self.assertNotIn('gateway_restart_sweep',lease['flags'])
        self.assertIn('durable_run_lease_expired',lease['flags'])

    def test_link_only_formatter_diagnoses_preview_and_wrong_chat(self):
        result=c['formatting_summary']('[点击查看日报网页]('+READER+')\n\n油气与金融市场日报\n'+'正文'*4000,THREAD,formatter(),ORIGIN)
        self.assertTrue(result['notification_is_link_only'])
        self.assertEqual(result['valid_current_reader_urls'],[READER])
        wrong=READER.replace(THREAD,c['THREADS'][1])
        result=c['formatting_summary']('[点击查看日报网页]('+wrong+')',THREAD,formatter(),ORIGIN)
        self.assertTrue(result['notification_would_be_empty'])

    def test_notification_sql_is_bound_and_ciphertext_never_selected(self):
        for name in ('NOTIFY_RUN_SQL','NOTIFY_OCCURRENCE_SQL','NOTIFY_TASK_SQL','NOTIFY_OUTBOX_SQL','NOTIFY_CONNECTION_SQL','NOTIFY_EVENT_SQL'):
            sql=c[name]
            self.assertTrue(sql.lstrip().startswith('SELECT'));self.assertIn('$1',sql)
            self.assertNotIn('SELECT ciphertext',sql)
        self.assertEqual(set(c['EXPECTED_SOURCES']),{'app/scheduler/work_notifications.py','app/scheduler/work_notification_delivery.py'})

    def test_source_drift_does_not_discard_execution_diagnosis(self):
        async def original(conn,thread):return {'thread_id':thread,'status':'TASK_DURABLE_STATE_OBSERVED_READONLY','runs':[{'status':'error'}]}
        with patch.dict(c,{'_original_diagnose_thread':original,'formatters':lambda:(_ for _ in ()).throw(c['Refused']('NOTIFICATION_SOURCE_NOT_QUALIFIED_418'))}):
            result=asyncio.run(c['diagnose_thread'](object(),THREAD))
        self.assertEqual(result['runs'][0]['status'],'error')
        self.assertEqual(result['work_notification']['status'],'INCOMPLETE')
        self.assertEqual(result['work_notification']['error_code'],'NOTIFICATION_SOURCE_NOT_QUALIFIED_418')

    def test_wrong_host_is_zero_query(self):
        with patch.object(h['sys'],'argv',['tool','--inspect-readonly']),patch.object(h['os'],'name','nt'),patch.object(h['subprocess'],'Popen') as process,patch('builtins.print') as output:
            code=h['main']()
        self.assertEqual(code,1);process.assert_not_called()
        result=json.loads(output.call_args.args[0]);self.assertEqual(result['queries'],0)
        self.assertFalse(result['task_retry_requested'])

    def test_rollout_spec_change_does_not_discard_durable_observation(self):
        dep={'metadata':{'uid':h['GATEWAY_UID'],'generation':419},'spec':{},'status':{'readyReplicas':70}}
        pod={'metadata':{'uid':'pod-uid','ownerReferences':[{'kind':'ReplicaSet','name':'gateway-rs','uid':'rs-uid','controller':True}]},'spec':{},'status':{}}
        rs={'metadata':{'uid':'rs-uid','ownerReferences':[{'kind':'Deployment','uid':h['GATEWAY_UID'],'controller':True}]}}
        record={'status':'TWO_TASKS_READONLY_COLLECTED','requested_thread_ids':seal.THREADS,'threads':[{'thread_id':THREAD,'runs':[{'status':'error'}]}]}
        dep_queries=[]
        op=h['Inspection']('child')
        def query(args,body=None):
            if args==['get','deployment','gateway','-o','json']:
                dep_queries.append(1)
                return json.dumps(dep if len(dep_queries)==1 else dict(dep,metadata=dict(dep['metadata'],generation=420))).encode()
            if args==h['LIST']:return b'gateway-test pod-uid Running True <none> 2026-11-16T01:20:00Z\n'
            if args[:2]==['get','pod']:return json.dumps(pod).encode()
            if args[:2]==['get','replicaset']:return json.dumps(rs).encode()
            if args[0]=='exec':return json.dumps(record).encode()
            raise AssertionError(args)
        with patch.object(op,'command',side_effect=query),patch('builtins.print'):
            result=op.inspect()
        self.assertFalse(result['stable_readback'])
        self.assertEqual(result['observation'],record)
        self.assertTrue(result['this_tool_does_not_establish_rollout_acceptance'])


if __name__=='__main__':unittest.main()
