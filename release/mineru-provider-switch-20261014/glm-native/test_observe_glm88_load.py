import ast
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
import observe_glm88_load as o
import review_glm88 as review
import switch_glm88 as switch
import test_review_glm88 as fixtures


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.mock(o,'s',new=o.s or switch); self.mock(o,'r',new=o.r or review)
        self.r=o.r; self.s=o.s
        self.mock(self.r,'a',new=self.r.a or fixtures.activation)
        self.mock(self.r,'sw',new=self.r.sw or self.r.a.sw)
        self.mock(self.r,'e',new=self.r.e or fixtures.tune)
        self.mock(self.r,'obs',new=self.r.obs or fixtures.observation)
        self.mock(self.r.sw,'e',new=self.r.e); self.mock(self.r.sw,'obs',new=self.r.obs)

    def mock(self,obj,name,**kwargs): return self.stack.enter_context(patch.object(obj,name,**kwargs))

    def sample(self,pending=200,processed=100,failed=10,busy=16):
        snap=fixtures.snapshot(); snap['queues']['default'].update(pending=pending,processed_total=processed,failed_total=failed)
        for pool in snap['effective_servers']: pool['concurrency']=self.r.TARGET[pool['pool']]; pool['active']=pool['concurrency']
        return dict(redis=snap,active_workers=busy,worker_reads_complete=True,dispatcher=dict(waiting=4,quarantined=0))

    def report(self,samples=None):
        return dict(samples=samples or [],warnings=[],resource_samples=[])

    def test_python37(self):
        ast.parse(Path(o.__file__).read_text(),feature_version=(3,7))

    def test_reader_checks_hash_permissions_and_links(self):
        p=self.root/'a'; p.write_bytes(b'fixed'); p.chmod(0o600)
        self.assertEqual(o.safe_read(p,hashlib.sha256(b'fixed').hexdigest()),b'fixed')
        with self.assertRaisesRegex(RuntimeError,'input_hash_changed'): o.safe_read(p,'wrong')
        link=self.root/'b'; os.link(p,link)
        with self.assertRaisesRegex(RuntimeError,'unsafe_private_file'): o.safe_read(p)
        link.unlink(); p.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError,'unsafe_private_file'): o.safe_read(p)

    def test_reader_rejects_symlink_and_writable_parent(self):
        p=self.root/'link'; p.symlink_to('/etc/passwd')
        with self.assertRaises(OSError): o.safe_read(p)
        p.unlink(); p.write_bytes(b'fixed'); self.root.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError,'unsafe_private_parent'): o.safe_read(p)
        self.root.chmod(0o700)

    def test_readonly_cli_has_no_mutation_mode(self):
        for mode in ('--apply','--apply-once','--reconcile','--rollback'):
            with self.subTest(mode=mode),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit): o.main([mode])

    def test_mutating_switch_helpers_are_never_called(self):
        tree=ast.parse(Path(o.__file__).read_text())
        forbidden={'prepare','signal_once','drain','apply_after_drain','advance','accept','save','startup','sql_write',
                   'kill','signal','patch_call','deployment_patch','dry_run','stop','terminate'}
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute): self.assertNotIn(n.func.attr,forbidden)

    def test_current_exact_new_uid_not_earlier_glm56(self):
        old=fixtures.pod(); self.mock(self.s,'new_pod',return_value=({},old))
        with self.assertRaisesRegex(RuntimeError,'accepted_glm88_pod_changed'): o.current({})

    def test_current_restart_identity_is_checked(self):
        pod=fixtures.pod(uid=o.POD_UID); self.mock(self.s,'new_pod',return_value=({},pod))
        self.assertEqual(o.current({})[1],pod)
        with self.assertRaisesRegex(RuntimeError,'app_process_changed'): o.current({},['wrong',[]])

    def test_counter_deltas_and_pool_occupancy_not_documents(self):
        rows=[self.sample(200,100,10,15),self.sample(50,260,12,16)]
        rows[1]['redis']['redis_sample_epoch']=160
        result=o.summary(self.report(rows)); trend=result['queue_trends']['default']
        self.assertEqual(trend['counter_deltas_not_document_counts'],{'processed_total':160,'failed_total':2})
        self.assertEqual(result['mineru']['sampled_full_count'],1); self.assertEqual(result['mineru']['sampled_active_mean'],15.5)
        self.assertEqual(result['pools']['core']['capacity_values'],[16]); self.assertEqual(result['pools']['core']['full_samples'],2)
        self.assertTrue(result['task_counters_are_not_document_counts']); self.assertEqual(result['model_actual_occupancy'],'NOT_MEASURED')
        self.assertEqual(result['sample_window_seconds'],60)

    def test_counter_reset_is_not_false_negative_throughput(self):
        rows=[self.sample(processed=100),self.sample(processed=10)]
        result=o.summary(self.report(rows))
        self.assertIsNone(result['queue_trends']['default']['counter_deltas_not_document_counts']['processed_total'])

    def test_missing_worker_reads_are_not_idle_workers(self):
        row=self.sample(); row['worker_reads_complete']=False; row['active_workers']=None
        result=o.summary(self.report([row]))
        self.assertEqual(result['mineru']['valid_samples'],0); self.assertIsNone(result['mineru']['sampled_active_mean'])

    def test_empty_report_is_not_success(self):
        result=o.summary(self.report())
        self.assertEqual(result['observation'],'INCOMPLETE'); self.assertIsNone(result['sample_window_seconds'])

    def test_unknown_pending_age_is_not_zero(self):
        row=self.sample(); row['redis']['queues']['default']['oldest_pending_seconds']=None
        result=o.summary(self.report([row])); self.assertIsNone(result['queue_trends']['default']['oldest_pending_seconds_max'])

    def test_resource_units_and_missing_not_zero(self):
        row=dict(node='datax-03',cpu={'usageNanoCores':1500000000},memory={'availableBytes':32*1024**3},
            filesystem={'availableBytes':80*1024**3},pods=[dict(pod='app',memory={'workingSetBytes':300*1024**2})])
        result=o.compact_resources([dict(resources=[row,dict(node='offline',unavailable={'check':'missing'})])])
        self.assertEqual(result['datax-03']['cpu_cores_max'],1.5)
        self.assertEqual(result['datax-03']['memory_available_GiB_min'],32)
        self.assertEqual(result['datax-03']['pod_memory_MiB_max'],{'app':300})
        self.assertIsNone(result['offline']['cpu_cores_max']); self.assertEqual(result['offline']['unavailable_samples'],1)

    def test_error_does_not_export_raw_credentials(self):
        report=self.report(); o.warning(report,1,'DB',ValueError('password=synthetic-secret'))
        self.assertNotIn('synthetic-secret',json.dumps(report)); self.assertFalse(report['warnings'][0]['raw_error_printed'])

    def test_resource_pressure_is_warning_not_automatic_restart(self):
        app=fixtures.pod(uid=o.POD_UID); report=self.report()
        self.mock(self.r.e,'database',return_value={'connections':{'all':99}})
        self.mock(self.r.e.review,'pg_binding',return_value=(fixtures.pod('pg'),{'uid':'pg'}))
        now=self.r.obs.datetime.datetime.now(self.r.obs.datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.mock(self.r.obs,'resource_snapshot',return_value=[dict(node='datax-03',cpu={'time':now},
            memory={'time':now},filesystem={'time':now},pods=[])])
        self.mock(self.r.e,'check_resource_rows',side_effect=RuntimeError('node_memory_headroom_below_24GiB'))
        stop=self.mock(self.s,'signal_once')
        o.resource_sample(app,{},report,1,{'uid':'pg'})
        self.assertEqual(len(report['warnings']),1); stop.assert_not_called()
        self.assertEqual(report['resource_samples'][0]['database_connections']['all'],99)

    def collect_stubs(self,quarantine=0):
        self.mock(o,'SAMPLES',new=3); self.mock(o,'INTERVAL',new=0)
        receipt=dict(glm88_switch='PASS',effective_pools=self.r.TARGET,model_governor_startup_limit=128,
                     pod_uid=o.POD_UID,database='GLM',mineru_slots=16)
        self.mock(o,'safe_read',return_value=json.dumps(receipt).encode())
        self.mock(self.s,'stored_plan',return_value=({},{}))
        app=fixtures.pod('new-app',o.POD_UID); self.mock(o,'current',return_value=({},app))
        self.mock(self.r.e,'runtime_env'); self.mock(self.r,'check_glm'); self.mock(self.r,'app_endpoint')
        workers,health=fixtures.health_rows(quarantine)
        pods={name:[] for name in self.r.obs.WORKERS}; pods[self.r.obs.WORKERS[0]]=workers
        pods.update({'weknora-app':[app],'weknora-docreader':[fixtures.pod('docreader')],
                     self.r.obs.DISPATCHER:[fixtures.pod('dispatcher')]})
        signature={'pods':{'weknora-app':[self.r.obs.identity(app)]}}
        self.mock(self.r.obs,'baseline',return_value=({},pods,signature,{'weknora-app':{'resources':{}}}))
        self.mock(self.r.e.review,'pg_binding',return_value=(fixtures.pod('pg'),{'uid':'pg'}))
        self.mock(self.r.e,'redis_binding'); snap=self.sample()['redis']; self.mock(self.r.e,'snapshot',return_value=snap)
        mapping={x['pod']:x for x in health}; self.mock(self.r.obs,'health',side_effect=lambda p:mapping[p['metadata']['name']])
        self.mock(o,'resource_sample',return_value={}); self.mock(self.r.obs,'logs',return_value={'values':{},'raw_logs_saved':False})
        return health

    def test_full_collect_readonly_and_summarized_logs(self):
        self.collect_stubs(); report=self.report(); stop=self.mock(self.s,'signal_once')
        o.collect(self.root,report)
        self.assertEqual(report['observation'],'COMPLETE'); self.assertEqual(len(report['samples']),3)
        self.assertTrue(report['baseline_stable']); stop.assert_not_called()
        self.assertEqual(len(list(self.root.glob('SAMPLE-*.json'))),3)
        self.assertTrue(all('values' not in x for x in report['log_coverage']))

    def test_quarantine_does_not_abort_load_observation_or_mutate(self):
        self.collect_stubs(1); report=self.report(); stop=self.mock(self.s,'signal_once')
        o.collect(self.root,report)
        self.assertEqual(report['observation'],'COMPLETE_WITH_WARNINGS'); self.assertEqual(len(report['samples']),3)
        self.assertEqual(o.summary(report)['mineru']['quarantined_max'],1); stop.assert_not_called()

    def test_partial_queue_reads_are_preserved_as_warning(self):
        self.collect_stubs(); self.r.e.snapshot.side_effect=RuntimeError('queue_unavailable')
        report=self.report(); o.collect(self.root,report)
        self.assertEqual(len(report['samples']),3); self.assertTrue(report['warnings'])
        self.assertEqual(o.summary(report)['queue_trends'],{})

    def test_baseline_change_fails_after_retaining_samples(self):
        self.collect_stubs(); self.r.obs.baseline.side_effect=[self.r.obs.baseline.return_value,({}, {},{'pods':{}},{})]
        report=self.report()
        with self.assertRaisesRegex(RuntimeError,'dependent_workload_changed'): o.collect(self.root,report)
        self.assertEqual(len(report['samples']),3); self.assertNotIn('baseline_stable',report)

    def test_unexpected_worker_concurrency_not_credited_as16(self):
        health=self.collect_stubs(); health[0]['counters']['active']=3
        report=self.report(); o.collect(self.root,report)
        self.assertTrue(report['warnings']); self.assertIsNone(o.summary(report)['mineru']['sampled_active_mean'])

    def test_status_is_stored_summary_only_without_loading_cluster_helpers(self):
        self.mock(o.socket,'gethostname',return_value='datax-01')
        self.mock(o,'safe_read',return_value=b'{"observation":"COMPLETE","production_writes":0}')
        loader=self.mock(o,'initialize')
        self.assertEqual(o.main(['--status','/var/lib/weknora-glm88-load-abcdefgh']),0); loader.assert_not_called()
        with self.assertRaises(RuntimeError): o.main(['--status','/tmp/other'])


if __name__=='__main__': unittest.main()
