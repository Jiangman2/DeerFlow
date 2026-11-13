"""Production operator boundaries without Kubernetes or real credentials."""
import ast
import copy
import contextlib
import io
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch

FILE=Path(__file__).resolve().parents[1]/'deployment/public-runtime-20261112-r1/repair-energy-public-runtime-20261112-r1.py'
SPEC=importlib.util.spec_from_file_location('browser_fix_install',FILE)
host=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(host)
ASSETS={'publisher':b'print("publisher")','verifier':b'print("verifier")','package':b'zip','test_files':b'{}'}


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.operation=host.Operation(ASSETS)

    def test_operator_keeps_python37_syntax(self):
        ast.parse(FILE.read_text(encoding='utf-8'),feature_version=(3,7))

    def test_only_own_pod_and_configmap_can_mutate(self):
        for args in (['patch','deployment','gateway'],['delete','pod','sandbox-business'],['create','secret','credential'],['get','secret','deer-flow-env']):
            with self.assertRaisesRegex(host.Refused,'FIXED_OPERATION_REQUIRED'):
                self.operation.command(args)

    def test_no_secret_business_or_privileged_mount_in_maintenance_pod(self):
        spec=self.operation.requested['spec']
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertEqual(spec['securityContext'],{'fsGroup':1000})
        for container in spec['containers']+spec['initContainers']:
            self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
            self.assertEqual(container['securityContext']['capabilities'],{'drop':['ALL']})
            self.assertEqual(container['imagePullPolicy'],'Never')
            self.assertNotIn('envFrom',container)
        self.assertFalse(any('secret' in volume for volume in spec['volumes']))
        nonroot=spec['containers'][1]
        self.assertEqual(nonroot['securityContext']['runAsUser'],1000)
        self.assertEqual(nonroot['volumeMounts'][0]['mountPath'],'/tmp')
        self.assertTrue(all(row['readOnly'] for row in nonroot['volumeMounts'] if row['name']=='skills'))

    def running_pod(self):
        pod=copy.deepcopy(self.operation.requested)
        pod['metadata']['uid']='12345678-1234-1234-1234-123456789abc'
        pod['status']={'phase':'Running','initContainerStatuses':[{'imageID':host.IMAGE_ID,'restartCount':0,'state':{'terminated':{'exitCode':0}}}],
                       'containerStatuses':[{'name':name,'ready':True,'restartCount':0,'imageID':host.IMAGE_ID} for name in ('publisher','qualification')]}
        return pod

    def test_init_failure_does_not_pass_as_ready(self):
        pod=self.running_pod();pod['status']['initContainerStatuses'][0]['state']['terminated']['exitCode']=1
        with self.assertRaisesRegex(host.Refused,'STAGING_INIT_FAILED'):
            host.guard_pod(pod,self.operation.requested)

    def test_different_pod_uid_cannot_be_cleaned(self):
        self.operation.create_pod_requested=True
        self.operation.pod_uid='00000000-0000-0000-0000-000000000000'
        commands=[]
        def command(args,*a,**kw):
            commands.append(args);return self.running_pod()
        with patch.object(self.operation,'command',side_effect=command):
            with self.assertRaisesRegex(host.Refused,'OWN_POD_IDENTITY'):
                self.operation.cleanup()
        self.assertFalse(any(args[0]=='delete' for args in commands))

    def test_arbitrary_exec_source_is_rejected(self):
        self.operation.pod_uid='12345678-1234-1234-1234-123456789abc'
        args=['exec','-i',self.operation.pod_name,'-c','publisher','--','python3','-I','-B','-c','print("arbitrary")']
        with self.assertRaisesRegex(host.Refused,'FIXED_OPERATION_REQUIRED'):
            self.operation.command(args,b'{}')

    def test_ordinary_visibility_exec_is_fixed_to_the_selected_sample(self):
        self.operation.ordinary_sample={'pod':'sandbox-abcd1234','uid':'12345678-1234-1234-1234-123456789abc','node':'ecs-zdz-pro-0017'}
        args=['exec','sandbox-abcd1234','-c','sandbox','--','python3','-I','-B','-c',host.VISIBILITY_CHILD,'a'*64]
        class Process:
            returncode=0
            def poll(self):return 0
        def popen(command,**kwargs):
            kwargs['stdout'].write(b'{}')
            return Process()
        with patch.object(host.subprocess,'Popen',side_effect=popen) as execute:
            self.assertEqual(self.operation.command(args),{})
        self.assertEqual(execute.call_args.args[0][-11:],args)
        for index,value in ((1,'sandbox-another'),(9,'print("unrestricted")')):
            changed=list(args);changed[index]=value
            with self.assertRaisesRegex(host.Refused,'FIXED_OPERATION_REQUIRED'):
                self.operation.command(changed)

    def test_ordinary_visibility_rejects_replaced_uid_before_exec(self):
        uid='12345678-1234-1234-1234-123456789abc'
        line='sandbox-abcd1234 '+uid+' ecs-zdz-pro-0017 Running <none> true '+host.IMAGE_ID
        calls=[]
        def command(args,*unused,**kwargs):
            calls.append(args)
            if args==host.ORDINARY_LIST:return line
            return {'metadata':{'uid':'00000000-0000-0000-0000-000000000000'}}
        with patch.object(self.operation,'command',side_effect=command):
            with self.assertRaisesRegex(host.Refused,'ORDINARY_POD_REPLACED'):
                self.operation.ordinary_visibility('a'*64)
        self.assertFalse(any(args[0]=='exec' for args in calls))

    def test_ordinary_visibility_rejects_wrong_category_before_exec(self):
        uid='12345678-1234-1234-1234-123456789abc'
        line='sandbox-abcd1234 '+uid+' ecs-zdz-pro-0017 Running <none> true '+host.IMAGE_ID
        calls=[]
        def command(args,*unused,**kwargs):
            calls.append(args)
            if args==host.ORDINARY_LIST:return line
            return {'metadata':{'uid':uid},'spec':{'containers':[{'name':'sandbox','volumeMounts':[{'name':'skills','mountPath':'/mnt/skills/public','subPath':'another','readOnly':True}]}]}}
        with patch.object(self.operation,'command',side_effect=command):
            with self.assertRaisesRegex(host.Refused,'ORDINARY_CATEGORY_MOUNT_CHANGED'):
                self.operation.ordinary_visibility('a'*64)
        self.assertFalse(any(args[0]=='exec' for args in calls))

    def test_actual_visibility_child_observes_tmp_without_writing_or_launching(self):
        tree=ast.parse(host.VISIBILITY_CHILD)
        calls=[node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)]
        self.assertIn('statvfs',calls)
        self.assertFalse(set(calls)&{'write','write_bytes','write_text','mkdir','unlink','rmdir','gettempdir','TemporaryDirectory','launch','Popen'})
        self.assertIn("Path('/tmp')",host.VISIBILITY_CHILD)
        self.assertNotIn('JKX_',host.VISIBILITY_CHILD)

    def test_gateway_rollout_progress_does_not_block_independent_skill_fix(self):
        items=[]
        for name,uid in host.DEPLOYMENT_UIDS.items():
            items.append({'metadata':{'name':name,'uid':uid,'generation':500},'spec':{'name':name},
                          'status':{'updatedReplicas':28 if name=='gateway' else 1}})
        with patch.object(self.operation,'command',return_value={'items':items}):
            result=self.operation.baseline()
        self.assertEqual(set(result),set(host.DEPLOYMENT_UIDS))
        self.assertEqual(result['gateway']['generation'],500)

    def test_real_category_mount_is_the_only_runtime_parent(self):
        mounts=self.operation.requested['spec']['containers'][1]['volumeMounts']
        skills=[row for row in mounts if row['name']=='skills']
        self.assertEqual(len(skills),2)
        parent,leaf=skills
        self.assertEqual(parent,{'name':'skills','mountPath':'/mnt/skills/public','subPath':'public','readOnly':True})
        self.assertEqual(leaf['mountPath'],'/mnt/skills/public/energy-market-daily')
        self.assertTrue(leaf['readOnly'])
        self.assertFalse(any('.energy-browser-runtime' in row['mountPath'] for row in mounts))
        self.assertFalse(any(row['mountPath']=='/mnt/skills' for row in mounts))

    def test_mount_or_image_drift_is_rejected(self):
        pod=self.running_pod();pod['spec']['containers'][1]['volumeMounts'][1]['readOnly']=False
        with self.assertRaisesRegex(host.Refused,'CONTAINER_SPEC_CHANGED'):
            host.guard_pod(pod,self.operation.requested)

    def simulate_publication(self, acceptance_changes=None, publish_error=False):
        operation=self.operation
        actions=[]
        tree='a'*64;runtime='b'*64;png=host.EXPECTED_FIXED_PNG_SHA
        accepted={'status':'NONROOT_ARM64_BROWSER_AND_FIXED_REPORT_QUALIFIED','uid':1000,'gid':1000,
                  'package_sha256':host.PACKAGE_SHA,'runtime_manifest_sha256':runtime,'after_tree_sha256':tree,'png_sha256':png,
                  'credentials_read':False,'provider_requests':0,'automatic_binding_used':True,'private_local_copy_cleaned':True,'temporary_project_cleaned':True}
        accepted.update(acceptance_changes or {})
        def command(args,*unused,**kwargs):
            if args[:2]==['get','node']:
                return {'metadata':{'uid':host.NODE_UID},'spec':{},'status':{'conditions':[{'type':'Ready','status':'True'}]}}
            if args[:2]==['get','pvc']:
                return {'metadata':{'uid':host.CLAIM_UID},'status':{'phase':'Bound'}}
            if args[0]=='create':
                value=json.loads(unused[0]);value['metadata']['uid']='12345678-1234-1234-1234-123456789abc';return value
            if args[:2]==['get','pod']:
                return self.running_pod()
            raise AssertionError(args)
        def child(name,key,payload,**kwargs):
            action=payload.get('action','render');actions.append(action)
            if action=='inspect-stage':return {'status':'PUBLIC_RUNTIME_REPAIR_STAGED','after_tree_sha256':tree,'before_tree_sha256':host.OLD_TREE,'after_files':107}
            if action=='render':return accepted
            if action=='publish':
                if publish_error:raise host.Refused('UNCERTAIN_PUBLISH_TEST')
                return {'status':'PUBLIC_RUNTIME_REPAIR_PUBLISHED','after_tree_sha256':tree,'before_tree_sha256':host.OLD_TREE,'after_files':107}
            if action=='verify':return {'status':'PUBLIC_RUNTIME_REPAIR_VERIFIED','after_tree_sha256':tree}
            raise AssertionError(action)
        with tempfile.TemporaryDirectory(prefix='browser-install-model-') as location:
            with patch.object(host,'CONTROL',Path(location)/'control'),patch.object(host,'qualification_binding',return_value=({}, {},runtime,{'png_sha256':png})),\
                 patch.object(operation,'baseline',return_value={'stable':'identity'}),patch.object(operation,'command',side_effect=command),\
                 patch.object(operation,'child',side_effect=child),\
                 patch.object(operation,'ordinary_visibility',return_value={'status':'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE'}),contextlib.redirect_stdout(io.StringIO()):
                try:operation.run()
                except host.Refused as exc:return actions,exc
        return actions,None

    def test_wrong_png_never_requests_publication(self):
        actions,error=self.simulate_publication({'png_sha256':'0'*64})
        self.assertEqual(str(error),'SHARED_NONROOT_PNG_NOT_ACCEPTED')
        self.assertNotIn('publish',actions)

    def test_only_accepted_shared_png_switches_skill(self):
        actions,error=self.simulate_publication()
        self.assertIsNone(error)
        self.assertEqual(actions,['inspect-stage','render','publish','verify'])
        self.assertTrue(self.operation.out['skill_updated'])
        self.assertEqual(self.operation.out['status'],'ENERGY_PUBLIC_RUNTIME_REPAIR_INSTALLED')

    def test_uncertain_publish_is_recorded_without_retry(self):
        actions,error=self.simulate_publication(publish_error=True)
        self.assertEqual(str(error),'UNCERTAIN_PUBLISH_TEST')
        self.assertEqual(actions.count('publish'),1)
        self.assertTrue(self.operation.out['skill_switch_may_have_applied'])
        pod=self.running_pod();pod['status']['containerStatuses'][1]['imageID']='sha256:'+'0'*64
        with self.assertRaisesRegex(host.Refused,'CACHED_RUNTIME_CHANGED'):
            host.guard_pod(pod,self.operation.requested)


if __name__=='__main__':
    unittest.main()
