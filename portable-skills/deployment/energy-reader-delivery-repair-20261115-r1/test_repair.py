"""Focused serving-target, CAS, isolation and link-only delivery regressions."""
import ast
import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent

def load(name):
    spec=importlib.util.spec_from_file_location('repair_tests_'+name,HERE/(name+'.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module

sealer=load('seal_repair')
RAW,ASSETS,NEXT=sealer.build()
operator=load('repair_operator');operator.BINDING=json.loads((HERE/'FIELD-BINDING.json').read_bytes())
reader=load('nginx_reader')
CONF='''events {}\nhttp { upstream gateway_upstream { server 127.0.0.1:18081; } server { listen 18082;
  location ~ "\\.html$" { return 404; }
  location /api/ { proxy_set_header X-Forwarded-Proto $scheme; proxy_pass http://gateway_upstream; }
  location / { return 404; }
} }\n'''

class RepairTests(unittest.TestCase):
    def operation(self):return operator.Repair(ASSETS)

    def test_sealed_assets_round_trip_and_exact_two_gateway_deltas(self):
        scope={'__name__':'sealed_operator_test'};exec(compile(RAW,'<sealed>','exec'),scope)
        self.assertEqual(scope['load_assets'](),ASSETS)
        parents=operator.BINDING['gateway_parent_sources']
        self.assertEqual([p for p in parents if parents[p]!=NEXT[p]],['/app/backend/app/scheduler/work_notification_delivery.py','/app/backend/app/scheduler/work_notifications.py'])

    def test_modified_sealed_asset_refused(self):
        scope={'__name__':'sealed_operator_test'};exec(compile(RAW,'<sealed>','exec'),scope)
        scope['ASSETS']['reader']['base64']=base64.b64encode(b'CHANGED').decode()
        with self.assertRaisesRegex(scope['Refused'],'SEALED_ASSET_CHANGED'):scope['load_assets']()

    def test_python37_host_and_sealed_helpers_parse(self):
        for raw in [RAW]+[ASSETS[k] for k in ('reader','startup','gateway_child')]:ast.parse(raw.decode(),feature_version=(3,7))

    def test_reader_precedes_existing_html_regex_without_changing_other_bytes(self):
        candidate,_=reader.add_reader(CONF)
        start=candidate.index('  # BEGIN ENERGY DAILY');end=candidate.index('  location ~ "\\.html$"')
        self.assertLess(start,end);self.assertEqual(candidate[:start]+candidate[end:],CONF)

    def test_braced_proxy_and_proto_variables_are_supported(self):
        source=CONF.replace('http://gateway_upstream','http://${gateway_upstream}').replace('$scheme','${forwarded_proto}')
        facts=reader.inspect(source)
        self.assertEqual(facts['proxy_pass'],'http://${gateway_upstream}')
        self.assertEqual(facts['forwarded_proto'],'$forwarded_proto')
        self.assertTrue(reader.inspect(reader.add_reader(source)[0])['report_reader_route_present'])

    def test_runtime_upstream_override_preserves_original_template(self):
        source=CONF.replace('http://gateway_upstream','http://${gateway_upstream}')
        candidate,_=reader.add_reader(source,'http://gateway-upstream:8001','$forwarded_proto')
        self.assertIn('proxy_pass http://gateway-upstream:8001;',candidate)
        self.assertIn('proxy_pass http://${gateway_upstream};',candidate)

    def test_reader_credentials_arbitrary_uri_and_repeated_insert_rejected(self):
        for upstream in ['http://user:secret@upstream','http://upstream/path','https://outside.invalid']:
            with self.assertRaises(reader.Refused):reader.add_reader(CONF.replace('http://gateway_upstream',upstream))
        with self.assertRaisesRegex(reader.Refused,'ALREADY_OCCUPIED'):reader.add_reader(reader.add_reader(CONF)[0])

    def fixture(self):
        dep={'metadata':{'name':'gateway','uid':'dep-uid','generation':417},'spec':{
            'replicas':70,'strategy':{'rollingUpdate':{'maxSurge':1,'maxUnavailable':0},'type':'RollingUpdate'},
            'template':{'spec':{'containers':[{'name':'gateway','image':'fixed-image','volumeMounts':[{'name':'source','mountPath':'/fixed','readOnly':True}]}],
                                'volumes':[{'name':'source','configMap':{'name':operator.LINK_CM,'defaultMode':420}},{'name':'ca','secret':{'secretName':'not-read'}}]}}}}
        old={'metadata':{'name':operator.LINK_CM,'uid':'old-uid'},'immutable':True,'data':{'same':'untouched','changed':'old'},'binaryData':{'keep':'AA=='}}
        op=self.operation();role=op.map_role(dep,old,{'same':'untouched','changed':'new'},'gateway')
        return op,dep,old,role

    def test_only_existing_configmap_reference_changes(self):
        op,dep,old,role=self.fixture()
        expected=copy.deepcopy(dep['spec']);expected['template']['spec']['volumes'][0]['configMap']['name']=role['new_configmap']['metadata']['name']
        self.assertEqual(role['candidate_spec'],expected)
        self.assertEqual(role['before_spec'],dep['spec'])
        self.assertEqual(role['new_configmap']['binaryData'],old['binaryData'])
        self.assertTrue(role['new_configmap']['immutable'])

    def test_patch_uses_uid_and_whole_spec_cas_once(self):
        op,dep,old,role=self.fixture();cm=copy.deepcopy(role['new_configmap']);cm['metadata']['uid']='new-uid';calls=[]
        def get(kind,name):return dep if kind=='deployment' else old if name==old['metadata']['name'] else cm
        op.get=get
        def command(args,body):calls.append((args,json.loads(body)));return {'metadata':{'name':'gateway','uid':'dep-uid','generation':418},'spec':role['candidate_spec']}
        op.command=command
        with patch.object(operator,'private_write') as write:op.patch(role,{'name':cm['metadata']['name'],'uid':'new-uid'})
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1],[{'op':'test','path':'/metadata/uid','value':'dep-uid'},{'op':'test','path':'/spec','value':dep['spec']},
                                      {'op':'replace','path':'/spec/template/spec/volumes/0/configMap/name','value':cm['metadata']['name']}])
        self.assertEqual(write.call_count,2)

    def test_concurrent_spec_change_refuses_before_any_write(self):
        op,dep,old,role=self.fixture();changed=copy.deepcopy(dep);changed['spec']['replicas']=71
        op.get=lambda *args:changed
        with patch.object(operator,'private_write') as write,patch.object(op,'command') as command:
            with self.assertRaisesRegex(operator.Refused,'BASELINE_CHANGED_BEFORE_PATCH'):op.patch(role,{'name':'new','uid':'new'})
        write.assert_not_called();command.assert_not_called()

    def test_parent_configmap_change_refuses_before_any_write(self):
        op,dep,old,role=self.fixture();old['data']['same']='changed by colleague'
        op.get=lambda kind,name:dep if kind=='deployment' else old
        with patch.object(operator,'private_write') as write,patch.object(op,'command') as command:
            with self.assertRaisesRegex(operator.Refused,'PARENT_CONFIGMAP_CHANGED'):op.patch(role,{'name':'new','uid':'new'})
        write.assert_not_called();command.assert_not_called()

    def test_wrong_serving_deployment_refuses_before_claim_or_mutation(self):
        op=self.operation();op.get=lambda kind,name:{'metadata':{'uid':'wrong','generation':1},'spec':{}}
        with patch.object(operator,'CONTROL',Path('/does-not-exist-energy-test')),patch.object(operator,'private_write') as write:
            with self.assertRaisesRegex(operator.Refused,'SERVING_NGINX_BASELINE_CHANGED'):op.activate()
        write.assert_not_called();self.assertFalse(op.claimed)

    def test_service_selector_change_never_accepted_as_ready(self):
        op=self.operation();bound=operator.BINDING['services'][0]
        wrong={'metadata':{'uid':bound['uid']},'spec':{'selector':{'app':'unused-nginx'}}}
        op.get=lambda *args:wrong
        with self.assertRaisesRegex(operator.Refused,'PRODUCTION_SERVICE_CHANGED'):op.services()

    def test_verify_never_calls_patch_create_or_wait(self):
        op,dep,old,role=self.fixture()
        plan={'schema':'energy-reader-work-repair-plan.v1','diagnostic_sha256':operator.BINDING['diagnostic_sha256'],'operation':'original-operation','roles':{'nginx':role,'gateway':role}}
        made={key:{'name':key+'-new','uid':key+'-uid'} for key in ('nginx','gateway')}
        with patch.object(operator,'private_read',side_effect=[plan,made]),patch.object(op,'role_state',return_value=(False,{'phase':'ROLLOUT_IN_PROGRESS'})),\
             patch.object(op,'patch') as mutation,patch.object(op,'create_maps') as create,patch.object(op,'wait') as wait:
            op.verify()
        mutation.assert_not_called();create.assert_not_called();wait.assert_not_called()
        self.assertEqual(op.operation,'original-operation');self.assertEqual(op.out['gateway_patch_requests'],0)

    def test_once_marker_prevents_replay(self):
        op=self.operation()
        with tempfile.TemporaryDirectory() as directory,patch.object(op,'preflight') as preflight:
            with patch.object(operator,'CONTROL',Path(directory)):
                with self.assertRaisesRegex(operator.Refused,'ALREADY_CLAIMED'):op.activate()
        preflight.assert_not_called()

    def test_full_preflight_reads_serving_api_template_not_stale_mount(self):
        op,gw,old,unused=self.fixture()
        def healthy(dep):
            dep['status']={k:dep['spec']['replicas'] for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas')}
            dep['status']['observedGeneration']=dep['metadata']['generation']
        healthy(gw);gw['spec']['template']['metadata']={'labels':{'app':'gateway'}}
        ng=copy.deepcopy(gw);ng['metadata']={'name':'nginx-security-heai-20261012-r1','uid':'ng-uid','generation':2};ng['spec']['replicas']=1
        ng['spec']['template']['spec']['volumes'][0]['configMap']['name']='actual-template';healthy(ng)
        source=CONF;cm={'metadata':{'name':'actual-template','uid':'template-uid'},'data':{'nginx.conf':source,'kept':'kept'}}
        mounts=[m for m in operator.BINDING['gateway_mount_metadata'] if m.get('source_name')==operator.LINK_CM]
        old['data']={m['subpath']:'source-fixture-'+str(index) for index,m in enumerate(mounts)}
        binding=copy.deepcopy(operator.BINDING)
        binding['gateway']=operator.identify(gw)
        binding['nginx'].update(deployment_uid='ng-uid',generation=2,spec_sha256=operator.identify(ng)['spec_sha256'],
                                configmaps=[{'name':'actual-template','uid':'template-uid','data_sha256':operator.sha(operator.canonical(cm['data'])),
                                             'files':[{'sha256':operator.sha(source.encode())}]}],runtime_config={'sha256':operator.sha(source.encode())})
        for m in mounts:
            raw=old['data'][m['subpath']].encode();binding['gateway_parent_sources'][m['mount_path']]={'bytes':len(raw),'sha256':operator.sha(raw)}
        np={'metadata':{'name':'actual-serving-pod','uid':'np'},'spec':{'containers':[{'name':'nginx','image':binding['nginx']['image']}]}}
        gp={'metadata':{'name':'gateway-fixture','uid':'gp'}}
        looked=[]
        def get(kind,key):
            looked.append((kind,key))
            return {('deployment',ng['metadata']['name']):ng,('deployment','gateway'):gw,('configmap','actual-template'):cm,
                    ('configmap',operator.LINK_CM):old,('service','gateway'):{'metadata':{'uid':'service-uid'},'spec':{'selector':{'app':'gateway'}}}}[(kind,key)]
        op.get=get;op.pods=lambda dep:[np] if dep is ng else [gp]*70
        op.owner=lambda *args:None;op.services=lambda ids=None:None;op.endpoints=lambda value:{'gp'}
        op.replay=lambda pod,container,raw:raw
        op.gateway_child=lambda *args:{'status':'GATEWAY_READER_NOTIFICATION_SOURCES_QUALIFIED'}
        reads=[]
        def command(args,*rest,**kwargs):
            reads.append(args)
            if args[-2:]==['cat','/tmp/nginx.conf']:return source.encode()
            self.assertIn(operator.SYNTAX,args);return b''
        op.command=command
        with patch.object(operator,'BINDING',binding):plan=op.preflight()
        self.assertEqual(plan['roles']['nginx']['deployment_name'],ng['metadata']['name'])
        self.assertEqual(plan['roles']['nginx']['old_configmap'],cm)
        self.assertNotIn(('deployment','nginx'),looked)
        self.assertTrue(all('/etc/nginx/nginx.conf.template' not in args for args in reads))
        self.assertEqual(op.out['gateway_patch_requests'],0);self.assertFalse(op.claimed)

    def test_gateway_audit_rejects_network_and_file_mutations(self):
        scope={'__name__':'gateway_isolation_test'};exec(compile(ASSETS['gateway_child'],'<child>','exec'),scope)
        for event,args in [('socket.__new__',()),('open',('some-file','w',0)),('subprocess.Popen',()),('sqlite3.connect',())]:
            with self.assertRaises(scope['Refused']):scope['audit'](event,args)
        scope['audit']('open',('some-file','r',0))

    def test_sealed_tests_run_twelve_cases_without_files_network_or_messages(self):
        scope={'__name__':'gateway_isolation_test'};exec(compile(ASSETS['gateway_child'],'<child>','exec'),scope)
        candidates={n:base64.b64decode(row['base64']).decode() for n,row in scope['CANDIDATES'].items()}
        isolated={'__name__':'isolated_tests','CANDIDATES':candidates}
        exec(compile(scope['TEST_SOURCE'],'<test>','exec'),isolated)
        suite=unittest.defaultTestLoader.loadTestsFromTestCase(isolated['NotificationLinkTests'])
        import io
        result=unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        self.assertEqual(result.testsRun,12);self.assertTrue(result.wasSuccessful());self.assertFalse(result.skipped)

if __name__=='__main__':unittest.main()
