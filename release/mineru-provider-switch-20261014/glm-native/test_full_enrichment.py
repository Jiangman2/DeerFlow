import ast
import base64
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from contextlib import ExitStack
from types import SimpleNamespace

import accept_enrichment as a
import enrichment_fixture_runtime as r

NS='weknora-enrich-check-'+'a'*16


def fixture():
    gw={'service_ip':'10.96.1.2','pods':[{'ip':'10.244.1.4'}]}
    with patch.object(a,'RUNTIME_SOURCE','reviewed source'),patch.object(a,'SAMPLE_B64',base64.b64encode(b'png').decode()):
        return a.manifests(NS,{'app':'candidate','postgres':'postgres','redis':'redis'},b'\xff\x00CA',{},
            ['10.77.1.2'],[],gw,{k:'a'*32 for k in ('bootstrap','app','jwt','aes')})


def indexed():
    return {'knowledge_id':'k','parse_status':'completed','chunks':[
        {'id':'c1','kind':'image_ocr','characters':12,'parent_bound':True,'vectors':1,'has_orbit':True,'has_number':True},
        {'id':'c2','kind':'image_caption','characters':40,'parent_bound':True,'vectors':1,'has_blue':True}]}


class ManifestTests(unittest.TestCase):
    def test_binary_ca_exact_and_payload_not_secret(self):
        policy,cm,secret,pod=fixture()
        self.assertEqual(base64.b64decode(cm['binaryData']['ca.bin']),b'\xff\x00CA')
        self.assertTrue(cm['immutable']); self.assertTrue(secret['immutable'])
        self.assertEqual(set(secret['data']),{'bootstrap','app','jwt','aes'})

    def test_stores_disposable_and_loopback(self):
        p=fixture()[-1]['spec']; text=json.dumps(p)
        for forbidden in ('hostPath','persistentVolumeClaim','envFrom','containerPort'):
            self.assertNotIn(forbidden,text)
        self.assertFalse(p['automountServiceAccountToken']); self.assertFalse(p['enableServiceLinks'])
        self.assertEqual(p['restartPolicy'],'Never'); self.assertEqual(p['activeDeadlineSeconds'],1200)
        self.assertEqual(len(p['containers']),3)
        pg=p['containers'][1]['command']; redis=p['containers'][2]['command']
        self.assertEqual(pg[:2],['/usr/bin/env','-i']); self.assertIn('/usr/lib/postgresql/16/bin',pg[2])
        self.assertIn('listen_addresses=127.0.0.1',pg); self.assertIn('shared_preload_libraries=pg_search',pg)
        self.assertIn('max_connections=40',pg); self.assertEqual(redis[:2],['/usr/bin/env','-i'])
        self.assertEqual(redis[redis.index('--bind')+1],'127.0.0.1')

    def test_egress_exact_ports(self):
        policy,*_=fixture(); s=policy['spec']
        self.assertEqual(s['ingress'],[])
        self.assertEqual([x['ports'][0]['port'] for x in s['egress']],[443,8001])
        self.assertNotIn('0.0.0.0/0',json.dumps(s))

    def test_default_empty_ingress_and_tolerations(self):
        objs=fixture()
        for obj in objs:
            actual=copy.deepcopy(obj); actual['metadata']['uid']='ours'
            if obj['kind']=='NetworkPolicy': del actual['spec']['ingress']
            if obj['kind']=='Pod':
                actual['spec']['nodeName']='datax-01'
                actual['spec']['tolerations']=[{'key':'node.kubernetes.io/'+k,'operator':'Exists','effect':'NoExecute','tolerationSeconds':300} for k in ('not-ready','unreachable')]
                actual['spec']['serviceAccountName']='default'
            a.validate(actual,obj,'ours')

    def test_bad_pod_admission(self):
        expected=fixture()[-1]
        for mutation in ('hostNetwork','hostPID','hostIPC','env','args','sidecar','init','token','volume','image','uid','node','caps','privileged'):
            actual=copy.deepcopy(expected); actual['metadata']['uid']='ours'; s=actual['spec']; c=s['containers'][0]
            if mutation in ('hostNetwork','hostPID','hostIPC'): s[mutation]=True
            elif mutation=='env': c['env']=[{'name':'DB_HOST','value':'production'}]
            elif mutation=='args': c['args']=['oops']
            elif mutation=='sidecar': s['containers'].append(copy.deepcopy(c))
            elif mutation=='init': s['initContainers']=[{'name':'injected'}]
            elif mutation=='token': s['automountServiceAccountToken']=True
            elif mutation=='volume': s['volumes'].append({'name':'outside','hostPath':{'path':'/'}})
            elif mutation=='image': c['image']='other'
            elif mutation=='uid': actual['metadata']['uid']='foreign'
            elif mutation=='node': s['nodeName']='datax-03'
            elif mutation=='caps': c['securityContext']['capabilities']['add']=['NET_ADMIN']
            elif mutation=='privileged': c['securityContext']['privileged']=True
            with self.subTest(mutation=mutation),self.assertRaises(a.native.AcceptanceFailure): a.validate(actual,expected,'ours')

    def test_policy_injection_rejected(self):
        e=fixture()[0]; p=copy.deepcopy(e); p['spec']['ingress']=[{}]
        with self.assertRaises(a.native.AcceptanceFailure): a.validate(p,e)
        p=copy.deepcopy(e); p['spec']['egress'].append({})
        with self.assertRaises(a.native.AcceptanceFailure): a.validate(p,e)

    def test_payload_injection_rejected(self):
        for e in fixture()[1:3]:
            p=copy.deepcopy(e); p['data']['unexpected']='injected'
            with self.assertRaises(a.native.AcceptanceFailure): a.validate(p,e)


