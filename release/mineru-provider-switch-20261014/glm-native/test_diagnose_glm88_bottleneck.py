import ast
import datetime
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
from contextlib import ExitStack

import diagnose_glm88_bottleneck as m


class BottleneckTests(unittest.TestCase):
    def test_time_offsets_and_no_arbitrary_timestamp_export(self):
        self.assertEqual(m.timestamp('2026-10-17T14:52:00+08:00 x'), '2026-10-17T06:52:00+00:00')
        self.assertIsNone(m.timestamp('secret filename'))

    def test_quarantine_is_history_not_current_membership(self):
        raw = ('2026-10-17T14:52:00+08:00 MinerU dispatcher quarantined backend=10.0.0.1 '
               'cooldown_seconds=30.0 reason=ReadTimeout\n'
               '2026-10-17T06:53:00Z MinerU dispatcher quarantined backend=10.0.0.1 '
               'cooldown_seconds=30.0 reason=SECRET PASSWORD\n'
               'MinerU dispatcher quarantined backend=10.0.0.2 cooldown_seconds=30.0 reason=HTTP 503\n').encode()
        row=m.quarantine_counts(raw, {'10.0.0.1':'worker-1'})
        self.assertEqual(row['matched_events'],3)
        self.assertEqual(row['unmapped_events'],1)
        self.assertFalse(row['proves_current_membership'])
        self.assertEqual(row['workers'][0]['reason_counts'],{'ReadTimeout':1,'UNCLASSIFIED':1})
        for secret in ('SECRET','PASSWORD','10.0.0.1'): self.assertNotIn(secret,json.dumps(row))

    def test_error_classification_does_not_export_messages(self):
        row=m.error_counts(b'Enterprise MinerU task PRIVATE_ID exceeded 300 seconds\n'
            b'LibreOffice timed out after 30s converting PRIVATE_FILE\n'
            b'[ImageMultimodal] Caption failed for SECRET: enterprise GLM: HTTP status 503\n'
            b'[ImageMultimodal] OCR failed for SECRET: enterprise GLM: context deadline exceeded\n')
        self.assertEqual(row['mineru_async_deadline'],1)
        self.assertEqual(row['libreoffice_deadline'],1)
        self.assertEqual(row['glm_http_503'],1)
        self.assertEqual(row['glm_deadline'],1)
        for word in ('PRIVATE','SECRET'): self.assertNotIn(word,json.dumps(row))

    def test_model_parser_includes_glm_without_mutating_old_ids(self):
        raw=('T\t1800000000\n'+''.join('M\t%s\t%d\n'%(v,i) for i,v in enumerate(m.MODELS))+'END\n').encode()
        self.assertEqual(m.parse_models(raw)['holders']['deerflow-cnooc-glm-5.3-flash'],3)
        for bad in (raw.replace(b'\t3\n',b'\t-3\n'),raw.replace(b'END\n',b''),raw+ b'PRIVATE\n'):
            with self.assertRaises(RuntimeError): m.parse_models(bad)

    def test_model_shell_only_reads_leases(self):
        for text in ('ZCOUNT','TIME','REDISCLI_AUTH','deerflow-cnooc-glm-5.3-flash'):
            self.assertIn(text,m.MODEL_SHELL)
        for text in ('EVAL','ZREMRANGE','SCAN','GETALL','redis-cli -a'):
            self.assertNotIn(text,m.MODEL_SHELL)

    def test_log_window_limits_and_no_fabricated_call_timings(self):
        row=m.coverage(b'2026-10-17T06:52:00Z first\n2026-10-17T06:53:00Z last\n',10,1000)
        self.assertFalse(row['byte_limit_reached'])
        self.assertEqual(row['first_retained_utc'],'2026-10-17T06:52:00+00:00')
        self.assertFalse(row['complete_history_proven'])
        self.assertEqual(m.new_report()['separate_glm_vs_embedding_wall_time'],'NOT_INSTRUMENTED')

    def test_resource_attribution_uses_cpu_cores_not_percent_and_no_args(self):
        now=datetime.datetime.now(datetime.timezone.utc).isoformat()
        data={'node':{'cpu':{'time':now,'usageNanoCores':15_000_000_000}},'pods':[
            {'podRef':{'name':'other-work','namespace':'deer-flow'},'cpu':{'time':now,'usageNanoCores':14_000_000_000}},
            {'podRef':{'name':'mineru-worker','namespace':'deer-flow'},'cpu':{'time':now,'usageNanoCores':100_000_000}},
            {'podRef':{'name':'unknown','namespace':'deer-flow'},'cpu':{}}]}
        out=m.node_projection('ecs-zdz-pro-0011',data,{'status':{'allocatable':{'cpu':'32000m'}}})
        self.assertEqual(out['allocatable_cpu_cores'],32)
        self.assertEqual(out['node_cpu_cores'],15)
        self.assertEqual(out['top_pods'][0]['cpu_cores'],14)
        self.assertEqual(out['unavailable_or_stale_pod_cpu'],1)
        self.assertNotIn('unattributed_host_cpu',out)

    def test_stale_node_metric_not_false_idle(self):
        with self.assertRaisesRegex(RuntimeError,'stale_node_cpu'):
            m.node_projection('ecs-zdz-pro-0011',{'node':{'cpu':{'time':'2000-01-01T00:00:00Z','usageNanoCores':0}}},
                              {'status':{'allocatable':{'cpu':'32'}}})

    def test_timeout_probe_only_explicit_safe_keys(self):
        self.assertNotIn('KEY',m.TIMEOUT_PROBE)
        self.assertNotIn('TOKEN',m.TIMEOUT_PROBE)
        self.assertNotIn('dict(os.environ)',m.TIMEOUT_PROBE)
        row=m.clean_timeouts({k:None for k in m.TIMEOUT_KEYS})
        self.assertTrue(all(v is None for v in row.values()))
        with self.assertRaises(RuntimeError): m.clean_timeouts({'PRIVATE':'secret'})

    def test_prior_warning_receipt_and_current88_bindings(self):
        with patch.object(m.o,'safe_read',return_value=json.dumps(dict(baseline_stable=True,
             observation='COMPLETE_WITH_WARNINGS',sample_count=11,mineru={'quarantined_max':1})).encode()) as read:
            m.prior_receipt()
            read.assert_called_once_with(m.PRIOR,m.PRIOR_SHA)
        with patch.object(m.o,'safe_read',return_value=b'{}'),self.assertRaises(RuntimeError): m.prior_receipt()

    def test_no_unavailable_read_interpreted_as_zero(self):
        out=m.finish_summary(m.new_report())
        self.assertIsNone(out['model_holders'])
        self.assertIsNone(out['latest_queues'])
        self.assertFalse(out['ready_to_expand'])

    def test_all_writes_are_evidence_not_production_mutators(self):
        tree=ast.parse(Path(m.__file__).read_text(),feature_version=(3,7))
        forbidden={'signal_once','drain','apply_after_drain','advance','accept','mutate','kill',
                   'send_signal','patch_call','dry_run','wait_drain','prepare','database_state'}
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                self.assertNotIn(node.func.attr,forbidden)
        self.assertEqual((m.SAMPLES-1)*m.INTERVAL,60)

    def test_status_does_not_initialize_or_query_live(self):
        with patch.object(m,'host_gate'),patch.object(m.o,'safe_read',return_value=b'{"stored":true}'), \
             patch.object(m,'initialize',side_effect=AssertionError('live')),patch.object(m,'emit') as emit:
            self.assertEqual(m.main(['--status','/var/lib/weknora-glm88-diagnostic-abcdefgh']),0)
            emit.assert_called_once_with({'stored':True})

    def test_status_rejects_traversal(self):
        with patch.object(m,'host_gate'),self.assertRaises(RuntimeError): m.main(['--status','/tmp/secret'])

    def fixtures(self,stack):
        app={'metadata':{'name':'weknora-app-current','uid':m.o.POD_UID}}
        workers=[{'metadata':{'name':'worker-%d'%i},'status':{'podIP':'10.0.0.%d'%(i+1)},
                  'spec':{'nodeName':'ecs-zdz-pro-0011'}} for i in range(16)]
        dispatcher={'metadata':{'name':'dispatcher'}}
        signature={'pods':{'weknora-app':[{'uid':m.o.POD_UID}]}}
        obs=Mock(WORKERS=['workers'],DISPATCHER='dispatcher',ThreadPoolExecutor=ThreadPoolExecutor)
        obs.identity.return_value={'uid':m.o.POD_UID}
        obs.baseline.return_value=({}, {'workers':workers,'dispatcher':[dispatcher]},signature,{})
        obs.utc.return_value='2026-10-17T07:10:00Z'
        obs.health.return_value={'role':'dispatcher','counters':{'quarantined':1}}
        obs.interpret.return_value={'all_queue_trends':{'default':{'samples':5}}}
        engine=Mock(); engine.review.pg_binding.return_value=({'metadata':{'name':'pg'}},{'uid':'pg'})
        engine.redis_binding.return_value=({},'0','host')
        engine.snapshot.return_value={'queues':{},'effective_servers':[]}
        r=Mock(obs=obs,e=engine,TARGET={'core':16,'enrichment':48})
        r.sw.same.side_effect=lambda a,b:a==b
        r.sw.recovery.safe_error.return_value='SAFE_DIAGNOSTIC_REQUIRED'
        s=Mock(); s.stored_plan.return_value=({}, {})
        d=Mock(); d.span_snapshot.return_value={'groups':[]}; d.database_waits.return_value={}
        q=Mock(); q.remote_health.return_value=[]
        stack.enter_context(patch.object(m.o,'r',r)); stack.enter_context(patch.object(m.o,'s',s))
        stack.enter_context(patch.object(m,'d',d)); stack.enter_context(patch.object(m,'q',q))
        receipt=dict(glm88_switch='PASS',pod_uid=m.o.POD_UID,effective_pools=r.TARGET,
                     model_governor_startup_limit=128,database='GLM',mineru_slots=16)
        stack.enter_context(patch.object(m.o,'safe_read',return_value=json.dumps(receipt).encode()))
        stack.enter_context(patch.object(m,'prior_receipt',return_value={}))
        current=stack.enter_context(patch.object(m.o,'current',return_value=({},app)))
        for method in ('logs','runtime_timeouts','node_attribution'):
            stack.enter_context(patch.object(m,method,return_value={}))
        stack.enter_context(patch.object(m,'model_snapshot',return_value={'holders':{k:3 for k in m.MODELS}}))
        stack.enter_context(patch.object(m.time,'sleep'))
        stack.enter_context(patch.object(m,'emit'))
        return r,s,d,q,current

    def test_complete_lifecycle_only_bounded_readers(self):
        with ExitStack() as stack:
            r,s,d,q,current=self.fixtures(stack); report=m.new_report()
            m.collect(Path('/private-evidence'),report)
            result=m.finish_summary(report)
            self.assertEqual(result['diagnostic'],'COMPLETE')
            self.assertTrue(result['baseline_stable'])
            self.assertEqual(len(result['samples']),5)
            self.assertEqual(d.span_snapshot.call_count,2)
            self.assertEqual(q.remote_health.call_count,5)
            self.assertEqual(r.e.snapshot.call_count,5)
            self.assertEqual(r.obs.write_new.call_count,6)
            self.assertEqual(result['model_holders'][m.MODELS[-1]]['max'],3)
            self.assertEqual(result['queue_trends']['default']['samples'],5)
            self.assertFalse(result['ready_to_expand'])
            self.assertEqual({call[0] for call in s.mock_calls},{'stored_plan'})
            self.assertEqual({call[0] for call in r.e.mock_calls},{'runtime_env','review.pg_binding','redis_binding',
                                                                  'snapshot','check_capacities'})

    def test_schema_failure_is_unknown_not_zero_and_collection_continues(self):
        with ExitStack() as stack:
            r,s,d,q,current=self.fixtures(stack); d.span_snapshot.side_effect=RuntimeError('PRIVATE ERROR')
            report=m.new_report(); m.collect(Path('/private-evidence'),report)
            self.assertEqual(report['diagnostic'],'COMPLETE_WITH_UNAVAILABLE_CHECKS')
            self.assertIsNone(report['spans'][0]['statistics'])
            self.assertEqual(len(report['samples']),5)
            self.assertNotIn('PRIVATE ERROR',json.dumps(report))

    def test_mid_sample_pod_change_invalidates_comparison(self):
        with ExitStack() as stack:
            r,s,d,q,current=self.fixtures(stack)
            initial=current.return_value
            current.side_effect=[initial,initial,RuntimeError('accepted_glm88_pod_changed')]
            report=m.new_report()
            with self.assertRaisesRegex(RuntimeError,'accepted_glm88_pod_changed'):
                m.collect(Path('/private-evidence'),report)
            self.assertEqual(len(report['samples']),1)
            self.assertEqual(report['diagnostic'],'INCOMPLETE')
            self.assertNotIn('baseline_stable',report)

    def test_dependency_drift_after_collection_blocks_conclusion(self):
        with ExitStack() as stack:
            r,s,d,q,current=self.fixtures(stack)
            initial=r.obs.baseline.return_value
            r.obs.baseline.side_effect=[initial,({}, {},{'pods':{}},{})]
            report=m.new_report()
            with self.assertRaisesRegex(RuntimeError,'dependent_workload_changed'):
                m.collect(Path('/private-evidence'),report)
            self.assertEqual(report['diagnostic'],'INCOMPLETE')
            self.assertEqual(len(report['samples']),5)

    def test_incomplete_report_no_false_model_headroom(self):
        with ExitStack() as stack:
            r,s,d,q,current=self.fixtures(stack)
            with patch.object(m,'model_snapshot',side_effect=RuntimeError('unavailable')):
                report=m.new_report(); m.collect(Path('/private-evidence'),report)
            self.assertIsNone(m.finish_summary(report)['model_holders'])
            self.assertEqual(report['diagnostic'],'COMPLETE_WITH_UNAVAILABLE_CHECKS')


if __name__=='__main__': unittest.main()
