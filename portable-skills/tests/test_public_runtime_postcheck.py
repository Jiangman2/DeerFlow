"""Read-only public runtime postcheck boundaries; no Kubernetes or credentials."""
import ast
import copy
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]/'deployment/public-runtime-20261112-r1'
FILE=HERE/'verify-energy-public-runtime-20261112-r1.py'
SPEC=importlib.util.spec_from_file_location('public_runtime_postcheck',FILE)
host=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(host)
repair=ast.parse((HERE/'repair-energy-public-runtime-20261112-r1.py').read_text(encoding='utf-8'))
host.VISIBILITY_CHILD=next(ast.literal_eval(row.value) for row in repair.body if isinstance(row,ast.Assign)
                           and any(isinstance(target,ast.Name) and target.id=='VISIBILITY_CHILD' for target in row.targets))
UID='12345678-1234-1234-1234-123456789abc'
SAMPLE={'pod':'sandbox-abcd1234','uid':UID,'node':'ecs-zdz-pro-0017'}
LINE='sandbox-abcd1234 '+UID+' ecs-zdz-pro-0017 Running <none> true '+host.IMAGE_ID


def pod():
    return {'metadata':{'name':SAMPLE['pod'],'uid':UID,'labels':{'app.kubernetes.io/component':'sandbox'}},
            'spec':{'nodeName':SAMPLE['node'],'containers':[{'name':'sandbox','volumeMounts':[
                {'name':'skills','mountPath':'/mnt/skills/public','subPath':'public','readOnly':True},
                {'name':'user-data','mountPath':'/mnt/user-data'}]}],
                'volumes':[{'name':'skills','persistentVolumeClaim':{'claimName':host.CLAIM}}]},
            'status':{'phase':'Running','containerStatuses':[{'name':'sandbox','ready':True,'imageID':host.IMAGE_ID,
                'containerID':'containerd://private-id','restartCount':0,'state':{'running':{'startedAt':'fixed'}}}]}}


