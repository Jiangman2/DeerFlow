import ast
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import review_glm104 as m
import test_review_glm88 as old


class Review104Tests(old.ReviewTests):
    # Reuse fixtures, not the older 56-to-88 test methods.
    def setUp(self):
        super().setUp()
        self.mock(m.o, 'r', new=old.r)
        self.mock(m.o, 's', new=type('Switch', (), {'ROOT': self.root})())

    def current_dep(self):
        d=old.dep()
        d['spec']=m.o.r.e.change_env(d['spec'],m.o.r.e.TARGET_ENV)
        return d

    def app(self): return old.pod(uid=m.o.POD_UID)

    def prepare_collect(self,quarantine=0):
        d=self.current_dep(); pod=self.app()
        plan=dict(candidate=d['spec'],placement={},candidate_binary='a'*64)
        activation=dict(configmap={},document_catalog_after={})
        self.mock(m,'bind_inputs',return_value=(plan,activation))
        self.mock(m.o,'current',return_value=(d,pod))
        self.mock(m,'startup_proof',return_value={'limit':128})
        self.mock(old.r,'check_glm',return_value={'glm':23965,'legacy':0,'inline':0})
        self.mock(old.r,'app_endpoint'); self.mock(old.r.e,'runtime_env')
        self.mock(old.r.sw,'placement',return_value={}); self.mock(old.r.sw,'binary',return_value={'pid':1})
        workers,states=old.health_rows(quarantine)
        pods={name:[] for name in old.r.obs.WORKERS}; pods[old.r.obs.WORKERS[0]]=workers
        pods['weknora-app']=[pod]; pods[old.r.obs.DISPATCHER]=[old.pod('dispatcher','d')]
        self.mock(old.r.obs,'baseline',return_value=({},pods,{'stable':True},{}))
        self.mock(old.r.obs,'read_expansion')
        self.mock(old.r.e,'redis_binding',return_value='bound')
        def snap(_):
            s=old.snapshot()
            s['effective_servers']=[dict(pool=k,concurrency=v,status='active',active=0) for k,v in m.CURRENT.items()]
            return s
        self.mock(old.r.e,'snapshot',side_effect=snap)
        self.mock(old.r.obs,'health',side_effect=lambda p:next(x for x in states if x['pod']==p['metadata']['name']))
        self.mock(old.r.e.review,'pg_binding',return_value=(old.pod('pg','pg'),['pg',[]]))
        self.mock(old.r.e,'database',return_value={'connections':{'all':10}})
        self.mock(old.r.obs,'resource_snapshot',return_value=[{'node':'datax-03'}])
        self.mock(m,'app_metric_freshness')
        resource=self.mock(old.r.e,'check_resource_rows')
        dry=self.mock(old.r,'dry_run',return_value=[{'op':'test'}])
        self.mock(m.time,'sleep')
        return dict(glm104_review='INCOMPLETE',samples=[],blockers=[]),dry,resource

    def test104_only_enrichment_changes(self):
        d=self.current_dep(); before=copy.deepcopy(d); target=m.proposal(d['spec'])
        key=old.r.e.review.SETTINGS['enrichment'][1]
        env=target['template']['spec']['containers'][0]['env']
        self.assertEqual([x['value'] for x in env if x['name']==key],['64'])
        self.assertEqual(old.r.e.change_env(target,{key:'48'}),d['spec'])
        self.assertEqual(d,before); self.assertEqual(sum(m.TARGET.values()),104)
        self.assertEqual(old.r.e.TARGET_ENV[old.r.e.review.SETTINGS['model'][1]],'128')

    def test104_rejects_88_env_drift(self):
        d=self.current_dep(); d['spec']['template']['spec']['containers'][0]['env'][0]['value']='999'
        with self.assertRaisesRegex(RuntimeError,'literal_concurrency'): m.proposal(d['spec'])

    def test104_rejects_duplicate_env(self):
        d=self.current_dep(); env=d['spec']['template']['spec']['containers'][0]['env']; env.append(copy.deepcopy(env[0]))
        with self.assertRaises(RuntimeError): m.proposal(d['spec'])

    def test104_rejects_valuefrom(self):
        d=self.current_dep(); env=d['spec']['template']['spec']['containers'][0]['env']; env[0]['valueFrom']={}
        with self.assertRaises(RuntimeError): m.proposal(d['spec'])

    def test104_rejects_envfrom(self):
        d=self.current_dep(); d['spec']['template']['spec']['containers'][0]['envFrom']=[{}]
        with self.assertRaises(RuntimeError): m.proposal(d['spec'])

    def test104_cannot_change_model_or_image_or_core(self):
        d=self.current_dep(); change=self.mock(old.r.e,'change_env',return_value=copy.deepcopy(d['spec']))
        change.return_value['replicas']=2
        with self.assertRaisesRegex(RuntimeError,'only_enrichment'): m.proposal(d['spec'])

    def test104_startup128_exact_identity(self):
        row=dict(pod_identity=old.r.obs.identity(self.app()),limit=128,distributed_via_redis=True,raw_logs_saved=False)
        raw=json.dumps(row).encode(); f=self.root/'MODEL-STARTUP-1.json'; f.write_bytes(raw); f.chmod(0o600)
        self.assertEqual(m.startup_proof(self.app())['limit'],128)
        row['limit']=64; f.write_text(json.dumps(row))
        with self.assertRaisesRegex(RuntimeError,'startup_limit'): m.startup_proof(self.app())

    def test104_missing_startup_has_no_fallback_write(self):
        with self.assertRaisesRegex(RuntimeError,'startup_receipt_missing'): m.startup_proof(self.app())
        self.assertEqual(list(self.root.iterdir()),[])

    def bind_fixture(self):
        receipt=dict(glm88_switch='PASS',effective_pools=m.CURRENT,model_governor_startup_limit=128,
                     pod_uid=m.o.POD_UID,database='GLM',mineru_slots=16)
        diagnosis=dict(diagnostic='COMPLETE',baseline_stable=True,warnings=[],samples=[{}]*5,
                       production_writes=0,ready_to_expand=False)
        self.mock(m.o.s,'stored_plan',create=True,return_value=({'candidate':'exact'},{}))
        reader=self.mock(m.o,'safe_read',side_effect=lambda p,h:json.dumps(receipt if p==m.o.PASS else diagnosis).encode())
        return receipt,diagnosis,reader

    def test104_binds_both_pinned_receipts(self):
        _,_,reader=self.bind_fixture(); self.assertEqual(m.bind_inputs()[0],{'candidate':'exact'})
        self.assertEqual(reader.call_args_list[0].args,(m.o.PASS,m.o.PASS_SHA))
        self.assertEqual(reader.call_args_list[1].args,(m.DIAGNOSTIC,m.DIAGNOSTIC_SHA))

    def test104_rejects_old_capacity_receipt(self):
        receipt,_,_=self.bind_fixture(); receipt['effective_pools']=old.r.CURRENT
        with self.assertRaisesRegex(RuntimeError,'accepted_glm88'): m.bind_inputs()

    def test104_rejects_diagnostic_warnings(self):
        _,diagnosis,_=self.bind_fixture(); diagnosis['warnings']=[{'scope':'missing'}]
        with self.assertRaisesRegex(RuntimeError,'diagnostic_receipt'): m.bind_inputs()

    def test104_rejects_incomplete_diagnostic(self):
        _,diagnosis,_=self.bind_fixture(); diagnosis['samples']=[]
        with self.assertRaisesRegex(RuntimeError,'diagnostic_receipt'): m.bind_inputs()

    def test104_fresh_pod_metrics(self):
        now=m.datetime.datetime.now(m.datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        rows=[dict(node='datax-03',pods=[dict(pod=self.app()['metadata']['name'],cpu={'time':now},memory={'time':now})])]
        m.app_metric_freshness(self.app(),rows)
        rows[0]['pods'][0]['memory']['time']='2000-01-01T00:00:00Z'
        with self.assertRaisesRegex(RuntimeError,'stale_app_resource'): m.app_metric_freshness(self.app(),rows)

    def test104_rejects_missing_or_other_pod_metrics(self):
        for rows in ([],[dict(node='other')],[dict(node='datax-03',pods=[])],
                     [dict(node='datax-03',pods=[dict(pod='other')])]):
            with self.subTest(rows=rows),self.assertRaises(RuntimeError): m.app_metric_freshness(self.app(),rows)

    def test104_blocked_review_has_no_proposal(self):
        report,dry,resource=self.prepare_collect(); resource.side_effect=RuntimeError('database_headroom_requires_review')
        m.collect(self.root,report)
        self.assertFalse((self.root/'PROPOSAL.private.json').exists()); dry.assert_not_called()
        self.assertEqual(len(report['blockers']),4)

    def test104_startup_wrong_identity(self):
        f=self.root/'MODEL-STARTUP-1.json'; f.write_text(json.dumps(dict(pod_identity=['old',[]],limit=128))); f.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError,'startup_receipt_missing'): m.startup_proof(self.app())

    def test104_complete_dryrun_does_not_apply(self):
        report,dry,resource=self.prepare_collect(); m.collect(self.root,report)
        self.assertEqual(report['glm104_review'],'PASS'); self.assertEqual(len(report['samples']),5)
        self.assertEqual(resource.call_count,4); dry.assert_called_once()
        p=json.loads((self.root/'PROPOSAL.private.json').read_bytes())
        self.assertFalse(p['apply_permitted']); self.assertTrue(p['fresh_apply_preflight_required'])
        self.assertEqual(p['current'],m.CURRENT); self.assertEqual(p['target'],m.TARGET)
        self.assertEqual(p['target_model_default'],128); self.assertEqual(p['original_model_default'],128)
        self.assertEqual((self.root/'PROPOSAL.private.json').stat().st_mode&0o777,0o600)
        old.r.sw.a.native.call.assert_not_called()

    def test104_quarantine_blocks_not_clears(self):
        report,dry,_=self.prepare_collect(quarantine=1); m.collect(self.root,report)
        self.assertEqual(report['glm104_review'],'BLOCKED'); self.assertEqual(len(report['blockers']),5)
        dry.assert_not_called(); self.assertFalse((self.root/'PROPOSAL.private.json').exists())

    def test104_memory_or_db_blockers_keep_queues_running(self):
        report,dry,resource=self.prepare_collect(); resource.side_effect=RuntimeError('node_memory_headroom_below_24GiB')
        m.collect(self.root,report)
        self.assertEqual(report['glm104_review'],'BLOCKED'); dry.assert_not_called()
        self.assertTrue(all(p['status']=='active' for x in report['samples'] for p in x['queue']['pools']))

    def test104_server_identity_drift_blocks(self):
        report,dry,_=self.prepare_collect(); original=old.r.e.snapshot.side_effect
        a=original(None); b=copy.deepcopy(a); b['server_identity_hashes']=['changed']
        old.r.e.snapshot.side_effect=[a,b]
        with self.assertRaisesRegex(RuntimeError,'consumer_identity'): m.collect(self.root,report)
        dry.assert_not_called()

    def test104_pg_identity_drift_blocks(self):
        report,dry,_=self.prepare_collect()
        old.r.e.review.pg_binding.side_effect=[(old.pod('pg','pg'),['pg',[]]),(old.pod('pg','new'),['new',[]])]
        with self.assertRaisesRegex(RuntimeError,'postgres_identity'): m.collect(self.root,report)
        dry.assert_not_called()

    def test104_dependency_drift_blocks(self):
        report,dry,_=self.prepare_collect(); before=old.r.obs.baseline.return_value
        old.r.obs.baseline.side_effect=[before,(before[0],before[1],{'changed':True},before[3])]
        with self.assertRaisesRegex(RuntimeError,'dependencies_changed'): m.collect(self.root,report)
        dry.assert_not_called()

    def test104_dryrun_race_cannot_seal_proposal(self):
        report,dry,_=self.prepare_collect(); d,p= m.o.current.return_value
        def race(*_):
            changed=copy.deepcopy(d); changed['spec']['replicas']=2
            m.o.current.return_value=(changed,p)
            return [{'op':'test'}]
        dry.side_effect=race
        with self.assertRaisesRegex(RuntimeError,'environment_changed'): m.collect(self.root,report)
        self.assertFalse((self.root/'PROPOSAL.private.json').exists())

    def test104_final_resource_failure_blocks(self):
        report,dry,resource=self.prepare_collect(); resource.side_effect=[None,None,None,RuntimeError('database_headroom_requires_review')]
        m.collect(self.root,report); self.assertEqual(report['glm104_review'],'BLOCKED'); dry.assert_not_called()

    def test104_reset_in_middle_is_unknown(self):
        samples=[]
        for epoch,total in [(1,100),(2,1),(3,102)]:
            s=old.r.queue_projection(old.snapshot()); s['epoch']=epoch; s['queues']['default']['processed_total']=total
            samples.append({'queue':s})
        result=m.trends(samples)['default']
        self.assertIsNone(result['processed_counter_delta']); self.assertTrue(result['counter_reset_or_rollover'])

    def test104_safe_public_summary_not_approval(self):
        s=m.public_summary(dict(glm104_review='PASS',samples=[],secret='NEVER',candidate={'env':'private'}))
        self.assertNotIn('secret',s); self.assertNotIn('candidate',s)
        self.assertEqual(s['proposed_worker_slots'],104); self.assertEqual(s['current_worker_slots'],88)
        self.assertFalse(s['ready_to_apply']); self.assertFalse(s['queue_empty_required'])
        self.assertEqual(s['proposed_model_default'],128); self.assertEqual(s['mineru_slots'],16)

    def test104_status_only_stored_summary(self):
        self.mock(m.socket,'gethostname',return_value='datax-01')
        self.mock(m.o,'safe_read',return_value=json.dumps(dict(glm104_review='PASS',samples=[])).encode())
        loader=self.mock(m,'initialize'); writer=self.mock(old.r.obs,'write_new')
        self.assertEqual(m.main(['--status','/var/lib/weknora-glm104-review-abcdefgh']),0)
        loader.assert_not_called(); writer.assert_not_called()

    def test104_status_rejects_other_dirs(self):
        self.mock(m.socket,'gethostname',return_value='datax-01')
        for path in ('/','/var/lib','/var/lib/weknora-glm88-review-abcdefgh','relative'):
            with self.subTest(path=path),self.assertRaises(RuntimeError): m.main(['--status',path])

    def test104_no_mutation_cli(self):
        for flag in ('--apply','--resume','--rollback','--reconcile'):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit): m.main([flag])

    def test104_python37_and_no_mutation_entrypoints(self):
        source=(Path(__file__).parent/'review_glm104.py').read_text(); tree=ast.parse(source,feature_version=(3,7))
        forbidden={'signal_once','patch_once','capture_startup_limit','startup','apply','reconcile','terminate','kill','save'}
        names={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertFalse(names&forbidden)


# Keep only this class's 104 tests; the older suite is run independently.
for name in dir(old.ReviewTests):
    if name.startswith('test_'): setattr(Review104Tests,name,None)


if __name__=='__main__': unittest.main()
