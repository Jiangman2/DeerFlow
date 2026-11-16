"""Offline privacy, scope and formatting-replay checks for the new inspector."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('work_return_sealer',HERE/'seal.py')
seal=importlib.util.module_from_spec(spec);spec.loader.exec_module(seal)
HOST,CHILD=seal.build()
host={'__name__':'host_inspection_test'};exec(compile(HOST,'<host>','exec'),host)
child={'__name__':'child_inspection_test'};exec(compile(CHILD,'<child>','exec'),child)
THREAD=child['THREAD'];ORIGIN='https://cnoocclaw.cnooc.com.cn'
READER=ORIGIN+'/reports/energy-daily/'+THREAD+'/2026-11-15.html'

def scoped_formatter():
    import datetime,re
    from urllib.parse import urlsplit
    from zoneinfo import ZoneInfo
    namespace={'re':re,'datetime':datetime.datetime,'date':datetime.date,'UTC':datetime.timezone.utc,'urlsplit':urlsplit,
               'ZoneInfo':ZoneInfo,'validate_thread_id':lambda value:value}
    source=HERE.parent/'energy-reader-delivery-repair-20261115-r2/backend/app/scheduler'
    child['extracted']((source/'work_notifications.py').read_text(encoding='utf-8'),{'_notification_summary'},namespace)
    child['extracted']((source/'work_notification_delivery.py').read_text(encoding='utf-8'),{'_daily_reader_url','_message'},namespace)
    return namespace

class WorkInspectionTests(unittest.TestCase):
    def test_sealed_exact_thread_and_python37_parse(self):
        self.assertEqual(host['THREAD'],THREAD);self.assertEqual(THREAD,'aa98356f-e332-4019-9d68-0d1e79c30ed7')
        for raw in (HOST,CHILD):ast.parse(raw.decode(),feature_version=(3,7))

    def test_source_manifest_is_exact_qualified_two_files(self):
        self.assertEqual(set(child['EXPECTED_SOURCES']),{'app/scheduler/work_notification_delivery.py','app/scheduler/work_notifications.py'})

    def test_host_rejects_mutations_credentials_and_other_task_before_subprocess(self):
        op=host['Inspection']('synthetic child');op.pod='gateway-test';op.rs='gateway-rs'
        commands=[(['patch','deployment','gateway'],None),(['get','secrets','deer-flow-env'],None),
                  (['exec','gateway-test','--','bash'],None),
                  (['exec','-i','gateway-test','-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c','synthetic child'],b'{"thread_id":"other"}')]
        with patch.object(host['subprocess'],'Popen') as process:
            for args,body in commands:
                with self.assertRaises(host['Refused']):op.command(args,body)
        process.assert_not_called();self.assertEqual(op.queries,0)

    def test_child_blocks_provider_network_file_writes_and_locks(self):
        for event,args in [('socket.connect',(None,('1.1.1.1',443))),('open',('file','w',0)),('subprocess.Popen',()),('fcntl.flock',())]:
            with self.assertRaises(child['Refused']):child['audit'](event,args)
        child['audit']('open',('file','r',0))

    def test_standalone_link_replay_is_sendable_without_exporting_body(self):
        value='油气与金融市场日报\nPRIVATE_REPORT_BODY\n[点击查看日报网页]('+READER+')'
        result=child['formatting_summary'](value,THREAD,scoped_formatter(),ORIGIN)
        self.assertFalse(result['notification_would_be_empty']);self.assertTrue(result['notification_is_link_only'])
        self.assertEqual(result['valid_current_reader_urls'],[READER])
        self.assertNotIn('PRIVATE_REPORT_BODY',json.dumps(result))

    def test_punctuation_can_be_identified_as_pre_send_formatting_block(self):
        value='油气与金融市场日报\n[点击查看日报网页]('+READER+')。'
        result=child['formatting_summary'](value,THREAD,scoped_formatter(),ORIGIN)
        self.assertTrue(result['notification_would_be_empty']);self.assertEqual(result['standalone_reader_lines'],0)
        self.assertEqual(result['valid_current_reader_urls'],[READER])

    def test_empty_result_and_wrong_thread_not_claimed_sendable(self):
        result=child['formatting_summary']('',THREAD,scoped_formatter(),ORIGIN)
        self.assertFalse(result['text_present'])
        bad='油气与金融市场日报\n[点击查看日报网页]('+READER.replace(THREAD,'other-thread')+')'
        self.assertTrue(child['formatting_summary'](bad,THREAD,scoped_formatter(),ORIGIN)['notification_would_be_empty'])

    def test_signed_external_urls_and_secret_text_never_exported(self):
        value='油气与金融市场日报\n[点击查看日报网页]('+READER+'?token=PRIVATE)\nhttps://evil.invalid/PRIVATE'
        result=child['formatting_summary'](value,THREAD,scoped_formatter(),ORIGIN)
        self.assertEqual(result['valid_current_reader_urls'],[])
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('evil.invalid',json.dumps(result))

    def test_sql_is_readonly_bound_and_does_not_retrieve_ciphertext_or_credentials(self):
        for name in ('RUN_SQL','OCCURRENCE_SQL','TASK_SQL','OUTBOX_SQL','CONNECTION_SQL','EVENT_SQL'):
            sql=child[name]
            self.assertTrue(sql.lstrip().startswith('SELECT'));self.assertIn('$1',sql)
            self.assertNotIn('channel_credentials',sql)
        self.assertIn('octet_length(ciphertext)>0',child['OUTBOX_SQL'])
        self.assertNotIn('SELECT ciphertext',child['OUTBOX_SQL'])
        self.assertIn("->'_work_notification_target'",child['CONNECTION_SQL'])

    def test_wrong_host_main_is_zero_query_and_zero_send(self):
        with patch.object(host['sys'],'argv',['inspector','--inspect-readonly']),patch.object(host['os'],'getuid',return_value=123,create=True):
            with patch.object(host['subprocess'],'Popen') as process,patch('builtins.print') as display:
                code=host['main']()
        self.assertEqual(code,1);process.assert_not_called()
        result=json.loads(display.call_args.args[0]);self.assertEqual(result['queries'],0);self.assertEqual(result['work_messages_sent'],0)

if __name__=='__main__':unittest.main()
