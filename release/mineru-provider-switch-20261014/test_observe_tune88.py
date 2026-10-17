import ast
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import observe_tune88 as m
from test_tune_weknora88 import pod, dep, spec, snap


class Tests(unittest.TestCase):
    def test_syntax_and_fixed_window(self):
        ast.parse(Path(m.__file__).read_text(), feature_version=(3,7))
        self.assertEqual((m.SAMPLES-1)*m.INTERVAL, 600)

    def test_summary_never_claims_document_counts_or_acceptance(self):
        def sample(n):
            return dict(redis={'redis_sample_epoch':n, 'queues':{'default':{
                'processed_total':n, 'failed_total':0, 'pending':5000-n, 'active':16, 'retry':0}}},
                worker_reads_complete=True, active_workers=16, dispatcher={'waiting':4,'quarantined':0})
        r = m.public_summary({'samples':[sample(100),sample(700)], 'observation':'COMPLETE'})
        self.assertEqual(r['sample_window_seconds'],600)
        self.assertEqual(r['performance_acceptance'],'PENDING_REVIEW')
        self.assertTrue(r['task_counters_are_not_document_counts'])
        self.assertEqual(r['model_actual_occupancy'],'NOT_MEASURED')
        self.assertEqual(r['production_writes'],0)

    def test_empty_summary_safe(self):
        r = m.public_summary({'samples':[]})
        self.assertIsNone(r['mineru_active_sample_max'])
        self.assertIsNone(r['sample_window_seconds'])

    def test_guard_warning_not_silently_accepted(self):
        r = m.public_summary({'samples':[], 'runtime_guard_warnings':[{'check':'memory_headroom'}]})
        self.assertEqual(len(r['runtime_guard_warnings']),1)
        self.assertFalse(r['automatic_rollback'])

    def test_observation_has_no_mutation_entry_points(self):
        tree=ast.parse(Path(m.__file__).read_text())
        names={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertFalse(names & {'mutate','patch_call','wait_drain','kill','change_env'})

    def test_full_collection_21_samples_five_db_checks(self):
        p=pod(); p['metadata']['name']='app'
        target=m.tune.change_env(spec(),m.tune.TARGET_ENV)
        pods={k:[pod()]*(3 if i<4 else 2) for i,k in enumerate(m.obs.WORKERS)}
        pods.update({m.obs.DISPATCHER:[pod()], 'weknora-app':[p], 'weknora-docreader':[pod()], 'weknora-redis':[pod()]})
        snapshot=snap(m.tune.TARGET,'active'); snapshot['redis_sample_epoch']=1
        snapshot['queues']['default'].update(processed_total=1,failed_total=0,retry=0)
        def health(p):
            if p is pods[m.obs.DISPATCHER][0]:
                return dict(role='dispatcher',counters=dict(waiting=0,quarantined=0))
            return dict(role='worker',counters=dict(active=1))
        report=dict(samples=[],resource_and_database_samples=[],runtime_guard_warnings=[])
        with tempfile.TemporaryDirectory() as td, \
             patch.object(m.tune,'read_plan',return_value={'candidate':target}), \
             patch.object(m.tune,'load',return_value=dict(configuration_switch='PASS',effective_pools=m.tune.TARGET,model_governor_startup_limit=128)), \
             patch.object(m.tune,'deployment',return_value=dep(target)),patch.object(m.tune,'app_for',return_value=p), \
             patch.object(m.obs,'baseline',return_value=({},pods,{'stable':True},{})), \
             patch.object(m.tune,'redis_binding'),patch.object(m.tune,'runtime_env'), \
             patch.object(m.tune,'snapshot',return_value=snapshot),patch.object(m.obs,'health',side_effect=health), \
             patch.object(m.obs,'write_new'),patch.object(m.obs,'emit'),patch.object(m.time,'sleep') as sleep, \
             patch.object(m.tune,'database',return_value={'connections':{}}) as database, \
             patch.object(m.obs,'resource_snapshot',return_value=[]),patch.object(m.tune,'check_resource_rows'), \
             patch.object(m.obs,'logs',return_value={'values':{},'byte_limit_reached':True}):
            m.collect(Path(td),report)
        self.assertEqual(len(report['samples']),21)
        self.assertEqual(database.call_count,5)
        self.assertEqual(sleep.call_count,20)
        self.assertTrue(report['baseline_stable'])
        self.assertTrue(report['log_coverage'][0]['byte_limit_reached'])


if __name__=='__main__': unittest.main()
