"""Native allocation parsing and no-mutation capacity snapshot fixtures."""
import ast
import importlib.util
import json
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parents[1]/'deployment/capacity-20261112-r1'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
host=load('capacity_host',HERE/'inspect-sandbox-capacity-20261112-r1.py')
child=load('capacity_child',HERE/'inspect_capacity_child.py')

DESCRIBE='''Name:               node-a
InternalIP:         private-not-exported
Allocatable:
  cpu:                16
Non-terminated Pods:          (25 in total)
  Namespace Name CPU Requests CPU Limits Memory Requests Memory Limits Age
  private   hidden  100m 1 1Gi 2Gi 1d
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests          Limits
  --------           --------          ------
  cpu                15000m (93%)      32 (200%)
  memory             60Gi (93%)        100Gi (156%)
  ephemeral-storage  20Gi (20%)        60Gi (60%)
Events:
  Warning private-message-never-exported
'''

class ParsingTests(unittest.TestCase):
    def test_native_allocated_strings_not_recomputed(self):
        result=host.native_allocations(DESCRIBE,['node-a'])
        self.assertEqual(result[0]['native_nonterminated_pod_count'],25)
        self.assertEqual(result[0]['allocated_resources']['cpu']['requests_native'],'15000m (93%)')
        self.assertNotIn('private',json.dumps(result));self.assertNotIn('hidden',json.dumps(result))

    def test_missing_allocated_section_and_unknown_nodes_refuse(self):
        with self.assertRaises(host.Refused):host.native_allocations(DESCRIBE,['node-b'])
        with self.assertRaises(host.Refused):host.native_allocations('Name: node-a\n',['node-a'])
        partial=DESCRIBE.replace('  cpu                15000m (93%)      32 (200%)\n','').replace('  memory             60Gi (93%)        100Gi (156%)\n','')
        with self.assertRaises(host.Refused):host.native_allocations(partial,['node-a'])

    def test_nodes_export_only_scheduling_fields(self):
        value={'items':[{'metadata':{'name':'node-a','labels':{'kubernetes.io/hostname':'node-a','other':'private'}},'spec':{'unschedulable':True,'taints':[{'key':'node-role.kubernetes.io/control-plane','effect':'NoSchedule'}]},
            'status':{'conditions':[{'type':'Ready','status':'True','message':'private'}],'allocatable':{'cpu':'16','memory':'64Gi','pods':'110'},'addresses':[{'address':'private'}]}}]}
        result=host.summarize_nodes(value);self.assertTrue(result[0]['unschedulable']);self.assertEqual(result[0]['ready'],'True');self.assertNotIn('private',json.dumps(result))

    def test_sandbox_projection_has_no_environment_and_matches_only_named_threads(self):
        thread=host.NAMED_THREADS[0]
        line='sandbox-abcd1234 11111111-1111-1111-1111-111111111111 node-a Running <none> true 2026-11-12T00:00:00Z users/private-owner/threads/'+thread+'/user-data,public'
        rows,counts=host.sandbox_rows(line)
        self.assertEqual(rows[0]['named_thread_matches'],[thread]);self.assertEqual(counts['node-a']['ready'],1)
        self.assertNotIn('private-owner',json.dumps(rows));self.assertNotIn('env',host.SANDBOX_LIST[-1].lower())

    def test_template_includes_both_containers_init_and_pvc_not_environment(self):
        chosen={'name':'sandbox-abcd1234','uid':'fixed-uid'}
        pod={'metadata':chosen,'spec':{'nodeName':'node-a','containers':[{'name':'sandbox','resources':{'requests':{'cpu':'500m','memory':'512Mi'}},'env':[{'name':'PASSWORD','value':'private'}]},
            {'name':'ppt-master-agent-host','resources':{'requests':{'cpu':'250m','memory':'512Mi'}}}],
            'initContainers':[{'name':'init','resources':{'requests':{'cpu':'10m'}}}],'volumes':[{'persistentVolumeClaim':{'claimName':'data-pvc'}}]},
            'status':{'conditions':[{'type':'Ready','status':'False'}],'containerStatuses':[{'name':'sandbox','ready':True,'imageID':host.EXPECTED_IMAGE}]}}
        result=host.sandbox_template(pod,chosen);self.assertEqual(len(result['containers']),2);self.assertEqual(len(result['init_containers']),1)
        self.assertTrue(result['sandbox_container_ready']);self.assertFalse(result['pod_ready'])
        self.assertNotIn('private',json.dumps(result));self.assertEqual(result['pvc_claims'],['data-pvc'])

