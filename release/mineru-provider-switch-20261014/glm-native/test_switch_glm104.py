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
from unittest.mock import Mock,patch
import switch_glm104 as s
import review_glm104 as upgrade
import review_glm88 as review
import test_review_glm88 as legacy
def pod(name='weknora-app-74f56b97f5-6x5ft',uid=upgrade.o.POD_UID): return legacy.pod(name,uid)
def dep():
    d=legacy.dep(); d['spec']=legacy.tune.change_env(d['spec'],legacy.tune.TARGET_ENV); return d
def snapshot():
    row=legacy.snapshot()
    row['effective_servers']=[dict(pool=k,concurrency=v,status='active',active=0) for k,v in upgrade.CURRENT.items()]
    return row
fixtures=SimpleNamespace(pod=pod,dep=dep,snapshot=snapshot,health_rows=legacy.health_rows,
    tune=legacy.tune,activation=legacy.activation,observation=legacy.observation)


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.mock(s,'ROOT',new=self.root); self.mock(s,'r',new=s.r or review)
        self.r=s.r
        self.mock(s,'u',new=s.u or upgrade)
        self.mock(s.u.o,'r',new=self.r)
        self.mock(self.r,'a',new=self.r.a or fixtures.activation)
        self.mock(self.r,'sw',new=self.r.sw or self.r.a.sw)
        self.mock(self.r,'e',new=self.r.e or fixtures.tune)
        self.mock(self.r,'obs',new=self.r.obs or fixtures.observation)
        self.mock(self.r.sw,'e',new=self.r.e); self.mock(self.r.sw,'obs',new=self.r.obs)
        self.mock(self.r.sw,'a',new=SimpleNamespace(native=SimpleNamespace(call=Mock(),get=Mock())))
        self.pod=fixtures.pod(); self.dep=fixtures.dep(); self.dep['metadata']['generation']=4
        self.process=dict(start_ticks=123,pid=1)
        self.sig={'deployments':{'weknora-app':['app','sha'],'redis':['redis','sha']},
                  'pods':{'weknora-app':[self.r.obs.identity(self.pod)],'redis':[['redis',[]]]},'service':['svc','sha']}
        self.plan=dict(original=self.dep['spec'],candidate=s.u.proposal(self.dep['spec']),
            pod_identity=self.r.obs.identity(self.pod),process=self.process,server_ids=list('abcdef'),
            candidate_binary='a'*64,placement={'node':'datax-03'},configuration={'immutable':True},
            document_policy_catalog={},accepted_glm88_sha256=s.u.o.PASS_SHA,diagnostic_sha256=s.u.DIAGNOSTIC_SHA,
            deployment_uid=self.dep['metadata']['uid'],postgres_identity=['pg',[]],current=s.u.CURRENT,
            only_change='ENRICHMENT_48_TO_64',pending_queue_preserved=True,queue_empty_required=False,
            dependency_signature=self.sig,target=s.u.TARGET,target_model_default=128,original_model_default=128,
            apply_permitted=False,fresh_apply_preflight_required=True)
        self.activation=dict(candidate=self.plan['original'],configmap=self.plan['configuration'],
            placement=self.plan['placement'],candidate_binary='a'*64,document_catalog_after={})

    def mock(self,obj,name,**kw): return self.stack.enter_context(patch.object(obj,name,**kw))
    def saved(self,name,value):
        (self.root/name).write_text(json.dumps(value)); (self.root/name).chmod(0o600)

    def test_python37_grammar(self):
        ast.parse((Path(__file__).parent/'switch_glm104.py').read_text(),feature_version=(3,7))

    def test_loader_rejects_changed_helper(self):
        p=self.root/'review.py'; p.write_text('raise AssertionError("must not run")'); p.chmod(0o600)
        self.mock(s,'REVIEW_TOOL',new=p)
        with self.assertRaisesRegex(RuntimeError,'review_helper_hash_changed'): s.load_review()

    def test_loader_rejects_symlink(self):
        p=self.root/'review.py'; p.symlink_to('/etc/passwd'); self.mock(s,'REVIEW_TOOL',new=p)
        with self.assertRaises(OSError): s.load_review()

    def test_loader_rejects_world_writable_helper(self):
        p=self.root/'review.py'; p.write_text('pass'); p.chmod(0o666); self.mock(s,'REVIEW_TOOL',new=p)
        with self.assertRaisesRegex(RuntimeError,'unsafe_review_helper'): s.load_review()

    def test_atomic_journal_no_replace_no_old_state_change(self):
        original=self.r.a.ROOT
        s.save('A.json',{'a':1}); self.assertEqual(s.read('A.json'),{'a':1})
        with self.assertRaises(FileExistsError): s.save('A.json',{'a':2})
        self.assertEqual(self.r.a.ROOT,original); self.assertEqual(s.read('A.json'),{'a':1})
        self.assertEqual((self.root/'A.json').stat().st_mode&0o777,0o600)
        self.assertEqual(list(self.root.glob('.receipt-*')),[])

    def pin_stubs(self):
        report=dict(glm104_review='PASS',blockers=[],ready_for_controlled_upgrade_review=True,
                    server_dry_run='PASS',proposal_sha256=s.PROPOSAL_SHA)
        reader=self.mock(self.r,'safe_bytes',side_effect=lambda path,expected=None:
            json.dumps(report if path.name=='RESULT.json' else self.plan).encode())
        self.mock(s.u,'bind_inputs',return_value=(dict(candidate=self.plan['original']),self.activation))
        self.mock(self.r.sw.recovery,'read',return_value=({},'activation-hash'))
        return report,reader

    def test_pins_exact_review_and_private_proposal(self):
        _,reader=self.pin_stubs(); p,_=s.pinned_inputs(); self.assertTrue(self.r.sw.same(p,self.plan))
        self.assertEqual([x.args[1] for x in reader.call_args_list],[s.REVIEW_SHA,s.PROPOSAL_SHA])

    def test_failed_review_rejected(self):
        report,_=self.pin_stubs(); report['blockers']=['bad']
        with self.assertRaisesRegex(RuntimeError,'review_not_exact_pass'): s.pinned_inputs()

    def test_image_change_in_proposal_rejected(self):
        self.pin_stubs(); self.plan['candidate']['template']['spec']['containers'][0]['image']='other'
        with self.assertRaisesRegex(RuntimeError,'proposal_scope_changed'): s.pinned_inputs()

    def test_old_pod_or_activation_rebinding_rejected(self):
        self.pin_stubs(); self.plan['pod_identity'][0]='obsolete'
        with self.assertRaisesRegex(RuntimeError,'proposal_binding_changed'): s.pinned_inputs()

    def test_other_signature_removes_only_app(self):
        before=copy.deepcopy(self.sig); result=s.other_signature(self.sig)
        self.assertNotIn('weknora-app',result['pods']); self.assertEqual(result['service'],self.sig['service'])
        self.assertEqual(self.sig,before); self.assertEqual(result['deployments']['redis'],['redis','sha'])

    def fresh_stubs(self):
        self.mock(s,'current',return_value=(self.dep,self.pod))
        self.mock(self.r.e,'runtime_env'); self.mock(s.u,'startup_proof')
        self.mock(self.r,'check_glm',return_value={'legacy':0}); self.mock(self.r,'app_endpoint')
        self.mock(self.r.sw,'placement',return_value=self.plan['placement'])
        self.mock(self.r.e,'database',return_value={'connections':{}})
        self.mock(self.r.obs,'resource_snapshot',return_value=[{'node':'datax-03'}])
        self.mock(self.r.e,'check_resource_rows'); self.mock(s.u,'app_metric_freshness')
        self.mock(self.r.e.review,'pg_binding',return_value=(fixtures.pod('pg','pg'),['pg',[]]))
        self.mock(s,'health',return_value={})
        self.mock(self.r.e,'redis_binding',return_value='redis')
        snapshot=fixtures.snapshot(); self.mock(self.r.e,'snapshot',return_value=snapshot)
        return snapshot

    def test_fresh_does_not_require_pending_queue_empty(self):
        snapshot=self.fresh_stubs(); snapshot['queues']['default']['pending']=20000
        _,_,evidence=s.fresh(self.plan,self.activation)
        self.assertEqual(evidence['queues']['queues']['default']['pending'],20000)
        self.r.e.check_resource_rows.assert_called_once_with(self.pod,{'connections':{}},[{'node':'datax-03'}],expanding=True)

    def test_fresh_rejects_changed_consumers(self):
        snapshot=self.fresh_stubs(); snapshot['server_identity_hashes']=['changed']
        with self.assertRaisesRegex(RuntimeError,'consumer_identity_changed'): s.fresh(self.plan,self.activation)

    def test_fresh_stopped_requires_zero_claimed_tasks(self):
        snapshot=self.fresh_stubs()
        for pool in snapshot['effective_servers']: pool['status']='stopped'
        snapshot['queues']['default']['active']=1
        with self.assertRaisesRegex(RuntimeError,'claimed_work_not_finished'): s.fresh(self.plan,self.activation,True)

    def test_resource_failure_happens_before_stop(self):
        self.fresh_stubs(); self.r.e.check_resource_rows.side_effect=RuntimeError('memory')
        sender=self.mock(self.r.obs,'call')
        with self.assertRaisesRegex(RuntimeError,'memory'): s.signal_once(self.plan,self.activation)
        sender.assert_not_called(); self.assertFalse((self.root/'SIGNAL-REQUESTED.json').exists())

    def test_glm_policy_failure_happens_before_stop(self):
        self.fresh_stubs(); self.r.check_glm.side_effect=RuntimeError('glm_drift')
        sender=self.mock(self.r.obs,'call')
        with self.assertRaisesRegex(RuntimeError,'glm_drift'): s.signal_once(self.plan,self.activation)
        sender.assert_not_called()

    def signal_stubs(self):
        self.mock(s,'fresh',return_value=(self.dep,self.pod,{}))
        self.mock(self.r,'dry_run'); self.mock(s,'current',return_value=(self.dep,self.pod))
        self.mock(self.r.sw,'binary',return_value=self.process)
        return self.mock(self.r.obs,'call',return_value=json.dumps(dict(tstp_sent_once=True,pid=1,start_ticks=123)).encode())

    def test_signal_once_is_journalled_before_exec(self):
        caller=self.signal_stubs()
        def once(*args,**kwargs):
            self.assertEqual(s.read('SIGNAL-REQUESTED.json')['process'],self.process)
            self.assertIn('1000:1000',args[0]); self.assertIn(self.r.e.SIGNAL_CODE,args[0])
            return b'{"tstp_sent_once":true,"pid":1,"start_ticks":123}'
        caller.side_effect=once; s.signal_once(self.plan,self.activation)
        self.assertTrue((self.root/'SIGNAL-ACK.json').exists()); self.assertEqual(caller.call_count,1)
        with self.assertRaisesRegex(RuntimeError,'signal_already_requested'): s.signal_once(self.plan,self.activation)
        self.assertEqual(caller.call_count,1)

    def test_uncertain_signal_keeps_request_and_never_acks(self):
        caller=self.signal_stubs(); caller.side_effect=RuntimeError('lost_response')
        with self.assertRaisesRegex(RuntimeError,'lost_response'): s.signal_once(self.plan,self.activation)
        self.assertTrue((self.root/'SIGNAL-REQUESTED.json').exists())
        self.assertFalse((self.root/'SIGNAL-ACK.json').exists())

    def test_binary_change_prevents_signal(self):
        caller=self.signal_stubs(); self.r.sw.binary.return_value={'start_ticks':999}
        with self.assertRaisesRegex(RuntimeError,'process_before_signal_changed'): s.signal_once(self.plan,self.activation)
        caller.assert_not_called()

    def test_drain_never_calls_obsolete56_waiter(self):
        self.saved('SIGNAL-REQUESTED.json',dict(pod_identity=self.plan['pod_identity'],process=self.process))
        self.mock(s,'current',return_value=(self.dep,self.pod))
        self.mock(self.r.e,'redis_binding')
        snap=fixtures.snapshot()
        for pool in snap['effective_servers']: pool['status']='stopped'
        self.mock(self.r.e,'snapshot',return_value=snap); self.mock(s.time,'sleep')
        obsolete=self.mock(self.r.sw,'wait_drain')
        before=self.r.sw.ROOT; s.drain(self.plan,{})
        obsolete.assert_not_called(); self.assertEqual(self.r.sw.ROOT,before)

    def patch_stubs(self):
        self.mock(s,'drain'); self.mock(s,'fresh',return_value=(self.dep,self.pod,{}))
        self.mock(self.r,'dry_run'); self.mock(s,'current',return_value=(self.dep,self.pod))
        self.mock(self.r.e,'redis_binding'); snapshot=fixtures.snapshot()
        for pool in snapshot['effective_servers']: pool['status']='stopped'
        self.mock(self.r.e,'snapshot',return_value=snapshot)
        return snapshot

    def test_live_patch_is_exact_env_cas_once_after_stop(self):
        self.patch_stubs()
        def call(argv,**kwargs):
            self.assertTrue((self.root/'PATCH-REQUESTED.json').exists())
            ops=json.loads(kwargs['data'])
            self.assertEqual(ops[0],dict(op='test',path='/metadata/uid',value=self.dep['metadata']['uid']))
            self.assertEqual(ops[1]['path'],'/metadata/resourceVersion'); self.assertEqual(ops[2]['value'],self.plan['original'])
            self.assertEqual(ops[-1]['path'],'/spec/template/spec/containers/0/env')
            self.assertNotIn('--dry-run=server',argv)
            return None,json.dumps(dict(metadata=self.dep['metadata'],spec=self.plan['candidate'])).encode()
        self.r.sw.a.native.call.side_effect=call
        s.apply_after_drain(self.plan,self.activation,{})
        self.assertTrue((self.root/'PATCH-ACK.json').exists()); self.assertEqual(self.r.sw.a.native.call.call_count,1)

    def test_postdrain_failure_never_patches_or_restarts(self):
        self.patch_stubs(); s.fresh.side_effect=RuntimeError('quarantine')
        with self.assertRaisesRegex(RuntimeError,'quarantine'): s.apply_after_drain(self.plan,self.activation,{})
        self.r.sw.a.native.call.assert_not_called(); self.assertFalse((self.root/'PATCH-REQUESTED.json').exists())

    def test_late_claimed_task_prevents_patch(self):
        snap=self.patch_stubs(); snap['queues']['default']['active']=1
        with self.assertRaisesRegex(RuntimeError,'claimed_work_changed'): s.apply_after_drain(self.plan,self.activation,{})
        self.r.sw.a.native.call.assert_not_called()

    def test_uncertain_patch_journal_prevents_repeat(self):
        self.patch_stubs(); self.r.sw.a.native.call.side_effect=RuntimeError('lost_ack')
        with self.assertRaisesRegex(RuntimeError,'lost_ack'): s.apply_after_drain(self.plan,self.activation,{})
        with self.assertRaisesRegex(RuntimeError,'patch_already_requested'): s.apply_after_drain(self.plan,self.activation,{})
        self.assertEqual(self.r.sw.a.native.call.call_count,1)

    def test_mutating_admission_does_not_pass(self):
        self.patch_stubs()
        self.r.sw.a.native.call.return_value=(None,json.dumps(dict(metadata=self.dep['metadata'],spec={'replicas':2})).encode())
        with self.assertRaisesRegex(RuntimeError,'environment_patch_ack_uncertain'): s.apply_after_drain(self.plan,self.activation,{})
        self.assertFalse((self.root/'PATCH-ACK.json').exists())

    def test_startup_reads_canonical_persisted128_without_logs(self):
        self.saved('MODEL-STARTUP-1.json',dict(pod_identity=self.r.obs.identity(self.pod),limit=128,
                                            distributed_via_redis=True,raw_logs_saved=False))
        capture=self.mock(self.r.e,'capture_startup_limit')
        self.assertEqual(s.startup(self.pod)['limit'],128); capture.assert_not_called()

    def test_startup_wrong_limit_rejected(self):
        self.saved('MODEL-STARTUP-1.json',dict(pod_identity=self.r.obs.identity(self.pod),limit=64,
                                            distributed_via_redis=True,raw_logs_saved=False))
        with self.assertRaisesRegex(RuntimeError,'startup_evidence_changed'): s.startup(self.pod)

    def test_new_startup_requires128(self):
        capture=self.mock(self.r.e,'capture_startup_limit'); s.startup(self.pod)
        capture.assert_called_once_with(self.root,self.pod,128)

    def reconcile_stubs(self,candidate=True):
        ops=self.r.e.patch_plan(self.dep,self.plan['candidate'])
        self.saved('PATCH.private.json',ops)
        self.saved('PATCH-REQUESTED.json',dict(candidate_sha256=self.r.obs.digest(self.plan['candidate']),
                    patch_sha256=self.r.obs.digest(ops),uid=self.dep['metadata']['uid'],
                    resource_version=self.dep['metadata']['resourceVersion']))
        self.mock(self.r.e,'deployment',return_value=dict(metadata=self.dep['metadata'],
            spec=self.plan['candidate'] if candidate else self.plan['original']))
        self.mock(s,'signal_once'); self.mock(s,'apply_after_drain'); self.mock(s,'accept')

    def test_reconcile_recorded_patch_only_accepts_not_mutates(self):
        self.reconcile_stubs(); s.advance(self.plan,self.activation,{},False)
        s.signal_once.assert_not_called(); s.apply_after_drain.assert_not_called(); s.accept.assert_called_once()

    def test_uncertain_unapplied_patch_is_not_replayed(self):
        self.reconcile_stubs(False)
        with self.assertRaisesRegex(RuntimeError,'uncertain_patch_NOT_REPLAYED'): s.advance(self.plan,self.activation,{},False)
        s.apply_after_drain.assert_not_called(); s.accept.assert_not_called()

    def test_reconcile_before_any_signal_cannot_start(self):
        sender=self.mock(s,'signal_once'); mutator=self.mock(s,'apply_after_drain')
        with self.assertRaisesRegex(RuntimeError,'no_signal_to_reconcile'): s.advance(self.plan,self.activation,{},False)
        sender.assert_not_called(); mutator.assert_not_called()

    def test_reconcile_signalled_state_can_finish_without_resignal(self):
        self.saved('SIGNAL-REQUESTED.json',{})
        sender=self.mock(s,'signal_once'); mutator=self.mock(s,'apply_after_drain'); self.mock(s,'accept')
        s.advance(self.plan,self.activation,{},False); sender.assert_not_called(); mutator.assert_called_once()

    def test_status_is_readonly_and_running_idle_not_called_drained(self):
        self.saved('PREPARED.json',{}); self.mock(s,'stored_plan',return_value=(self.plan,self.activation))
        self.mock(self.r.e,'deployment',return_value=self.dep); self.mock(self.r.obs,'owned',return_value=[self.pod])
        self.mock(self.r.obs,'ready',return_value=True); self.mock(self.r.e,'redis_binding')
        self.mock(self.r.e,'snapshot',return_value=fixtures.snapshot()); self.mock(self.r.a,'database_state',return_value='GLM')
        emit=self.mock(self.r.obs,'emit'); writer=self.mock(s,'save'); before=list(self.root.iterdir())
        self.assertEqual(s.status(),0); out=emit.call_args.args[0]
        self.assertEqual(out['live_configuration'],'GLM88'); self.assertFalse(out['consumers_stopped_and_claimed_empty'])
        self.assertEqual(out['production_writes'],0); writer.assert_not_called(); self.assertEqual(list(self.root.iterdir()),before)

    def test_success_needs_three_samples_and_keeps_throughput_pending(self):
        new=fixtures.pod('new','newuid'); self.mock(s,'new_pod',return_value=(self.dep,new))
        self.mock(s,'startup'); self.mock(self.r.e,'runtime_env'); self.mock(self.r.e,'redis_binding')
        snap=fixtures.snapshot()
        for p in snap['effective_servers']: p['concurrency']=s.u.TARGET[p['pool']]
        snapshot=self.mock(self.r.e,'snapshot',return_value=snap)
        self.mock(s.time,'sleep'); self.mock(self.r.sw,'binary'); self.mock(self.r,'check_glm',return_value={'legacy':0})
        self.mock(self.r,'app_endpoint'); self.mock(self.r.sw,'placement',return_value=self.plan['placement'])
        self.mock(self.r.e,'database',return_value={'connections':{}}); self.mock(self.r.obs,'resource_snapshot',return_value=[])
        self.mock(self.r.e,'check_resource_rows'); self.mock(s.u,'app_metric_freshness')
        self.mock(self.r.e.review,'pg_binding',return_value=(fixtures.pod('pg','pg'),['pg',[]]))
        self.mock(s,'health',return_value={})
        report={}; s.accept(self.plan,self.activation,report)
        self.assertEqual(snapshot.call_count,3); self.assertEqual(report['effective_worker_slots'],104)
        self.assertEqual(report['model_governor_startup_limit'],128)
        self.assertTrue(report['throughput_acceptance'].startswith('PENDING'))
        self.assertTrue((self.root/'SUCCESS.json').exists()); self.r.sw.a.native.call.assert_not_called()

    def test_new_pod_validation_preserves_runtime_image_and_restart_guards(self):
        self.mock(self.r.e,'deployment',return_value=self.dep)
        validator=self.mock(self.r.sw,'validate_new_pod',return_value=self.pod)
        s.new_pod(self.plan)
        validator.assert_called_once_with(self.dep,dict(candidate=self.plan['candidate'],old_pod_uid=s.u.o.POD_UID))

    def test_actual_drain_requires_three_stopped_zero_samples_not_empty_queue(self):
        self.saved('SIGNAL-REQUESTED.json',dict(pod_identity=self.plan['pod_identity'],process=self.process))
        self.mock(self.r.e,'deployment',return_value=self.dep)
        self.mock(s,'current',return_value=(self.dep,self.pod)); self.mock(self.r.e,'redis_binding')
        snap=fixtures.snapshot()
        for pool in snap['effective_servers']: pool['status']='stopped'
        snap['queues']['default']['pending']=16000
        getter=self.mock(self.r.e,'snapshot',return_value=snap); self.mock(self.r.sw.time,'sleep')
        report={}; s.drain(self.plan,report)
        self.assertEqual(getter.call_count,3)
        self.assertEqual(report['live_stop_behavior'],'SIX_POOLS_STOPPED_AND_ZERO_ACTIVE_CONFIRMED')
        self.assertEqual(len(list(self.root.glob('DRAIN-VERIFIED-*.json'))),1)

    def test_health_rejects_dependent_workload_drift(self):
        changed=copy.deepcopy(self.sig); changed['pods']['redis']=[['replacement',[]]]
        self.mock(self.r.obs,'baseline',return_value=({}, {},changed,{}))
        with self.assertRaisesRegex(RuntimeError,'dependent_workload_changed'): s.health(self.plan,self.plan['pod_identity'])

    def test_health_rejects_degraded_mineru_even_when56_would_allow(self):
        workers,rows=fixtures.health_rows(quarantine=1)
        pods={k:[] for k in self.r.obs.WORKERS}; pods[self.r.obs.WORKERS[0]]=workers
        pods[self.r.obs.DISPATCHER]=[fixtures.pod('dispatcher','d')]; pods['weknora-app']=[self.pod]
        self.mock(self.r.obs,'baseline',return_value=({},pods,self.sig,{}))
        byname={x['pod']:x for x in rows}
        self.mock(self.r.obs,'health',side_effect=lambda pod:byname[pod['metadata']['name']])
        with self.assertRaisesRegex(RuntimeError,'mineru_16_not_healthy'): s.health(self.plan,self.plan['pod_identity'])

    def test_reconcile_corrupt_patch_scope_rejected(self):
        self.reconcile_stubs(); record=s.read('PATCH-REQUESTED.json'); record['uid']='unrelated'
        self.saved('PATCH-REQUESTED.json',record)
        with self.assertRaisesRegex(RuntimeError,'patch_journal_scope_changed'): s.advance(self.plan,self.activation,{},False)
        s.accept.assert_not_called(); s.apply_after_drain.assert_not_called()

    def test_stored_plan_revalidates_hash_and_pinned_proposal(self):
        self.mock(s,'pinned_inputs',return_value=(self.plan,self.activation))
        self.saved('PLAN.private.json',self.plan)
        self.saved('PREPARED.json',dict(review_sha256=s.REVIEW_SHA,proposal_sha256=s.PROPOSAL_SHA,
            plan_sha256=hashlib.sha256((self.root/'PLAN.private.json').read_bytes()).hexdigest()))
        plan,activation=s.stored_plan(); self.assertTrue(self.r.sw.same(plan,self.plan))
        self.plan['target_model_default']=999
        with self.assertRaisesRegex(RuntimeError,'stored_plan_binding_changed'): s.stored_plan()

    def test_prepare_persists_only_after_fresh_checks_and_dryrun(self):
        self.mock(s,'pinned_inputs',return_value=(self.plan,self.activation))
        self.mock(s,'fresh',return_value=(self.dep,self.pod,{'safe':True}))
        self.mock(self.r.sw,'binary',return_value=self.process); dry=self.mock(self.r,'dry_run')
        s.prepare(); dry.assert_called_once_with(self.dep,self.plan['candidate'])
        self.assertEqual(s.read('PREPARED.json')['proposal_sha256'],s.PROPOSAL_SHA)
        self.assertTrue(self.r.sw.same(s.read('PLAN.private.json'),self.plan))
        self.assertFalse((self.root/'SIGNAL-REQUESTED.json').exists())

    def test_104_target_env_never_changes_frozen_88_constants(self):
        before=copy.deepcopy(self.r.e.TARGET_ENV); env=s.target_env()
        changed=[k for k in env if env[k]!=before[k]]
        self.assertEqual(changed,[self.r.e.review.SETTINGS['enrichment'][1]])
        self.assertEqual(env[changed[0]],'64'); self.assertEqual(self.r.e.TARGET_ENV,before)

    def test_current_binds_accepted_88_identity_and_full_spec(self):
        reader=self.mock(s.u.o,'current',return_value=(self.dep,self.pod))
        s.current(self.plan)
        reader.assert_called_once_with(dict(candidate=self.plan['original']),self.plan['pod_identity'])
        replacement=copy.deepcopy(self.dep); replacement['metadata']['uid']='recreated'
        reader.return_value=(replacement,self.pod)
        with self.assertRaisesRegex(RuntimeError,'original_environment_changed'): s.current(self.plan)

    def test_pg_identity_drift_prevents_stop(self):
        self.fresh_stubs(); self.r.e.review.pg_binding.return_value=(self.pod,['replaced',[]])
        sender=self.mock(self.r.obs,'call')
        with self.assertRaisesRegex(RuntimeError,'postgres_identity_changed'): s.signal_once(self.plan,self.activation)
        sender.assert_not_called()

    def test_stale_app_metrics_prevent_stop(self):
        self.fresh_stubs(); s.u.app_metric_freshness.side_effect=RuntimeError('stale_app_resource_sample')
        sender=self.mock(self.r.obs,'call')
        with self.assertRaisesRegex(RuntimeError,'stale_app_resource'): s.signal_once(self.plan,self.activation)
        sender.assert_not_called()

    def test_recreated_controller_not_accepted_during_reconcile(self):
        self.reconcile_stubs()
        self.r.e.deployment.return_value['metadata']={'uid':'recreated'}
        with self.assertRaisesRegex(RuntimeError,'uncertain_patch_NOT_REPLAYED'): s.advance(self.plan,self.activation,{},False)
        s.accept.assert_not_called(); s.apply_after_drain.assert_not_called()

    def test_drain_active_idle_cannot_clear_stopped_gate(self):
        self.saved('SIGNAL-REQUESTED.json',dict(pod_identity=self.plan['pod_identity'],process=self.process))
        self.mock(s,'current',return_value=(self.dep,self.pod)); self.mock(self.r.e,'redis_binding')
        self.mock(self.r.e,'snapshot',return_value=fixtures.snapshot())
        self.mock(s.time,'monotonic',side_effect=[0,1,100,100])
        with self.assertRaisesRegex(RuntimeError,'not_all_pools_stopped'): s.drain(self.plan,{})
        self.assertEqual(list(self.root.glob('DRAIN-VERIFIED-*')),[])

    def test_drain_56_capacity_cannot_pass_88_current(self):
        self.saved('SIGNAL-REQUESTED.json',dict(pod_identity=self.plan['pod_identity'],process=self.process))
        self.mock(s,'current',return_value=(self.dep,self.pod)); self.mock(self.r.e,'redis_binding')
        snap=fixtures.snapshot()
        for pool in snap['effective_servers']:
            pool['concurrency']=self.r.e.CURRENT[pool['pool']]; pool['status']='stopped'
        self.mock(self.r.e,'snapshot',return_value=snap)
        with self.assertRaisesRegex(Exception,'effective_capacity_mismatch'): s.drain(self.plan,{})

    def test_drain_deadline_never_force_terminates(self):
        self.saved('SIGNAL-REQUESTED.json',dict(pod_identity=self.plan['pod_identity'],process=self.process))
        self.mock(s.time,'monotonic',side_effect=[0,1801])
        call=self.mock(self.r.obs,'call')
        with self.assertRaisesRegex(RuntimeError,'claimed_work_deadline'): s.drain(self.plan,{})
        call.assert_not_called()

    def test_main_status_never_acquires_mutation_locks(self):
        self.mock(s.socket,'gethostname',return_value='datax-01')
        self.mock(s,'load_review',return_value=s.u); status=self.mock(s,'status',return_value=0)
        locker=self.mock(self.r.sw.recovery,'lock'); opener=self.mock(self.r.sw.frozen_consumers,'open_safe')
        self.assertEqual(s.main(['--status']),0)
        status.assert_called_once(); locker.assert_not_called(); opener.assert_not_called()

    def test_repeat_apply_stops_before_prepare_signal_or_patch(self):
        self.mock(s.socket,'gethostname',return_value='datax-01'); self.mock(s,'load_review',return_value=s.u)
        self.mock(s.u.o,'s',new=SimpleNamespace(ROOT=self.root/'old88'))
        self.mock(self.r.sw.recovery,'lock',side_effect=lambda _:contextlib.nullcontext())
        self.mock(s.signal,'signal')
        self.mock(self.r.sw.frozen_consumers,'open_safe',side_effect=lambda _:os.open(self.root/'l1',os.O_CREAT|os.O_RDWR,0o600))
        self.mock(self.r.e,'acquire_previous_lock',side_effect=lambda:os.open(self.root/'l2',os.O_CREAT|os.O_RDWR,0o600))
        prepare=self.mock(s,'prepare'); advance=self.mock(s,'advance')
        with self.assertRaisesRegex(RuntimeError,'previous_attempt_USE_STATUS'): s.main(['--apply-once'])
        prepare.assert_not_called(); advance.assert_not_called()

    def test_no_obsolete_mutator_or_root_repointing_in_source(self):
        source=(Path(__file__).parent/'switch_glm104.py').read_text(); tree=ast.parse(source)
        for n in ast.walk(tree):
            if isinstance(n,ast.Assign):
                self.assertFalse(any(isinstance(t,ast.Attribute) and t.attr=='ROOT' for t in n.targets))
        for text in ('r.sw.wait_drain(', 'r.e.main(', 'u.o.s.main(', 'r.a.main(', 'r.e.signal_once('):
            self.assertNotIn(text,source)


if __name__=='__main__': unittest.main()

