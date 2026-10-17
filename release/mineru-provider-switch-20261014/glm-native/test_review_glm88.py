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
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import review_glm88 as r
import activate_glm56 as activation
import observe_pipeline as observation
import tune_weknora88 as tune


def pod(name=r.POD,uid=r.POD_UID):
    return dict(metadata=dict(name=name,uid=uid),spec=dict(nodeName='datax-03',containers=[dict(name='app',resources={})]),
        status=dict(containerStatuses=[dict(name='app',containerID='containerd://synthetic',restartCount=0)]))


def dep():
    return dict(metadata=dict(uid=tune.process.DEP_UID,resourceVersion='7'),spec=dict(replicas=1,
        strategy=dict(type='Recreate'),template=dict(spec=dict(containers=[dict(name='app',image='PINNED-IMAGE',
            env=[dict(name=k,value=v) for k,v in tune.CURRENT_ENV.items()]+[dict(name='KEEP',value='same')])],
            volumes=[dict(name='config',configMap=dict(name='GLM-CONFIG'))]))))


def queue(**kw):
    result=dict(pending=20,active=0,scheduled=0,retry=0,archived=0,completed=0,processed_total=100,
        failed_total=10,paused=0,oldest_pending_seconds=30.5)
    result.update(kw); return result


def snapshot():
    return dict(redis_sample_epoch=100,queues={'default':queue()},server_identity_hashes=list('abcdef'),
        effective_servers=[dict(pool=k,concurrency=v,status='active',active=0) for k,v in r.CURRENT.items()])


