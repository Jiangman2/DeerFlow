"""Synthetic Round3 read-only preflight checks. No cluster/network access."""
import ast, copy, importlib.util, json, os, pathlib, tempfile, time, unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[2]
P=ROOT/'deploy/enterprise/inspect-portal-research-fund-skill-20261105-r3.py'
spec=importlib.util.spec_from_file_location('finance_inspect',P); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def deployment(name,replicas=1):
    env=[{'name':'SKILLS_PVC_NAME','value':m.EXPECTED_PVC['name']},{'name':'SANDBOX_IMAGE','value':'local/sandbox@sha256:'+'a'*64}]
    return {'metadata':{'name':name,'namespace':'deer-flow','uid':name+'uid','generation':{'gateway':407,'ppt-master-runner':58,'provisioner':85,'frontend':241}[name]},
            'spec':{'replicas':replicas,'selector':{'matchLabels':{'app':name}},'template':{'metadata':{'labels':{'app':name}},'spec':{
                'containers':[{'name':name,'image':'local/'+name+':v1','env':env,'volumeMounts':[{'name':'skills','mountPath':'/app/skills','readOnly':True}]}],
                'volumes':[{'name':'skills','persistentVolumeClaim':{'claimName':m.EXPECTED_PVC['name']}}]}}},
            'status':{'observedGeneration':{'gateway':407,'ppt-master-runner':58,'provisioner':85,'frontend':241}[name],'replicas':replicas,'updatedReplicas':replicas,'readyReplicas':replicas,'availableReplicas':replicas}}

def pod(name='gateway-0',node='node-0',container='gateway',owner='rsuid'):
    return {'metadata':{'name':name,'namespace':'deer-flow','labels':{'app':container},'uid':name+'uid','ownerReferences':[{'kind':'ReplicaSet','name':container+'-rs','uid':owner,'controller':True}]},
            'spec':{'nodeName':node,'containers':[{'name':container,'image':'local/'+container+':v1','volumeMounts':[{'name':'skills','mountPath':'/app/skills','readOnly':True}]}],
                    'volumes':[{'name':'skills','persistentVolumeClaim':{'claimName':m.EXPECTED_PVC['name']}}]},
            'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}],'containerStatuses':[{
                'name':container,'ready':True,'restartCount':0,'containerID':'containerd://'+name,'imageID':'sha256:'+'b'*64,'state':{'running':{'startedAt':'2026-11-05T00:00:00Z'}}}]}}