class AcceptanceTests(unittest.TestCase):
    def test_index_acceptance_needs_actual_vectors(self):
        self.assertTrue(a.index_pass(indexed()))
        for field,value in (('vectors',0),('parent_bound',False),('characters',0),('has_number',False),('has_orbit',False)):
            result=indexed(); result['chunks'][0][field]=value
            with self.subTest(field=field): self.assertFalse(a.index_pass(result))
        result=indexed(); result['chunks'][1]['has_blue']=False; self.assertFalse(a.index_pass(result))
        result=indexed(); result['parse_status']='processing'; self.assertFalse(a.index_pass(result))

    def test_no_flag_only_or_disabled_index_pass(self):
        for value in ({'indexed':True},{'parse_status':'completed','chunks':[]},{}):
            self.assertFalse(a.index_pass(value))

    def test_query_requires_our_child_or_merged_subchunk(self):
        for item in ({'id':'c1','knowledge_id':'k'},{'id':'parent','knowledge_id':'k','sub_chunk_id':['c2']}):
            self.assertTrue(a.search_pass({'matches':[item]},indexed()))
        for item in ({'id':'c1','knowledge_id':'other'},{'id':'parent','knowledge_id':'k'},{'id':'other','knowledge_id':'k','sub_chunk_id':None}):
            self.assertFalse(a.search_pass({'matches':[item]},indexed()))

    def test_quantity(self):
        for v,expected in [('500m',.5),('2Gi',2*1024**3),('123',123),('250000000n',.25)]:
            self.assertEqual(a.number(v),expected)
        for v in ('oops','-1','1Z'):
            with self.assertRaises(a.native.AcceptanceFailure): a.number(v)

    def test_clean_child_environment(self):
        with patch.object(r,'secret',return_value='a'*32),patch.dict(os.environ,{'DB_HOST':'production','HTTPS_PROXY':'secret','PROD_PASSWORD':'notcopied'}):
            env=r.environment('app'); bootstrap=r.environment('bootstrap')
        self.assertEqual(env['DB_HOST'],'127.0.0.1'); self.assertEqual(env['REDIS_ADDR'],'127.0.0.1:6379')
        self.assertEqual(env['AUTO_MIGRATE'],'false'); self.assertEqual(bootstrap['AUTO_MIGRATE'],'true')
        self.assertEqual(env['DB_USER'],'fixture_app'); self.assertNotIn('PROD_PASSWORD',env)
        self.assertNotIn('HTTPS_PROXY',env); self.assertEqual(env['WEKNORA_MODEL_MAX_CONCURRENCY'],'1')

    def test_models_real_routes_password_and_scope(self):
        calls=[]
        def request(method,path,body,token=None,*args,**kwargs):
            calls.append((method,path,body))
            if path.endswith('/login'): return {'success':True,'token':'testtoken'}
            return {'success':True,'data':{'id':'test-id'}}
        with patch.object(r,'save'),patch.object(r,'secret',return_value='a'*64),patch.object(r,'request',side_effect=request):
            r.setup_models({'glm_key':'glmtestkey','relay_key':'relaytestkey'})
        self.assertEqual(len(calls),5)
        self.assertEqual(len(calls[0][2]['password']),32)
        self.assertEqual(calls[2][2]['parameters']['extra_config'],{'wire_profile':'enterprise_glm_single_image_sse_v1'})
        self.assertEqual(calls[3][2]['parameters']['embedding_parameters']['dimension'],1024)
        self.assertEqual(calls[3][2]['parameters']['base_url'],'http://gateway:8001/api/weknora/internal-models/v1')
        self.assertFalse(calls[4][2]['indexing_strategy']['wiki_enabled'])
        self.assertEqual(calls[4][2]['summary_model_id'],'')

    def test_safe_diagnostics_do_not_contain_log_text(self):
        raw=b'password=private token=abc\n[ImageMultimodal] Processing image: x\n[ImageMultimodal] Created image_ocr chunk x\n'
        d=r.diagnostics(raw); self.assertEqual(d['multimodal_started'],1); self.assertEqual(d['ocr_chunk_created'],1)
        self.assertNotIn('private',json.dumps(d)); self.assertTrue(all(isinstance(x,int) for x in d.values()))

    def test_fail_fast_on_native_startup_exit(self):
        with patch.object(a,'remote',return_value=(0,{'STOPPED-app.json':{'exit_code':1}})):
            with self.assertRaisesRegex(a.native.AcceptanceFailure,'exited_before_health'): a.healthy(NS,'app')

    def test_non_superuser_grants_and_no_production_dsn(self):
        replies=['',json.dumps({'superuser':False,'createdb':False,'createrole':False,'connection_limit':24})]
        with patch.object(a,'sql',side_effect=replies) as sql:
            a.grants(NS,'a'*64)
        statement=sql.call_args_list[0].args[1]
        self.assertIn('NOSUPERUSER',statement); self.assertIn('CONNECTION LIMIT 24',statement)
        self.assertNotIn('GRANT ALL',statement)
        with patch.object(a,'bind_pod'),patch.object(a.native,'call',return_value=(0,b'{}')) as call:
            a.sql(NS,'SELECT 1;')
        argv=call.call_args.args[0]
        self.assertIn('/usr/bin/env',argv); self.assertIn('-i',argv)
        self.assertEqual(argv[argv.index('-h')+1],'/var/run/postgresql')
        self.assertNotIn('production',json.dumps(argv))

    def test_sql_scope_rejects_uuid_injection(self):
        with self.assertRaises(a.native.AcceptanceFailure): a.index_receipt(NS,"';DROP DATABASE fixture;--",'b'*36)

    def test_python37_grammar(self):
        for name in ('accept_enrichment.py','enrichment_fixture_runtime.py'):
            ast.parse(Path(name).read_text(),feature_version=(3,7))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/root'); self.path=Path(self.temp.name)
        self.p=patch.object(a,'STATE',self.path); self.p.start()
    def tearDown(self): self.p.stop(); self.temp.cleanup()

    def test_once_only_records(self):
        a.record('ATTEMPT.json',{'attempts':1})
        with self.assertRaises(FileExistsError): a.record('ATTEMPT.json',{'attempts':2})
        self.assertEqual(json.loads(a.audit.read(self.path/'ATTEMPT.json')),{'attempts':1})

    def test_existing_attempt_never_restarted(self):
        with self.assertRaisesRegex(a.native.AcceptanceFailure,'previous_attempt'): a.run()

    def test_cleanup_uid_guard_and_no_force(self):
        a.record('NAMESPACE-ACK.json',{'name':NS,'uid':'ours'})
        with patch.object(a.native,'get',return_value={'metadata':{'uid':'foreign'}}),patch.object(a.native,'call') as call:
            with self.assertRaises(a.native.AcceptanceFailure): a.cleanup(NS)
        call.assert_not_called()
        with patch.object(a.native,'get',side_effect=[{'metadata':{'uid':'ours'}},None]),patch.object(a.native,'call',return_value=(0,b'')) as call:
            self.assertEqual(a.cleanup(NS),'NAMESPACE_ABSENT_VERIFIED')
        args,data=call.call_args.args
        self.assertEqual(json.loads(data)['preconditions'],{'uid':'ours'}); self.assertNotIn('--force',args)
        self.assertTrue((self.path/'CLEANUP-VERIFIED.json').exists())

    def test_cleanup_absent_is_readonly_cluster(self):
        a.record('NAMESPACE-ACK.json',{'name':NS,'uid':'ours'})
        with patch.object(a.native,'get',return_value=None),patch.object(a.native,'call') as call:
            a.cleanup(NS)
        call.assert_not_called()

    def test_remote_uses_exact_identity_and_preserves_safe_label(self):
        with patch.object(a,'bind_pod') as bind,patch.object(a.native,'call',return_value=(1,b'{"fixture_error":"local_api_http_403"}')):
            with self.assertRaisesRegex(a.native.AcceptanceFailure,'fixture_local_api_http_403'): a.remote(NS,'setup',{'secret':'do-not-print'})
        bind.assert_called_once_with(NS)

    def test_failure_capture_before_cleanup_and_safe(self):
        a.record('POD-ACK.json',{'uid':'ours'})
        with patch.object(a.native,'get',return_value={'metadata':{'uid':'ours'},'status':{}}),patch.object(a,'remote',return_value=(0,{})),patch.object(a.time,'sleep'),patch.object(a.native,'call',return_value=(0,b'private_password\ndatabase system is ready to accept connections')):
            a.capture_failure(NS)
        raw=a.audit.read(self.path/'FAILURE-DIAGNOSTICS.json'); self.assertNotIn(b'private_password',raw)
        self.assertTrue(json.loads(raw)['postgres_startup']['database_ready'])

    def test_runtime_upload_marker_blocks_duplicate_before_http(self):
        with patch.object(r,'STATE',self.path),patch.object(r,'request') as request:
            r.save('UPLOAD-REQUESTED.json',{'once':True})
            with self.assertRaises(FileExistsError): r.upload()
        request.assert_not_called()

    def test_runtime_search_marker_blocks_duplicate_before_http(self):
        with patch.object(r,'STATE',self.path),patch.object(r,'request') as request:
            r.save('SEARCH-REQUESTED.json',{'once':True})
            with self.assertRaises(FileExistsError): r.search()
        request.assert_not_called()


