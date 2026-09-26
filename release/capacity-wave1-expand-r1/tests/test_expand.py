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
    def test_exact_two_node_anti_affinity_and_no_disruption(self):
        old=deployment(); new=m.desired(old)
        self.assertEqual(new['spec']['replicas'],2)
        self.assertEqual(new['spec']['strategy']['rollingUpdate'],{'maxSurge':1,'maxUnavailable':0})
        ps=new['spec']['template']['spec']
        expr=ps['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]['matchExpressions'][0]
        self.assertEqual(set(expr['values']),{'datax-02','ecs-zdz-pro-0015'})
        anti=ps['affinity']['podAntiAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][0]
        self.assertEqual(anti['labelSelector']['matchLabels'],{'app':'frontend'})
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
        for args in (['scale','deployment','frontend','--replicas=12'],['patch','deployment','gateway'],['delete','pod','frontend']):
            with self.subTest(args=args),self.assertRaises(m.p.Blocked): m.kubectl(args,[])


if __name__=='__main__':unittest.main()