class Fixture:
    def __init__(self):
        self.ds={n:deployment(n,70 if n=='gateway' else 1) for n in ('gateway','ppt-master-runner','provisioner','frontend')}
        self.sets=[{'metadata':{'uid':'rsuid','name':'gateway-rs','ownerReferences':[{'kind':'Deployment','uid':'gatewayuid','controller':True}]},'spec':{}}]
        self.sets[0]['spec']['template']=copy.deepcopy(self.ds['gateway']['spec']['template'])
        self.pods=[pod('gateway-'+str(i),'node-'+str(i%5)) for i in range(70)]
        self.provisioner_pod=pod('provisioner-0','node-0','provisioner','provisioner-rsuid')
        self.provisioner_pod['spec']['containers'][0]['env']=copy.deepcopy(self.ds['provisioner']['spec']['template']['spec']['containers'][0]['env'])
        self.provisioner_sets=[{'metadata':{'uid':'provisioner-rsuid','name':'provisioner-rs','ownerReferences':[{'kind':'Deployment','uid':'provisioneruid','controller':True}]},'spec':{}}]

        self.provisioner_sets[0]['spec']['template']=copy.deepcopy(self.ds['provisioner']['spec']['template'])
        self.extra_provisioner=[]
        self.sandbox=pod('sandbox-synthetic','node-0','sandbox')
        self.sandbox['spec']['containers'][0].update(image='local/sandbox@sha256:'+'a'*64,volumeMounts=[{'name':'skills','mountPath':'/mnt/skills/public','subPath':'public','readOnly':True}])
        self.pvc={'metadata':{'name':m.EXPECTED_PVC['name'],'uid':m.EXPECTED_PVC['uid']},'spec':{'volumeName':m.EXPECTED_PVC['volume'],'accessModes':['ReadWriteMany']},'status':{'phase':'Bound'}}
        self.pv={'metadata':{'name':m.EXPECTED_PVC['volume'],'uid':m.EXPECTED_PVC['pv_uid']},'spec':{'claimRef':{'uid':m.EXPECTED_PVC['uid']},'nfs':{'server':'synthetic-only','path':'/synthetic'}}}
        self.ob={'public':dict(copy.deepcopy(m.EXPECTED_SKILL),present=True),'custom':{'present':False},'runtime_sources':copy.deepcopy(m.EXPECTED_RUNTIME),'skills_configuration':{'path':'/app/skills','use':'local','container_path':'/mnt/skills'}}
        self.calls=[]; self.change_pod=False; self.change_volume=False; self.pv_reads=0; self.source_reads=0
    def get(self,kind,name=None,selector=None):
        self.calls.append((kind,name,selector))
        if kind=='deployment': return copy.deepcopy(self.ds[name])
        if kind=='replicaset': return copy.deepcopy(next(r for r in self.sets+self.provisioner_sets if r['metadata']['name']==name))
        if kind=='replicasets': return copy.deepcopy({'items':self.provisioner_sets if selector=='app=provisioner' else self.sets})
        if kind=='pods': return copy.deepcopy({'items':[self.sandbox] if selector=='app=deer-flow-sandbox' else [self.provisioner_pod]+self.extra_provisioner if selector=='app=provisioner' else self.pods})
        if kind=='pod':
            p=copy.deepcopy(next(p for p in self.pods+[self.sandbox,self.provisioner_pod] if p['metadata']['name']==name))
            if self.change_pod: p['status']['containerStatuses'][0]['restartCount']=1
            return p
        if kind=='pvc': return copy.deepcopy(self.pvc)
        if kind=='pv':
            self.pv_reads+=1; p=copy.deepcopy(self.pv)
            if self.change_volume and self.pv_reads>1: p['spec']['nfs']['path']='/other'
            return p
        raise AssertionError(kind)
    def observe(self,p,c,mode):
        self.source_reads+=1
        if mode=='gateway': return copy.deepcopy(self.ob)
        return {'present':True,'files':[r for r in m.EXPECTED_SKILL['files'] if r['path'] in ('SKILL.md','scripts/report_cli.py','scripts/group_template.py','scripts/standard_input.py','scripts/text_color.py')]}
    def inspect(self):
        m.STARTED=time.monotonic();m.COUNTER=0
        with patch.object(m.socket,'gethostname',return_value='datax-01'),patch.object(m.os,'geteuid',return_value=0,create=True),patch.object(m,'get',side_effect=self.get),patch.object(m,'observe',side_effect=self.observe):
            return m.inspect()

