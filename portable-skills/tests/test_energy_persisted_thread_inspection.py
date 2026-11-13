"""No real database: fixed owner lookup, storage closure and sealed-read boundaries."""
import ast
import asyncio
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]/'deployment/public-runtime-20261112-r1'

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

host=load('persisted_host',HERE/'inspect-energy-daily-persisted-thread-20261112-r1.py')
child=load('persisted_child',HERE/'inspect_persisted_child.py')
legacy=load('fixed_thread_classifier',HERE/'inspect-energy-daily-thread-20261112-r1.py')

MOUNTS=[{'mount_path':'/app/backend/.deer-flow','claim':'deer-flow-data-rwx-pvc','subpath_sha256':hashlib.sha256(b'').hexdigest()}]
ROOT=MOUNTS[0]['mount_path']+'/users/owner-private/threads/'+host.THREAD+'/user-data'

def pod():
    return {'metadata':{'name':'gateway-abcd-xyz','uid':'11111111-1111-1111-1111-111111111111','labels':{'app':'gateway'},
            'ownerReferences':[{'kind':'ReplicaSet','name':'gateway-abcd','uid':'rs-uid','controller':True}]},
            'spec':{'containers':[{'name':'gateway','volumeMounts':[{'name':'data','mountPath':MOUNTS[0]['mount_path']}]}],
                    'volumes':[{'name':'data','persistentVolumeClaim':{'claimName':MOUNTS[0]['claim']}}]},
            'status':{'phase':'Running','containerStatuses':[{'name':'gateway','ready':True,'imageID':'fixed-image','containerID':'fixed-container','restartCount':0}]}}

def rs():
    return {'metadata':{'name':'gateway-abcd','uid':'rs-uid','ownerReferences':[{'kind':'Deployment','name':'gateway','uid':host.GATEWAY_UID,'controller':True}]}}

