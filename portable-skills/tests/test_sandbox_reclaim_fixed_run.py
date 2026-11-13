"""Fixed operator-named run: bounded metadata/events, no raw task content."""
import asyncio
import datetime
import importlib.util
import json
from pathlib import Path
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'deployment/reclaim-plan-20261112-r1/inspect_reclaim_child.py'
spec=importlib.util.spec_from_file_location('reclaim_diag_child',SOURCE);child=importlib.util.module_from_spec(spec);spec.loader.exec_module(child)
NOW=datetime.datetime(2026,11,12,3,52,tzinfo=datetime.timezone.utc)

def event(seq,kind,obj):
    raw=json.dumps(obj);return {'seq':seq,'event_type':kind,'created_at':NOW,'bytes':len(raw.encode()),'content':raw}

def events(skill='energy-market-daily'):
    command='python3 /mnt/skills/public/'+skill+'/scripts/'+('daily.py prepare' if skill=='energy-market-daily' else 'entry.py check')+' --workspace /mnt/user-data/workspace/private-project'
    return [event(1,'llm.ai.response',{'tool_calls':[{'id':'call-1','name':'bash','args':{'command':command}}],'content':'private-prose'}),
        event(2,'llm.tool.result',{'name':'bash','tool_call_id':'call-1','status':'success','content':'Exit code: 0\n'+json.dumps({'status':'READY_FOR_NEWS_SUMMARIES','market_complete':True,'private':'private-token'})}),
        event(3,'run.error',{'content':'GraphRecursionError: Recursion limit of 300 reached without hitting a stop condition. private-token'})]

class EventTests(unittest.TestCase):
    def test_proposals_results_and_recursion_are_separate(self):
        result=child.diag_events(events());self.assertEqual(result['actual_tool_result_count'],1);self.assertEqual(result['proposed_call_count_in_selected_window'],1)
        self.assertTrue(result['events'][1]['owning_call_present_in_selected_window']);self.assertEqual(result['events'][1]['receipt']['exit_codes'],[0])
        self.assertEqual(result['events'][2]['error_signals']['recursion_limit_values'],[300])
        self.assertNotIn('private-token',json.dumps(result));self.assertNotIn('private-project',json.dumps(result));self.assertNotIn('private-prose',json.dumps(result))
    def test_other_skill_is_not_forced_into_daily_diagnosis(self):
        result=child.diag_events(events('research-fund-settlement'))
        self.assertEqual(result['task_skill_confirmation'],'NOT_CONFIRMED_DAILY_SKILL');self.assertIn('research-fund-settlement',result['observed_known_skill_markers'])
        self.assertEqual(result['events'][0]['proposed_calls'][0]['script_verbs'],['check'])
    def test_sql_budgets_limit_before_returning_content(self):
        self.assertIn('LIMIT 8',child.DIAG_EVENT_SQL);self.assertIn('LIMIT 24',child.DIAG_EVENT_SQL);self.assertIn('LIMIT 32',child.DIAG_EVENT_SQL)
        self.assertIn('b.bytes<=8192 AND b.cumulative_bytes<=63488',child.DIAG_EVENT_SQL);self.assertEqual(63488+2048,65536)
        self.assertNotIn('checkpoint',child.DIAG_EVENT_SQL);self.assertNotIn('human_message',child.DIAG_KINDS)
    def test_missing_result_cannot_be_counted_as_execution(self):
        result=child.diag_events(events()[:1]);self.assertEqual(result['actual_tool_result_count'],0);self.assertEqual(result['proposed_call_count_in_selected_window'],1)
    def test_non_scalar_nested_status_is_skipped_without_returning_content(self):
        value={'status':{'private':'private-token'},'stdout':'Exit code: 1'}
        result=child.diag_receipt(value);self.assertEqual(result['exit_codes'],[1]);self.assertNotIn('private-token',json.dumps(result))

class DatabaseTests(unittest.TestCase):
    def run_diagnostic(self,mapped=False):
        log=[]
        class Tx:
            async def __aenter__(self):pass
            async def __aexit__(self,*args):return False
        class Conn:
            def transaction(self,**kwargs):log.append(('transaction',kwargs));return Tx()
            async def execute(self,sql):log.append(('execute',sql))
            async def fetchval(self,sql):return 'on'
            async def fetch(self,sql,*args):
                log.append(('fetch',sql,args))
                if sql==child.DIAG_RUN_SQL:
                    if mapped and args[0]==child.FIXED_DIAGNOSTIC_ID:return []
                    return [{'run_id':'resolved-run','thread_id':'synthetic-private-thread','status':'error','created_at':NOW,'updated_at':NOW,'stop_reason':'recursion_capped','message_count':38,'error_bytes':80,
                        'error':'GraphRecursionError: Recursion limit of 300 reached private-token','configured_recursion_limit':'300'}]
                if sql==child.DIAG_OCCURRENCE_ID_SQL:return []
                if sql==child.DIAG_OCCURRENCE_TASK_SQL:return [{'run_id':'resolved-run','status':'failed','created_at':NOW,'started_at':NOW,'finished_at':NOW}]
                if sql==child.DIAG_EVENT_SQL:return events()
                raise AssertionError('unapproved query')
        return asyncio.run(child.diagnose_fixed_run(Conn())),log
    def test_exact_run_primary_lookup_and_scalar_budget(self):
        result,log=self.run_diagnostic();self.assertEqual(result['status'],'FIXED_RUN_BOUNDED_DIAGNOSTIC_OBSERVED_NO_CHANGE')
        self.assertEqual(result['requested_identifier'],child.FIXED_DIAGNOSTIC_ID);self.assertEqual(result['id_mapping_basis'],'EXACT_RUN_ID')
        self.assertEqual(result['run']['projected_config_recursion_limit'],300);self.assertEqual(result['run']['message_count'],38)
        self.assertNotIn('private-thread',json.dumps(result));self.assertNotIn('private-token',json.dumps(result))
        self.assertFalse(any(row[0]=='fetch' and row[1]==child.DIAG_OCCURRENCE_TASK_SQL for row in log))
    def test_latest_task_mapping_has_explicit_as_of_limitation(self):
        result,log=self.run_diagnostic(mapped=True);self.assertEqual(result['id_mapping_basis'],'TASK_LATEST_OCCURRENCE_AS_OF_SNAPSHOT')
        self.assertTrue(result['latest_mapping_not_proof_of_requested_1152_occurrence']);self.assertIn('finished_at',result['occurrence'])

if __name__=='__main__':unittest.main()
