import ast
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import diagnose_glm104_mineru as d


class DiagnosticTests(unittest.TestCase):
    def summary(self, text, mapping=None):
        return d.aggregate(text.encode(), mapping or {'10.1.2.3':'worker-reviewed'}, '2026-10-17T07:45:00Z')

    def test_redaction(self):
        raw = ('2026-10-17T16:00:00.123456789+08:00 ERROR MinerU dispatcher quarantined '
               'backend=10.1.2.3 cooldown_seconds=30.0 reason=HTTP 502\n'
               '2026-10-17T08:00:00Z MinerU upstream request rejected: '
               'Enterprise MinerU HTTP 503 during task status: Bearer SECRET customer.pdf\n')
        row = self.summary(raw)
        value = json.dumps(row)
        for secret in ('SECRET','customer.pdf','10.1.2.3','Bearer'):
            self.assertNotIn(secret, value)
        self.assertEqual(row['quarantine_events'][0]['reason'], 'HTTP 502')
        self.assertEqual(row['upstream_codes'][0]['code'], '503')
        self.assertEqual(row['upstream_codes'][0]['operation'], 'task status')

    def test_unknown_reason_not_printed(self):
        row = self.summary('MinerU dispatcher quarantined backend=10.1.2.3 cooldown_seconds=1.0 reason=SECRET')
        self.assertEqual(row['quarantine_events'][0]['reason'], 'UNCLASSIFIED')
        self.assertNotIn('SECRET', json.dumps(row))

    def test_unmapped_not_current_membership(self):
        row = self.summary('MinerU dispatcher quarantined backend=10.9.9.9 cooldown_seconds=1.0 reason=HTTP 502')
        self.assertEqual(row['unmapped_quarantine_events'], 1)
        self.assertFalse(row['historical_events_prove_current_membership'])
        self.assertFalse(row['quarantine_events'])

    def test_business_only_code_and_operation(self):
        row = self.summary('Enterprise MinerU business error during task result; code=20006; msg=SECRET')
        self.assertEqual(row['upstream_codes'][0]['code'], '20006')
        self.assertNotIn('SECRET', json.dumps(row))

    def test_malformed_business_code_not_exported(self):
        row = self.summary('Enterprise MinerU business error during task result; code=SECRET; msg=SECRET')
        self.assertFalse(row['upstream_codes'])

    def test_bounds_and_no_success_inference(self):
        row = d.aggregate(b'x'*d.LIMIT, {}, 'x')
        self.assertTrue(row['coverage']['byte_limit_reached'])
        self.assertFalse(row['coverage']['complete_history_proven'])
        self.assertTrue(self.summary('\n'*d.TAIL)['coverage']['line_limit_reached'])

    def test_timestamp(self):
        self.assertEqual(d.stamp('2026-10-17T16:00:00.123456789+08:00 text'), '2026-10-17T08:00:00+00:00')
        self.assertEqual(d.stamp('2026-10-17T08:00:00Z text'), '2026-10-17T08:00:00+00:00')
        self.assertIsNone(d.stamp('2026-99-99T08:00:00Z text'))
        self.assertIsNone(d.stamp('unknown SECRET'))

    def test_quarantine_count_per_minute(self):
        line = '2026-10-17T08:00:00Z MinerU dispatcher quarantined backend=10.1.2.3 cooldown_seconds=1.0 reason=ReadTimeout\n'
        row = self.summary(line*2)
        self.assertEqual(row['quarantine_events_per_utc_minute'], {'2026-10-17T08:00Z':2})

    def test_error_markers_not_unique_failures(self):
        row = self.summary('ReadTimeout\nReadTimeout\nMinerU adapter request failed\nEnterprise MinerU asynchronous task failed: SECRET')
        self.assertEqual(row['error_marker_lines']['read_timeout'], 2)
        self.assertEqual(row['error_marker_lines']['async_task_failed'], 1)
        self.assertTrue(row['marker_counts_are_not_unique_failures'])

    def test_safe_failure(self):
        self.assertNotIn('SECRET', json.dumps(d.safe_error(RuntimeError('SECRET'))))
        self.assertEqual(d.safe_error(d.DiagnosticFailure('bound'))['check'], 'bound')

    def test_read_logs_flags(self):
        calls=[]
        def call(args, **kwargs):
            calls.append((args,kwargs)); return b''
        obs=types.SimpleNamespace(call=call, container=lambda p:{'name':'worker'},
            log_stats=lambda b:({},0), summary_stats=lambda b:{})
        row=d.logs(obs, {'metadata':{'name':'reviewed-worker'}}, {}, '2026-10-17T08:00:00Z', True)
        self.assertNotIn('unavailable', row)
        self.assertEqual(calls[0][0][0], 'logs')
        self.assertIn('--previous=true',calls[0][0])
        self.assertIn('--limit-bytes=524288',calls[0][0])

    def test_log_failure_no_retry_or_raw_error(self):
        calls=[]
        def call(*a, **kw):
            calls.append(1); raise RuntimeError('SECRET')
        obs=types.SimpleNamespace(call=call,container=lambda p:{'name':'worker'})
        row=d.logs(obs, {'metadata':{'name':'reviewed-worker'}}, {}, 'x')
        self.assertEqual(len(calls),1)
        self.assertNotIn('SECRET',json.dumps(row))

    def test_receipt(self):
        good=dict(live_patch_acknowledged=True,failed_check='mineru_16_not_healthy_for_expansion',glm104_switch='UNCONFIRMED')
        with patch.object(d,'safe_read',return_value=json.dumps(good).encode()):
            d.bind_receipt()
        good['live_patch_acknowledged']=False
        with patch.object(d,'safe_read',return_value=json.dumps(good).encode()):
            with self.assertRaises(d.DiagnosticFailure): d.bind_receipt()

    def test_grammar_and_no_mutator_calls(self):
        tree=ast.parse(Path(d.__file__).read_text(),feature_version=(3,7))
        attrs={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertFalse(attrs & {'apply_after_drain','signal_once','reconcile','save','write_new',
                                 'database','redis_binding','snapshot','startup','main'})

    def collect_fixture(self, unavailable=False, drift=False, worker_count=16):
        def pod(n):
            return dict(metadata=dict(name=n,uid=n),status=dict(podIP=n))
        workers=[pod('worker-%s'%i) for i in range(worker_count)]
        pods={'workers':workers,'dispatcher':[pod('dispatch')],'weknora-app':[pod('app')]}
        seen=[]; output=[]
        def logs(obs,p,m,s,previous=False):
            seen.append(p['metadata']['name'])
            return dict(unavailable={}) if unavailable else dict(pod=p['metadata']['name'])
        obs=types.SimpleNamespace(WORKERS=['workers'],DISPATCHER='dispatcher',
            health=lambda p:dict(pod=p['metadata']['name']),utc=lambda:'2026-10-17T08:00:00Z',
            resource_snapshot=lambda p:[])
        with patch.object(d,'inventory',side_effect=[(pods,{'id':1}),(pods,{'id':2 if drift else 1})]), \
             patch.object(d,'pod_status',side_effect=lambda o,p:dict(pod=p['metadata']['name'],restarts=0)), \
             patch.object(d,'logs',side_effect=logs),patch.object(d,'emit',side_effect=output.append), \
             patch.object(d.time,'sleep'):
            code=d.collect(obs)
        return code,output,seen

    def test_all_workers_and_app_logs_collected(self):
        code,out,seen=self.collect_fixture()
        self.assertEqual(code,0)
        self.assertEqual(len(seen),18)
        self.assertIn('worker-15',seen)
        self.assertIn('app',seen)
        self.assertEqual(out[-1]['diagnostic'],'COMPLETE')
        self.assertFalse(out[-1]['ready_to_expand'])
        self.assertTrue(out[-1]['collection_is_not_runtime_acceptance'])
        self.assertEqual(out[-1]['provider_probe_requests'],0)

    def test_collection_drift_incomplete(self):
        code,out,_=self.collect_fixture(drift=True)
        self.assertEqual(code,1)
        self.assertFalse(out[-1]['baseline_stable'])

    def test_collection_unavailable_incomplete(self):
        code,out,_=self.collect_fixture(unavailable=True)
        self.assertEqual(code,1)
        self.assertEqual(out[-1]['unavailable_reads'],18)

    def test_missing_worker_is_incomplete(self):
        code,out,_=self.collect_fixture(worker_count=15)
        self.assertEqual(code,1)
        self.assertFalse(out[-1]['expected_topology_present'])

    def test_pod_state_filters_raw_termination_message(self):
        obs=types.SimpleNamespace(container=lambda p:dict(name='worker'),ready=lambda p:True)
        p=dict(metadata=dict(name='worker-0',uid='uid'),spec=dict(nodeName='node'),
            status=dict(phase='Running',containerStatuses=[dict(name='worker',restartCount=1,
                lastState=dict(terminated=dict(reason='OOMKilled',exitCode=137,message='SECRET')))]))
        row=d.pod_status(obs,p)
        self.assertEqual(row['last_termination_reason'],'OOMKilled')
        self.assertNotIn('SECRET',json.dumps(row))


if __name__=='__main__':
    unittest.main()