def health_rows(quarantine=0):
    workers=[pod('worker-'+str(i),'uid-'+str(i)) for i in range(16)]
    rows=[dict(pod=p['metadata']['name'],role='worker',counters=dict(active=i%2,waiting=0)) for i,p in enumerate(workers)]
    rows.append(dict(pod='dispatcher',role='dispatcher',counters=dict(discovered=16,live=16,quarantined=quarantine,
        ready=True,capacity_target_met=True,busy=8,waiting=0)))
    return workers,rows


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.mock(r,'a',new=r.a or activation)
        self.mock(r,'sw',new=r.sw or activation.sw)
        self.mock(r,'e',new=r.e or tune)
        self.mock(r,'obs',new=r.obs or observation)
        self.mock(r.sw,'e',new=r.e); self.mock(r.sw,'obs',new=r.obs)
        self.mock(r.sw,'a',new=SimpleNamespace(native=SimpleNamespace(call=Mock(),get=Mock())))

    def mock(self,obj,name,**kw): return self.stack.enter_context(patch.object(obj,name,**kw))

    def test_read_pinned_private_file(self):
        f=self.root/'input'; f.write_bytes(b'fixed'); f.chmod(0o600)
        self.assertEqual(r.safe_bytes(f,hashlib.sha256(b'fixed').hexdigest()),b'fixed')
        with self.assertRaisesRegex(RuntimeError,'pinned_file_hash_changed'): r.safe_bytes(f,'a'*64)

    def test_reject_symlink_and_multiple_links(self):
        f=self.root/'input'; f.write_bytes(b'fixed'); f.chmod(0o600)
        link=self.root/'link'; link.symlink_to(f)
        with self.assertRaises(OSError): r.safe_bytes(link)
        link.unlink(); os.link(f,link)
        with self.assertRaisesRegex(RuntimeError,'unsafe_private_file'): r.safe_bytes(f)

    def test_reject_writable_private_file_or_parent(self):
        f=self.root/'input'; f.write_bytes(b'fixed'); f.chmod(0o666)
        with self.assertRaises(RuntimeError): r.safe_bytes(f)
        f.chmod(0o600); self.root.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError,'unsafe_private_ancestor'): r.safe_bytes(f)
        self.root.chmod(0o700)

    def test_reject_oversized_input(self):
        f=self.root/'large'; f.write_bytes(b'x'*(4194305)); f.chmod(0o600)
        with self.assertRaises(RuntimeError): r.safe_bytes(f)

    def test_python37_grammar(self):
        ast.parse((Path(__file__).parent/'review_glm88.py').read_text(),feature_version=(3,7))

    def test_cli_has_no_mutation_mode(self):
        for arg in ('--apply','--apply-once','--reconcile','--rollback'):
            with self.subTest(arg=arg),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit): r.main([arg])

    def test_loader_uses_non_main_namespace(self):
        self.mock(r,'safe_bytes',return_value=b'raise RuntimeError("must_not_execute_in_this_test")')
        intercept=self.mock(r,'load_activation',return_value=r.a)
        self.mock(r.sw,'initialize')
        r.load_helpers(); intercept.assert_called_once()

    def test_proposal_changes_only_three_numeric_env_values(self):
        d=dep(); before=copy.deepcopy(d); candidate=r.proposal(d['spec'])
        self.assertEqual(d,before)
        env={x['name']:x['value'] for x in candidate['template']['spec']['containers'][0]['env']}
        self.assertEqual(env[r.e.review.SETTINGS['core'][1]],'16')
        self.assertEqual(env[r.e.review.SETTINGS['enrichment'][1]],'48')
        self.assertEqual(env[r.e.review.SETTINGS['model'][1]],'128')
        for key,value in r.e.CURRENT_ENV.items(): env[key]=value
        self.assertEqual(env,{x['name']:x['value'] for x in d['spec']['template']['spec']['containers'][0]['env']})
        candidate['template']['spec']['containers'][0]['env']=d['spec']['template']['spec']['containers'][0]['env']
        self.assertEqual(candidate,d['spec'])

    def test_proposal_fails_on_current_env_drift(self):
        d=dep(); d['spec']['template']['spec']['containers'][0]['env'][0]['value']='999'
        with self.assertRaises(RuntimeError): r.proposal(d['spec'])

    def test_dryrun_has_no_live_branch_and_keeps_cas(self):
        d=dep(); target=r.proposal(d['spec'])
        r.sw.a.native.call.return_value=(None,json.dumps(dict(metadata=d['metadata'],spec=target)).encode())
        ops=r.dry_run(d,target); argv=r.sw.a.native.call.call_args[0][0]
        self.assertIn('--dry-run=server',argv)
        self.assertEqual(argv[:3],['patch','deployment','weknora-app'])
        self.assertEqual([x['op'] for x in ops],['test','test','test','replace'])
        self.assertEqual(ops[-1]['path'],'/spec/template/spec/containers/0/env')

    def test_dryrun_rejects_mutating_admission(self):
        d=dep(); target=r.proposal(d['spec']); changed=copy.deepcopy(target); changed['replicas']=2
        r.sw.a.native.call.return_value=(None,json.dumps(dict(metadata=d['metadata'],spec=changed)).encode())
        with self.assertRaisesRegex(RuntimeError,'server_admission_changed_candidate'): r.dry_run(d,target)

    def test_current_requires_exact_post_glm_uid(self):
        self.mock(r.e,'deployment',return_value=dep()); self.mock(r.sw,'validate_new_pod',return_value=pod(uid='OLD'))
        with self.assertRaisesRegex(RuntimeError,'post_glm_pod_changed'): r.current({})

    def test_current_preserves_container_and_restart_identity(self):
        self.mock(r.e,'deployment',return_value=dep()); self.mock(r.sw,'validate_new_pod',return_value=pod())
        with self.assertRaisesRegex(RuntimeError,'process_changed'): r.current({},['wrong',[]])

    def test_startup_proof_is_readonly_exact_uid(self):
        self.mock(r,'ACTIVATION',new=self.root); (self.root/'MODEL-STARTUP-1.json').write_text('{}')
        reader=self.mock(r.sw.recovery,'read',return_value=(dict(pod_identity=r.obs.identity(pod()),limit=64,
            distributed_via_redis=True,raw_logs_saved=False),'x'))
        self.assertEqual(r.startup_proof(pod())['limit'],64); reader.assert_called_once()
        reader.return_value=(dict(pod_identity=r.obs.identity(pod()),limit=32),'x')
        with self.assertRaises(RuntimeError): r.startup_proof(pod())

    def test_missing_startup_proof_does_not_write(self):
        self.mock(r,'ACTIVATION',new=self.root)
        with self.assertRaisesRegex(RuntimeError,'startup_receipt_missing'): r.startup_proof(pod())
        self.assertEqual(list(self.root.iterdir()),[])

    def test_glm_policy_drift_stops(self):
        self.mock(r.a,'database_state',return_value='DRIFT_REVIEW_REQUIRED')
        with self.assertRaisesRegex(RuntimeError,'glm_model_or_reference_policy_drift'): r.check_glm(pod(),{})

    def test_glm_document_override_stops(self):
        self.mock(r.a,'database_state',return_value='GLM'); self.mock(r.a,'sql_read',return_value={'legacy':1,'inline':0})
        with self.assertRaisesRegex(RuntimeError,'unreviewed_document_override'): r.check_glm(pod(),{})

    def test_queue_projection_does_not_misname_stopped_drain(self):
        s=snapshot(); value=r.queue_projection(s)
        self.assertEqual(value['queues']['default']['pending'],20)
        self.assertEqual(value['pools'][0]['status'],'active')
        self.assertNotIn('claimed_work_finished',value)
        self.assertFalse(value['task_payloads_read'])

    def test_counter_trends_never_claim_documents_per_second(self):
        x=r.queue_projection(snapshot()); y=copy.deepcopy(x); y['epoch']+=60
        y['queues']['default'].update(pending=10,processed_total=114,failed_total=12)
        trend=r.queue_trends([{'queue':x},{'queue':y}])['default']
        self.assertEqual(trend['processed_counter_delta'],14); self.assertEqual(trend['failed_counter_delta'],2)
        self.assertEqual(trend['pending_delta'],-10); self.assertEqual(trend['window_seconds'],60)
        self.assertNotIn('completed_documents',trend)

    def test_counter_reset_is_not_negative_throughput(self):
        x=r.queue_projection(snapshot()); y=copy.deepcopy(x); y['queues']['default']['processed_total']=0
        trend=r.queue_trends([{'queue':x},{'queue':y}])['default']
        self.assertTrue(trend['counter_reset_or_rollover']); self.assertIsNone(trend['processed_counter_delta'])

    def test_empty_samples_and_missing_queue_coverage(self):
        self.assertEqual(r.queue_trends([]),{})
        x=r.queue_projection(snapshot()); y=copy.deepcopy(x); y['queues']={}
        self.assertEqual(r.queue_trends([{'queue':x},{'queue':y}])['default']['coverage'],'INCOMPLETE')

    def test_strict_mineru16_not_degraded56(self):
        workers,states=health_rows()
        self.assertEqual(r.mineru_check(states,workers)['active_workers'],8)
        for field,value in [('quarantined',1),('live',15),('discovered',15),('ready',False),('capacity_target_met',False)]:
            changed=copy.deepcopy(states); changed[-1]['counters'][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError): r.mineru_check(changed,workers)

    def test_worker_serialization_wait_and_coverage(self):
        workers,states=health_rows()
        for changed in (states[:-1],states+states[:1]):
            with self.assertRaises(RuntimeError): r.mineru_check(changed,workers)
        for field,value in [('active',2),('active',True),('waiting',1)]:
            changed=copy.deepcopy(states); changed[0]['counters'][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError): r.mineru_check(changed,workers)

    def test_endpoint_cannot_include_old_or_unready_pod(self):
        for body in ({'subsets':[]},{'subsets':[{'addresses':[{'targetRef':{'uid':'old'}}]}]},
                     {'subsets':[{'notReadyAddresses':[{}]}]}):
            r.sw.a.native.get.return_value=body
            with self.assertRaises(RuntimeError): r.app_endpoint(pod())

    def setup_collect(self,quarantine=0):
        plan=dict(placement={},candidate_binary='a'*64,configmap={},document_catalog_after={})
        self.mock(r,'baseline_receipt',return_value=plan); self.mock(r,'current',return_value=(dep(),pod()))
        self.mock(r,'startup_proof',return_value={'limit':64}); self.mock(r,'check_glm',return_value={'glm':23965,'legacy':0,'inline':0})
        self.mock(r,'app_endpoint'); self.mock(r.e,'runtime_env'); self.mock(r.sw,'placement',return_value={})
        self.mock(r.sw,'binary',return_value={'pid':1}); self.mock(r.obs,'read_expansion')
        workers,states=health_rows(quarantine)
        pods={name:[] for name in r.obs.WORKERS}; pods[r.obs.WORKERS[0]]=workers
        pods['weknora-app']=[pod()]; pods[r.obs.DISPATCHER]=[pod('dispatcher','dispatcher-uid')]
        self.mock(r.obs,'baseline',return_value=({},pods,{'same':True},{}))
        self.mock(r.e,'redis_binding',return_value='bound'); self.mock(r.e,'snapshot',side_effect=lambda _:copy.deepcopy(snapshot()))
        self.mock(r.obs,'health',side_effect=lambda p:next(x for x in states if x['pod']==p['metadata']['name']))
        self.mock(r.e,'database',return_value={'connections':{'all':10},'models':[],'settings':[]})
        self.mock(r.obs,'resource_snapshot',return_value=[{'node':'datax-03'}]); resource=self.mock(r.e,'check_resource_rows')
        self.mock(r.sw.recovery,'read',return_value=({},'a'*64)); self.mock(r.time,'sleep')
        dry=self.mock(r,'dry_run',return_value=[{'op':'test'}])
        report=dict(samples=[],blockers=[],glm88_review='INCOMPLETE')
        return report,dry,resource

    def test_complete_collection_is_dryrun_proposal_not_apply(self):
        report,dry,resource=self.setup_collect(); r.collect(self.root,report)
        self.assertEqual(report['glm88_review'],'PASS'); self.assertEqual(len(report['samples']),5)
        self.assertEqual(resource.call_count,3); dry.assert_called_once()
        proposal=json.loads((self.root/'PROPOSAL.private.json').read_bytes())
        self.assertFalse(proposal['apply_permitted']); self.assertTrue(proposal['fresh_apply_preflight_required'])
        self.assertEqual(proposal['target_model_default'],128)
        self.assertEqual((self.root/'PROPOSAL.private.json').stat().st_mode&0o777,0o600)
        r.sw.a.native.call.assert_not_called()

    def test_degraded_mineru_collects_evidence_but_no_dryrun(self):
        report,dry,_=self.setup_collect(quarantine=1); r.collect(self.root,report)
        self.assertEqual(report['glm88_review'],'BLOCKED'); self.assertEqual(len(report['blockers']),5)
        dry.assert_not_called(); self.assertFalse((self.root/'PROPOSAL.private.json').exists())

    def test_resource_failure_does_not_stop_consumers_or_create_proposal(self):
        report,dry,resource=self.setup_collect(); resource.side_effect=RuntimeError('node_memory_headroom_below_24GiB')
        r.collect(self.root,report)
        self.assertEqual(report['glm88_review'],'BLOCKED'); self.assertEqual(len(report['blockers']),3); dry.assert_not_called()

    def test_baseline_drift_prevents_proposal(self):
        report,dry,_=self.setup_collect(); original=r.obs.baseline.return_value
        r.obs.baseline.side_effect=[original,(original[0],original[1],{'changed':True},original[3])]
        with self.assertRaisesRegex(RuntimeError,'dependencies_changed'): r.collect(self.root,report)
        dry.assert_not_called()

    def test_public_summary_omits_private_plan_credentials_and_policies(self):
        summary=r.public_summary(dict(glm88_review='PASS',samples=[],secret='do-not-emit',candidate={'env':'private'}))
        self.assertNotIn('secret',summary); self.assertNotIn('candidate',summary)
        self.assertFalse(summary['ready_to_apply']); self.assertEqual(summary['production_writes'],0)
        self.assertEqual(summary['max_proposed_parse_handlers'],20); self.assertEqual(summary['interactive_slots_reserved'],0)
        self.assertTrue(summary['user_reported_glm_business_acceptance'])
        self.assertFalse(summary['business_acceptance_independently_replayed'])

    def test_status_does_not_load_helpers_or_write(self):
        self.mock(r.socket,'gethostname',return_value='datax-01')
        self.mock(r,'safe_bytes',return_value=json.dumps(dict(glm88_review='PASS',samples=[])).encode())
        loader=self.mock(r,'load_helpers')
        self.assertEqual(r.main(['--status','/var/lib/weknora-glm88-review-abcdefgh']),0); loader.assert_not_called()
        for bad in ('/','/var/lib','/var/lib/elsewhere','relative'):
            with self.subTest(bad=bad),self.assertRaises(RuntimeError): r.main(['--status',bad])


if __name__=='__main__': unittest.main()
