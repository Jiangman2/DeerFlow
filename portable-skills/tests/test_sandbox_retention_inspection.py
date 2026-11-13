"""Read-only config, node-local workload grouping and metadata privacy."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest

HERE=Path(__file__).resolve().parents[1]/'deployment/retention-20261112-r1'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
sealer=load('retention_sealer',HERE/'seal_retention_probe.py')
child=load('retention_child',HERE/'inspect_retention_child.py')
host=types.ModuleType('retention_host');exec(compile(sealer.source_host(),'<fixed-retention-host>','exec'),host.__dict__)

class PolicyTests(unittest.TestCase):
    def test_three_numeric_values_only_and_zero_idle_is_not_changed(self):
        cfg=types.SimpleNamespace(sandbox=types.SimpleNamespace(replicas=120,idle_timeout=0,startup_timeout=180,password='private-token'))
        result=child.configured_policy(cfg);self.assertEqual(result,{'replicas':120,'idle_timeout':0,'startup_timeout':180});self.assertNotIn('private-token',json.dumps(result))
    def test_unset_values_stay_unknown(self):self.assertEqual(child.configured_policy(types.SimpleNamespace()),{'replicas':None,'idle_timeout':None,'startup_timeout':None})
    def test_negative_disabled_idle_is_reported_without_normalization(self):
        result=child.configured_policy(types.SimpleNamespace(sandbox=types.SimpleNamespace(replicas=3,idle_timeout=-1,startup_timeout=180)))
        self.assertEqual(result['idle_timeout'],-1)
    def test_non_numeric_policy_rejected_without_value_echo(self):
        with self.assertRaisesRegex(child.Refused,'POLICY_NUMERIC_SHAPE'):child.configured_policy(types.SimpleNamespace(sandbox=types.SimpleNamespace(replicas='private-token')))
    def test_audit_forbids_network_write_cleanup_and_subprocess(self):
        for event,args in [('socket.__new__',()),('socket.connect',()),('subprocess.Popen',()),('os.remove',()),('open',('/tmp/test','w',0)),('sqlite3.connect',())]:
            with self.assertRaises(child.Refused):child.audit(event,args)
    def test_source_does_not_import_or_call_provider_or_database(self):
        source=(HERE/'inspect_retention_child.py').read_text(encoding='utf-8')
        self.assertNotIn('get_sandbox_provider',source);self.assertNotIn('asyncpg',source)
        self.assertNotIn('_cleanup',source);self.assertIn('get_app_config()',source)

class WorkloadTests(unittest.TestCase):
    def test_sandbox_names_threads_and_owners_are_omitted(self):
        raw='deer-flow sandbox-private-thread-id <none> <none> 750m,250m 1Gi,512Mi sandbox\n'
        result=host.workload_groups(raw);self.assertEqual(result['groups'][0]['workload_type'],'sandbox');self.assertEqual(result['groups'][0]['pod_count'],1)
        self.assertNotIn('private-thread-id',json.dumps(result));self.assertNotIn('namespace',result['groups'][0])
    def test_normal_workloads_group_by_owner_and_resources(self):
        raw='model model-server-aaa ReplicaSet model-server-rs 8 32Gi inference\nmodel model-server-bbb ReplicaSet model-server-rs 8 32Gi inference\ndeer-flow energy-maint-abcd1234 <none> <none> 500m 512Mi <none>\n'
        result=host.workload_groups(raw);groups=result['groups']
        model=next(row for row in groups if row.get('owner')=='model-server-rs');self.assertEqual(model['pod_count'],2);self.assertEqual(model['container_memory_requests'],['32Gi'])
        self.assertNotIn('abcd1234',json.dumps(result));self.assertEqual(result['nonterminated_pod_count'],3)
    def test_uuid_in_non_sandbox_owner_is_redacted(self):
        raw='jobs job-pod Job task-201fb3d5-49c0-4276-9e3f-3db67f8edfe7 1 1Gi <none>'
        result=host.workload_groups(raw);self.assertNotIn('201fb3d5',json.dumps(result));self.assertIn('[id]',result['groups'][0]['owner'])
    def test_scope_and_quantity_bounds(self):
        with self.assertRaises(host.Refused):host.quantities('https://private-token')
        with self.assertRaises(host.Refused):host.workload_groups('bad row')
        self.assertEqual(host.quantities('<none>'),[])

class GuardTests(unittest.TestCase):
    def test_python37_host_and_component_label_escape(self):
        ast.parse(sealer.source_host().decode(),feature_version=(3,7));self.assertIn('app\\.kubernetes\\.io/component',host.NODE_PODS[-1]);self.assertNotIn('app\\\\.',host.NODE_PODS[-1])
    def test_mutations_other_nodes_and_secrets_refused(self):
        operation=host.Operation('fixed-child');operation.pod='gateway-abcd-xyz'
        for args in (['get','pods','-A'],['get','secret','deer-flow-env'],['delete','pod','sandbox-private'],['exec',operation.pod,'--','sh']):
            with self.assertRaises(host.Refused):operation.command(args)

@unittest.skipUnless(os.name=='posix','Actual readonly audit and signal behavior')
class LinuxChildTests(unittest.TestCase):
    def test_fake_config_main_runs_with_audit_and_no_real_application(self):
        source=(HERE/'inspect_retention_child.py').read_text(encoding='utf-8')
        program="import sys,types;ns={'__name__':'fixture'};exec("+repr(source)+",ns);mod=types.ModuleType('deerflow.config');mod.get_app_config=lambda:types.SimpleNamespace(sandbox=types.SimpleNamespace(replicas=3,idle_timeout=0,startup_timeout=180));sys.modules['deerflow']=types.ModuleType('deerflow');sys.modules['deerflow.config']=mod;assert ns['main']()==0"
        result=subprocess.run([sys.executable,'-I','-B','-c',program],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'));parsed=json.loads(result.stdout)
        self.assertEqual(parsed['configured_policy']['idle_timeout'],0);self.assertFalse(parsed['provider_instantiation_requested']);self.assertEqual(parsed['network_requests'],0)

if __name__=='__main__':unittest.main()