class SourceTests(unittest.TestCase):
    def test_literal_defaults_and_live_env_only(self):
        source=b"import os\nSANDBOX_CPU_REQUEST=os.environ.get('SANDBOX_CPU_REQUEST','500m')\nSANDBOX_NODE_POOL=f(os.environ.get('SANDBOX_NODE_POOL'))\nSECRET=os.environ.get('PASSWORD','private')\n"
        result=child.source_summary(source,{'SANDBOX_CPU_REQUEST':'750m','SANDBOX_NODE_POOL':'node-a,node-b','PASSWORD':'private'})
        self.assertEqual(result['environment_inputs']['SANDBOX_CPU_REQUEST']['value'],'750m')
        self.assertEqual(result['environment_inputs']['SANDBOX_CPU_REQUEST']['source_defaults'],[{'kind':'literal','value':'500m'}])
        self.assertNotIn('private',json.dumps(result));self.assertNotIn('PASSWORD',json.dumps(result))

    def test_resource_expressions_not_evaluated(self):
        source=b"c=k.V1Container(name='ppt-master-agent-host',resources=k.V1ResourceRequirements(requests={'cpu':'250m','memory':'512Mi'},limits={'cpu':dangerous()}))"
        result=child.source_summary(source,{})
        self.assertEqual(result['container_resource_expressions'][0]['requests']['cpu'],{'kind':'literal','value':'250m'})
        self.assertEqual(result['container_resource_expressions'][0]['limits']['cpu'],{'kind':'unknown_expression'})

    def test_invalid_quantity_or_credential_like_resource_value_refuses(self):
        for key,value in [('SANDBOX_CPU_REQUEST','https://private-token'),('PPT_MASTER_AGENT_HOST_ENABLED','private-password'),('SANDBOX_NODE_POOL','node-a;rm')]:
            with self.assertRaises(ValueError):child.safe_input(key,value)
        source=b"c=k.V1Container(name='sandbox',resources=k.V1ResourceRequirements(requests={'cpu':'private-password'}))"
        with self.assertRaises(ValueError):child.source_summary(source,{})

class GuardTests(unittest.TestCase):
    def test_python37_host_syntax(self):ast.parse((HERE/'inspect-sandbox-capacity-20261112-r1.py').read_text(encoding='utf-8'),feature_version=(3,7))
    def test_mutations_secrets_locks_and_arbitrary_exec_refused(self):
        op=host.Operation('sealed-child');op.pod='provisioner-abcd-xyz'
        for args in (['delete','pod','sandbox-abcd1234'],['get','secrets'],['patch','deployment','provisioner'],['exec',op.pod,'--','sh'],['get','pods','-A','-o','json']):
            with self.assertRaises(host.Refused):op.command(args)
    def test_child_has_no_business_imports_subprocess_network_or_database(self):
        tree=ast.parse((HERE/'inspect_capacity_child.py').read_bytes());modules=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):modules.extend(item.name for item in node.names)
            elif isinstance(node,ast.ImportFrom):modules.append(node.module)
        self.assertFalse(set(modules)&{'asyncpg','requests','socket','subprocess','deerflow','app','kubernetes'})

if __name__=='__main__':unittest.main()
