"""Check queue evidence interpretation, privacy and read-only boundaries."""
import ast
import asyncio
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('queue_seal',HERE/'seal.py')
seal=importlib.util.module_from_spec(spec);spec.loader.exec_module(seal)
HOST,CHILD=seal.build()
host={'__name__':'host_test'};exec(compile(HOST,'<host>','exec'),host)
child={'__name__':'child_test'};exec(compile(CHILD,'<child>','exec'),child)
NOW=dt.datetime(2026,11,16,2,tzinfo=dt.timezone.utc)
CONFIG={'enabled':True,'max_concurrent_runs':3,'queue_timeout_seconds':3600}


def occurrence(**updates):
    value={'id':'PRIVATE_OCCURRENCE','task_id':'PRIVATE_TASK','thread_id':'PRIVATE_THREAD','run_id':None,
           'status':'queued','created_at':NOW-dt.timedelta(minutes=8),'started_at':None,'attempt_count':0,
           'launch_lease_expires_at':None,'underlying_status':None,'run_lease_expires_at':None,
           'run_updated_at':None,'run_owner_present':False,'error_kind':'NONE','trigger':'manual','finished_at':None,
           'older_same_thread':False,'thread_has_active_run':False}
    value.update(updates)
    return value


class QueueInspectionTests(unittest.TestCase):
    def test_sealed_python37_entry_and_same_fixed_title(self):
        for raw in (HOST,CHILD):ast.parse(raw.decode(),feature_version=(3,7))
        self.assertEqual(host['TITLE'],child['TITLE'])
        self.assertEqual(child['TITLE'],'油气与金融市场日报')

    def test_host_blocks_mutation_and_secrets_before_subprocess(self):
        op=host['Inspection']('child');op.pod='gateway-test'
        with patch.object(host['subprocess'],'Popen') as process:
            for args in (['patch','deployment','gateway'],['get','secrets'],['delete','pod','sandbox-test'],['exec','gateway-test','--','bash']):
                with self.assertRaises(host['Refused']):op.command(args)
        process.assert_not_called()
        self.assertEqual(op.queries,0)

    def test_child_blocks_non_db_network_process_and_file_writes(self):
        for event,args in [('socket.connect',(None,('1.1.1.1',443))),('open',('file','w',0)),('subprocess.Popen',()),('fcntl.flock',())]:
            with self.assertRaises(child['Refused']):child['audit'](event,args)

    def test_raw_identifiers_and_error_text_not_exported(self):
        row=child['occurrence_summary'](occurrence(),NOW)
        self.assertNotIn('PRIVATE_',json.dumps(row))
        self.assertEqual(row['age_seconds'],480)
        self.assertEqual(row['run_lease_state'],'UNKNOWN_OR_ABSENT')

    def test_capacity_limit_uses_fresh_actual_value_not_default_three(self):
        counts=[{'status':'running','count':3},{'status':'queued','count':2}]
        result=child['findings'](counts,[],[],dict(CONFIG,max_concurrent_runs=16))
        self.assertNotIn('GLOBAL_EXECUTING_COUNT_REACHES_FRESH_CONFIG_LIMIT',result['signals'])
        result=child['findings'](counts,[],[],CONFIG)
        self.assertIn('GLOBAL_EXECUTING_COUNT_REACHES_FRESH_CONFIG_LIMIT',result['signals'])
        self.assertTrue(result['fresh_config_not_loaded_scheduler_instance'])

    def test_stale_terminal_run_is_distinct_from_healthy_capacity(self):
        row=child['occurrence_summary'](occurrence(status='running',underlying_status='success'),NOW)
        result=child['findings']([{'status':'running','count':3}],[row],[],CONFIG)
        self.assertIn('EXECUTING_OCCURRENCE_HAS_TERMINAL_DURABLE_RUN',result['signals'])

    def test_unknown_lease_is_never_called_expired(self):
        row=child['occurrence_summary'](occurrence(status='running',underlying_status='running'),NOW)
        result=child['findings']([], [row],[],CONFIG)
        self.assertNotIn('RUNNING_OCCURRENCE_HAS_EXPIRED_RUN_LEASE',result['signals'])
        self.assertEqual(child['lease_state'](NOW+dt.timedelta(seconds=1),NOW),'NOT_EXPIRED')
        row['run_lease_state']='EXPIRED'
        self.assertIn('RUNNING_OCCURRENCE_HAS_EXPIRED_RUN_LEASE',child['findings']([], [row],[],CONFIG)['signals'])

    def test_overlap_and_queue_timeout_evidence_remain_separate(self):
        target=child['occurrence_summary'](occurrence(attempt_count=4,error_kind='OVERLAP_CONFLICT',created_at=NOW-dt.timedelta(hours=2)),NOW)
        target.update(older_same_thread=False,thread_has_active_run=True)
        result=child['findings']([],[],[target],CONFIG)
        self.assertIn('TARGET_LAUNCH_ATTEMPT_REQUEUED_FOR_OVERLAP',result['signals'])
        self.assertIn('TARGET_THREAD_HAS_EARLIER_OR_ACTIVE_RUN',result['signals'])
        self.assertIn('TARGET_QUEUE_OLDER_THAN_FRESH_CONFIG_TIMEOUT',result['signals'])

    def test_disabled_config_not_reported_as_live_poller_state(self):
        result=child['findings']([],[],[],dict(CONFIG,enabled=False))
        self.assertIn('FRESH_CONFIG_BACKGROUND_SCHEDULER_DISABLED',result['signals'])
        self.assertTrue(result['one_pod_observation_cannot_prove_fleet_poller_health'])

    def test_sql_only_reads_bound_metadata_never_messages(self):
        for key in ('COUNTS_SQL','ACTIVE_SQL','TASKS_SQL','TARGET_SQL'):
            source=child[key]
            self.assertTrue(source.lstrip().startswith('SELECT'));self.assertIn('$1',source)
            for forbidden in ('prompt','last_ai_message','ciphertext','FOR UPDATE','pg_advisory','run_events'):
                self.assertNotIn(forbidden,source)
        self.assertIn('LIMIT 64',child['ACTIVE_SQL']);self.assertIn('LIMIT 3',child['TARGET_SQL'])

    def test_ambiguous_title_never_reads_occurrence_detail(self):
        class Connection:
            async def fetchval(self,sql):return NOW
            async def fetch(self,sql,*params):
                if sql==child['COUNTS_SQL']:return []
                if sql==child['ACTIVE_SQL']:return []
                if sql==child['TASKS_SQL']:return [{'id':'a','user_id':'one'},{'id':'b','user_id':'two'}]
                raise AssertionError('Unscoped occurrence read')
        result=asyncio.run(child['inspect_database'](Connection(),CONFIG))
        self.assertEqual(result['targets'],[])
        self.assertEqual(result['task_scope'],'TITLE_MATCH_AMBIGUOUS_NO_TASK_DETAIL_READ')

    def test_two_same_owner_tasks_are_scoped_without_prompts(self):
        class Connection:
            async def fetchval(self,sql):return NOW
            async def fetch(self,sql,*params):
                if sql in (child['COUNTS_SQL'],child['ACTIVE_SQL']):return []
                if sql==child['TASKS_SQL']:
                    return [dict(id=str(index),user_id='private-owner',status='enabled',schedule_type=kind,context_mode='fresh_thread_per_run',run_count=0,
                                 next_run_at=NOW-dt.timedelta(hours=1),last_run_at=None,created_at=NOW,updated_at=NOW,lease_expires_at=None)
                            for index,kind in enumerate(('interval','cron'))]
                if sql==child['TARGET_SQL']:
                    assert params[0] in ('0','1')
                    return [occurrence(task_id=params[0])]
                raise AssertionError('Unexpected query')
        result=asyncio.run(child['inspect_database'](Connection(),CONFIG))
        self.assertEqual(len(result['targets']),2)
        self.assertTrue(all(row['next_run_is_past'] for row in result['targets']))
        self.assertNotIn('private-owner',json.dumps(result))

    def test_resource_projection_ignores_completed_and_unscheduled_pods(self):
        nodes=b'node-a 4 8Gi <none> True <none>\nnode-b 8 16Gi true True NoSchedule\n'
        pods=b'deer-flow node-a Running 750m,250m 1Gi,512Mi\ndeer-flow node-a Succeeded 2 4Gi\ndeer-flow <none> Pending 1 2Gi\n'
        result=host['resource_projection'](nodes,pods,['node-a'])
        row=result['nodes'][0]
        self.assertEqual(row['regular_container_cpu_requests'],'1.000')
        self.assertEqual(row['regular_container_memory_requests_bytes'],1536*1024**2)
        self.assertEqual(row['nonterminal_pods'],1)
        self.assertTrue(result['scheduler_admission_or_real_usage_not_proven'])
        self.assertIsNone(host['resource_projection'](nodes,pods,[])['nodes'][0]['in_provisioner_pool'])

    def test_wrong_host_zero_queries_no_process(self):
        with patch.dict(host,{'__name__':'guard_test'}),patch.object(host['sys'],'argv',['tool','--inspect-readonly']):
            with patch.object(host['os'],'name','nt'),patch.object(host['subprocess'],'Popen') as process,patch('builtins.print') as display:
                code=host['main']()
        self.assertEqual(code,1);process.assert_not_called()
        row=json.loads(display.call_args.args[0]);self.assertEqual(row['queries'],0)
        self.assertFalse(row['workload_modified'])


if __name__=='__main__':unittest.main()
