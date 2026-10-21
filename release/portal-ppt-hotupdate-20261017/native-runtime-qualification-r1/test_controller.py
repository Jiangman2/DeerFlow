"""New orchestration fixtures only. No Kubernetes, remote CNI, DB or model calls."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
import uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('native_controller',HERE/'run_native.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
P=m.payload()
OWNER='a'*32
NS='portal-ppt-'+OWNER
PIN=dict(manifest_digest='sha256:9b3a6d80d20b73585c6642213077236bb9948f35d0d5daa262e78d120422eb9e',
         config_digest='sha256:5b43bc9cd5f51029b87aaf0b212c064124e29fb814c5543b7de7d864812f2902')


class Clock:
    def __init__(self):self.value=1
    def now(self):return self.value
    def sleep(self,n):self.value+=n


class FakeKube:
    def __init__(self):
        self.ns=dict(apiVersion='v1',kind='Namespace',metadata=dict(name=NS,uid=str(uuid.uuid4()),labels={m.OWNER_LABEL:OWNER}))
        self.objects={};self.deleted=[];self.creates=[]
        self.mutate=None
    def get(self,kind,name=None,namespace=None):
        if kind=='node':
            return dict(metadata=dict(uid=m.NODE_UID,labels={'kubernetes.io/arch':'arm64','kubernetes.io/os':'linux','kubernetes.io/hostname':m.NODE}),
                        spec={},status=dict(nodeInfo=dict(operatingSystem='linux',architecture='arm64'),
                            conditions=[dict(type='Ready',status='True')]+[dict(type=x,status='False') for x in ('MemoryPressure','DiskPressure','PIDPressure','NetworkUnavailable')]))
        if kind=='namespace':return copy.deepcopy(self.ns)
        if name is None:
            return dict(items=[copy.deepcopy(v) for (k,n),v in self.objects.items() if k.lower()==kind.lower()])
        return copy.deepcopy(self.objects[(kind.lower(),name)])
    def create(self,obj):
        result=copy.deepcopy(obj);result['metadata']['uid']=str(uuid.uuid4())
        if obj['kind']=='Pod':
            result['spec']['nodeName']=m.NODE
            role=result['metadata']['name'].split('-')[-1]
            result['status']=dict(phase='Running',podIP='10.244.2.'+('11' if role=='subject' else '12'),
                containerStatuses=[dict(name=c['name'],imageID=PIN['config_digest'],containerID='containerd://'+('b' if role=='subject' else 'c')*64,
                                        restartCount=0,state=dict(running={})) for c in obj['spec']['containers']])
        if self.mutate:self.mutate(result)
        self.objects[(obj['kind'].lower(),obj['metadata']['name'])]=result
        self.creates.append(obj['kind']+':'+obj['metadata']['name'])
        return copy.deepcopy(result)
    def delete_uid(self,kind,namespace,name,uid):
        key=(kind.lower(),name)
        if self.objects[key]['metadata']['uid']!=uid:raise m.Refused('server_uid_precondition_failed')
        self.deleted.append((kind,name,uid));del self.objects[key]


def session(directory):
    clock=Clock();k=FakeKube();s=m.Session(k,OWNER,directory,P,now=clock.now,sleep=clock.sleep)
    s.namespace_uid=k.ns['metadata']['uid']
    return s,k,clock


def first_pod():
    t=json.loads(P['TEMPLATE.json'])
    pod=t['pods'][0]
    text=json.dumps(pod).replace('__UNBOUND_OWNER_UUID__',OWNER).replace('__UNBOUND_FRESH_OWNED_NAMESPACE__',NS)
    pod=json.loads(text)
    pod['spec']['containers'][0]['args'][pod['spec']['containers'][0]['args'].index('__UNBOUND_PINNED_INLINE_NETWORK_ADAPTER__')]=P['network_probe.py']
    return pod


def install_packets(s,k,mode='good'):
    pulse={role:0 for role in ('subject','peer')}
    def call(role,target,operation,binding):
        pulse[target]+=1
        deny=('networkpolicy','subject-temporary-allow-peer') not in k.objects
        status='CONNECT_BLOCKED' if operation=='cross' and deny else 'SUCCESS'
        if mode=='baseline_bad' and operation=='cross' and not deny:status='CONNECT_BLOCKED'
        if mode=='one_direction' and operation=='cross' and deny and role=='peer':status='SUCCESS'
        if mode=='http_unproven' and operation=='cross' and deny:status='UNPROVEN'
        challenge=uuid.uuid4().hex
        packet=dict(schema='portal-ppt-owned-network-call.v1',operation=operation,source_role=role,target_role=target,
                    source_pod_uid=binding['pods'][role]['pod_uid'],target_pod_uid=binding['pods'][target]['pod_uid'],
                    challenge=challenge,status=status,seconds=.1,connection_established=status=='SUCCESS')
        if status=='SUCCESS':
            packet['response']=dict(schema='portal-ppt-owned-listener.v1',owner=OWNER,role=target,**binding['pods'][target],
                                    instance=('d' if target=='subject' else 'e')*32,pid=101 if target=='subject' else 102,
                                    started_ns=777,heartbeat=pulse[target],challenge=challenge)
        else:packet.update(failure_code='TimeoutError',errno=None)
        return packet
    s.call=call


class ControllerTests(unittest.TestCase):
    def test_exact_embedded_assets_and_all_renderer_safety(self):
        self.assertEqual(P['preload_report'],dict(bytes=24125,sha256=m.PRELOAD_SHA))
        hf=m.module(P['host_frontend_stage.py'],'hf_fixture')
        stages=hf.render(OWNER,NS,P['host_runtime_inputs'])['stages']
        self.assertEqual([x['name'] for x in stages],['host','frontend'])
        for stage in stages:
            hf.validate_stage_objects(stage,OWNER,NS)
            pod=next(x for x in stage['objects'] if x['kind']=='Pod')
            m.pod_safety(pod)
            names={c['name'] for c in pod['spec'].get('initContainers',[])+pod['spec']['containers']}
            self.assertEqual(names,set(stage['images']))
            self.assertIn(stage['terminal_container'],names)
        db=m.module(P['db_stage.py'],'db_fixture')
        probe=db.render_pg_probe(OWNER,NS,P['postgres_cache']);m.pod_safety(probe)
        profile=dict(schema='portal-native-pg-profile.v1',owner=OWNER,uid=999,gid=999,server_binary_version='16.4',database_started=False,**db.PG_FILES)
        pod=db.render_db(OWNER,NS,profile);m.pod_safety(pod)
        self.assertEqual([x['name'] for x in pod['spec']['initContainers']],['postgres-fixture','gateway-upgrade','runner-restart','gateway-rollback-restart','runner-rollback-restart','gateway-reforward-restart'])

    def test_namespace_or_object_replacement_never_deletes(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);row=s.create(first_pod())
            k.ns['metadata']['uid']=str(uuid.uuid4())
            with self.assertRaisesRegex(m.Refused,'namespace_ownership_changed'):s.remove(row)
            self.assertFalse(k.deleted)
            k.ns['metadata']['uid']=s.namespace_uid
            k.objects[('pod',row['name'])]['metadata']['uid']=str(uuid.uuid4())
            with self.assertRaisesRegex(m.Refused,'delete_object_ownership_changed'):s.remove(row)
            self.assertFalse(k.deleted)
            k.objects[('pod',row['name'])]['metadata']['uid']=row['uid']
            k.objects[('pod',row['name'])]['metadata']['labels'][m.OWNER_LABEL]='f'*32
            with self.assertRaises(m.Refused):s.remove(row)
            self.assertFalse(k.deleted)

    def test_admission_drift_stays_ledgered_and_blocks(self):
        for mutate in (lambda p:p['spec']['containers'][0].update(imagePullPolicy='IfNotPresent'),
                       lambda p:p['spec']['containers'].append(copy.deepcopy(p['spec']['containers'][0])),
                       lambda p:p['spec'].update(nodeName='other-node'),
                       lambda p:p['spec']['containers'][0]['resources']['requests'].update(cpu='1')):
            with self.subTest(mutate=mutate),tempfile.TemporaryDirectory() as d:
                s,k,c=session(d);k.mutate=mutate
                with self.assertRaises(m.Refused):s.create(first_pod())
                self.assertEqual(len(s.ledger),1)
                self.assertTrue((Path(d)/'object-01.json').is_file())

    def test_runtime_wrong_image_restart_or_container_id_block(self):
        for key,value in (('imageID','sha256:'+'0'*64),('restartCount',1),('containerID','')):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as d:
                s,k,c=session(d);row=s.create(first_pod())
                k.objects[('pod',row['name'])]['status']['containerStatuses'][0][key]=value
                with self.assertRaises(m.Refused):s.runtime(row,{'proof':PIN})

    def test_pending_deadline_is_bounded_by_clock(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);row=s.create(first_pod())
            k.objects[('pod',row['name'])]['status']=dict(phase='Pending')
            with self.assertRaisesRegex(m.Refused,'pending_120s_deadline'):s.wait_running(row,{'proof':PIN})
            self.assertEqual(c.now(),121)

    def test_actual_network_checker_full_orchestration_and_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);install_packets(s,k)
            s.network()
            self.assertTrue(s.network_passed)
            self.assertEqual(s.receipts['network']['denial_rounds_final_streak'],3)
            self.assertEqual(list(k.objects),[('networkpolicy','namespace-deny-all')])
            self.assertTrue((Path(d)/'NETWORK-EVIDENCE.json').is_file())
            self.assertEqual(len(k.deleted),4)

    def test_bad_baseline_one_direction_and_protocol_never_qualify(self):
        for mode in ('baseline_bad','one_direction','http_unproven'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                s,k,c=session(d);install_packets(s,k,mode)
                with self.assertRaises(Exception):s.network()
                self.assertFalse(s.network_passed)
                self.assertNotIn('host',s.receipts)
                self.assertFalse(any('host' in x for x in k.creates))

    def test_policy_injection_prevents_candidate_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);install_packets(s,k);s.network()
            x=copy.deepcopy(next(iter(k.objects.values())));x['metadata']['name']='unowned-allow';x['metadata']['uid']=str(uuid.uuid4())
            k.objects[('networkpolicy','unowned-allow')]=x
            with self.assertRaisesRegex(m.Refused,'unexpected_additive_policy'):s.deny_only()

    def test_transport_real_nonzero_private_stderr_and_returncode(self):
        with tempfile.TemporaryDirectory() as d:
            original=m.KUBE;m.KUBE=[sys.executable,'-c',"import sys; print('safe'); sys.stderr.write('private-token'); sys.exit(7)"]
            try:
                t=m.Transport(d)
                with self.assertRaisesRegex(m.Refused,'kubectl_nonzero_7'):t.run([])
                receipt=json.loads((Path(d)/'command-0001.json').read_bytes())
                self.assertEqual(receipt['returncode'],7)
                self.assertEqual((Path(d)/'command-0001.stderr.private.log').read_bytes(),b'private-token')
                self.assertNotIn('private-token',json.dumps(receipt))
                self.assertFalse((Path(d)/'command-0001.stdout.private.log').exists())
            finally:m.KUBE=original

    def test_transport_interrupt_preserves_real_child_exit(self):
        with tempfile.TemporaryDirectory() as d:
            original=m.KUBE
            m.KUBE=[sys.executable,'-c',"import os,signal,sys,time; sys.stderr.write('before-cancel'); sys.stderr.flush(); os.kill(os.getppid(),signal.SIGTERM); time.sleep(60)"]
            previous=signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(m.Refused('test_interrupt')))
            try:
                with self.assertRaisesRegex(m.Refused,'test_interrupt'):m.Transport(d).run([])
                receipt=json.loads((Path(d)/'command-0001.json').read_bytes())
                self.assertIn(receipt['returncode'],(-15,-9))
                self.assertEqual((Path(d)/'command-0001.stderr.private.log').read_bytes(),b'before-cancel')
            finally:m.KUBE=original;signal.signal(signal.SIGTERM,previous)

    def test_pg_sidecar_shutdown_requires_success_and_order(self):
        statuses=[dict(name='final-receipt',restartCount=0,state=dict(terminated=dict(exitCode=0,finishedAt='2026-10-17T14:00:00Z'))),
                  dict(name='postgres-fixture',restartCount=0,state=dict(terminated=dict(exitCode=143,reason='Error',finishedAt='2026-10-17T14:00:01Z')))]
        pod=dict(status=dict(phase='Succeeded',containerStatuses=statuses))
        self.assertTrue(m.terminal_success(pod))
        for key,value in (('exitCode',137),('reason','OOMKilled'),('finishedAt','2026-10-17T13:59:59Z')):
            bad=copy.deepcopy(pod);bad['status']['containerStatuses'][1]['state']['terminated'][key]=value
            with self.subTest(key=key),self.assertRaises(m.Refused):m.terminal_success(bad)
        bad=copy.deepcopy(pod);bad['status']['containerStatuses'][0]['state']['terminated']['exitCode']=1
        with self.assertRaises(m.Refused):m.terminal_success(bad)

    def test_production_reads_server_allowlist_only(self):
        class Projection:
            def __init__(self):self.commands=[]
            def run(self,args,**kwargs):
                self.commands.append(args)
                name=args[2]
                return ('D|'+name+'|'+str(uuid.uuid4())+'|1|1|1|1|0\nC|main|example@sha256:'+'a'*64+'|1|1Gi|1|1Gi\n').encode()
        k=Projection();self.assertEqual(len(m.production(k)),4)
        for args in k.commands:
            self.assertTrue(args[-1].startswith('-o=jsonpath='))
            self.assertNotIn('env',args[-1].lower())
            self.assertNotIn('-o=json',args)

    def test_unknown_create_is_not_retried_adopted_or_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d)
            original=k.create
            def lost(obj):original(obj);raise m.Refused('kubectl_timeout')
            k.create=lost
            with self.assertRaisesRegex(m.Refused,'kubectl_timeout'):s.create(first_pod())
            self.assertEqual(len(k.creates),1)
            self.assertEqual(s.ledger,[])
            self.assertEqual(s.cleanup(),[])
            self.assertEqual(len(s.uncertain_creates),1)
            self.assertFalse(s.uncertain_creates[0]['deletion_authorized'])
            self.assertTrue((Path(d)/'create-intent-01.json').is_file())
            self.assertTrue((Path(d)/'uncertain-create-01.json').is_file())

    def test_admission_rejected_owned_uid_can_be_cleaned(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d)
            k.mutate=lambda p:p['spec']['containers'][0].update(imagePullPolicy='IfNotPresent')
            with self.assertRaises(m.Refused):s.create(first_pod())
            s.remove(s.ledger[0])
            self.assertEqual(len(k.deleted),1)
            self.assertEqual(k.deleted[0][2],s.ledger[0]['uid'])

    def test_known_api_default_serialization_and_unknown_annotation(self):
        expected=first_pod();actual=copy.deepcopy(expected)
        actual['metadata']['uid']=str(uuid.uuid4())
        for key in ('hostNetwork','hostPID','hostIPC'):actual['spec'].pop(key)
        actual['spec']['serviceAccountName']='default'
        actual['spec']['containers'][0]['terminationMessagePath']='/dev/termination-log'
        actual['spec']['containers'][0]['terminationMessagePolicy']='File'
        for env in actual['spec']['containers'][0]['env']:
            env['valueFrom']['fieldRef']['apiVersion']='v1'
        m.admitted(expected,actual)
        actual['metadata']['annotations']={'cni.projectcalico.org/podIP':'10.244.2.11/32'}
        m.admitted(expected,actual)
        actual['metadata']['annotations']['injected-network']='unrequested'
        with self.assertRaisesRegex(m.Refused,'unrequested_annotation'):m.admitted(expected,actual)

    def test_db_init_started_pending_not_scheduling_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);row=s.create(first_pod())
            pod=k.objects[('pod',row['name'])]
            pod['status']=dict(phase='Pending',initContainerStatuses=[dict(name='fixture',state=dict(running={}))])
            with self.assertRaisesRegex(m.Refused,'candidate_deadline'):s.wait_terminal(row,{'proof':PIN},125,pending=120)
            self.assertEqual(c.now(),126)

    def test_pg_index_allowed_only_with_exact_selected_manifest_link(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d)
            db=m.module(P['db_stage.py'],'db_index_fixture')
            pod=db.render_pg_probe(OWNER,NS,P['postgres_cache'])
            row=s.create(pod)
            k.objects[('pod',row['name'])]['status']['containerStatuses'][0]['imageID']=P['postgres_cache']['target_digest']
            result=s.runtime(row,{'pg-profile':db.CONTAINER_IDENTITIES['pg-profile']})
            self.assertEqual(result[1]['pg-profile']['image_digest'],P['postgres_cache']['target_digest'])
            pin=copy.deepcopy(db.CONTAINER_IDENTITIES['pg-profile']);pin['manifest_digest']='sha256:'+'0'*64
            with self.assertRaisesRegex(m.Refused,'postgres_platform_link_changed'):s.runtime(row,{'pg-profile':pin})

    def test_transport_leader_exit_still_terminates_owned_descendant(self):
        with tempfile.TemporaryDirectory() as d:
            original=m.KUBE
            m.KUBE=[sys.executable,'-c',"import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);print(p.pid)"]
            try:
                raw=m.Transport(d).run([])
                pid=int(raw)
                status=Path('/proc/'+str(pid)+'/status')
                self.assertTrue(not status.exists() or '\nState:\tZ ' in status.read_text())
                self.assertEqual(json.loads((Path(d)/'command-0001.json').read_bytes())['returncode'],0)
            finally:m.KUBE=original

    def test_policy_empty_lists_omitted_and_nonempty_rule_drift(self):
        expected=json.loads(P['TEMPLATE.json'])['policies'][0]
        expected=json.loads(json.dumps(expected).replace('__UNBOUND_OWNER_UUID__',OWNER).replace('__UNBOUND_FRESH_OWNED_NAMESPACE__',NS))
        actual=copy.deepcopy(expected);actual['metadata']['uid']=str(uuid.uuid4())
        actual['spec'].pop('ingress');actual['spec'].pop('egress')
        m.admitted(expected,actual)
        actual['spec']['egress']=[{}]
        with self.assertRaisesRegex(m.Refused,'admission_policy_spec'):m.admitted(expected,actual)

    def test_host_frontend_main_selection_order_and_failure_stops_next_stage(self):
        original=m.module
        actual=original(P['host_frontend_stage.py'],'hf_orchestration')
        for failure in (False,True):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as d:
                s,k,c=session(d);s.network_passed=True;events=[]
                class Helper:
                    render=staticmethod(actual.render)
                    @staticmethod
                    def validate_terminal(role,raw,observed,namespace):
                        events.append('validate:'+role)
                        self.assertEqual(raw,('receipt-'+role).encode())
                        self.assertEqual(observed['image_reference'],next(x for x in actual.render(OWNER,NS,P['host_runtime_inputs'])['stages'] if x['name']==role)['images'][('host-probe' if role=='host' else 'frontend-probe')]['digest_reference'])
                        if failure and role=='host':raise ValueError('fixture_host_failed')
                        return dict(role=role,status='PASS')
                m.module=lambda source,name:Helper if name=='host_frontend_stage' else original(source,name)
                s.deny_only=lambda:events.append('deny-only')
                def create(obj):
                    events.append('create:'+obj['metadata']['name'])
                    return dict(kind=obj['kind'],name=obj['metadata']['name'],uid=str(uuid.uuid4()),expected=obj)
                s.create=create
                s.remove=lambda row:events.append('remove:'+row['name'])
                s.db=lambda:events.append('backend-db')
                def terminal(row,images,seconds,pending):
                    obj=copy.deepcopy(row['expected']);obj['status']=dict(phase='Succeeded')
                    ids={name:dict(image_id=pin['config_digest'],container_id='containerd://'+'b'*64) for name,pin in images.items()}
                    role='host' if 'host-probe' in images else 'frontend'
                    logs={name:(('receipt-'+role).encode() if name in ('host-probe','frontend-probe') else b'projection-init') for name in images}
                    return obj,ids,logs
                s.wait_terminal=terminal
                try:
                    if failure:
                        with self.assertRaisesRegex(ValueError,'fixture_host_failed'):s.candidates()
                        self.assertFalse(any('frontend' in x or x=='backend-db' for x in events))
                    else:
                        s.candidates()
                        self.assertLess(events.index('validate:host'),events.index('validate:frontend'))
                        self.assertLess(events.index('validate:frontend'),events.index('backend-db'))
                finally:m.module=original

    def test_transport_timeout_and_output_limit_preserve_actual_result(self):
        original=m.KUBE
        try:
            with tempfile.TemporaryDirectory() as d:
                m.KUBE=[sys.executable,'-c',"import sys,time; print('started',flush=True); time.sleep(60)"]
                with self.assertRaisesRegex(m.Refused,'kubectl_timeout'):m.Transport(d).run([],timeout=.05)
                receipt=json.loads((Path(d)/'command-0001.json').read_bytes())
                self.assertTrue(receipt['timeout']);self.assertIn(receipt['returncode'],(-15,-9))
            with tempfile.TemporaryDirectory() as d:
                m.KUBE=[sys.executable,'-c',"print('x'*8192)"]
                with self.assertRaisesRegex(m.Refused,'kubectl_output_limit'):m.Transport(d).run([],limit=1024,private=True)
                receipt=json.loads((Path(d)/'command-0001.json').read_bytes())
                self.assertTrue(receipt['output_limit'])
                self.assertLessEqual((Path(d)/'command-0001.stdout.private.log').stat().st_size,1024)
        finally:m.KUBE=original

    def test_empty_pod_security_context_default_only(self):
        helper=m.module(P['host_frontend_stage.py'],'hf_default_fixture')
        for stage in helper.render(OWNER,NS,P['host_runtime_inputs'])['stages']:
            expected=next(x for x in stage['objects'] if x['kind']=='Pod')
            self.assertNotIn('securityContext',expected['spec'])
            actual=copy.deepcopy(expected);actual['metadata']['uid']=str(uuid.uuid4())
            actual['spec']['securityContext']={}
            m.admitted(expected,actual)
            actual['spec']['securityContext']={'runAsUser':0}
            with self.subTest(stage=stage['name']),self.assertRaises(m.Refused):m.admitted(expected,actual)

    def test_current_pod_ip_drift_prevents_exec(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);row=s.create(first_pod())
            binding=dict(owner=OWNER,pods=dict(subject=dict(pod_uid=row['uid'],pod_ip='10.244.2.11'),
                                             peer=dict(pod_uid=str(uuid.uuid4()),pod_ip='10.244.2.12')))
            k.objects[('pod',row['name'])]['status']['podIP']='10.244.2.99'
            with self.assertRaisesRegex(m.Refused,'network_current_pod_ip_changed'):s.call('subject','subject','health',binding)


if __name__=='__main__':unittest.main(verbosity=2)
