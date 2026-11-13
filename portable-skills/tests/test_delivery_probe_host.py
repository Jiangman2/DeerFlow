"""Fixed identity, non-secret config mount metadata and read-only transport."""
import ast
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]/'deployment/webhook-budget-20261112-r1'
spec=importlib.util.spec_from_file_location('delivery_sealer',HERE/'seal_delivery_probe.py')
sealer=importlib.util.module_from_spec(spec);spec.loader.exec_module(sealer)
host=types.ModuleType('delivery_probe_host');exec(compile(sealer.source_host(),'<delivery-probe-host>','exec'),host.__dict__)
UID='11111111-2222-3333-4444-555555555555'

def pod():
    return {'metadata':{'name':'gateway-abc-def','uid':UID,'labels':{'app':'gateway'},'ownerReferences':[{'controller':True,'kind':'ReplicaSet','name':'gateway-abc','uid':'rs-uid'}]},
        'spec':{'volumes':[{'name':'config','configMap':{'name':'runtime-config','items':[{'key':'config.yaml','path':'config.yaml'}]}},
            {'name':'secret','secret':{'secretName':'never-export-secret-name'}}],
            'containers':[{'name':'gateway','env':[{'name':'PRIVATE','value':'never-export-secret-value'}],
                'volumeMounts':[{'name':'config','mountPath':'/app/backend/config.yaml','subPath':'config.yaml','readOnly':True},
                    {'name':'secret','mountPath':'/private/key','readOnly':True}]}]},
        'status':{'phase':'Running','containerStatuses':[{'name':'gateway','ready':True,'restartCount':0,'imageID':'image','containerID':'container'}]}}

class HostTests(unittest.TestCase):
    def test_configmap_subpath_retained_without_config_contents(self):
        metadata=host.mount_metadata(pod());self.assertEqual(metadata[0]['source_name'],'runtime-config')
        self.assertEqual(metadata[0]['subpath'],'config.yaml');self.assertTrue(metadata[0]['read_only'])
        self.assertEqual(metadata[0]['items'],[{'key':'config.yaml','path':'config.yaml'}])
    def test_secret_name_values_and_environment_are_not_exported(self):
        raw=json.dumps(host.mount_metadata(pod()));self.assertNotIn('never-export',raw);self.assertNotIn('PRIVATE',raw)
        self.assertIn('Secret',raw)
    def test_malformed_mount_refused_without_echo(self):
        value=pod();value['spec']['containers'][0]['volumeMounts'][0]['mountPath']='/app/../private-token'
        with self.assertRaisesRegex(host.Refused,'MOUNT_METADATA_PATH'):host.mount_metadata(value)
    def test_command_mutations_and_wrong_thread_body_refused(self):
        operation=host.Operation('sealed-child');operation.pod='gateway-abc-def'
        for args,body in [(['delete','pod','gateway-abc-def'],None),(['get','secret','private'],None),
            (['exec','-i',operation.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c','sealed-child'],b'{"thread_id":"other"}')]:
            with self.assertRaises(host.Refused):operation.command(args,body)
    def test_python37_host_syntax_and_no_reclaim_or_send_api(self):
        source=sealer.source_host().decode();ast.parse(source,feature_version=(3,7))
        self.assertNotIn('flock',source);self.assertNotIn('get_sandbox_provider',source);self.assertNotIn('requests.post',source)
    def test_combined_child_compiles_and_wires_only_readonly_runtime_observer(self):
        raw=sealer.combined_child();ast.parse(raw.decode(),feature_version=(3,7))
        namespace={'__name__':'combined_child_fixture'};exec(compile(raw,'<combined-child-fixture>','exec'),namespace)
        self.assertIs(namespace['RUNTIME_OBSERVER'],namespace['observe_runtime'])
        self.assertIn(host.THREAD,raw.decode())

class IntegrationTests(unittest.TestCase):
    def run_host(self,status='DELIVERY_TIMELINE_OBSERVED_READONLY',thread=None,changed=False):
        gateway=pod();dep={'metadata':{'uid':host.GATEWAY_UID,'generation':414},'spec':{'replicas':70}}
        rs={'metadata':{'name':'gateway-abc','uid':'rs-uid','ownerReferences':[{'controller':True,'kind':'Deployment','name':'gateway','uid':host.GATEWAY_UID}]}}
        calls=[];reads=0
        class FakeOperation(host.Operation):
            def command(self,args,body=None):
                nonlocal reads
                self.queries+=1;calls.append(args)
                if args==host.LIST:return 'gateway-abc-def '+UID+' Running <none> true\n'
                if args==['get','deployment','gateway','-o','json']:return json.dumps(dep)
                if args==['get','pod','gateway-abc-def','-o','json']:
                    reads+=1
                    if changed and reads==2:gateway['status']['containerStatuses'][0]['restartCount']=1
                    return json.dumps(gateway)
                if args==['get','replicaset','gateway-abc','-o','json']:return json.dumps(rs)
                if args[0]=='exec':
                    self_outer.assertEqual(json.loads(body),{'thread_id':host.THREAD})
                    return json.dumps({'requested_thread_id':thread or host.THREAD,'status':status,'work_messages_sent':0})
                raise AssertionError('unexpected command')
        self_outer=self;captured=[]
        with patch.object(host,'Operation',FakeOperation),patch.object(host,'assets',return_value='sealed-child'),patch.object(host.os,'name','posix'),\
            patch.object(host.os,'uname',return_value=types.SimpleNamespace(nodename='datax-01'),create=True),patch.object(host.os,'geteuid',return_value=0,create=True),\
            patch.object(host.sys,'argv',['fixture','--inspect-readonly']),patch('builtins.print',side_effect=lambda value,**kwargs:captured.append(value)):
            code=host.main()
        return code,json.loads(captured[-1]),calls
    def test_seven_reads_and_sanitized_metadata(self):
        code,result,calls=self.run_host();self.assertEqual(code,0);self.assertEqual(len(calls),7)
        self.assertNotIn('never-export',json.dumps(result));self.assertFalse(result['task_retry_requested']);self.assertEqual(result['work_messages_sent'],0)
    def test_incomplete_and_wrong_thread_fail_closed(self):
        code,result,_=self.run_host(status='INCOMPLETE');self.assertEqual(code,1);self.assertEqual(result['error_code'],'DELIVERY_TIMELINE_INCOMPLETE')
        code,result,_=self.run_host(thread='other');self.assertEqual(code,1);self.assertEqual(result['error_code'],'FIXED_THREAD_DIAGNOSTIC_SCOPE_REQUIRED')
    def test_restart_invalidates_readback(self):
        code,result,_=self.run_host(changed=True);self.assertEqual(code,1);self.assertEqual(result['error_code'],'GATEWAY_CHANGED_DURING_READ')
    def test_missing_run_is_a_completed_bounded_observation(self):
        code,result,_=self.run_host(status='NAMED_THREAD_RUN_NOT_FOUND');self.assertEqual(code,0)
        self.assertEqual(result['observation']['status'],'NAMED_THREAD_RUN_NOT_FOUND')

if __name__=='__main__':unittest.main()
