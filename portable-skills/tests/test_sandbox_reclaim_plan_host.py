"""Review-plan projections, fixed transport and synthetic host/child handshake."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]/'deployment/reclaim-plan-20261112-r1'
spec=importlib.util.spec_from_file_location('reclaim_plan_sealer',HERE/'seal_reclaim_plan.py')
sealer=importlib.util.module_from_spec(spec);spec.loader.exec_module(sealer)
host=types.ModuleType('review_plan_host');exec(compile(sealer.source_host(),'<review-plan-host>','exec'),host.__dict__)
UID='11111111-2222-3333-4444-555555555555'

def sandbox():
    return {'metadata':{'namespace':'deer-flow','name':'sandbox-abcdef12','uid':UID,'labels':{'app.kubernetes.io/component':'sandbox'}},
        'spec':{'nodeName':'ecs-zdz-pro-0015','volumes':[{'name':'user-data','persistentVolumeClaim':{'claimName':host.USERDATA_CLAIM}}],
            'containers':[{'name':'sandbox','env':[{'name':'PRIVATE','value':'never-export-this'}],
                'resources':{'requests':{'cpu':'750m','memory':'1Gi'}},'volumeMounts':[{'name':'user-data','mountPath':'/mnt/user-data','subPath':'users/private/threads/thread/user-data'}]},
                {'name':'ppt-master-agent-host','resources':{'requests':{'cpu':'250m','memory':'512Mi'}}}]},
        'status':{'phase':'Running','containerStatuses':[{'name':'sandbox','ready':True}],'conditions':[{'type':'Ready','status':'True'}]}}

def gateway():
    return {'metadata':{'name':'gateway-abc-def','uid':UID,'labels':{'app':'gateway'},'ownerReferences':[{'controller':True,'kind':'ReplicaSet','name':'gateway-abc','uid':'rs-uid'}]},
        'spec':{'volumes':[{'name':'data','persistentVolumeClaim':{'claimName':host.USERDATA_CLAIM}},{'name':'overlay','configMap':{'name':'source'}}],
            'containers':[{'name':'gateway','volumeMounts':[{'name':'data','mountPath':'/app/backend/.deer-flow'},{'name':'overlay','mountPath':'/app/backend/source.py'}]}]},
        'status':{'phase':'Running','containerStatuses':[{'name':'gateway','ready':True,'restartCount':0,'imageID':'image','containerID':'container'}]}}

class ProjectionTests(unittest.TestCase):
    def test_projection_discards_environment_and_preserves_requests(self):
        rows=host.project_sandboxes({'items':[sandbox()]});self.assertEqual(rows[0]['cpu_requests'],'750m,250m')
        self.assertEqual(rows[0]['memory_requests'],'1Gi,512Mi');self.assertNotIn('never-export-this',json.dumps(rows))
    def test_foreign_claim_cannot_supply_storage_binding(self):
        pod=sandbox();pod['spec']['volumes'][0]['persistentVolumeClaim']['claimName']='other'
        row=host.project_sandboxes({'items':[pod]})[0];self.assertIsNone(row['userdata_claim']);self.assertIsNone(row['userdata_subpath'])
    def test_variable_or_ambiguous_mount_is_unknown(self):
        pod=sandbox();pod['spec']['containers'][0]['volumeMounts'][0]['subPathExpr']='$(PRIVATE)'
        row=host.project_sandboxes({'items':[pod]})[0];self.assertIsNone(row['userdata_subpath'])
    def test_spec_change_is_part_of_internal_stability_projection(self):
        pod=sandbox();first=host.project_sandboxes({'items':[pod]})[0]
        pod['spec']['containers'][0]['image']='changed-image'
        after=host.project_sandboxes({'items':[pod]})[0]
        self.assertNotEqual(first['spec_sha256'],after['spec_sha256'])
    def test_count_and_identity_fail_closed(self):
        for payload in ({'items':[sandbox()]*257},{'items':[sandbox(),sandbox()]},{'items':[{'metadata':{'name':'not-a-sandbox'}}]}):
            with self.assertRaises(host.Refused):host.project_sandboxes(payload)
    def test_storage_and_shadow_projection(self):
        mounts,shadow=host.gateway_storage(gateway());self.assertEqual(mounts,[{'mount_path':'/app/backend/.deer-flow','claim':host.USERDATA_CLAIM,'subpath':''}])
        self.assertEqual(shadow,['/app/backend/source.py'])
        pod=gateway();pod['spec']['containers'][0]['volumeMounts'][0]['subPathExpr']='$(private)'
        with self.assertRaises(host.Refused):host.gateway_storage(pod)
    def test_no_provider_or_delete_command_and_python37_host(self):
        source=sealer.source_host().decode();ast.parse(source,feature_version=(3,7))
        self.assertNotIn('get_sandbox_provider',source)
        operation=host.Operation('sealed-child');operation.pod='gateway-abc-def';operation.payload={}
        for args,body in [(['delete','pod','sandbox-abcdef12'],None),(['get','secret','deer-flow-env'],None),
            (['exec','-i',operation.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c','sealed-child'],b'changed')]:
            with self.assertRaises(host.Refused):operation.command(args,body)

class IntegrationTests(unittest.TestCase):
    def run_host(self,changed=False,bad_uid=False,diag_incomplete=False):
        dep={'metadata':{'uid':host.GATEWAY_UID,'generation':414},'spec':{'replicas':70}}
        pod=gateway();rs={'metadata':{'name':'gateway-abc','uid':'rs-uid','ownerReferences':[{'controller':True,'kind':'Deployment','name':'gateway','uid':host.GATEWAY_UID}]}}
        calls=[];sandbox_reads=0
        class FakeOperation(host.Operation):
            def command(self,args,body=None):
                nonlocal sandbox_reads
                self.queries+=1;calls.append(args)
                if args==host.LIST:return 'gateway-abc-def '+UID+' Running <none> true\n'
                if args==['get','deployment','gateway','-o','json']:return json.dumps(dep)
                if args==['get','pod','gateway-abc-def','-o','json']:return json.dumps(pod)
                if args==['get','replicaset','gateway-abc','-o','json']:return json.dumps(rs)
                if args==host.SANDBOXES:
                    sandbox_reads+=1;value=sandbox()
                    if changed and sandbox_reads==2:value['metadata']['deletionTimestamp']='2026-11-12T00:00:00Z'
                    return json.dumps({'items':[value]})
                if args[0]=='exec':
                    payload=json.loads(body);self_outer.assertEqual(set(payload),{'pods','userdata_mounts','shadow_mount_paths'})
                    return json.dumps({'status':'SANDBOX_RECLAIM_REVIEW_PLAN_OBSERVED_NO_CHANGE',
                        'fixed_run_diagnostic':{'requested_identifier':host.FIXED_TASK_IDENTIFIER,'status':'INCOMPLETE' if diag_incomplete else 'OBSERVED'},
                        'review_candidates':[{'pod':'sandbox-abcdef12','uid':'wrong' if bad_uid else UID}]})
                raise AssertionError('unexpected command')
        self_outer=self
        captured=[]
        with patch.object(host,'Operation',FakeOperation),patch.object(host,'assets',return_value='sealed-child'),patch.object(host.os,'name','posix'),\
            patch.object(host.os,'uname',return_value=types.SimpleNamespace(nodename='datax-01'),create=True),patch.object(host.os,'geteuid',return_value=0,create=True),\
            patch.object(host.sys,'argv',['fixture','--inspect-scheduled-run-and-capacity']),patch('builtins.print',side_effect=lambda value,**kwargs:captured.append(value)):
            exitcode=host.main()
        return exitcode,json.loads(captured[-1]),calls
    def test_sealed_child_handshake_and_nine_fixed_reads(self):
        code,result,calls=self.run_host();self.assertEqual(code,0);self.assertEqual(result['review_candidate_count'],1);self.assertEqual(len(calls),9)
        self.assertTrue(result['no_deletion_authorization']);self.assertTrue(result['unleased_nonrun_activity_not_excluded'])
        self.assertNotIn('users/private',json.dumps(result));self.assertNotIn('never-export-this',json.dumps(result))
    def test_changed_projection_removed_from_review(self):
        code,result,_=self.run_host(changed=True);self.assertEqual(code,0);self.assertEqual(result['review_candidate_count'],0)
        self.assertEqual(result['candidates_removed_due_to_changed_projection'],1);self.assertEqual(result['observation']['review_candidates'],[])
        self.assertEqual(result['observation']['candidate_count'],0);self.assertEqual(result['observation']['candidate_count_before_pod_recheck'],1)
    def test_wrong_candidate_uid_is_incomplete(self):
        code,result,_=self.run_host(bad_uid=True);self.assertEqual(code,1);self.assertEqual(result['error_code'],'REVIEW_CANDIDATE_IDENTITY')
    def test_failed_run_diagnostic_cannot_be_hidden_by_completed_capacity_plan(self):
        code,result,_=self.run_host(diag_incomplete=True);self.assertEqual(code,1)
        self.assertEqual(result['error_code'],'FIXED_RUN_DIAGNOSTIC_INCOMPLETE');self.assertEqual(result['review_candidate_count'],1)

if __name__=='__main__':unittest.main()