class OrchestratorTests(unittest.TestCase):
    def exercise(self,fail_index=False):
        with tempfile.TemporaryDirectory(dir='/root') as folder,ExitStack() as stack:
            state=Path(folder)/'attempt'; stack.enter_context(patch.object(a,'STATE',state))
            stack.enter_context(patch.object(a,'RUNTIME_SOURCE','source')); stack.enter_context(patch.object(a,'SAMPLE_B64','cG5n'))
            p=SimpleNamespace(IMAGE='candidate',CONFIG='sha256:app',MANIFEST='sha256:manifest')
            guard=SimpleNamespace(live=Mock()); gw={'service_ip':'10.96.1.2','pods':[{'ip':'10.244.1.4','name':'gateway'}]}
            stack.enter_context(patch.object(a.native,'initialize',return_value=(p,object())))
            stack.enter_context(patch.object(a.native,'preflight',return_value=(guard,{},b'ca',{},['10.77.1.2'],[])))
            stores=SimpleNamespace(ITEMS=[{'role':'postgres','image':'pg'},{'role':'redis','image':'redis'}],image_check=Mock())
            stack.enter_context(patch.object(a.frozen,'load',return_value=stores))
            stack.enter_context(patch.object(a,'capacity',return_value=({},[])))
            stack.enter_context(patch.object(a,'gateway_baseline',return_value=gw))
            stack.enter_context(patch.object(a,'read_json',side_effect=lambda path,sha: (
                {'classification':{'byte_differences_explained':True}} if path==a.BYTE_REPORT else
                {'native_glm_acceptance':'PASS','enrichment_store_preload_recovery':'PASS'})))
            stack.enter_context(patch.object(a.getpass,'getpass',return_value='glmfixturekey'))
            calls=[]; objects={}; child=indexed()
            def call(args,data=None,**kwargs):
                calls.append((args,data))
                if args[0]=='create':
                    obj=json.loads(data); obj['metadata']['uid']='ours'
                    objects[obj['kind']]=obj
                    return 0,json.dumps(obj).encode()
                if 'WEKNORA_INTERNAL_MODEL_RELAY_KEY' in args[-1]: return 0,b'testrelaykey'
                return 0,b''
            stack.enter_context(patch.object(a.native,'call',side_effect=call))
            def get(kind,name,ns=None,**kwargs):
                if kind=='pod':
                    pod=copy.deepcopy(objects['Pod']); pod['status']={'phase':'Running','containerStatuses':[
                        {'name':'app','state':{'running':{}},'restartCount':0,'imageID':p.CONFIG},
                        {'name':'postgres','state':{'running':{}},'restartCount':0,'imageID':'pg'},
                        {'name':'redis','state':{'running':{}},'restartCount':0,'imageID':'redis'}]}
                    # Real Kubernetes running states include startedAt.
                    for c in pod['status']['containerStatuses']: c['state']['running']['startedAt']='now'
                    return pod
                return None
            stack.enter_context(patch.object(a.native,'get',side_effect=get))
            modes=[]
            def remote(ns,mode,data=None,**kwargs):
                modes.append(mode)
                if mode=='status': return 0,{'READY.json':{'runtime_files':'PASS'},'STOPPED-bootstrap.json':{'exit_code':0},
                    'STOPPED-app.json':{'logs_truncated':False,'classes':dict(multimodal_enqueued=1,multimodal_started=1,
                        ocr_chunk_created=1,caption_chunk_created=1,panic=0)}}
                if mode=='upload': return 0,{'knowledge_id':'k','kb_id':'kb'}
                if mode=='search': return 0,{'matches':[{'knowledge_id':'k','id':'c1'}]}
                return 0,{}
            stack.enter_context(patch.object(a,'remote',side_effect=remote))
            stack.enter_context(patch.object(a,'healthy',return_value=True))
            stack.enter_context(patch.object(a,'schema_receipt',return_value={'version':90,'dirty':False,
                'knowledge_rows':0,'model_rows':0,'extensions':['vector','pg_search','pg_trgm','uuid-ossp']}))
            stack.enter_context(patch.object(a,'grants',return_value={'superuser':False}))
            stack.enter_context(patch.object(a,'index_receipt',return_value=child))
            original_wait=a.wait_for
            def wait(predicate,seconds,label):
                if fail_index and label=='REAL_INDEX_ACCEPTANCE': raise a.native.AcceptanceFailure('test_index_deadline')
                return original_wait(predicate,seconds,label)
            stack.enter_context(patch.object(a,'wait_for',side_effect=wait))
            capture=stack.enter_context(patch.object(a,'capture_failure'))
            clean=stack.enter_context(patch.object(a,'cleanup',return_value='NAMESPACE_ABSENT_VERIFIED'))
            stack.enter_context(patch.object(a,'emit'))
            rc=a.run(); result=json.loads(a.audit.read(state/'RESULT.json'))
            self.assertFalse(result['ready_for_rollout']); self.assertEqual(result['production_writes'],0)
            self.assertEqual(modes.count('upload'),1); self.assertEqual(modes.count('setup'),1)
            self.assertEqual(modes.count('search'),0 if fail_index else 1)
            self.assertEqual(capture.call_count,1 if fail_index else 0); clean.assert_called_once()
            for path in state.iterdir():
                self.assertNotIn(b'glmfixturekey',path.read_bytes()); self.assertNotIn(b'testrelaykey',path.read_bytes())
            self.assertEqual(result['native_enrichment_acceptance'],'UNCONFIRMED' if fail_index else 'PASS')
            self.assertEqual(rc,1 if fail_index else 0)

    def test_complete_simulated_control_flow_no_server_requests(self): self.exercise()
    def test_failure_no_resubmit_and_cleanup_preserves_failure(self): self.exercise(fail_index=True)

if __name__=='__main__': unittest.main()
