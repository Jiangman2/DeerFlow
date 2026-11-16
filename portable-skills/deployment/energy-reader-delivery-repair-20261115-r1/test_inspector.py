import ast
import datetime
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('new_reader_inspection_builder',HERE/'seal_inspector.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
RAW,ASSETS,CHILD=builder.build()
scope={'__name__':'qualified_task_child'};exec(compile(CHILD,'<qualified-child>','exec'),scope)
edge={'__name__':'qualified_serving_edge'};exec(compile(ASSETS['edge'],'<qualified-edge>','exec'),edge)
THREAD=builder.THREAD
URL='https://cnoocclaw.cnooc.com.cn/reports/energy-daily/'+THREAD+'/2026-11-15.html'


class InspectionTests(unittest.TestCase):
    def test_exact_one_thread_and_assets(self):
        self.assertEqual(scope['THREADS'],(THREAD,))
        task={'__name__':'qualified_task_host'};exec(compile(ASSETS['task'],'<host>','exec'),task)
        self.assertEqual(task['THREADS'],[THREAD])
        self.assertEqual(task['assets']().encode(),CHILD)

    def test_reader_link_filters_wrong_task_and_signed_links(self):
        value='[点击查看日报网页]('+URL+')'
        self.assertEqual(scope['report_links'](value,THREAD),[URL])
        for candidate in [URL+'?token=PRIVATE',URL+'#x',URL.replace(THREAD,'other-thread'),URL.replace('2026-11-15','2026-02-30')]:
            self.assertEqual(scope['report_links'](candidate,THREAD),[])

    def test_event_export_has_link_and_send_kind_without_webhook_secret(self):
        secret='WEBHOOK_SECRET_MUST_STAY_PRIVATE'
        now=datetime.datetime(2026,11,15,tzinfo=datetime.timezone.utc)
        objects=[('llm.ai.response',{'content':'油气与金融市场日报\n[点击查看日报网页]('+URL+')','tool_calls':[]}),
                 ('llm.ai.response',{'content':'','tool_calls':[{'name':'bash','id':'send','args':{'command':"requests.post('https://example.invalid/open-apis/bot/v2/hook/"+secret+"',json={})"}}]})]
        rows=[]
        for index,(kind,obj) in enumerate(objects):
            raw=json.dumps(obj);rows.append({'seq':index+1,'event_type':kind,'created_at':now,'bytes':len(raw.encode()),'content':raw})
        result=scope['event_summary'](rows,THREAD)
        self.assertEqual(result['reader_and_send_evidence'][0]['reader_urls'],[URL])
        self.assertTrue(result['reader_and_send_evidence'][1]['http_send_proposals'][0]['custom_robot_webhook_literal_present'])
        self.assertNotIn(secret,json.dumps(result))

    def test_native_work_schedule_flag_is_selected_without_secret_payload(self):
        sql=scope['SCHEDULE_SQL']
        self.assertIn("'run_context'->'work_notification'",sql)
        self.assertNotIn('ciphertext',sql)

    def test_mutating_queries_blocked_before_subprocess(self):
        inspector=edge['Inspector']()
        with patch.object(edge['subprocess'],'run') as run:
            for args in [['patch','deployment','nginx'],['get','secret','deer-flow-env','-o','json'],['exec','unknown','--','bash']]:
                with self.assertRaises(edge['Refused']):inspector.query(args)
        run.assert_not_called()

    def test_non_pod_endpoint_cannot_select_arbitrary_workload(self):
        inspector=edge['Inspector']()
        listing={'items':[{'metadata':{'labels':{'kubernetes.io/service-name':'nginx'}},'endpoints':[{'conditions':{'ready':True},'targetRef':{'kind':'Service','name':'other','uid':'x'}}]}]}
        with patch.object(inspector,'query',return_value=listing):
            with self.assertRaisesRegex(edge['Refused'],'POD_ENDPOINT_IDENTITY_REQUIRED'):inspector.endpoints('nginx')

    def test_foreign_namespace_endpoint_is_rejected(self):
        inspector=edge['Inspector']()
        listing={'items':[{'metadata':{'labels':{'kubernetes.io/service-name':'nginx'}},'endpoints':[{'conditions':{'ready':True},'targetRef':{'kind':'Pod','namespace':'other','name':'nginx','uid':'x'}}]}]}
        with patch.object(inspector,'query',return_value=listing):
            with self.assertRaises(edge['Refused']):inspector.endpoints('nginx')


if __name__=='__main__':unittest.main()
