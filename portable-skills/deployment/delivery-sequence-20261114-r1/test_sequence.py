"""Offline checks; no database, network or production task is executed."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
spec=importlib.util.spec_from_file_location('sequence_child',HERE/'inspect_delivery_child.py')
child=importlib.util.module_from_spec(spec);spec.loader.exec_module(child)
spec=importlib.util.spec_from_file_location('sequence_sealer',HERE/'seal.py')
seal=importlib.util.module_from_spec(spec);spec.loader.exec_module(seal)

# Re-run inherited safety tests against the new fixed child, without changing
# historical test source. Only explicit event-window assertions change.
historical=ROOT/'portable-skills/tests/test_energy_webhook_delivery_child.py'
source=historical.read_text(encoding='utf-8').replace('deployment/webhook-budget-20261112-r1/inspect_delivery_child.py',
    'deployment/delivery-sequence-20261114-r1/inspect_delivery_child.py')
source=source.replace("'LIMIT 32'","'LIMIT 64'").replace("'LIMIT 40'","'LIMIT 72'")
source=source.replace('b.bytes<=16384 AND b.cumulative_bytes<=131072','b.bytes<=32768 AND b.cumulative_bytes<=524288')
source=source.replace('b.bytes>16384 AND b.cumulative_bytes<=131072','b.bytes>32768 AND b.cumulative_bytes<=524288')
inherited=types.ModuleType('sequence_inherited_tests');inherited.__file__=str(historical)
exec(compile(source,str(historical),'exec'),inherited.__dict__)


class SequenceTests(unittest.TestCase):
    def test_final_text_receipt_flag_and_no_receipt_are_distinct(self):
        for suffix,expected in (('',False),(' --publication-receipt /tmp/p/delivery/host-publication.json',True)):
            result=child.command_summary({'id':'a','name':'bash','args':{'command':'python /mnt/skills/public/energy-market-daily/scripts/daily.py final-text --workspace /tmp/p'+suffix}})
            self.assertEqual(result['daily_actions'],['final-text'])
            self.assertIs(result['final_text_publication_receipt_flag_present'],expected)

    def test_host_receipt_write_shape_and_link_without_raw_content(self):
        content=json.dumps({'name':'present_files','status':'success','tool_call_id':'secret-call',
            'content':'Successfully presented files\nconversation_url: '+inherited.URL,'requested_files':['secret-file']})
        result=child.command_summary({'id':'a','name':'write_file','args':{'path':'/tmp/p/delivery/host-publication.json','content':content}})
        self.assertTrue(result['host_receipt_write_proposed'])
        self.assertTrue(result['proposed_receipt_signals']['current_thread_url_present'])
        self.assertEqual(result['proposed_receipt_shape']['requested_files_count'],1)
        encoded=json.dumps(result)
        for secret in (inherited.URL,'secret-call','secret-file','/tmp/p'):
            self.assertNotIn(secret,encoded)

    def test_final_text_read_and_file_mention(self):
        result=child.command_summary({'name':'read_file','args':{'file_path':'/tmp/p/delivery/final.md'}})
        self.assertTrue(result['final_delivery_text_read_proposed'])
        result=child.command_summary({'name':'bash','args':{'command':"text=open('/tmp/p/delivery/final.md').read()"}})
        self.assertTrue(result['final_delivery_file_mentioned_in_command'])

    def test_unconfirmed_and_supplement_markers_are_separate(self):
        first=child.signals('日报文件已生成，附件发布状态尚未确认。\n当前平台未返回可用的会话链接。')
        second=child.signals('补充：已发布\n'+inherited.URL)
        self.assertTrue(first['publication_unconfirmed_marker_present'])
        self.assertTrue(first['conversation_unavailable_marker_present'])
        self.assertFalse(first['current_thread_url_present'])
        self.assertTrue(second['supplement_marker_present']);self.assertTrue(second['current_thread_url_present'])

    def test_final_text_structured_receipt_keeps_only_status_flags(self):
        result=child.receipt_summary(json.dumps({'status':'FINAL_TEXT_READY','conversation_link_available':False,
          'publication_confirmed_by_caller_projection':False,'final_text_file':'/private/file'}))
        self.assertEqual(result['structured_receipts'][0]['status'],'FINAL_TEXT_READY')
        self.assertIs(result['structured_receipts'][0]['conversation_link_available'],False)
        self.assertNotIn('/private/file',json.dumps(result))

    def test_sequence_pairs_real_receipt_not_proposed_command(self):
        calls=[{'id':'build','name':'bash','args':{'command':'python /s/daily.py build --workspace /p'}},
               {'id':'final','name':'bash','args':{'command':'python /s/daily.py final-text --workspace /p'}},
               {'id':'publish','name':'present_files','args':{}}]
        rows=[inherited.event(1,'llm.ai.response',{'tool_calls':[calls[0]]}),
              inherited.event(2,'llm.tool.result',{'name':'bash','tool_call_id':'build','status':'success','content':'{"status":"REPORT_GENERATED_VISUAL_REVIEW_REQUIRED"}'}),
              inherited.event(3,'llm.ai.response',{'tool_calls':[calls[1]]}),
              inherited.event(4,'llm.ai.response',{'tool_calls':[calls[2]]}),
              inherited.event(5,'llm.tool.result',{'name':'present_files','tool_call_id':'publish','status':'success','content':'Successfully presented files\nconversation_url: '+inherited.URL})]
        timeline=child.summarize_events(rows)['delivery_action_timeline']
        self.assertEqual([x['actions'] for x in timeline],[['build'],['final-text'],['present_files']])
        self.assertTrue(timeline[0]['result_observed']);self.assertFalse(timeline[1]['result_observed'])
        self.assertTrue(timeline[2]['result_signals']['current_thread_url_present'])
        self.assertTrue(all(x['tool_result_not_business_delivery_confirmation'] for x in timeline))

    def test_sealed_host_fixed_scope_and_asset_identity(self):
        raw,code=seal.build();scope={'__name__':'not_main'}
        exec(compile(raw,'<sealed>','exec'),scope)
        self.assertEqual(scope['assets'](),code.decode())
        self.assertEqual(scope['THREAD'],child.THREAD)
        ast.parse(raw.decode(),feature_version=(3,7))
        op=scope['Operation'](code.decode())
        for args in (['get','secrets','-o','json'],['delete','pods','x'],['patch','deployment','gateway']):
            with self.assertRaises(scope['Refused']):op.command(args)
        self.assertEqual(op.queries,0)

    def test_source_hash_paths_fixed_and_no_import_to_read(self):
        with patch.object(child.Path,'stat',side_effect=FileNotFoundError),patch.object(child.Path,'is_symlink',return_value=False):
            result=child.fixed_source_hashes()
        self.assertEqual(len(result),3)
        self.assertTrue(all(row=={'present':False} for row in result.values()))
        self.assertTrue(all(name.startswith('packages/harness/deerflow/') for name in result))


def load_tests(loader,tests,pattern):
    return unittest.TestSuite([loader.loadTestsFromModule(inherited),loader.loadTestsFromTestCase(SequenceTests)])


if __name__=='__main__':unittest.main()