class Checks(unittest.TestCase):
    def test_old_failed_succeeded_pending_and_terminating_are_not_current(self):
        f=Fixture()
        for i,phase in enumerate(('Failed','Succeeded','Pending','Unknown')):
            old=copy.deepcopy(f.provisioner_pod);old['metadata']['name']='old-'+str(i);old['metadata']['uid']='old-uid-'+str(i);old['status']['phase']=phase;f.extra_provisioner.append(old)
        dying=copy.deepcopy(f.provisioner_pod);dying['metadata'].update(name='terminating',uid='dying',deletionTimestamp='2026-11-05T00:00:00Z');f.extra_provisioner.append(dying)
        r=f.inspect();self.assertEqual(r['exitcode'],0,r)
        diag=r['pod_selection_diagnostics']['provisioner'];self.assertEqual(diag['candidate_count'],6);self.assertEqual(diag['current_owned_ready_count'],1)
        self.assertEqual(diag['selection_reason_counts']['not_running_ready_or_identity_missing'],5)
    def test_r1_reproduced_for_retained_terminal_pod(self):
        f=Fixture();old=copy.deepcopy(f.provisioner_pod);old['status']['phase']='Failed'
        r1_owned=[p for p in (f.provisioner_pod,old) if m.controlled_by(p,'ReplicaSet','provisioner-rsuid')]
        self.assertFalse(len(r1_owned)==1 and all(m.ready(p,'provisioner') for p in r1_owned))
    def test_current_rs_selected_by_owner_name_not_rs_metadata_labels(self):
        f=Fixture();r=f.inspect();self.assertEqual(r['exitcode'],0,r)
        self.assertIn(('replicaset','provisioner-rs',None),f.calls)
        self.assertFalse(any(k=='replicasets' for k,n,s in f.calls))
    def test_current_rs_template_required(self):
        f=Fixture();f.provisioner_sets[0]['spec']['template']['spec']['containers'][0]['env'][0]['value']='old'
        r=f.inspect();self.assertEqual(r['error_code'],'CURRENT_READY_POD_COUNT_provisioner')
        self.assertEqual(r['pod_selection_diagnostics']['provisioner']['candidates'][0]['selection_reason'],'not_current_deployment_template')
    def test_missing_container_id_has_actionable_diagnostic(self):
        f=Fixture();del f.provisioner_pod['status']['containerStatuses'][0]['containerID']
        r=f.inspect();self.assertEqual(r['error_code'],'CURRENT_READY_POD_COUNT_provisioner');self.assertFalse(r['pod_selection_diagnostics']['provisioner']['candidates'][0]['container_id_present'])
    def test_multiple_current_provisioner_pods_refused(self):
        f=Fixture();p=copy.deepcopy(f.provisioner_pod);p['metadata'].update(name='second',uid='second');f.extra_provisioner.append(p)
        r=f.inspect();self.assertEqual(r['error_code'],'CURRENT_READY_POD_COUNT_provisioner');self.assertEqual(r['pod_selection_diagnostics']['provisioner']['current_owned_ready_count'],2)
    def test_terminal_gateway_pod_not_counted(self):
        f=Fixture();p=copy.deepcopy(f.pods[0]);p['metadata'].update(name='gateway-old',uid='old');p['status']['phase']='Failed';f.pods.append(p)
        r=f.inspect();self.assertEqual(r['exitcode'],0,r);self.assertEqual(r['gateway_scope']['replicas'],70)
    def test_seventy_pods_select_five_nodes(self):
        f=Fixture();r=f.inspect();self.assertEqual(r['exitcode'],0,r);self.assertEqual(len(r['samples']),5);self.assertFalse(r['ready_for_update']);self.assertTrue(r['stable_readback'])
    def test_does_not_pin_old_generation_or_pod(self):
        f=Fixture();f.ds['gateway']['metadata']['generation']=409;f.ds['gateway']['status']['observedGeneration']=409
        self.assertEqual(f.inspect()['exitcode'],0)
    def test_in_progress_rollout_stops_before_exec(self):
        f=Fixture();f.ds['gateway']['status']['updatedReplicas']=69;r=f.inspect();self.assertEqual(r['error_code'],'GATEWAY_ROLLOUT_NOT_CONVERGED');self.assertEqual(f.source_reads,0)
    def test_changed_skill_fails_with_expected_and_observed_hash(self):
        f=Fixture();f.ob['public']['files'][0]['sha256']='0'*64;f.ob['public']['tree_sha256']=m.digest(f.ob['public']['files'])
        r=f.inspect();self.assertEqual(r['error_code'],'RELEASED_SKILL_CHANGED');self.assertFalse(r['samples'][0]['skill']['matches_expected'])
    def test_runtime_change_collected_but_needs_review(self):
        f=Fixture();f.ob['runtime_sources']['activation']['sha256']='0'*64;r=f.inspect()
        self.assertEqual(r['exitcode'],2,r);self.assertTrue(r['stable_readback']);self.assertIn('RUNTIME_SKILL_LOADING_SOURCES_CHANGED',r['review_reasons'])
    def test_shadow_skill_refused(self):
        f=Fixture();f.ob['custom']['present']=True;self.assertEqual(f.inspect()['error_code'],'CUSTOM_SKILL_SHADOW_REQUIRES_REVIEW')
    def test_restart_during_read_refused(self):
        f=Fixture();f.change_pod=True;self.assertEqual(f.inspect()['error_code'],'SAMPLED_GATEWAY_CHANGED')
    def test_pv_source_change_detected(self):
        f=Fixture();f.change_volume=True;self.assertEqual(f.inspect()['error_code'],'SKILLS_PV_CHANGED_DURING_READ')
    def test_provisioner_pod_configuration_checked(self):
        f=Fixture();f.provisioner_pod['spec']['containers'][0]['env'][0]['value']='other'
        self.assertEqual(f.inspect()['error_code'],'PROVISIONER_POD_CONFIGURATION_CHANGED')
    def test_provisioner_wrong_owner_refused(self):
        f=Fixture();f.provisioner_pod['metadata']['ownerReferences'][0]['uid']='other'
        self.assertEqual(f.inspect()['error_code'],'CURRENT_READY_POD_COUNT_provisioner')
    def test_orphan_replacement_not_accepted(self):
        f=Fixture();f.pods[-1]['metadata']['ownerReferences'][0]['uid']='other';self.assertEqual(f.inspect()['error_code'],'CURRENT_READY_POD_COUNT_gateway')
    def test_pod_ready_condition_required(self):
        f=Fixture();f.pods[0]['status']['conditions'][0]['status']='False';self.assertEqual(f.inspect()['error_code'],'CURRENT_READY_POD_COUNT_gateway')
    def test_mounted_claim_changed_refused(self):
        f=Fixture();f.pods[0]['spec']['volumes'][0]['persistentVolumeClaim']['claimName']='other';self.assertEqual(f.inspect()['error_code'],'GATEWAY_POD_MOUNT_CHANGED')
    def test_unknown_filename_not_exported(self):
        obs=dict(copy.deepcopy(m.EXPECTED_SKILL),present=True);obs['files'].append({'path':'private-customer-secret.txt','bytes':5,'sha256':'a'*64})
        self.assertNotIn('private-customer-secret',json.dumps(m.compare_skill(obs)))
    def test_missing_sandbox_is_review_not_fabricated_success(self):
        f=Fixture();f.sandbox['status']['phase']='Pending';r=f.inspect();self.assertEqual(r['exitcode'],2);self.assertIn('NO_READY_SANDBOX_SAMPLE',r['review_reasons'])
    def test_bound_manifest_matches_released_tree(self):
        self.assertEqual(len(m.EXPECTED_SKILL['files']),30);self.assertEqual(m.digest(m.EXPECTED_SKILL['files']),'6c393b11a0023a50de29a1958088322324f759e85c6f3080816aed023c61e951')
    def test_wrong_host_fails_before_reads(self):
        m.STARTED=time.monotonic()
        with patch.object(m.socket,'gethostname',return_value='not-datax'),patch.object(m,'get') as g:
            self.assertEqual(m.inspect()['error_code'],'WRONG_HOST');g.assert_not_called()
    def test_only_fixed_resource_reads(self):
        with self.assertRaises(m.Refused):m.get('secret','anything')
        with self.assertRaises(m.Refused):m.get('pods',selector='app=gateway;echo')
    def test_python37_host_and_child_syntax(self):
        ast.parse(P.read_text(encoding='utf8'),feature_version=(3,7));ast.parse(m.CHILD,feature_version=(3,7))
    def test_no_app_import_network_or_database_in_child(self):
        t=ast.parse(m.CHILD)
        modules={a.name for n in ast.walk(t) if isinstance(n,ast.Import) for a in n.names}
        modules|={n.module for n in ast.walk(t) if isinstance(n,ast.ImportFrom)}
        self.assertEqual(modules-{'ast','hashlib','json','os','pathlib','stat','sys','time','yaml'},set())
    def test_real_child_tree_file_change_detected(self):
        ns={};exec(m.CHILD.split('\ntry:\n')[0],ns)
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td);(p/'SKILL.md').write_text('---\nname: synthetic\n---\nbody',encoding='utf8')
            first=ns['tree'](p);(p/'SKILL.md').write_text('---\nname: synthetic\n---\nchanged',encoding='utf8');second=ns['tree'](p)
            self.assertNotEqual(first['tree_sha256'],second['tree_sha256'])
    @unittest.skipIf(os.name=='nt','Linux symlink policy check')
    def test_child_rejects_symlink(self):
        ns={};exec(m.CHILD.split('\ntry:\n')[0],ns)
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td);(p/'SKILL.md').symlink_to('/etc/hosts')
            with self.assertRaisesRegex(RuntimeError,'SYMLINK_REFUSED'):ns['tree'](p)


    def test_full_embedded_manifest_is_the_deployed_nov5_inventory(self):
        original=json.loads((ROOT/'release/research-fund-feedback-20261105/candidate-manifest.json').read_bytes())
        self.assertEqual(m.EXPECTED_SKILL,original)
        self.assertEqual(original['bytes'],788816)
        self.assertEqual(len(original['files']),30)
    def test_nov4_tree_is_not_accepted_as_current(self):
        old=json.loads((ROOT/'release/research-fund-feedback-20261104/candidate-manifest.json').read_bytes())
        self.assertFalse(m.compare_skill(dict(old,present=True))['matches_expected'])
    def test_frontend241_is_observed_without_changing_skill_workload_scope(self):
        f=Fixture();r=f.inspect();self.assertEqual(r['exitcode'],0,r)
        self.assertEqual(r['tool_revision'],'20261105-r3')
        self.assertEqual(set(r['deployments']),{'gateway','ppt-master-runner','provisioner'})
        self.assertEqual(r['frontend_observation']['deployment']['generation'],241)
        self.assertTrue(r['frontend_observation']['stable_readback'])
        self.assertFalse(r['frontend_observation']['included_in_skill_update'])
    def test_fresh_later_frontend_generation_is_not_pinned_to241(self):
        f=Fixture();f.ds['frontend']['metadata']['generation']=242;f.ds['frontend']['status']['observedGeneration']=242
        self.assertEqual(f.inspect()['exitcode'],0)
    def test_frontend_change_during_read_is_visible(self):
        f=Fixture();old=f.get;reads=[]
        def get(kind,name=None,selector=None):
            value=old(kind,name,selector)
            if kind=='deployment' and name=='frontend':
                reads.append(name)
                if len(reads)>1:value['spec']['template']['spec']['containers'][0]['image']='local/frontend:changed'
            return value
        f.get=get
        self.assertEqual(f.inspect()['error_code'],'FRONTEND_CHANGED_DURING_READ')
    def test_frontend_rollout_is_not_misreported_as_stable(self):
        f=Fixture();f.ds['frontend']['status']['updatedReplicas']=0
        r=f.inspect();self.assertEqual(r['error_code'],'FRONTEND_ROLLOUT_NOT_CONVERGED');self.assertEqual(f.source_reads,0)
    def test_current_workload_identity_is_rechecked(self):
        f=Fixture();old=f.get;reads=[]
        def get(kind,name=None,selector=None):
            value=old(kind,name,selector)
            if kind=='deployment' and name=='ppt-master-runner':
                reads.append(name)
                if len(reads)>1:value['metadata']['uid']='replacement'
            return value
        f.get=get
        self.assertEqual(f.inspect()['error_code'],'DEPLOYMENT_CHANGED_ppt-master-runner')
    def test_inspection_never_claims_mutation_or_business_acceptance(self):
        r=Fixture().inspect();self.assertEqual(r['exitcode'],0,r)
        for key in ('files_modified','workload_modified','database_access','application_started','skill_update_requested','ready_for_update','historical_inventories_modified'):
            self.assertIs(r[key],False,key)
        for key in ('secret_api_reads','provider_requests','business_requests','pods_created'):
            self.assertEqual(r[key],0,key)
    def test_mutating_kubernetes_commands_stop_before_process_launch(self):
        for verb in ('create','apply','patch','delete','replace','scale','rollout'):
            m.STARTED=time.monotonic();m.COUNTER=0
            with self.subTest(verb=verb),patch.object(m.subprocess,'Popen') as launch:
                with self.assertRaisesRegex(m.Refused,'READONLY_COMMAND_REQUIRED'):m.command([verb,'pod','synthetic'])
                launch.assert_not_called()

if __name__=='__main__': unittest.main()
