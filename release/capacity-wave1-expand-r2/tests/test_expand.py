import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('expander', str(ROOT/'expand-wave1.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def deployment():
    return {'apiVersion':'apps/v1','kind':'Deployment',
        'metadata':{'name':'frontend','namespace':'deer-flow','uid':'old-uid','resourceVersion':'123','generation':3},
        'status':{'observedGeneration':3,'replicas':1,'updatedReplicas':1,'readyReplicas':1,'availableReplicas':1},
        'spec':{'replicas':1,'selector':{'matchLabels':{'app':'frontend'}},
            'strategy':{'type':'RollingUpdate','rollingUpdate':{'maxSurge':'25%','maxUnavailable':'25%'}},
            'template':{'metadata':{'labels':{'app':'frontend'}},'spec':{
                'containers':[{'name':'frontend','image':m.p.IMAGE,'imagePullPolicy':'Never',
                    'envFrom':[{'secretRef':{'name':'deer-flow-env'}}],
                    'readinessProbe':{'httpGet':{'path':'/api/runtime-config','port':3000}},
                    'resources':{'requests':{'cpu':'100m','memory':'256Mi'},
                                 'limits':{'cpu':'1','memory':'1Gi'}}}],
                'volumes':[{'name':'demo','configMap':{'name':'demo'}}]}}}}


def preflight():
    old=deployment(); new=m.desired(old)
    return {'deployment_uid':'old-uid','old_spec_sha256':m.p.sha(old['spec']),
            'desired_spec_sha256':m.p.sha(new['spec']),'old_strategy':old['spec']['strategy'],
            'new_affinity':new['spec']['template']['spec']['affinity'],
            'new_tolerations':new['spec']['template']['spec']['tolerations'],
            'old_affinity_present':False,'old_tolerations_present':False}


class ExpansionTests(unittest.TestCase):
    def test_exact_two_node_revision_anti_affinity(self):
        old=deployment(); new=m.desired(old)
        self.assertEqual(new['spec']['replicas'],2)
        self.assertEqual(new['spec']['strategy']['rollingUpdate'],{'maxSurge':1,'maxUnavailable':0})
        ps=new['spec']['template']['spec']
        expr=ps['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]['matchExpressions'][0]
        self.assertEqual(set(expr['values']),{'datax-02','ecs-zdz-pro-0015'})
        anti=ps['affinity']['podAntiAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][0]
        self.assertEqual(anti['labelSelector']['matchLabels'],{'app':'frontend'})
        self.assertEqual(anti['matchLabelKeys'],['pod-template-hash'])
        self.assertEqual(anti['topologyKey'],'kubernetes.io/hostname')
        self.assertEqual(ps['tolerations'],[{'key':'node.kubernetes.io/unschedulable','operator':'Exists','effect':'NoSchedule'}])
        self.assertNotIn('nodeName',ps)
        self.assertEqual(ps['containers'],old['spec']['template']['spec']['containers'])
        self.assertEqual(ps['volumes'],old['spec']['template']['spec']['volumes'])

    def test_existing_scheduling_blocks(self):
        for field,val in [('nodeName','datax-03'),('affinity',{'nodeAffinity':{}}),('tolerations',[{'operator':'Exists'}])]:
            old=deployment(); old['spec']['template']['spec'][field]=val
            with self.subTest(field=field),self.assertRaises(m.p.Blocked): m.desired(old)

    def test_json_patch_binds_uid_and_resource_version(self):
        old=deployment(); new=m.desired(old); ops=m.patch_ops(old,new,old['metadata']['resourceVersion'])
        self.assertEqual(ops[0],{'op':'test','path':'/metadata/uid','value':'old-uid'})
        self.assertEqual(ops[1],{'op':'test','path':'/metadata/resourceVersion','value':'123'})
        self.assertEqual([o['path'] for o in ops[2:]],['/spec/replicas','/spec/strategy','/spec/template/spec/affinity','/spec/template/spec/tolerations'])

    def test_restore_recovers_original_spec_after_partial_rollout(self):
        old=deployment(); new=m.desired(old); new['metadata']['resourceVersion']='456'
        ops=m.restore_ops(new,preflight())
        self.assertEqual([o['op'] for o in ops[-2:]],['remove','remove'])
        self.assertEqual(ops[1]['value'],'456')
        restored=copy.deepcopy(new)
        restored['spec']['replicas']=ops[2]['value']; restored['spec']['strategy']=ops[3]['value']
        del restored['spec']['template']['spec']['affinity'];del restored['spec']['template']['spec']['tolerations']
        self.assertEqual(m.p.sha(restored['spec']),m.p.sha(old['spec']))

    def test_restore_rejects_another_spec(self):
        new=m.desired(deployment()); new['spec']['template']['spec']['containers'][0]['image']='different'
        with self.assertRaises(m.p.Blocked): m.restore_ops(new,preflight())

    def test_patch_data_on_stdin_only(self):
        calls=[]
        class Fake:
            returncode=0
            def communicate(self,data,timeout):
                calls.append(data)
                return ('{"kind":"Deployment"}','')
        with patch.object(m.subprocess,'Popen',return_value=Fake()) as proc:
            m.kubectl(['patch','deployment','frontend','--type=json','--patch-file=/dev/stdin','-o','json'],
                      m.patch_ops(deployment(),m.desired(deployment()),'123'))
            argv=proc.call_args.args[0]
            self.assertIn('--patch-file=/dev/stdin',argv)
            self.assertNotIn('deer-flow-env',' '.join(argv))
            self.assertEqual(json.loads(calls[0])[1]['value'],'123')
            m.kubectl(['patch','deployment','frontend','--type=json','--patch-file=/dev/stdin','--dry-run=server','-o','json'],
                      m.patch_ops(deployment(),m.desired(deployment()),'123'))
            self.assertIn('--dry-run=server',proc.call_args.args[0])

    def test_no_arbitrary_mutation(self):
        for args in (['scale','deployment','frontend','--replicas=12'],['patch','deployment','gateway'],['delete','pod','frontend'],['rollout','status']):
            with self.subTest(args=args),self.assertRaises(m.p.Blocked): m.kubectl(args,[])

    def test_r1_deadlocks_but_r2_can_complete_with_old_pod_alive(self):
        # Small state model, NOT an integration test of kube-scheduler. Apply the
        # label matching rule and the real rolling-controller maxScaledDown formula.
        term=m.desired(deployment())['spec']['template']['spec']['affinity']['podAntiAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][0]
        def simulate(anti):
            pods=[('datax-02','old')]
            for _ in range(2):
                for node in sorted(m.NODES):
                    if not any(n == node and ('pod-template-hash' not in anti.get('matchLabelKeys',[]) or rev == 'new') for n,rev in pods):
                        pods.append((node,'new')); break
            new_ready=sum(rev == 'new' for _,rev in pods)
            min_available=2
            max_scaled_down=3-min_available-(2-new_ready)
            return pods,max_scaled_down
        old_term=copy.deepcopy(term); del old_term['matchLabelKeys']
        old_pods,old_scale=simulate(old_term)
        self.assertEqual(old_pods,[('datax-02','old'),('ecs-zdz-pro-0015','new')])
        self.assertEqual(old_scale,0)
        new_pods,new_scale=simulate(term)
        self.assertEqual(new_scale,1)
        self.assertEqual({n for n,r in new_pods if r == 'new'},m.NODES)
        self.assertIn(('datax-02','old'),new_pods)

    def test_progress_redacts_env_message_and_container_error_details(self):
        obj=deployment(); obj['status']['conditions']=[{'type':'Progressing','status':'False','reason':'ProgressDeadlineExceeded','message':'SECRET'}]
        pod={'metadata':{'name':'frontend-a','uid':'p','labels':{'app':'frontend','pod-template-hash':'x'}},
             'spec':{'nodeName':'datax-02','containers':[{'env':[{'name':'KEY','value':'SECRET'}]}]},
             'status':{'phase':'Pending','conditions':[{'type':'PodScheduled','status':'False','reason':'Unschedulable','message':'SECRET'}],
                       'containerStatuses':[{'name':'frontend','state':{'waiting':{'reason':'ErrImageNeverPull','message':'SECRET'}}}]}}
        with patch.object(m.p,'get',return_value={'items':[pod]}):
            snapshot=m.progress_snapshot(obj)
        encoded=json.dumps(snapshot)
        self.assertNotIn('SECRET',encoded)
        self.assertNotIn('message',encoded)
        self.assertIn('ErrImageNeverPull',encoded)
        self.assertIn('Unschedulable',encoded)

    def test_restore_wait_ignores_stale_failure_until_generation_observed(self):
        obj=deployment(); obj['metadata']['generation']=5
        obj['status']['observedGeneration']=4
        obj['status']['conditions']=[{'type':'Progressing','status':'False','reason':'ProgressDeadlineExceeded'}]
        observed=copy.deepcopy(obj); observed['status']['observedGeneration']=5
        with patch.object(m.p,'get',side_effect=[obj,observed]),patch.object(m,'progress_snapshot',return_value={}), \
             patch.object(m,'original_ready',return_value={'original_spec':'RESTORED'}) as verify, \
             patch.object(m.time,'sleep') as sleep,patch.object(m.p,'emit'):
            self.assertEqual(m.wait_converged(preflight(),'restore',5),{'original_spec':'RESTORED'})
        verify.assert_called_once(); sleep.assert_called_once()

    def test_wait_requires_endpoint_verification_after_replica_counts(self):
        obj=m.desired(deployment()); obj['metadata']['generation']=4
        obj['status']={'observedGeneration':4,'replicas':2,'updatedReplicas':2,'readyReplicas':2,'availableReplicas':2}
        with patch.object(m.p,'get',return_value=obj),patch.object(m,'progress_snapshot',return_value={}), \
             patch.object(m,'verify_two',side_effect=[m.p.Blocked('frontend_ready_endpoints_not_exactly_two'),{'ok':True}]) as verify, \
             patch.object(m.time,'sleep') as sleep,patch.object(m.p,'emit'):
            self.assertEqual(m.wait_converged(preflight(),'expand',4),{'ok':True})
        self.assertEqual(verify.call_count,2);sleep.assert_called_once()

    def test_wait_rejects_spec_drift_without_rollback_patch(self):
        obj=m.desired(deployment()); obj['spec']['replicas']=3
        with patch.object(m.p,'get',return_value=obj),patch.object(m,'kubectl') as mutation, \
             self.assertRaisesRegex(m.p.Blocked,'drift'):
            m.wait_converged(preflight(),'expand',4)
        mutation.assert_not_called()

    def test_pending_wait_is_bounded_and_never_passes(self):
        obj=m.desired(deployment());obj['metadata']['generation']=4
        with patch.object(m.p,'get',return_value=obj),patch.object(m,'progress_snapshot',return_value={}), \
             patch.object(m.time,'monotonic',side_effect=[0,1,2,99]),patch.object(m.time,'sleep'), \
             patch.object(m.p,'emit'),self.assertRaisesRegex(m.p.Blocked,'timeout'):
            m.wait_converged(preflight(),'expand',4,timeout=10)

    def test_rollback_waits_for_patch_generation_not_rollout_status(self):
        current=m.desired(deployment()); restored=deployment();restored['metadata']['generation']=8
        with patch.object(m.p,'get',return_value=current),patch.object(m,'kubectl',return_value=restored) as mutate, \
             patch.object(m,'wait_converged',return_value={}) as wait,patch.object(m.p,'run') as command:
            self.assertEqual(m.rollback(preflight()),'PASS_ORIGINAL_ONE_REPLICA')
        wait.assert_called_once_with(preflight(),'restore',8)
        mutate.assert_called_once();command.assert_not_called()

    def test_rollback_already_original_but_not_yet_ready_waits(self):
        current=deployment();current['status']['readyReplicas']=0
        with patch.object(m.p,'get',return_value=current),patch.object(m,'kubectl') as mutate, \
             patch.object(m,'wait_converged',return_value={}) as wait:
            self.assertEqual(m.rollback(preflight()),'ALREADY_ORIGINAL_READY')
        wait.assert_called_once_with(preflight(),'restore',3);mutate.assert_not_called()

    def test_original_ready_rejects_stale_generation_before_pod_checks(self):
        obj=deployment();obj['status']['observedGeneration']=2
        with patch.object(m.p,'get',return_value=obj) as get, \
             self.assertRaisesRegex(m.p.Blocked,'not_converged'):
            m.original_ready(preflight())
        get.assert_called_once_with('deployment','frontend')

    def test_failure_diagnostics_keep_categories_not_raw_event_text(self):
        events={'items':[{'involvedObject':{'name':'frontend-abc','uid':'p'},'reason':'FailedScheduling',
                          'message':"SECRET 2 nodes didn't match pod anti-affinity rules; Insufficient cpu"},
                         {'involvedObject':{'name':'gateway-abc'},'message':'SECRET'}]}
        with patch.object(m,'progress_snapshot',return_value={}),patch.object(m.p,'get',return_value=events), \
             patch.object(m.p,'write_new') as write,patch.object(m.p,'emit'):
            m.record_failure(Path('/evidence'))
        result=write.call_args.args[1]
        self.assertEqual(len(result['frontend_events']),1)
        self.assertEqual(result['frontend_events'][0]['categories'],['anti_affinity','insufficient_cpu'])
        self.assertNotIn('SECRET',json.dumps(result))

    def test_failed_convergence_records_diagnostics_before_guarded_rollback(self):
        old=deployment(); new=m.desired(old);new['metadata']['generation']=4
        order=[]
        with patch.object(m,'validate_preflight',return_value=old),patch.object(m,'kubectl',return_value=new), \
             patch.object(m,'patch',return_value=new),patch.object(m.p,'write_new') as write, \
             patch.object(m.p,'emit'),patch.object(m,'wait_converged',side_effect=m.p.Blocked('timeout')), \
             patch.object(m,'record_failure',side_effect=lambda _:order.append('diagnose')), \
             patch.object(m,'rollback',side_effect=lambda _:order.append('restore') or 'PASS_ORIGINAL_ONE_REPLICA'), \
             self.assertRaises(SystemExit):
            m.execute(preflight(),{}, {},Path('/evidence'))
        self.assertEqual(order,['diagnose','restore'])
        self.assertEqual(write.call_args.args[1]['frontend_expansion'],'NOT_PASSED')
        self.assertEqual(write.call_args.args[1]['automatic_rollback'],'PASS_ORIGINAL_ONE_REPLICA')

    def test_server_dry_run_field_loss_blocks_before_actual_patch(self):
        old=deployment();new=m.desired(old)
        del new['spec']['template']['spec']['affinity']['podAntiAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][0]['matchLabelKeys']
        with patch.object(m,'validate_preflight',return_value=old),patch.object(m,'kubectl',return_value=new), \
             patch.object(m,'patch') as mutation,patch.object(m,'rollback') as rollback, \
             self.assertRaisesRegex(m.p.Blocked,'dry_run'):
            m.execute(preflight(),{}, {},Path('/evidence'))
        mutation.assert_not_called();rollback.assert_not_called()


if __name__=='__main__':unittest.main()