class HostTests(unittest.TestCase):
    def test_python37_operator_syntax(self):
        ast.parse((HERE/'inspect-energy-daily-persisted-thread-20261112-r1.py').read_text(encoding='utf-8'),feature_version=(3,7))

    def test_fixed_thread_and_actual_mount_coverage(self):
        self.assertEqual(host.verify_root(ROOT,MOUNTS),ROOT)
        for root in (ROOT.replace(host.THREAD,'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),ROOT+'/../other',ROOT.replace('/app/backend/.deer-flow','/private'),ROOT.replace('/owner-private/','/../')):
            with self.assertRaises(host.Refused):host.verify_root(root,MOUNTS)

    def test_controller_ownership_not_label_only(self):
        self.assertEqual(host.ownership(pod(),rs(),host.GATEWAY_UID)['uid'],'rs-uid')
        bad=rs();bad['metadata']['ownerReferences'][0]['uid']='unrelated'
        with self.assertRaisesRegex(host.Refused,'DEPLOYMENT_OWNERSHIP'):host.ownership(pod(),bad,host.GATEWAY_UID)

    def test_shadow_mount_and_subpath_expression_refused(self):
        obj=pod();obj['spec']['containers'][0]['volumeMounts'].append({'name':'secret','mountPath':ROOT+'/workspace'})
        with self.assertRaisesRegex(host.Refused,'SHADOW'):host.reject_shadow_mounts(ROOT,obj,MOUNTS)
        obj=pod();obj['spec']['volumes'].append({'name':'other-pvc','persistentVolumeClaim':{'claimName':'unrelated-pvc'}})
        obj['spec']['containers'][0]['volumeMounts'].append({'name':'other-pvc','mountPath':ROOT+'/workspace'})
        with self.assertRaisesRegex(host.Refused,'SHADOW'):host.reject_shadow_mounts(ROOT,obj,host.userdata_mounts(obj))
        obj=pod();obj['spec']['containers'][0]['volumeMounts'][0]['subPathExpr']='$(arbitrary)'
        with self.assertRaisesRegex(host.Refused,'MOUNT_PATH'):host.userdata_mounts(obj)

    def test_command_guard_refuses_mutations_secrets_and_unbound_exec(self):
        op=host.Operation('fixed-child');op.pod='gateway-abcd-xyz'
        for args in (['delete','pod',op.pod],['get','secret','deer-flow-env'],['get','pod','gateway-other','-o','json'],
                     ['exec','-i',op.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c','arbitrary','inspect']):
            with self.assertRaisesRegex(host.Refused,'READONLY_FIXED_COMMAND'):op.command(args)

    def test_host_redacts_owner_root_from_final_json(self):
        deployment={'metadata':{'uid':host.GATEWAY_UID,'generation':414},'spec':{'replicas':70},'status':{'readyReplicas':70,'updatedReplicas':70}}
        resolved={'status':'NAMED_THREAD_OWNER_ROOT_RESOLVED_READONLY','trusted_root':ROOT,'database_credentials_used':True,
                  'owner_fingerprint':'a'*64,'path_resolver_source':{'sha256':'b'*64}}
        class FakeOperation:
            def __init__(self,code):self.child,self.pod,self.queries,self.mounts,self.scope,self.rs=code,None,0,None,None,None
            def command(self,args,body=None):
                self.queries+=1
                if args==host.LIST:return 'gateway-abcd-xyz 11111111-1111-1111-1111-111111111111 Running <none> true'
                if args[:2]==['get','deployment']:return json.dumps(deployment)
                if args[:2]==['get','pod']:return json.dumps(pod())
                if args[:2]==['get','replicaset']:return json.dumps(rs())
                self.assert_payload=body
                if args[-1]=='resolve':return json.dumps(resolved)
                return json.dumps({'status':'NAMED_THREAD_PERSISTED_MARKET_OBSERVED_NO_CHANGE','database_credentials_used':True})
        output=io.StringIO()
        with patch.object(host,'Operation',FakeOperation),patch.object(host,'assets',return_value='sealed-child'),patch.object(host.os,'name','posix'),patch.object(host.os,'uname',return_value=types.SimpleNamespace(nodename='datax-01'),create=True),patch.object(host.os,'geteuid',return_value=0,create=True),patch.object(sys,'argv',['probe','--inspect-readonly']),contextlib.redirect_stdout(output):
            self.assertEqual(host.main(),0)
        value=json.loads(output.getvalue());self.assertEqual(value['queries'],8)
        self.assertTrue(value['database_credentials_used']);self.assertEqual(value['gateway_replicas']['updated'],70)
        self.assertNotIn('owner-private',output.getvalue());self.assertNotIn('trusted_root',output.getvalue())

class AuditTests(unittest.TestCase):
    def test_write_subprocess_and_external_network_are_blocked(self):
        for event,args in [('open',('/tmp/x','w',0)),('os.mkdir',('/tmp/x',511,-1)),('subprocess.Popen',('sh',[],None,None)),('socket.connect',(None,('116.204.43.227',6688)))]:
            with self.assertRaises(child.Refused):child.audit(event,args)

    def test_audit_allows_readonly_open_and_only_database_destination(self):
        child.audit('open',('/tmp/x','r',0));child.audit('socket.__new__',(None,1,1,0))
        with patch.object(child,'DB_ADDRESSES',{'192.0.2.1'}),patch.object(child,'DB_PORT',5432):
            child.audit('socket.connect',(None,('192.0.2.1',5432)))
            with self.assertRaises(child.Refused):child.audit('socket.connect',(None,('192.0.2.1',80)))

class TransactionTests(unittest.TestCase):
    def test_only_parameterized_owner_select_and_readonly_limits(self):
        log=[]
        class Tx:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
        class Connection:
            def transaction(self,**kwargs):log.append(('transaction',kwargs));return Tx()
            async def execute(self,query):log.append(('execute',query))
            async def fetchval(self,query,*args):log.append(('fetchval',query,args));return 'on' if query.startswith('SHOW') else 'owner'
        self.assertEqual(asyncio.run(child.readonly_owner(Connection())),'owner')
        self.assertEqual(log[0],('transaction',{'readonly':True,'isolation':'repeatable_read'}))
        self.assertIn(('execute',"SET LOCAL lock_timeout='500ms'"),log)
        selects=[row for row in log if row[0]=='fetchval' and row[1].startswith('SELECT')]
        self.assertEqual(selects,[('fetchval',child.READONLY_SQL,(child.THREAD,))])

@unittest.skipUnless(os.name=='posix','Real POSIX storage and audit behavior')
class StoredTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='persisted-probe-');self.base=Path(self.temp.name)
        self.root=self.base/'users/owner/threads'/child.THREAD/'user-data';(self.root/'workspace').mkdir(parents=True)
        self.source=self.base/'paths.py';self.source.write_text('# fake path resolver\n')
        self.paths=types.SimpleNamespace(__file__=str(self.source),get_paths=lambda:types.SimpleNamespace(base_dir=self.base,sandbox_user_data_dir=lambda thread,user_id:self.base/'users'/user_id/'threads'/thread/'user-data'))
        self.mounts=[{'mount_path':str(self.base),'claim':'data-pvc','subpath_sha256':'a'*64}]

    def tearDown(self):self.temp.cleanup()

    def fake_observe(self,mode='inspect',scope=None,owners=('owner','owner')):
        values=iter(owners)
        class Connection:
            async def close(self,timeout):pass
        async def connect(*args,**kwargs):return Connection()
        async def owner(conn):return next(values)
        config=lambda:types.SimpleNamespace(database=types.SimpleNamespace(backend='postgres',postgres_url='postgresql://fake-user:private@database.internal:5432/fake'))
        with patch.object(child,'runtime_modules',return_value=(types.SimpleNamespace(connect=connect),config,self.paths)),patch.object(child.socket,'getaddrinfo',return_value=[(2,1,6,'',('192.0.2.1',5432))]),patch.object(child,'readonly_owner',side_effect=owner),patch.object(child,'inspect_stored',return_value={'projects':[]}):
            return asyncio.run(child.observe(self.mounts,mode,scope))

    def scope(self):return {'root':str(self.root),'owner_fingerprint':child.digest(b'owner'),'path_resolver_sha256':child.digest(self.source.read_bytes())}

    def test_two_stage_owner_resolution_and_matching_inspection(self):
        resolved=self.fake_observe('resolve');self.assertEqual(resolved['trusted_root'],str(self.root));self.assertEqual(resolved['sql_owner_reads'],2)
        inspected=self.fake_observe(scope=self.scope());self.assertEqual(inspected['status'],'NAMED_THREAD_PERSISTED_MARKET_OBSERVED_NO_CHANGE')
        self.assertNotIn('trusted_root',inspected);self.assertNotIn('private@',json.dumps(inspected))

    def test_scope_and_owner_changes_refuse(self):
        result=self.fake_observe(scope={'root':'/other'});self.assertEqual(result['error_code'],'TRUSTED_OWNER_STORAGE_SCOPE_CHANGED')
        result=self.fake_observe(scope=self.scope(),owners=('owner','someoneelse'));self.assertEqual(result['error_code'],'THREAD_OWNER_CHANGED_DURING_READ')

    def test_symlink_bucket_refuses_before_read(self):
        target=self.base/'real-owner';target.mkdir()
        link=self.base/'users/linked';link.symlink_to(target,target_is_directory=True)
        with self.assertRaisesRegex(child.Refused,'STORAGE_LINK'):
            child.trusted_root(self.paths,'linked',[self.base])

    def test_jkm_only_cache_outputs_classifications_not_values_or_owner(self):
        namespace={'__name__':'stored_classifier'};exec(compile(legacy.CHILD,'<fixed>','exec'),namespace)
        project=self.root/'workspace/energy-daily-20261111';(project/'cache/jkx').mkdir(parents=True)
        target='2026-11-11';cache={'errcode':'0','data':[]};market={'data_date':target,'retrieved_at':'2026-11-12T00:33:00+08:00','observations':[],'oil':[],'gas':[],'freight':[],'acquisition_failures':{}}
        for index,code in enumerate(namespace['CODES']):
            available=code=='XA0942';close='31.825' if available else ''
            cache['data'].append({'code_name':code,'value_date':target,'close':close})
            row={'code':code,'date':target,'raw_close':close,'status':'available' if available else 'missing','value':31.825 if available else None}
            market['observations'].append(row);market['oil' if index<3 else 'gas' if index<6 else 'freight'].append(row)
        def save(name,data):(project/name).write_bytes(json.dumps(data).encode())
        save('market.json',market);save('cache/jkx/jkx_new_'+target+'.json',cache)
        save('PROJECT.json',{'publication_date':'2026-11-12','data_date':target,'prepared_at':'2026-11-12T00:33:00+08:00','input_hashes':{'market':child.digest((project/'market.json').read_bytes())}})
        with patch.object(child,'classifier',return_value=namespace):result=child.inspect_stored(self.root)
        row=result['projects'][0];self.assertTrue(row['market_matches_project_input_hash'])
        self.assertEqual(row['target_cache']['series'][0]['close_classes'],['recognized_missing'])
        self.assertNotIn('31.825',json.dumps(result));self.assertNotIn(str(self.base),json.dumps(result))

    def test_installed_audit_keeps_asyncio_socketpair_usable(self):
        source=(HERE/'inspect_persisted_child.py').read_text(encoding='utf-8')
        program="import sys;ns={'__name__':'readonly_audit_fixture'};exec("+repr(source)+",ns);sys.addaudithook(ns['audit']);exec('async def noop():\\n return 1',ns);assert ns['asyncio'].run(ns['noop']())==1"
        result=subprocess.run([sys.executable,'-I','-B','-c',program],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))

if __name__=='__main__':unittest.main()
