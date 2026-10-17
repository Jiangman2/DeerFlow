"""Focused r3 link-guard orchestration fixtures. No cluster/business execution."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('controller_r3',HERE/'run_native.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
P=m.payload();OWNER='a'*32;NS='portal-ppt-'+OWNER
PIN=dict(manifest_digest='sha256:9b3a6d80d20b73585c6642213077236bb9948f35d0d5daa262e78d120422eb9e',
         config_digest='sha256:5b43bc9cd5f51029b87aaf0b212c064124e29fb814c5543b7de7d864812f2902')


class Clock:
    def __init__(self):self.value=1
    def now(self):return self.value
    def sleep(self,value):self.value+=value


def namespace():
    return dict(apiVersion='v1',kind='Namespace',metadata=dict(name=NS,uid=str(uuid.uuid4()),labels={m.OWNER_LABEL:OWNER}))


class ProofSession(m.Session):
    """Exercise actual controller proof orchestration and frozen packet verifier."""
    def __init__(self,directory):
        self.clock=Clock()
        super().__init__(self,OWNER,directory,P,now=self.clock.now,sleep=self.clock.sleep)
        self.namespace_uid=str(uuid.uuid4());self.objs={};self.events=[];self.guard_done=False
        self.denial=True;self.partial=False;self.bad_guard=False;self.heartbeats={'subject':0,'peer':0}
        self.guard.validate_guard_receipt=self.guard_receipt
    def guard_receipt(self,raw,owner,pod_uid,image_id):
        if self.bad_guard:raise m.Refused('guard_receipt_fixture_invalid')
        if (owner,pod_uid,image_id)!=(OWNER,self.objs['network-subject']['metadata']['uid'],PIN['config_digest']):
            raise m.Refused('guard_receipt_fixture_binding')
        return dict(status='PASS',owner=owner,pod_uid=pod_uid,mechanism=m.MECHANISM)
    def create(self,obj):
        actual=copy.deepcopy(obj);actual['metadata']['uid']=str(uuid.uuid4())
        if obj['kind']=='Pod':
            role=obj['metadata']['name'].split('-')[-1]
            actual['status']=dict(phase='Running',podIP='10.244.2.'+('11' if role=='subject' else '12'))
        row=dict(kind=obj['kind'],name=obj['metadata']['name'],uid=actual['metadata']['uid'],expected=copy.deepcopy(obj))
        self.ledger.append(row);self.objs[row['name']]=actual
        return row
    def read(self,row):return copy.deepcopy(self.objs[row['name']])
    def remove(self,row):
        self.events.append('delete:'+row['name']);del self.objs[row['name']]
        self.cleaned.append(dict(kind=row['kind'],name=row['name'],uid=row['uid']))
    def runtime(self,row,images,allow_terminal=False):
        role=row['name'].split('-')[-1]
        return self.read(row),{'proof':dict(pod_uid=row['uid'],restart_count=0,container_id='containerd://'+('b' if role=='subject' else 'c')*64,
            image_id=PIN['config_digest'],image_digest=PIN['config_digest'],node_name=m.NODE,node_uid=m.NODE_UID)}
    def wait_running(self,row,images,pending=120):return self.runtime(row,images)
    def get(self,resource,name=None,namespace=None):
        return dict(items=[copy.deepcopy(v) for v in self.objs.values() if v['kind']=='NetworkPolicy'])
    def deny_only(self):
        assert sorted(v['metadata']['name'] for v in self.objs.values() if v['kind']=='NetworkPolicy')==['namespace-deny-all']
    def exec(self,namespace,pod,container,argv,body=None,timeout=20):
        self.events.append('guard-exec')
        assert 'subject-temporary-allow-peer' not in self.objs
        assert any(e=='baseline-cross:peer' for e in self.events)
        assert pod=='network-subject' and container=='proof'
        self.guard_done=True
        return b'{}\n'
    def call(self,role,target,operation,binding):
        self.events.append(('guarded-' if self.guard_done else 'baseline-')+operation+':'+role)
        self.clock.sleep(.25)
        if self.partial and self.guard_done and operation=='cross' and role=='subject':
            raise m.Refused('fixture_exec_nonzero')
        challenge=uuid.uuid4().hex
        value=dict(schema='portal-ppt-owned-network-call.v1',source_role=role,target_role=target,operation=operation,
                   source_pod_uid=binding['pods'][role]['pod_uid'],target_pod_uid=binding['pods'][target]['pod_uid'],challenge=challenge)
        if operation=='cross' and self.guard_done and (self.denial is True or self.denial=='one-way' and role=='subject'):
            return dict(value,status='CONNECT_BLOCKED',connection_established=False,errno=101,failure_code='OSError')
        self.heartbeats[target]+=1
        response=dict(schema='portal-ppt-owned-listener.v1',challenge=challenge,owner=OWNER,role=target,
                      pod_uid=binding['pods'][target]['pod_uid'],pod_ip=binding['pods'][target]['pod_ip'],
                      instance=('d' if target=='subject' else 'e')*32,pid=10 if target=='subject' else 11,
                      started_ns=111,heartbeat=self.heartbeats[target])
        return dict(value,status='SUCCESS',connection_established=True,response=response)


class LinkControllerTests(unittest.TestCase):
    def test_fixed_r2_failure_and_new_once_boundary(self):
        evidence=json.loads((HERE/'PRIOR-RECEIPT-FIXTURE.json').read_bytes())
        old=m.read_pin
        calls=[]
        try:
            def read(path,size,sha):calls.append((path,size,sha));return evidence
            m.read_pin=read
            result=m.prior_failure()
            self.assertEqual(calls,[(m.PRIOR_ROOT+'/REPORT.json',2873,m.PRIOR_REPORT_SHA)])
            self.assertEqual(result['cause'],'CONTROLLED_PEER_REMAINED_REACHABLE_AFTER_POLICY_WITHDRAWAL')
            evidence['revision']=1
            with self.assertRaisesRegex(m.Refused,'prior_failure_not_reviewed_boundary'):m.prior_failure()
        finally:m.read_pin=old
        self.assertIn('-r3.',m.ONCE);self.assertIn('-r3.',m.LOCK)

    def test_only_proof_subject_gets_net_admin_frozen_peer_and_adapter_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            s=ProofSession(d);rendered=s.render_network()
            subject,peer=rendered['pods']
            for pod in (subject,peer):
                pod=s.bind_priority(pod);m.pod_safety(pod,s.priority_name,s.guard,OWNER)
            self.assertEqual(subject['spec']['containers'][0]['securityContext']['capabilities'],{'drop':['ALL'],'add':['NET_ADMIN']})
            self.assertEqual(peer['spec']['containers'][0]['securityContext']['capabilities'],{'drop':['ALL']})
            self.assertEqual(peer['spec']['containers'][0]['securityContext']['runAsUser'],65532)
            self.assertIn(P['network_probe.py'],subject['spec']['containers'][0]['args'])
            bad=s.bind_priority(copy.deepcopy(peer));bad['spec']['containers'][0]['securityContext']['capabilities']['add']=['NET_ADMIN']
            with self.assertRaisesRegex(m.Refused,'business_capabilities_must_be_zero'):m.pod_safety(bad,s.priority_name,s.guard,OWNER)

    def test_business_guard_first_in_all_stages_and_pg_sidecar_order(self):
        with tempfile.TemporaryDirectory() as d:
            s=ProofSession(d);hf=m.module(P['host_frontend_stage.py'],'hf_fixture');db=m.module(P['db_stage.py'],'db_fixture')
            pods=[next(o for o in stage['objects'] if o['kind']=='Pod') for stage in hf.render(OWNER,NS,P['host_runtime_inputs'])['stages']]
            profile=dict(schema='portal-native-pg-profile.v1',owner=OWNER,uid=999,gid=999,
                         server_binary_version='16.5',database_started=False,**db.PG_FILES)
            pods += [db.render_pg_probe(OWNER,NS,P['postgres_cache']),db.render_db(OWNER,NS,profile)]
            for original in pods:
                decorated=s.guard.decorate_business_pod(original,OWNER,NS)
                self.assertEqual(decorated['spec']['initContainers'][0]['name'],s.guard.GUARD_CONTAINER)
                self.assertNotIn('restartPolicy',decorated['spec']['initContainers'][0])
                m.pod_safety(s.bind_priority(decorated),s.priority_name,s.guard,OWNER)
                if original['metadata']['name']=='db-bootstrap':
                    self.assertEqual(decorated['spec']['initContainers'][1]['name'],'postgres-fixture')
                    self.assertEqual(decorated['spec']['initContainers'][1]['restartPolicy'],'Always')

    def test_typed_list_exact_shape_and_single_transport_start(self):
        ns=namespace();pod=dict(apiVersion='v1',kind='Pod',metadata=dict(name='network-subject',namespace=NS,uid=str(uuid.uuid4())))
        transport=object.__new__(m.Transport);calls=[]
        def run(args,**kwargs):calls.append(args);return m.encoded(dict(apiVersion='v1',kind='List',items=[pod,ns]))
        transport.run=run
        self.assertEqual(transport.owned_namespace_pod(NS,'network-subject'),(ns,pod))
        self.assertEqual(calls,[['get','namespace/'+NS,'pod/network-subject','-n',NS,'-o=json']])
        for items in ([ns],[ns,pod,pod],[ns,ns],[dict(ns,kind='Secret'),pod],[ns,dict(pod,metadata=dict(pod['metadata'],namespace='production'))]):
            transport.run=lambda *a,items=items,**kw:m.encoded(dict(apiVersion='v1',kind='List',items=items))
            with self.assertRaises(m.Refused):transport.owned_namespace_pod(NS,'network-subject')

    def test_combined_read_replacement_or_ip_drift_blocks_exec(self):
        for mutation in ('namespace_uid','pod_uid','pod_ip','spec','deleting'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
                s=m.Session(None,OWNER,d,P);ns=namespace();s.namespace_uid=ns['metadata']['uid']
                pod=s.bind_priority(s.render_network()['pods'][0]);pod['metadata']['uid']=str(uuid.uuid4())
                pod['status']=dict(podIP='10.244.2.11')
                s.ledger=[dict(kind='Pod',name='network-subject',uid=pod['metadata']['uid'],expected=copy.deepcopy(pod))]
                binding=dict(owner=OWNER,pods=dict(subject=dict(pod_uid=pod['metadata']['uid'],pod_ip='10.244.2.11'),peer=dict(pod_uid=str(uuid.uuid4()),pod_ip='10.244.2.12')))
                if mutation=='namespace_uid':ns['metadata']['uid']=str(uuid.uuid4())
                if mutation=='pod_uid':pod['metadata']['uid']=str(uuid.uuid4())
                if mutation=='pod_ip':pod['status']['podIP']='10.244.2.13'
                if mutation=='spec':pod['spec']['containers'][0]['imagePullPolicy']='Always'
                if mutation=='deleting':pod['metadata']['deletionTimestamp']='2026-10-17T15:00:00Z'
                class K:
                    called=False
                    def owned_namespace_pod(self,*a):return ns,pod
                    def exec(self,*a,**kw):self.called=True;return b'{}'
                k=K();s.k=k
                with self.assertRaises(m.Refused):s.call('subject','peer','cross',binding)
                self.assertFalse(k.called)

    def test_successful_guarded_three_rounds_preserve_global_barriers_and_mechanism(self):
        with tempfile.TemporaryDirectory() as d:
            s=ProofSession(d);s.network()
            self.assertTrue(s.network_passed)
            result=s.receipts['network']
            self.assertEqual(result['mechanism'],'LINK_DOWN_LOOPBACK_ONLY')
            self.assertIs(result['network_policy_enforcement_proven'],False)
            self.assertEqual(result['packet_continuity_check']['denial_rounds_final_streak'],3)
            calls=[e for e in s.events if e.startswith('guarded-')]
            one=['guarded-health:subject','guarded-health:peer','guarded-cross:subject','guarded-cross:peer','guarded-health:subject','guarded-health:peer']
            self.assertEqual(calls,one*3)
            rounds=sorted(Path(d).glob('NETWORK-ROUND-*.json'));self.assertEqual(len(rounds),4)
            self.assertTrue(all(json.loads(p.read_bytes())['complete'] for p in rounds))

    def test_one_direction_or_reachable_cross_cannot_pass(self):
        for mode in (False,'one-way'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                s=ProofSession(d);s.denial=mode
                with self.assertRaises(Exception):s.network()
                self.assertFalse(s.network_passed);self.assertNotIn('network',s.receipts)
                self.assertTrue(list(Path(d).glob('NETWORK-ROUND-*.json')))

    def test_guard_failure_stops_before_post_guard_rounds(self):
        with tempfile.TemporaryDirectory() as d:
            s=ProofSession(d);s.bad_guard=True
            with self.assertRaisesRegex(m.Refused,'guard_receipt_fixture_invalid'):s.network()
            self.assertFalse(s.network_passed)
            self.assertFalse(any(e.startswith('guarded-') for e in s.events))

    def test_actual_create_decorates_business_once_before_owned_uid_adoption(self):
        with tempfile.TemporaryDirectory() as d:
            s=m.Session(None,OWNER,d,P);s.admission_ready=True
            s.namespace=lambda:None;s.priority=lambda:None;s.service_account=lambda:'fixed-sa';s.node=lambda:None
            hf=m.module(P['host_frontend_stage.py'],'hf_create_fixture')
            original=next(o for o in hf.render(OWNER,NS,P['host_runtime_inputs'])['stages'][0]['objects'] if o['kind']=='Pod')
            class K:
                count=0
                def create(self,obj):
                    self.count+=1
                    self.obj=copy.deepcopy(obj)
                    actual=copy.deepcopy(obj);actual['metadata']['uid']=str(uuid.uuid4())
                    return actual
            k=K();s.k=k
            row=s.create(original)
            self.assertEqual(k.count,1)
            self.assertEqual(k.obj['spec']['initContainers'][0]['name'],s.guard.GUARD_CONTAINER)
            self.assertEqual(row['expected'],k.obj)
            self.assertEqual(row['uid'],s.ledger[0]['uid'])
            self.assertNotIn('initContainers',original['spec'])

    def test_guard_runtime_image_and_container_identity_are_part_of_actual_gate(self):
        with tempfile.TemporaryDirectory() as d:
            s=m.Session(None,OWNER,d,P);s.priority=lambda:None;s.node=lambda:None
            pod=s.bind_priority(s.render_network()['pods'][0]);pod['spec']['nodeName']=m.NODE
            row=dict(name='host-probe',uid=str(uuid.uuid4()),expected=pod)
            obj=copy.deepcopy(pod)
            obj['status']=dict(containerStatuses=[],initContainerStatuses=[dict(name=s.guard.GUARD_CONTAINER,restartCount=0,
                imageID=s.guard.GUARD_IMAGE['config_digest'],containerID='containerd://'+'f'*64,state=dict(terminated=dict(exitCode=0)))])
            s.read=lambda r:copy.deepcopy(obj)
            _,ids=s.runtime(row,{s.guard.GUARD_CONTAINER:s.guard.GUARD_IMAGE},allow_terminal=True)
            self.assertEqual(ids[s.guard.GUARD_CONTAINER]['container_id'],'containerd://'+'f'*64)
            obj['status']['initContainerStatuses'][0]['imageID']='sha256:'+'0'*64
            with self.assertRaisesRegex(m.Refused,'runtime_image_not_bound'):
                s.runtime(row,{s.guard.GUARD_CONTAINER:s.guard.GUARD_IMAGE},allow_terminal=True)
            obj['status']['initContainerStatuses'][0]['imageID']=s.guard.GUARD_IMAGE['config_digest']
            obj['status']['initContainerStatuses'][0]['restartCount']=1
            with self.assertRaisesRegex(m.Refused,'runtime_restart_or_container_id'):
                s.runtime(row,{s.guard.GUARD_CONTAINER:s.guard.GUARD_IMAGE},allow_terminal=True)

    def test_terminal_guard_receipt_is_required_even_when_original_main_succeeded(self):
        with tempfile.TemporaryDirectory() as d:
            s=m.Session(None,OWNER,d,P);clock=Clock();s.now=clock.now;s.sleep=clock.sleep
            hf=m.module(P['host_frontend_stage.py'],'hf_terminal_fixture')
            original=next(o for o in hf.render(OWNER,NS,P['host_runtime_inputs'])['stages'][0]['objects'] if o['kind']=='Pod')
            pod=s.guard.decorate_business_pod(original,OWNER,NS)
            uid=str(uuid.uuid4());row=dict(name=pod['metadata']['name'],uid=uid,expected=pod)
            main=pod['spec']['containers'][0]['name'];guard=s.guard.GUARD_CONTAINER
            obj=copy.deepcopy(pod);obj['spec']['nodeName']=m.NODE
            obj['status']=dict(phase='Succeeded',initContainerStatuses=[dict(name=guard,state=dict(terminated=dict(exitCode=0)))],
                               containerStatuses=[dict(name=main,state=dict(terminated=dict(exitCode=0)))])
            identities={name:dict(image_id=PIN['config_digest'],container_id='containerd://'+'f'*64) for name in (main,guard)}
            s.read=lambda r:obj
            observed=[]
            def runtime(r,images,allow_terminal=False):
                observed.append(set(images));return obj,identities
            s.runtime=runtime
            class K:
                def logs(self,namespace,pod,name):return b'{}\n'
            s.k=K()
            with self.assertRaisesRegex(m.Refused,'link_guard_guard_receipt_identity'):
                s.wait_terminal(row,{main:PIN},90)
            self.assertEqual(observed,[{main,guard}])
            self.assertNotIn('link_guards',s.receipts)

    def test_failed_round_retains_both_health_before_and_partial_packet_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            s=ProofSession(d);s.partial=True
            with self.assertRaisesRegex(m.Refused,'fixture_exec_nonzero'):s.network()
            value=json.loads((Path(d)/'NETWORK-ROUND-02.json').read_bytes())
            self.assertFalse(value['complete']);self.assertEqual(value['error_code'],'fixture_exec_nonzero')
            self.assertEqual(set(value['round']['health_before']),{'subject','peer'})
            self.assertEqual(value['round']['flows'],{})
            self.assertFalse(s.network_passed)

    def test_actual_r2_timing_projection_reduced_cli_cost_without_deadline_relaxation(self):
        observed=json.loads((HERE/'R2-NETWORK-PROJECTION.json').read_bytes())
        unit=max(observed['command_elapsed_seconds']/observed['command_count'],max(x['seconds'] for x in observed['network_calls']))
        # 12 CLI starts per round after pair GET fusion, plus BOTH worst-case
        # 2-second connects, health spacing, inter-round spacing and 8s guard.
        three=3*(12*unit+4+.5)+2+8
        self.assertLess(three,60)
        self.assertEqual(6*2,12)
        source=(HERE/'controller_template.py').read_text()
        self.assertIn("self.now()-withdrawn<52",source)
        self.assertIn("self.now()-started<225",source)
        network=m.module(P['network_probe.py'],'frozen_network_fixture')
        self.assertIn("elapsed <= 240",P['network_probe.py'])
        self.assertIn("instant <= 60",P['network_probe.py'])


if __name__=='__main__':unittest.main(verbosity=2)