class PostcheckTests(unittest.TestCase):
    def test_python37_source_and_reused_child_syntax(self):
        ast.parse(FILE.read_text(encoding='utf-8'),feature_version=(3,7))
        ast.parse(host.VISIBILITY_CHILD,feature_version=(3,7))

    def test_wrong_host_makes_zero_queries(self):
        with patch.object(host.sys,'argv',['tool','--verify-readonly']),patch.object(host.os,'name','nt'),\
             patch.object(host.subprocess,'Popen') as execute,patch('builtins.print') as output:
            self.assertEqual(host.main(),1)
        self.assertFalse(execute.called)
        self.assertIn('DATAX01_ROOT_REQUIRED',output.call_args.args[0])

    def test_non_readonly_commands_are_rejected_before_spawn(self):
        operation=host.Operation();operation.sample=SAMPLE
        for args in (['patch','deployment','gateway'],['delete','pod',SAMPLE['pod']],['get','secret','deer-flow-env'],
                     ['get','pods','-A'],['exec',SAMPLE['pod'],'-c','sandbox','--','python3','-c','print("arbitrary")']):
            with patch.object(host.subprocess,'Popen') as execute:
                with self.assertRaisesRegex(host.Refused,'FIXED_READONLY_COMMAND_REQUIRED'):
                    operation.command(args)
            self.assertFalse(execute.called)

    def test_fixed_exec_succeeds_and_changed_target_or_code_is_rejected(self):
        operation=host.Operation();operation.sample=SAMPLE
        args=['exec',SAMPLE['pod'],'-c','sandbox','--','python3','-I','-B','-c',host.VISIBILITY_CHILD,host.TREE]
        class Process:
            returncode=0
            def poll(self):return 0
        def popen(command,**kwargs):
            kwargs['stdout'].write(b'{}');return Process()
        with patch.object(host.subprocess,'Popen',side_effect=popen) as execute:
            self.assertEqual(operation.command(args),{})
        self.assertEqual(execute.call_args.args[0][-11:],args)
        for index,value in ((1,'sandbox-other'),(9,'print("changed")'),(10,'0'*64)):
            altered=list(args);altered[index]=value
            with self.assertRaisesRegex(host.Refused,'FIXED_READONLY_COMMAND_REQUIRED'):
                operation.command(altered)

    def test_not_ready_and_wrong_image_are_not_selected(self):
        raw=LINE+'\n'+LINE.replace('true','false')+'\n'+LINE.replace(host.IMAGE_ID,'sha256:'+'0'*64)
        rows,counts=host.candidates(raw)
        self.assertEqual(rows,[SAMPLE]);self.assertEqual(counts['not_ready'],1);self.assertEqual(counts['image_mismatch'],1)

    def test_malformed_inventory_has_explicit_error(self):
        with self.assertRaisesRegex(host.Refused,'ORDINARY_POD_LIST_SHAPE'):
            host.candidates('too few fields')

    def test_mount_writable_field_is_observed_without_mutating_or_blocking_readonly_hash(self):
        value=pod();value['spec']['containers'][0]['volumeMounts'][0]['readOnly']=False
        summary,mount,mounts=host.mount_summary(value)
        self.assertIs(summary['read_only'],False);host.require_mount(summary,mount,mounts)

    def test_wrong_subpath_or_claim_and_shadow_mount_are_refused(self):
        for field,value,code in (('subPath','other','ORDINARY_PUBLIC_SUBPATH_CHANGED'),):
            current=pod();current['spec']['containers'][0]['volumeMounts'][0][field]=value
            with self.assertRaisesRegex(host.Refused,code):host.require_mount(*host.mount_summary(current))
        current=pod();current['spec']['volumes'][0]['persistentVolumeClaim']['claimName']='other'
        with self.assertRaisesRegex(host.Refused,'ORDINARY_SKILLS_CLAIM_CHANGED'):host.require_mount(*host.mount_summary(current))
        current=pod();current['spec']['containers'][0]['volumeMounts'].append({'name':'shadow','mountPath':'/mnt/skills/public/energy-market-daily'})
        with self.assertRaisesRegex(host.Refused,'ORDINARY_PUBLIC_SHADOW_MOUNT'):host.require_mount(*host.mount_summary(current))

    def simulate(self,child=None,pod_change=None):
        operation=host.Operation();calls=[];reads=0
        observation={'status':'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE','uid':1000,'gid':1000,'skill_tree_sha256':host.TREE,
                     'runtime_manifest_sha256':host.RUNTIME_SHA}
        observation.update(child or {})
        def command(args,**kwargs):
            nonlocal reads
            calls.append(args)
            if args==host.LIST:return LINE
            if args[:2]==['get','pvc']:return {'metadata':{'uid':host.CLAIM_UID},'status':{'phase':'Bound'}}
            if args[:2]==['get','pod']:
                reads+=1;value=pod()
                if reads==2 and pod_change:pod_change(value)
                return value
            if args[0]=='exec':return observation
            raise AssertionError(args)
        with patch.object(host,'receipt',return_value={'accepted':'fixed'}),patch.object(operation,'command',side_effect=command):
            try:operation.run()
            except host.Refused as exc:return operation,calls,str(exc)
        return operation,calls,None

    def test_fixed_visibility_complete_path(self):
        operation,calls,error=self.simulate()
        self.assertIsNone(error);self.assertEqual(operation.out['status'],'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE_READONLY_ACCEPTED')
        self.assertEqual([args[0] for args in calls],['get','get','get','exec','get'])
        self.assertFalse(operation.out['files_modified']);self.assertFalse(operation.out['provider_requests'])

    def test_child_error_code_survives_with_pod_recheck(self):
        operation,calls,error=self.simulate({'status':'INCOMPLETE','error_class':'ValueError','error_code':'ORDINARY_RUNTIME_MANIFEST_MISMATCH'})
        self.assertEqual(error,'ORDINARY_RUNTIME_MANIFEST_MISMATCH')
        self.assertEqual(calls[-1][:2],['get','pod']);self.assertEqual(operation.out['observation']['error_code'],error)

    def test_container_restart_or_replacement_invalidates_read(self):
        _,_,error=self.simulate(pod_change=lambda value:value['status']['containerStatuses'][0].update(restartCount=1))
        self.assertEqual(error,'ORDINARY_POD_OR_CONTAINER_CHANGED')
        _,_,error=self.simulate(pod_change=lambda value:value['metadata'].update(uid='0'*36))
        self.assertEqual(error,'ORDINARY_POD_REPLACED')

    def test_visibility_child_only_reads_public_skill_and_tmp_metadata(self):
        tree=ast.parse(host.VISIBILITY_CHILD)
        attrs={row.func.attr for row in ast.walk(tree) if isinstance(row,ast.Call) and isinstance(row.func,ast.Attribute)}
        self.assertFalse(attrs&{'write','write_bytes','write_text','mkdir','unlink','rmdir','launch','Popen','gettempdir','TemporaryDirectory'})
        self.assertIn('statvfs',attrs);self.assertNotIn('JKX_',host.VISIBILITY_CHILD)
        self.assertNotIn('/mnt/user-data',host.VISIBILITY_CHILD)


if __name__=='__main__':
    unittest.main()
