import ast
import contextlib
from contextlib import ExitStack
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import recover_weknora56_consumers as r
import tune_weknora88 as e
from test_tune_weknora88 import dep, pod, snap


class Tests(unittest.TestCase):
    def setUp(self):
        self.engine = patch.object(r, 'e', e); self.engine.start()
        self.observer = patch.object(r, 'obs', e.obs); self.observer.start()
        self.addCleanup(self.engine.stop); self.addCleanup(self.observer.stop)

    def test_annotation_only(self):
        original = dep()['spec']; before = copy.deepcopy(original)
        target = r.restart_spec(original, 'a'*32)
        self.assertEqual(original, before)
        self.assertEqual(target['template']['spec'], before['template']['spec'])
        self.assertEqual(target['replicas'], before['replicas'])
        self.assertEqual(target['template']['metadata']['annotations'][r.ANNOTATION], 'a'*32)

    def test_preserve_existing_metadata(self):
        source = dep()['spec']
        source['template']['metadata'] = {'labels':{'app':'weknora-app'}, 'annotations':{'keep':'same'}}
        target = r.restart_spec(source, 'b'*32)
        self.assertEqual(target['template']['metadata']['labels'], {'app':'weknora-app'})
        self.assertEqual(target['template']['metadata']['annotations']['keep'], 'same')

    def test_no_reuse_existing_marker(self):
        source = r.restart_spec(dep()['spec'], 'a'*32)
        with self.assertRaisesRegex(r.helper.AuditFailure, 'already_present'):
            r.restart_spec(source, 'b'*32)

    def test_token_is_fixed_hex(self):
        for token in ('', 'a'*31, '../path', 'g'*32):
            with self.assertRaises(r.helper.AuditFailure):
                r.restart_spec(dep()['spec'], token)

    def test_patch_has_full_cas_and_only_metadata_write(self):
        d = dep(); target = r.restart_spec(d['spec'], 'c'*32)
        ops = r.restart_patch(d, target, 'c'*32)
        self.assertEqual([x['op'] for x in ops], ['test','test','test','add'])
        self.assertEqual([x['path'] for x in ops], ['/metadata/uid','/metadata/resourceVersion','/spec','/spec/template/metadata'])

    def test_patch_rejects_env_or_image_or_scale_drift(self):
        d = dep()
        for mutate in (lambda s:s.update(replicas=2),
                       lambda s:s['template']['spec']['containers'][0].update(image='other'),
                       lambda s:s['template']['spec']['containers'][0]['env'].append({'name':'x','value':'y'})):
            target = r.restart_spec(d['spec'], 'a'*32); mutate(target)
            with self.assertRaisesRegex(r.helper.AuditFailure, 'annotation_only'):
                r.restart_patch(d, target, 'a'*32)

    def test_degraded_recovery_not_88_approval(self):
        h = {'role':'dispatcher','counters':{'discovered':16,'live':16,'quarantined':2,'ready':True}}
        safe = r.check_recovery_health(h)
        self.assertEqual(safe['usable_lower_bound'],14)
        self.assertFalse(safe['expansion_approved'])
        self.assertEqual(safe['state'],'DEGRADED_56_RECOVERY_ONLY')
        for key,val in [('live',15),('quarantined',3),('quarantined',True),('ready',False)]:
            bad = copy.deepcopy(h); bad['counters'][key] = val
            with self.assertRaises(r.helper.AuditFailure):
                r.check_recovery_health(bad)

    def test_other_baseline_excludes_only_app(self):
        before = {'deployments':{'weknora-app':'old','mineru':'same'},'pods':{'weknora-app':'old','mineru':'same'},'service':'same'}
        after = copy.deepcopy(before)
        after['deployments']['weknora-app']='new'; after['pods']['weknora-app']='new'
        self.assertEqual(r.other_baseline(before), r.other_baseline(after))
        after['pods']['mineru']='new'
        self.assertNotEqual(r.other_baseline(before), r.other_baseline(after))

    def test_drain_three_samples_without_resignal_or_empty_queue(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(r, 'ROOT', Path(tmp)), \
             patch.object(e,'deployment',return_value=dep()), patch.object(r,'exact_app',return_value=pod()), \
             patch.object(e,'redis_binding',return_value=None), patch.object(e,'snapshot',return_value=snap()) as sample, \
             patch.object(r,'write'), patch.object(r.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            r.confirm_stopped({'original':dep()['spec'], 'original_pod':['same'], 'server_ids':sorted(e.CURRENT)}, {})
            self.assertEqual(sample.call_count,3)

    def test_active_handler_blocks_before_restart(self):
        with patch.object(e,'deployment',return_value=dep()), patch.object(r,'exact_app',return_value=pod()), \
             patch.object(e,'redis_binding',return_value=None), patch.object(e,'snapshot',return_value=snap(active=1)), \
             patch.object(r,'write') as write:
            with self.assertRaisesRegex(r.helper.AuditFailure,'claimed_work'):
                r.confirm_stopped({'original':dep()['spec'], 'original_pod':['same'], 'server_ids':sorted(e.CURRENT)}, {})
            write.assert_not_called()

    def test_signal_receipt_does_not_authorize_another_signal(self):
        raw = Path(r.__file__).read_text(encoding='utf-8')
        tree = ast.parse(raw, feature_version=(3,7))
        calls = [ast.unparse(x.func) for x in ast.walk(tree) if isinstance(x,ast.Call)]
        self.assertNotIn('os.kill',calls)
        self.assertNotIn('e.mutate',calls)
        self.assertNotIn('e.check_dependencies',calls)
        self.assertNotIn('e.main',calls)

    def test_reconcile_never_patches(self):
        with patch.object(r,'exists',return_value=True), patch.object(r,'load_plan',return_value={'target':dep()['spec']}), \
             patch.object(e,'deployment',return_value=dep()), patch.object(r,'accept') as accept, \
             patch.object(e,'patch_call') as patch_call:
            r.reconcile({})
            accept.assert_called_once(); patch_call.assert_not_called()

    def test_reconcile_original_after_uncertain_patch_refuses_resubmit(self):
        with patch.object(r,'exists',return_value=True), patch.object(r,'load_plan',return_value={'target':{'different':True}}), \
             patch.object(e,'deployment',return_value=dep()), patch.object(e,'patch_call') as pc:
            with self.assertRaisesRegex(r.helper.AuditFailure,'no_resubmit'):
                r.reconcile({})
            pc.assert_not_called()

    def test_recovery_request_marker_precedes_live_patch(self):
        text = Path(r.__file__).read_text(encoding='utf-8')
        body = text[text.index('def recover('):text.index('def accept(')]
        self.assertLess(body.index("write(ROOT/'PATCH-REQUESTED.json'"), body.index('e.patch_call(patchfile,False)'))
        self.assertEqual(body.count('e.patch_call(patchfile,False)'),1)

    def test_durable_identity_retains_all_fields(self):
        p = pod(); ident = e.obs.identity(p)
        durable = json.loads(json.dumps(ident))
        self.assertNotEqual(ident,durable)
        with patch.object(e,'app_for',return_value=p):
            self.assertEqual(r.exact_app(dep(),dep()['spec'],durable),p)
            durable[1][0][2] += 1
            with self.assertRaisesRegex(r.helper.AuditFailure,'app_process_changed'):
                r.exact_app(dep(),dep()['spec'],durable)

    def test_stored_startup_proof_survives_json_roundtrip(self):
        p = pod(); proof = {'pod_identity':json.loads(json.dumps(e.obs.identity(p))),
                           'limit':64,'distributed_via_redis':True,'raw_logs_saved':False}
        with patch.object(Path,'glob',return_value=[Path('MODEL-STARTUP-one.json')]), \
             patch.object(e,'load',return_value=proof), patch.object(e,'capture_startup_limit') as logs:
            self.assertEqual(r.startup_limit(p),proof)
            logs.assert_not_called()

    def test_other_workload_identity_roundtrip(self):
        signature = {'deployments':{'weknora-app':['app','hash'],'mineru':['m','hash']},
                     'pods':{'weknora-app':e.obs.identity(pod()),'mineru':e.obs.identity(pod())}}
        original = r.other_baseline(signature)
        self.assertTrue(r.same_json(original,json.loads(json.dumps(original))))

    def test_wrong_host_refuses_before_loading_tools(self):
        with patch.object(r.sys,'platform','win32'), patch.object(r,'initialize') as init, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(r.main(['--recover']),1)
        init.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['failure']['check'],'run_on_datax01_as_root')

    def recovery_fixture(self, stack, drift=False, active=False, ambiguous=False):
        d = dep(); p = pod(); old = {'original':d['spec'], 'original_pod':e.obs.identity(p),
                                   'placement':{}, 'original_image_id':'same'}
        sig = {'start_ticks':123,'server_ids':sorted(e.CURRENT)}
        written = {}; events = []
        def write(path,value):
            written[path.name] = copy.deepcopy(value)
            events.append('write:'+path.name)
            return json.dumps(value).encode()
        def pc(path,dry):
            events.append('dry' if dry else 'live')
            if not dry and ambiguous:
                raise e.obs.CheckFailure('patch_response_deadline_use_status')
            result = copy.deepcopy(d)
            result['spec'] = written['PLAN.private.json']['target']
            if drift:
                result['spec']['replicas'] = 2
            return result
        for obj,name,value in [(r,'original_plan',(old,sig)),(e,'deployment',d),(r,'exact_app',p),
                               (e,'binary_probe',{'start_ticks':123}),(e,'runtime_env',{}),
                               (e,'scheduling_gate',{}),(e,'database',{}),(e,'resources',[]),
                               (r,'dependencies',({},'unchanged')),(r,'app_service',{}),
                               (r,'confirm_stopped',p),(e,'redis_binding',None),
                               (e,'snapshot',snap(active=1 if active else 0))]:
            stack.enter_context(patch.object(obj,name,return_value=value))
        stack.enter_context(patch.object(r,'write',side_effect=write))
        patcher = stack.enter_context(patch.object(e,'patch_call',side_effect=pc))
        accept = stack.enter_context(patch.object(r,'accept'))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return patcher,accept,written,events

    def test_recover_one_live_cas_only_after_request_marker(self):
        with ExitStack() as stack:
            pc,accept,written,events = self.recovery_fixture(stack)
            report = {}; r.recover(report)
        self.assertEqual([c.args[1] for c in pc.call_args_list],[True,False])
        self.assertLess(events.index('write:PATCH-REQUESTED.json'),events.index('live'))
        self.assertIn('PATCH-ACK.json',written)
        self.assertTrue(report['live_patch_acknowledged'])
        accept.assert_called_once()

    def test_dry_run_drift_no_restart(self):
        with ExitStack() as stack:
            pc,accept,written,_ = self.recovery_fixture(stack,drift=True)
            with self.assertRaisesRegex(r.helper.AuditFailure,'server_admission_changed'):
                r.recover({})
        self.assertEqual([c.args[1] for c in pc.call_args_list],[True])
        self.assertNotIn('PATCH-REQUESTED.json',written); accept.assert_not_called()

    def test_final_drain_drift_no_restart(self):
        with ExitStack() as stack:
            pc,accept,written,_ = self.recovery_fixture(stack,active=True)
            with self.assertRaisesRegex(r.helper.AuditFailure,'final_drain_changed'):
                r.recover({})
        self.assertEqual([c.args[1] for c in pc.call_args_list],[True])
        self.assertNotIn('PATCH-REQUESTED.json',written); accept.assert_not_called()

    def test_uncertain_patch_no_retry_no_acceptance(self):
        with ExitStack() as stack:
            pc,accept,written,_ = self.recovery_fixture(stack,ambiguous=True)
            with self.assertRaisesRegex(e.obs.CheckFailure,'deadline'):
                r.recover({})
        self.assertEqual([c.args[1] for c in pc.call_args_list],[True,False])
        self.assertIn('PATCH-REQUESTED.json',written)
        self.assertNotIn('PATCH-ACK.json',written); accept.assert_not_called()

    def test_endpoint_requires_new_pod_only(self):
        p = pod()
        def response(uid,ready=True):
            return {'items':[{'endpoints':[{'targetRef':{'uid':uid},'conditions':{'ready':ready}}]}]}
        with patch.object(r,'app_service',return_value={'uid':'svc'}), \
             patch.object(e.obs,'get',return_value=response(p['metadata']['uid'])):
            r.endpoint_check(p,{'uid':'svc'})
        for uid,ready in [('old',True),(p['metadata']['uid'],False)]:
            with patch.object(r,'app_service',return_value={'uid':'svc'}), \
                 patch.object(e.obs,'get',return_value=response(uid,ready)):
                with self.assertRaisesRegex(r.helper.AuditFailure,'not_converged'):
                    r.endpoint_check(p,{'uid':'svc'})

    def test_accept_cannot_claim_88_and_reuses_durable_startup(self):
        d = dep(); p = pod(); p['metadata']['uid'] = 'new-pod'
        plan = {'target':d['spec'], 'original_pod':['old-pod'], 'image_id':'same',
                'service':{},'placement':{},'other_baseline':'unchanged'}
        with ExitStack() as stack:
            for obj,name,value in [(e,'deployment',d),(r,'exact_app',p),(r,'startup_limit',{'limit':64}),
                                   (e,'redis_binding',None),(e,'snapshot',snap(state='active',active=2)),
                                   (r,'endpoint_check',None),(e,'runtime_env',{}),(e,'binary_probe',{}),
                                   (e,'database',{}),(e,'resources',[]),(e,'scheduling_gate',{}),
                                   (r,'dependencies',({'state':'DEGRADED_56_RECOVERY_ONLY'},'unchanged')),
                                   (r,'exists',False)]:
                stack.enter_context(patch.object(obj,name,return_value=value))
            w = stack.enter_context(patch.object(r,'write'))
            stack.enter_context(patch.object(r.time,'sleep'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            report = {}; r.accept(plan,report)
        self.assertEqual(report['consumer_recovery'],'PASS')
        self.assertEqual(report['effective_worker_slots'],56)
        self.assertFalse(report['ready_for88']); self.assertFalse(report['mineru_quarantine_fixed'])
        self.assertEqual(w.call_args.args[0].name,'SUCCESS.json')


if __name__ == '__main__':
    unittest.main()
