import ast
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import switch_image56 as r
import tune_weknora88 as consumer
import observe_pipeline as obs
from test_activation_review import deployment


def plan():
    d=deployment()
    _,target=r.activation.image_patch(d)
    return dict(original=d['spec'],candidate=target,old_pod_uid=r.old.POD_UID,
        old_identity=[r.old.POD_UID,[['app','containerd://original',0]]],old_binary='a'*64,
        candidate_binary='b'*64,process={'start_ticks':123},server_ids=list('abcdef'),
        placement={'node':'datax-03'},model_state={'state':'unchanged'},
        other_workloads={'pods':[['uid',[['container','id',0]]]]})


def pod(candidate=False):
    return dict(metadata={'uid':'new-uid' if candidate else r.old.POD_UID,'name':'new' if candidate else 'old'},
        status={'containerStatuses':[dict(name='app',containerID='containerd://new' if candidate else 'containerd://original',
            restartCount=0,imageID=r.image.CONFIG if candidate else r.old.CONFIG)]})


def snapshot(state='stopped',active=0,pending=8000):
    return dict(server_identity_hashes=list('abcdef'),effective_servers=[
        dict(pool=k,concurrency=v,status=state,active=active if k=='core' else 0) for k,v in r.CAPS.items()],
        queues={'default':dict(active=active,pending=pending)})


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))/'attempt'
        self.root.mkdir(mode=0o700)
        self.stack.enter_context(patch.object(r,'ROOT',self.root))
        self.stack.enter_context(patch.object(r,'e',consumer))
        self.stack.enter_context(patch.object(r,'obs',obs))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def patch(self,obj,key,**kw):
        return self.stack.enter_context(patch.object(obj,key,**kw))

    def test_private_exclusive_journal(self):
        r.save('x.json',{'value':True})
        self.assertEqual((self.root/'x.json').stat().st_mode&0o777,0o600)
        with self.assertRaises(FileExistsError): r.save('x.json',{})
        self.assertEqual(r.read('x.json'),{'value':True})

    def test_unsafe_parent_not_repaired(self):
        self.root.chmod(0o777)
        with self.assertRaises(Exception): r.save('x.json',{})
        self.assertEqual(self.root.stat().st_mode&0o777,0o777)

    def test_canonical_process_identity(self):
        self.assertTrue(r.same(obs.identity(pod()),plan()['old_identity']))
        p=pod(); p['status']['containerStatuses'][0]['restartCount']=1
        self.assertFalse(r.same(obs.identity(p),plan()['old_identity']))

    def test_exact_image_only_plan(self):
        r.check_plan_shape(plan())
        for part in ('replicas','env','image','uid','binary'):
            p=plan()
            if part=='replicas': p['candidate']['replicas']=2
            if part=='env': p['candidate']['template']['spec']['containers'][0]['env']=[]
            if part=='image': p['candidate']['template']['spec']['containers'][0]['image']='other'
            if part=='uid': p['old_pod_uid']='other'
            if part=='binary': p['candidate_binary']='bad'
            with self.subTest(part=part),self.assertRaises(Exception): r.check_plan_shape(p)

    def test_sql_whitelist_never_selects_secret_values(self):
        self.assertTrue(r.MODEL_SQL.startswith('BEGIN READ ONLY;'))
        self.assertTrue(r.MODEL_SQL.endswith('ROLLBACK;'))
        for token in ('api_key','custom_headers','app_secret','parameters,','UPDATE ','DELETE ','INSERT '):
            self.assertNotIn(token,r.MODEL_SQL)
        self.assertIn("statement_timeout='5s'",r.MODEL_SQL)

    def test_model_state_fails_closed(self):
        good={'read_only':'on','schema':[{'version':90,'dirty':False}],'vision_signature':'a'*32}
        sql=self.patch(r,'db_sql',return_value=good)
        self.assertEqual(r.model_state(pod()),good)
        for key,value in [('read_only','off'),('schema',[{'version':91,'dirty':False}]),('vision_signature',None)]:
            sql.return_value=dict(good,**{key:value})
            with self.subTest(key=key),self.assertRaises(Exception): r.model_state(pod())

    def test_policy_contract_exact(self):
        row=dict(definition='definition',body='body',function_schema='public',function_name='test',
                 table_name='models',enabled='O',trigger_type=7,identity_arguments='',security_definer=False,settings=None)
        h=r.activation.sha(b'definition')
        self.patch(r,'POLICIES',new={h:('models','test',r.activation.sha(b'body'),7)})
        self.assertEqual(r.policy_check([row]),[h])
        for key,value in [('body','tampered'),('definition','unknown'),('enabled','D'),('trigger_type',23),
                          ('security_definer',True),('settings',['search_path=other']),('table_name','other')]:
            with self.subTest(key=key),self.assertRaises(Exception): r.policy_check([dict(row,**{key:value})])
        with self.assertRaises(Exception): r.policy_check([row,row])

    def test_two_legacy_trigger_source_hashes_match_catalog(self):
        repo=Path(__file__).resolve().parents[3]
        cases=[('k8s/enterprise/weknora-mineru-defaults.sql','deerflow_ensure_mineru_parser_rule'),
            ('release/production-build-alignment-20260928/external/manage-weknora-spreadsheet-policy-datax-01.sh',
             'weknora_document_stability_r2_spreadsheet_default')]
        import re
        for filename,name in cases:
            source=(repo/filename).read_text(encoding='utf-8')
            source=source.replace('${FUNCTION_NAME}',name).replace('\\$','$')
            match=re.search(r'CREATE OR REPLACE FUNCTION public\.'+name+r'\(\).*?AS (\$[A-Za-z_]*\$)(.*?)\1',source,re.S|re.I)
            self.assertIsNotNone(match)
            expected=next(v[2] for v in r.POLICIES.values() if v[1]==name)
            self.assertEqual(r.activation.sha(match[2].encode()),expected)

    def test_signal_journal_before_exact_single_signal(self):
        p=plan(); self.patch(r,'fresh',return_value=(deployment(),pod(),snapshot('active')))
        self.patch(r,'patch',return_value={'spec':p['candidate']})
        self.patch(r,'binary',return_value=p['process'])
        def call(argv,**kw):
            self.assertTrue((self.root/'SIGNAL-REQUESTED.json').exists())
            self.assertIn(consumer.SIGNAL_CODE,argv)
            return json.dumps(dict(tstp_sent_once=True,pid=1,start_ticks=123))
        command=self.patch(obs,'call',side_effect=call)
        r.signal_once(p,{})
        self.assertEqual(command.call_count,1)
        with self.assertRaises(Exception): r.signal_once(p,{})
        self.assertEqual(command.call_count,1)

    def test_dryrun_drift_never_signals(self):
        self.patch(r,'fresh',return_value=(deployment(),pod(),snapshot('active')))
        self.patch(r,'patch',return_value={'spec':{'wrong':True}})
        command=self.patch(obs,'call')
        with self.assertRaises(Exception): r.signal_once(plan(),{})
        command.assert_not_called()
        self.assertFalse((self.root/'SIGNAL-REQUESTED.json').exists())

    def test_uncertain_signal_journal_retained(self):
        p=plan(); self.patch(r,'fresh',return_value=(deployment(),pod(),snapshot('active')))
        self.patch(r,'patch',return_value={'spec':p['candidate']}); self.patch(r,'binary',return_value=p['process'])
        command=self.patch(obs,'call',side_effect=RuntimeError('timeout'))
        with self.assertRaises(RuntimeError): r.signal_once(p,{})
        self.assertTrue((self.root/'SIGNAL-REQUESTED.json').exists())
        self.assertFalse((self.root/'SIGNAL-ACK.json').exists())
        with self.assertRaises(Exception): r.signal_once(p,{})
        self.assertEqual(command.call_count,1)

    def setup_drain(self,samples):
        self.patch(consumer,'deployment',return_value=deployment())
        self.patch(consumer,'app_for',return_value=pod())
        self.patch(consumer,'redis_binding',return_value='binding')
        snap=self.patch(consumer,'snapshot',side_effect=samples)
        self.patch(r.time,'sleep')
        return snap

    def test_pending_queue_need_not_empty_three_zero_samples(self):
        snap=self.setup_drain([snapshot()]*3); report={}
        r.wait_drain(json.loads(json.dumps(plan())),report)
        self.assertEqual(snap.call_count,3)
        self.assertEqual(report['live_stop_behavior'],'SIX_POOLS_STOPPED_AND_ZERO_ACTIVE_CONFIRMED')
        evidence=list(self.root.glob('DRAIN-VERIFIED-*.json'))
        self.assertEqual(len(evidence),1)
        self.assertEqual(r.read(evidence[0].name)['samples'][0]['pending_retained'],8000)

    def test_active_work_resets_zero_samples(self):
        snap=self.setup_drain([snapshot(),snapshot(active=1),snapshot(),snapshot(),snapshot()])
        r.wait_drain(plan(),{}); self.assertEqual(snap.call_count,5)

    def test_process_change_during_drain_blocks(self):
        self.setup_drain([snapshot()]*3)
        changed=pod(); changed['metadata']['uid']='other'
        consumer.app_for.return_value=changed
        with self.assertRaisesRegex(Exception,'original_process_changed'): r.wait_drain(plan(),{})

    def test_pool_server_change_during_drain_blocks(self):
        bad=snapshot(); bad['server_identity_hashes']=['changed']
        self.setup_drain([bad])
        with self.assertRaisesRegex(Exception,'consumer_identity_changed'): r.wait_drain(plan(),{})

    def test_drain_deadline_does_not_patch_or_force_terminate(self):
        self.setup_drain([snapshot(active=1)])
        self.patch(r.time,'monotonic',side_effect=[0,1801])
        patcher=self.patch(r,'patch')
        with self.assertRaisesRegex(Exception,'claimed_work_deadline'): r.wait_drain(plan(),{})
        patcher.assert_not_called()

    def test_patch_journal_after_drain_before_cas(self):
        p=plan(); order=[]
        self.patch(r,'wait_drain',side_effect=lambda *a:order.append('drain'))
        self.patch(r,'fresh',side_effect=lambda *a,**k:(order.append('fresh') or deployment(),pod(),snapshot()))
        def apply(dep,plan,dry):
            order.append('patch'); self.assertFalse(dry)
            self.assertTrue((self.root/'PATCH-REQUESTED.json').exists())
            return {'metadata':{'uid':consumer.process.DEP_UID,'generation':69},'spec':p['candidate']}
        self.patch(r,'patch',side_effect=apply)
        r.apply_after_drain(p,{})
        self.assertEqual(order,['drain','fresh','patch'])
        with self.assertRaises(Exception): r.apply_after_drain(p,{})
        self.assertEqual(order,['drain','fresh','patch'])

    def test_uncertain_patch_never_replayed(self):
        r.save('PATCH-REQUESTED.json',{})
        self.patch(consumer,'deployment',return_value=deployment())
        signal=self.patch(r,'signal_once'); patcher=self.patch(r,'apply_after_drain'); accept=self.patch(r,'accept')
        with self.assertRaisesRegex(Exception,'NOT_REPLAYED'): r.advance(plan(),{},False)
        signal.assert_not_called(); patcher.assert_not_called(); accept.assert_not_called()

    def test_confirmed_candidate_reconcile_only_accepts(self):
        p=plan(); r.save('PATCH-REQUESTED.json',{})
        self.patch(consumer,'deployment',return_value={'spec':p['candidate']})
        signal=self.patch(r,'signal_once'); patcher=self.patch(r,'apply_after_drain'); accept=self.patch(r,'accept')
        r.advance(p,{},False)
        signal.assert_not_called(); patcher.assert_not_called(); accept.assert_called_once()

    def test_signalled_reconcile_does_not_resignal(self):
        p=plan(); r.save('SIGNAL-REQUESTED.json',dict(pod_identity=p['old_identity'],start_ticks=123))
        signal=self.patch(r,'signal_once'); patcher=self.patch(r,'apply_after_drain'); accept=self.patch(r,'accept')
        r.advance(p,{},False)
        signal.assert_not_called(); patcher.assert_called_once(); accept.assert_called_once()

    def test_missing_signal_journal_cannot_continue(self):
        signal=self.patch(r,'signal_once'); patcher=self.patch(r,'apply_after_drain')
        with self.assertRaises(FileNotFoundError): r.advance(plan(),{},False)
        signal.assert_not_called(); patcher.assert_not_called()

    def test_startup_receipt_survives_json_roundtrip_and_log_rotation(self):
        r.save('MODEL-STARTUP-test.json',dict(pod_identity=obs.identity(pod(True)),limit=64,
                distributed_via_redis=True,raw_logs_saved=False))
        reader=self.patch(consumer,'capture_startup_limit')
        r.capture_startup(pod(True)); reader.assert_not_called()

    def test_startup_receipt_wrong_limit_blocks(self):
        r.save('MODEL-STARTUP-test.json',dict(pod_identity=obs.identity(pod(True)),limit=88,
                distributed_via_redis=True,raw_logs_saved=False))
        with self.assertRaisesRegex(Exception,'startup_receipt_changed'): r.capture_startup(pod(True))

    def test_new_pod_identity_and_image(self):
        p=plan(); d=deployment(); d['spec']=p['candidate']; d['metadata']['generation']=69
        d['status']=dict(observedGeneration=69,replicas=1,updatedReplicas=1,readyReplicas=1,availableReplicas=1)
        reader=self.patch(consumer,'app_for',return_value=pod(True))
        self.assertEqual(r.validate_new_pod(d,p)['metadata']['uid'],'new-uid')
        for field,value in [('restartCount',1),('imageID','wrong')]:
            bad=pod(True); bad['status']['containerStatuses'][0][field]=value; reader.return_value=bad
            with self.assertRaisesRegex(Exception,'candidate_image_or_restart_changed'): r.validate_new_pod(d,p)
        reader.return_value=pod()
        with self.assertRaisesRegex(Exception,'original_pod_still_serving'): r.validate_new_pod(d,p)

    def test_status_does_not_write_or_signal(self):
        save=self.patch(r,'save'); signal=self.patch(r,'signal_once'); patcher=self.patch(r,'patch')
        self.assertEqual(r.status(),0)
        save.assert_not_called(); signal.assert_not_called(); patcher.assert_not_called()

    def test_frozen_readers_have_expected_contract(self):
        self.assertEqual(consumer.CURRENT,r.CAPS); self.assertEqual(consumer.CURRENT_MODEL,64)
        self.assertTrue(consumer.drain_complete(snapshot()))
        self.assertFalse(consumer.drain_complete(snapshot(active=1)))
        ast.parse(consumer.SIGNAL_CODE,feature_version=(3,7))
        self.assertEqual(consumer.SIGNAL_CODE.count('os.kill('),1)

    def setup_accept(self):
        p=plan(); new=pod(True)
        d=deployment(); d['spec']=p['candidate']; d['metadata']['generation']=69
        d['status']=dict(observedGeneration=69,replicas=1,updatedReplicas=1,readyReplicas=1,availableReplicas=1)
        self.patch(consumer,'deployment',return_value=d); self.patch(consumer,'app_for',return_value=new)
        self.patch(r,'capture_startup'); self.patch(consumer,'redis_binding',return_value='binding')
        snap=self.patch(consumer,'snapshot',return_value=snapshot('active',active=8))
        self.patch(r.time,'sleep'); self.patch(r,'binary'); self.patch(consumer,'runtime_env')
        self.patch(consumer,'database'); self.patch(consumer,'resources')
        self.patch(r,'placement',return_value=p['placement']); self.patch(r,'configs')
        self.patch(r,'model_state',return_value=p['model_state']); self.patch(r,'policy_check')
        self.patch(r,'db_sql',return_value={'policies':[]})
        self.patch(r,'health',return_value={'mineru':{'counters':{'live':16}},'other_workloads':p['other_workloads']})
        native=SimpleNamespace(get=Mock(return_value={'subsets':[{'addresses':[{'targetRef':{'uid':'new-uid'}}]}]}))
        self.patch(r,'a',new=SimpleNamespace(native=native))
        return p,snap,native

    def test_new_image_acceptance_keeps56_model64(self):
        p,snap,_=self.setup_accept(); report={}
        r.accept(p,report)
        self.assertEqual(snap.call_count,3)
        self.assertEqual(report['effective_worker_slots'],56)
        self.assertEqual(report['model_governor_startup_limit'],64)
        self.assertEqual(report['model_configuration'],'UNCHANGED_QWEN_STILL_ACTIVE')
        self.assertTrue((self.root/'SUCCESS.json').exists())
        self.assertEqual(report['glm_activation'],'PENDING_SEPARATE_CONTROLLED_STEP')

    def test_foreign_endpoint_blocks_acceptance(self):
        p,_,native=self.setup_accept()
        native.get.return_value={'subsets':[{'addresses':[{'targetRef':{'uid':'foreign'}}]}]}
        with self.assertRaisesRegex(Exception,'app_service_not_exact_candidate'): r.accept(p,{})
        self.assertFalse((self.root/'SUCCESS.json').exists())

    def test_wrong_new_capacity_never_passes(self):
        p,snap,_=self.setup_accept(); bad=snapshot('active'); bad['effective_servers'][0]['concurrency']=88
        snap.return_value=bad
        with self.assertRaisesRegex(Exception,'effective_capacity_mismatch'): r.accept(p,{})
        self.assertFalse((self.root/'SUCCESS.json').exists())

    def test_real_cas_wire_payload_no_live_retry(self):
        p=plan(); dep=deployment()
        native=SimpleNamespace(call=Mock(return_value=(0,json.dumps({'spec':p['candidate']}))))
        self.patch(r,'a',new=SimpleNamespace(native=native))
        r.patch(dep,p,dry=True)
        args,kw=native.call.call_args
        self.assertIn('--dry-run=server',args[0]); self.assertIn('--patch-file=/dev/stdin',args[0])
        operations=json.loads(kw['data'])
        self.assertEqual([x['op'] for x in operations],['test','test','test','replace'])
        self.assertEqual(operations[-1]['path'],'/spec/template/spec/containers/0/image')
        native.call.side_effect=RuntimeError('timeout')
        with self.assertRaises(RuntimeError): r.patch(dep,p,dry=False)
        self.assertEqual(native.call.call_count,2)

    def test_full_fresh_checks_canonical_json_dependency_identity(self):
        p=plan()
        self.patch(consumer,'deployment',return_value=deployment()); self.patch(consumer,'app_for',return_value=pod())
        self.patch(consumer,'runtime_env'); self.patch(consumer,'database'); self.patch(consumer,'resources')
        self.patch(r,'placement',return_value=p['placement']); self.patch(r,'configs')
        self.patch(r,'model_state',return_value=p['model_state']); self.patch(r,'policy_check')
        self.patch(r,'db_sql',return_value={'policies':[]})
        self.patch(r,'health',return_value={'other_workloads':{'pods':[['uid',[('container','id',0)]]]}})
        self.patch(consumer,'redis_binding',return_value='binding'); snap=self.patch(consumer,'snapshot',return_value=snapshot())
        r.fresh(p,stopped=True)
        snap.return_value=snapshot(active=1)
        with self.assertRaisesRegex(Exception,'claimed_work_not_finished'): r.fresh(p,stopped=True)

    def setup_prepare(self):
        source=self.root/'review'; source.mkdir(mode=0o700)
        from export_weknora_base import write_new
        self.patch(r.old,'ROOT',new=source)
        result=dict(activation_review='PASS',blockers=['UNKNOWN_DB_TRIGGER'],image_only_server_dryrun='PASS',current_worker_slots=56)
        write_new('RESULT.json',result); write_new('POLICIES.private.json',{'policies':[]})
        self.patch(r.activation,'SOURCE',new=source)
        self.patch(r,'REVIEW_SHA',new=r.activation.sha((source/'RESULT.json').read_bytes()))
        build=dict(database_migrations=[],component=dict(image=r.image.IMAGE,manifest_digest=r.image.MANIFEST,
            config_digest=r.image.CONFIG,runtime_files={'app/WeKnora':{'sha256':'b'*64}}))
        raw=json.dumps(build).encode(); self.patch(r.image,'REPORT_SHA',new=r.activation.sha(raw))
        guard=SimpleNamespace(live=Mock())
        q=SimpleNamespace(package=Mock(return_value=(None,guard,{})),read=Mock(return_value=raw))
        self.patch(r,'a',new=SimpleNamespace(native=SimpleNamespace(initialize=lambda:(None,q))))
        self.patch(r.release,'accepted'); self.patch(r,'policy_check')
        self.patch(consumer,'deployment',return_value=deployment()); self.patch(consumer,'app_for',return_value=pod())
        self.patch(r,'binary',return_value={'start_ticks':123}); self.patch(consumer,'redis_binding',return_value='binding')
        self.patch(consumer,'snapshot',return_value=snapshot('active'))
        self.patch(r,'health',return_value={'other_workloads':{}})
        self.patch(r,'placement',return_value={}); self.patch(r,'model_state',return_value={})
        self.patch(r,'fresh'); self.patch(r,'patch',return_value={'spec':plan()['candidate']})
        return guard

    def test_prepare_binds_review_before_plan_and_signal(self):
        guard=self.setup_prepare(); signal=self.patch(r,'signal_once')
        report={}; got=r.prepare(report)
        guard.live.assert_called_once(); signal.assert_not_called()
        self.assertEqual(got['candidate'],plan()['candidate'])
        self.assertEqual(report['preflight'],'PASS')
        self.assertTrue(report['rollback_node_metadata_rechecked'])
        self.assertEqual(r.read_plan()['candidate'],plan()['candidate'])

    def test_preflight_failure_leaves_no_signal_or_prepared_receipt(self):
        self.setup_prepare(); r.fresh.side_effect=RuntimeError('fresh_drift')
        signal=self.patch(r,'signal_once')
        with self.assertRaisesRegex(RuntimeError,'fresh_drift'): r.prepare({})
        signal.assert_not_called()
        self.assertFalse((self.root/'PREPARED.json').exists())
        self.assertFalse((self.root/'SIGNAL-REQUESTED.json').exists())


if __name__=='__main__': unittest.main()
