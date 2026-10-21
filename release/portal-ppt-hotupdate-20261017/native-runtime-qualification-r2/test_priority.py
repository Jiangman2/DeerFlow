"""Focused r2 priority admission/lifecycle fixtures; no cluster or business execution."""
import copy
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('controller_r2',HERE/'run_native.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
P=m.payload();OWNER='a'*32;NS='portal-ppt-'+OWNER


class Clock:
    def __init__(self):self.value=1
    def now(self):return self.value
    def sleep(self,n):self.value+=n


class Kube:
    def __init__(self):
        self.objects={};self.actual_creates=[];self.deletes=[];self.dry_runs=[];self.events=[]
        self.namespace=dict(apiVersion='v1',kind='Namespace',metadata=dict(name=NS,uid=str(uuid.uuid4()),labels={m.OWNER_LABEL:OWNER,'kubernetes.io/metadata.name':NS}))
        self.sa_uid=str(uuid.uuid4());self.sa_missing=0;self.cache_misses=0;self.last_cache_miss=False
        self.unknown_kind=None;self.delete_pod_fails=False;self.dry_error=None
        self.dry_response_uids=[]
    def get(self,kind,name=None,namespace=None,timeout=30):
        kind=kind.lower()
        if kind=='namespace':return copy.deepcopy(self.namespace)
        if kind=='node':
            return dict(metadata=dict(uid=m.NODE_UID,labels={'kubernetes.io/arch':'arm64','kubernetes.io/os':'linux','kubernetes.io/hostname':m.NODE}),
                        spec={},status=dict(nodeInfo=dict(operatingSystem='linux',architecture='arm64'),conditions=[dict(type='Ready',status='True')]+
                        [dict(type=x,status='False') for x in ('MemoryPressure','DiskPressure','PIDPressure','NetworkUnavailable')]))
        if kind=='priorityclass':self.events.append('priority-read')
        if name is None:return dict(items=[copy.deepcopy(v) for (k,n),v in self.objects.items() if k==kind])
        return copy.deepcopy(self.objects[(kind,name)])
    def admit_pod(self,obj,dry=False):
        name=obj['spec'].get('priorityClassName')
        priority=self.objects.get(('priorityclass',name))
        policy=priority['preemptionPolicy'] if priority else 'PreemptLowerPriority'
        if obj['spec'].get('preemptionPolicy')!=policy:raise m.Refused('kubectl_nonzero_1')
        result=copy.deepcopy(obj);result['metadata']['uid']=str(uuid.uuid4())
        result['spec']['priority']=priority['value'] if priority else 0
        result['spec']['nodeName']=m.NODE
        result['status']=dict(phase='Running',podIP='10.244.2.11',containerStatuses=[dict(name='proof',restartCount=0,
            imageID='sha256:5b43bc9cd5f51029b87aaf0b212c064124e29fb814c5543b7de7d864812f2902',containerID='containerd://'+'b'*64,state=dict(running={}))])
        return result
    def create(self,obj):
        self.actual_creates.append((obj['kind'],obj['metadata']['name']))
        self.events.append('create:'+obj['kind'])
        if obj['kind']=='Pod':result=self.admit_pod(obj)
        else:
            result=copy.deepcopy(obj);result['metadata']['uid']=str(uuid.uuid4())
            if obj['kind']=='PriorityClass':result.pop('globalDefault')
        self.objects[(obj['kind'].lower(),obj['metadata']['name'])]=result
        if self.unknown_kind==obj['kind']:raise m.Refused('kubectl_timeout')
        return copy.deepcopy(result)
    def metadata_uid(self,resource,name,namespace=None,timeout=30):
        if resource=='serviceaccount':
            if self.sa_missing:
                self.sa_missing-=1;return ''
            return self.sa_uid
        obj=self.objects.get((resource.lower(),name))
        return obj['metadata']['uid'] if obj else ''
    def dry_run(self,obj,timeout):
        self.dry_runs.append(copy.deepcopy(obj));self.events.append('dry-run')
        if self.dry_error:raise self.dry_error
        if self.cache_misses:
            self.cache_misses-=1;self.last_cache_miss=True
            raise m.Refused('kubectl_nonzero_1')
        self.last_cache_miss=False
        value=self.admit_pod(obj,True);self.dry_response_uids.append(value['metadata']['uid'])
        return value
    def priority_cache_miss(self,name):return self.last_cache_miss
    def delete_uid(self,kind,namespace,name,uid):
        self.events.append('delete:'+kind)
        if kind=='Pod' and self.delete_pod_fails:raise m.Refused('fixture_delete_failed')
        key=(kind.lower(),name)
        if self.objects[key]['metadata']['uid']!=uid:raise m.Refused('server_uid_precondition_failed')
        self.deletes.append((kind,namespace,name,uid));del self.objects[key]


def session(directory):
    k=Kube();clock=Clock();s=m.Session(k,OWNER,directory,P,now=clock.now,sleep=clock.sleep)
    s.namespace_uid=k.namespace['metadata']['uid']
    return s,k,clock


class PriorityTests(unittest.TestCase):
    def test_proven_r1_rejection_and_bound_r2_admission(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);pod=s.render_network()['pods'][0]
            with self.assertRaisesRegex(m.Refused,'kubectl_nonzero_1'):k.create(pod)
            s.create_priority();self.assertFalse(s.priority_row['expected']['globalDefault'])
            s.prepare_admission();row=s.create(pod)
            actual=k.get('pod',row['name'],NS)
            self.assertEqual(actual['spec']['priorityClassName'],s.priority_name)
            self.assertEqual(actual['spec']['preemptionPolicy'],'Never')
            self.assertEqual(actual['spec']['priority'],0)
            self.assertEqual([x['kind'] for x in s.ledger],['Pod'])
            self.assertTrue(all(uid not in [x['uid'] for x in s.ledger] for uid in k.dry_response_uids))
            self.assertLess(k.events.index('priority-read'),k.events.index('dry-run'))

    def test_priority_class_semantics_and_api_false_omission(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);s.create_priority();s.priority()
            self.assertNotIn('globalDefault',k.objects[('priorityclass',s.priority_name)])
            for key,value in (('globalDefault',True),('value',1),('value',False),('preemptionPolicy','PreemptLowerPriority')):
                original=copy.deepcopy(k.objects[('priorityclass',s.priority_name)])
                k.objects[('priorityclass',s.priority_name)][key]=value
                with self.subTest(key=key,value=value),self.assertRaises(m.Refused):s.priority()
                k.objects[('priorityclass',s.priority_name)]=original

    def test_priority_uid_replacement_blocks_create_runtime_and_delete(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);s.create_priority();s.prepare_admission();pod=s.render_network()['pods'][0]
            row=s.create(pod)
            k.objects[('priorityclass',s.priority_name)]['metadata']['uid']=str(uuid.uuid4())
            before=len(k.actual_creates)
            with self.assertRaisesRegex(m.Refused,'priority_class_uid_replaced'):s.create(s.render_network()['pods'][1])
            self.assertEqual(len(k.actual_creates),before)
            with self.assertRaisesRegex(m.Refused,'priority_class_uid_replaced'):s.runtime(row,{'proof':dict(manifest_digest='sha256:'+'a'*64,config_digest='sha256:'+'b'*64)})
            results=s.cleanup()
            self.assertTrue(any(x.get('kind')=='PriorityClass' and x['status']=='RETAINED_REVIEW_REQUIRED' for x in results))
            self.assertFalse(any(x[0]=='PriorityClass' for x in k.deletes))

    def test_unknown_priority_create_never_adopts_or_retries_or_deletes(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);k.unknown_kind='PriorityClass'
            with self.assertRaisesRegex(m.Refused,'kubectl_timeout'):s.create_priority()
            self.assertIsNone(s.priority_row)
            self.assertEqual(len(k.actual_creates),1)
            self.assertEqual(s.cleanup(),[])
            self.assertFalse(s.uncertain_creates[0]['deletion_authorized'])
            self.assertTrue((Path(d)/'PRIORITY-CREATE-INTENT.json').is_file())
            self.assertEqual(s.uncertain_creates[0]['kind'],'PriorityClass')

    def test_priority_cleanup_occurs_after_owned_pods(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);s.create_priority();s.prepare_admission();row=s.create(s.render_network()['pods'][0])
            results=s.cleanup()
            self.assertEqual([x[0] for x in k.deletes],['Pod','PriorityClass'])
            self.assertEqual(k.deletes[-1][1],None)
            self.assertEqual(k.deletes[-1][3],s.priority_row['uid'])
            self.assertEqual(results[-1]['status'],'UID_PRECONDITION_DELETED')

    def test_priority_retained_for_failed_or_unknown_pod_cleanup(self):
        for unknown in (False,True):
            with self.subTest(unknown=unknown),tempfile.TemporaryDirectory() as d:
                s,k,c=session(d);s.create_priority();s.prepare_admission()
                if unknown:
                    k.unknown_kind='Pod'
                    with self.assertRaises(m.Refused):s.create(s.render_network()['pods'][0])
                else:
                    s.create(s.render_network()['pods'][0]);k.delete_pod_fails=True
                results=s.cleanup()
                self.assertEqual(results[-1]['kind'],'PriorityClass')
                self.assertEqual(results[-1]['status'],'RETAINED_REVIEW_REQUIRED')
                self.assertFalse(any(x[0]=='PriorityClass' for x in k.deletes))

    def test_readiness_only_fixed_cache_miss_repeats_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);s.create_priority();k.sa_missing=2;k.cache_misses=2
            s.prepare_admission()
            self.assertTrue(s.admission_ready)
            self.assertEqual(len(k.dry_runs),3)
            self.assertEqual(k.actual_creates,[('PriorityClass',s.priority_name)])
            self.assertEqual(c.now(),5)
            self.assertFalse(s.ledger)
            self.assertTrue(json.loads((Path(d)/'ADMISSION-READINESS.json').read_bytes())['dry_run_uid_not_adopted'])

    def test_readiness_stops_other_error_and_overall_thirty_second_budget(self):
        for error in (m.Refused('kubectl_nonzero_1'),m.Refused('kubectl_timeout'),ValueError('invalid_json')):
            with self.subTest(error=error),tempfile.TemporaryDirectory() as d:
                s,k,c=session(d);s.create_priority();k.dry_error=error
                with self.assertRaises(type(error)):s.prepare_admission()
                self.assertEqual(len(k.dry_runs),1);self.assertFalse(s.admission_ready)
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d);s.create_priority();k.sa_missing=100
            with self.assertRaisesRegex(m.Refused,'admission_readiness_30s_deadline'):s.prepare_admission()
            self.assertEqual(c.now(),31);self.assertFalse(k.dry_runs)

    def test_cluster_delete_url_has_uid_precondition_no_namespace(self):
        with tempfile.TemporaryDirectory() as d:
            t=m.Transport(d);seen=[]
            t.run=lambda args,body=None:seen.append((args,json.loads(body)))
            uid=str(uuid.uuid4());name='portal-ppt-'+OWNER
            t.delete_uid('PriorityClass',None,name,uid)
            self.assertEqual(seen[0][0],['delete','--raw=/apis/scheduling.k8s.io/v1/priorityclasses/'+name,'-f','-'])
            self.assertEqual(seen[0][1]['preconditions'],dict(uid=uid))

    def test_fixed_informer_error_match_is_narrow(self):
        with tempfile.TemporaryDirectory() as d:
            t=m.Transport(d);t.calls=1;name='portal-ppt-'+OWNER
            raw=('Error from server (Forbidden): error when creating "STDIN": pods "network-subject" is forbidden: no PriorityClass with name '+name+' was found\n').encode()
            p=Path(d)/'command-0001.stderr.private.log';p.write_bytes(raw)
            self.assertTrue(t.priority_cache_miss(name))
            for bad in (raw.replace(name.encode(),b'other-class'),raw.replace(b'no PriorityClass',b'permission denied PriorityClass'),b'warning\n'+raw):
                p.write_bytes(bad);self.assertFalse(t.priority_cache_miss(name))

    def test_helpers_unchanged_and_every_test_pod_gets_same_class(self):
        with tempfile.TemporaryDirectory() as d:
            s,k,c=session(d)
            hf=m.module(P['host_frontend_stage.py'],'hf_r2_fixture');db=m.module(P['db_stage.py'],'db_r2_fixture')
            pods=s.render_network()['pods']+[next(x for x in stage['objects'] if x['kind']=='Pod') for stage in hf.render(OWNER,NS,P['host_runtime_inputs'])['stages']]
            pods.append(db.render_pg_probe(OWNER,NS,P['postgres_cache']))
            profile=dict(schema='portal-native-pg-profile.v1',owner=OWNER,uid=999,gid=999,server_binary_version='16.4',database_started=False,**db.PG_FILES)
            pods.append(db.render_db(OWNER,NS,profile))
            self.assertEqual(len(pods),6)
            for pod in pods:
                before=copy.deepcopy(pod);bound=s.bind_priority(pod)
                m.pod_safety(bound,s.priority_name)
                before['spec']['priorityClassName']=s.priority_name
                self.assertEqual(bound,before)
            self.assertEqual(hashlib.sha256(P['host_frontend_stage.py'].encode()).hexdigest(),'4d1b7d01bc353df66e77b0178803f74a64915e878e163abf27928c6761c2b967')
            self.assertEqual(hashlib.sha256(P['db_stage.py'].encode()).hexdigest(),'cad8fe569e2461b3ce57e508c9ef832aea4479d1ec61bb260a5b8f45f1a0a398')

    def test_exact_prior_failure_and_error_preconditions(self):
        fixture=json.loads((HERE/'PRIOR-RECEIPT-FIXTURE.json').read_bytes())
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name,value in fixture.items():(root/name).write_bytes(base64.b64decode(value))
            original=m.PRIOR_ROOT;m.PRIOR_ROOT=d
            try:
                receipt=m.prior_failure()
                self.assertEqual(receipt['report_sha256'],m.PRIOR_REPORT_SHA)
                self.assertEqual(receipt['stderr_sha256'],m.PRIOR_STDERR_SHA)
                self.assertFalse(receipt['old_evidence_modified'])
                (root/'command-0015.stderr.private.log').write_bytes(b'x'*270)
                with self.assertRaises(m.Refused):m.prior_failure()
            finally:m.PRIOR_ROOT=original


if __name__=='__main__':unittest.main(verbosity=2)
