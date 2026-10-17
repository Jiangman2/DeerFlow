"""One isolated full WeKnora / GLM / real Embedding acceptance; no rollout."""
import argparse
import base64
import decimal
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import resource
import secrets
import signal
import socket
import sys
import time
import traceback
import uuid
import warnings

import native_glm_acceptance as native
import audit_enrichment_runtime as audit
import diagnose_enrichment_preload as frozen

STATE=Path('/var/lib/weknora-full-enrichment-20261017-r1')
BYTE_REPORT=Path('/var/lib/weknora-runtime-byte-compare-qikece4_/RESULT.json')
BYTE_SHA='28e69e7eee425af64dbb3b0825a0985bc26a72fbb213c3eeedf7bd07248cc068'
PHASE='PREFLIGHT'
RUNTIME_SOURCE=None  # populated by the tested single-file packager
SAMPLE_B64=None
LABEL={'app':'weknora-isolated-enrichment-qualification'}
REQUEST_BUDGET={'documents':1,'vector_searches':1,'glm_expected':2,
    'embedding_expected_batches':3,'native_image_task_max_retry':3,
    'native_embedding_transport_max_retry':3,'gateway_busy_retries_max':3,
    'orchestrator_submission_retry':False,'deadline_seconds':600,
    'note':'One document and one search only. Native task/HTTP retries remain; expected call counts are NOT hard provider caps.'}


def need(ok,label):
    if not ok: raise native.AcceptanceFailure(label)


def emit(row): native.emit(row)


def phase(label):
    global PHASE
    PHASE=label; native.PHASE=label
    emit(dict(full_enrichment='RUNNING',phase=label,production_writes=0))


def read_json(path,sha): return json.loads(audit.read(path,sha))


def record(name,value):
    audit.directory(STATE)
    fd=os.open(str(STATE/name),os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write((json.dumps(value,sort_keys=True)+'\n').encode()); f.flush(); os.fsync(f.fileno())


def spec_hash(obj): return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def number(raw):
    m=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([a-zA-Z]*)',str(raw)); need(m is not None,'unsupported_resource_quantity')
    units={'':1,'m':.001,'n':1e-9,'u':1e-6,'Ki':1024,'Mi':1024**2,'Gi':1024**3,'Ti':1024**4,
           'K':1000,'M':1000**2,'G':1000**3,'T':1000**4}
    need(m[2] in units,'unsupported_resource_unit'); return float(decimal.Decimal(m[1]))*units[m[2]]


def capacity():
    n=native.get('node','datax-01'); cond={c['type']:c['status'] for c in n['status']['conditions']}
    need(not n['spec'].get('unschedulable') and cond.get('Ready')=='True' and
         all(cond.get(k)=='False' for k in ('MemoryPressure','DiskPressure','PIDPressure')),'fixture_node_unhealthy')
    raw=native.call(['get','pods','-A','--field-selector=spec.nodeName=datax-01','-o','json'],limit=12*1024**2)[1]
    used={'cpu':0.,'memory':0.,'ephemeral-storage':0.}; count=0
    for p in json.loads(raw)['items']:
        if p.get('status',{}).get('phase') in ('Succeeded','Failed'): continue
        count+=1; s=p['spec']
        # Restartable init containers require scheduler-aware accounting; fail closed.
        need(not any(x.get('restartPolicy')=='Always' for x in s.get('initContainers',[])),
             'restartable_init_requires_resource_review')
        for k in used:
            summed=sum(number(c.get('resources',{}).get('requests',{}).get(k,'0')) for c in s['containers'])
            init=max([number(c.get('resources',{}).get('requests',{}).get(k,'0')) for c in s.get('initContainers',[])]+[0])
            used[k]+=max(summed,init)+number(s.get('overhead',{}).get(k,'0'))
    free={k:number(n['status']['allocatable'].get(k,'0'))-used[k] for k in used}
    # Limits + operating reserve, not just Kubernetes requests or allocatable.
    need(free['cpu']>=4.25 and free['memory']>=8*1024**3 and free['ephemeral-storage']>=10*1024**3,
         'fixture_schedulable_resource_headroom')
    need(count+1 < number(n['status']['allocatable']['pods']),'fixture_pod_capacity')
    mem=re.search(r'(?m)^MemAvailable:\s+(\d+)\s+kB$',Path('/proc/meminfo').read_text())
    need(mem is not None and int(mem[1])*1024>=10*1024**3,'fixture_host_memory_reserve')
    need(os.statvfs('/var/lib/containerd').f_bavail*os.statvfs('/var/lib/containerd').f_frsize>=20*1024**3,
         'fixture_disk_reserve')
    tolerations=[]
    for t in n['spec'].get('taints',[]):
        if t.get('effect') not in ('NoSchedule','NoExecute'): continue
        need(t['key'] in ('node-role.kubernetes.io/control-plane','node-role.kubernetes.io/master') and
             t['effect']=='NoSchedule','fixture_node_taint_review')
        tolerations.append({'key':t['key'],'operator':'Equal','value':t.get('value',''),'effect':'NoSchedule'})
    return {'node_uid':n['metadata']['uid'],'unreserved':free,'active_pods':count},tolerations


GATEWAY_CONFIG = r'''
import json,os
names={'model':'CNOOC_EMBEDDING_MODEL_NAME','dimension':'CNOOC_EMBEDDING_DIMENSION',
'upstream':'CNOOC_EMBEDDING_UPSTREAM_URL','retries':'WEKNORA_INTERNAL_EMBEDDING_BUSY_RETRIES',
'mrl':'CNOOC_EMBEDDING_MRL_TRUNCATION_ENABLED'}
defaults={'model':'Qwen3-Embedding-8B','dimension':'1024','upstream':'http://model.ai.cnooc/member1/qwen3-embedding-8b/v1/embeddings','retries':'3','mrl':'true'}
d={k:os.environ.get(v,defaults[k]) for k,v in names.items()}
d['relay_key_present']=bool(os.environ.get('WEKNORA_INTERNAL_MODEL_RELAY_KEY','').strip())
d['upstream_key_present']=bool(os.environ.get('CNOOC_RETRIEVAL_API_KEY','').strip())
print(json.dumps(d))
'''


def gateway_baseline():
    d=native.get('deployment','gateway','deer-flow'); s=d.get('status',{}); desired=d['spec'].get('replicas',1)
    need(1<=desired<=4 and s.get('observedGeneration',0)>=d['metadata']['generation'] and
         all(s.get(k,0)==desired for k in ('replicas','updatedReplicas','availableReplicas','readyReplicas')),
         'gateway_rollout_not_stable')
    svc=native.get('service','gateway','deer-flow'); ip=svc['spec']['clusterIP']
    need(ipaddress.ip_address(ip).version==4,'gateway_ip_family')
    need(svc['spec'].get('type','ClusterIP')=='ClusterIP' and any(x['port']==8001 for x in svc['spec']['ports']),
         'gateway_service_shape')
    endpoints=native.get('endpoints','gateway','deer-flow'); pods=[]
    for subset in endpoints.get('subsets',[]):
        need(not subset.get('notReadyAddresses') and any(x['port']==8001 for x in subset.get('ports',[])),
             'gateway_endpoints_not_ready')
        for a in subset.get('addresses',[]):
            ref=a.get('targetRef',{}); need(ref.get('kind')=='Pod','gateway_endpoint_not_pod')
            p=native.get('pod',ref['name'],'deer-flow'); need(p['metadata']['uid']==ref['uid'],'gateway_pod_uid_changed')
            need(not p['metadata'].get('deletionTimestamp'),'gateway_pod_terminating')
            c=[x for x in p['status'].get('containerStatuses',[]) if x['name']=='gateway']
            need(len(c)==1 and c[0].get('ready') and c[0].get('state',{}).get('running'),'gateway_container_not_ready')
            cfg=json.loads(native.call(['exec','-n','deer-flow',ref['name'],'-c','gateway','--',
                'python3','-I','-B','-c',GATEWAY_CONFIG],limit=4096)[1])
            need(cfg['model']=='Qwen3-Embedding-8B' and cfg['dimension']=='1024' and cfg['mrl'].lower()=='true'
                and cfg['upstream']=='http://model.ai.cnooc/member1/qwen3-embedding-8b/v1/embeddings'
                and cfg['relay_key_present'] and cfg['upstream_key_present'] and
                re.fullmatch('[0-3]',cfg['retries']),'gateway_embedding_contract_requires_review')
            pods.append(dict(name=ref['name'],uid=ref['uid'],ip=a['ip'],image_id=c[0]['imageID'],
                restarts=c[0].get('restartCount',0),contract=cfg))
    need(len(pods)==desired and len({p['uid'] for p in pods})==desired,'gateway_endpoint_coverage')
    return dict(deployment_uid=d['metadata']['uid'],generation=d['metadata']['generation'],
        spec_sha256=spec_hash(d['spec']),service_uid=svc['metadata']['uid'],service_ip=ip,
        pods=sorted(pods,key=lambda x:x['uid']))


def clean_config():
    return dict(server={'host':'127.0.0.1','port':8080,'shutdown_timeout':'15s'},
        conversation={'summary':{},'embedding_top_k':10,'enable_rerank':False},
        knowledge_base={'chunk_size':512,'chunk_overlap':0,'document_process_timeout':'300s',
                        'image_processing':{'enable_multimodal':True}},
        tenant={'enable_rbac':True},auth={'registration_mode':'self_serve','default_tenant_mode':'create_personal'},
        audit={'retention_days':0},oidc_auth={'enable':False},models=[],vector_database={'driver':'postgres'},
        docreader={'addr':''},stream_manager={'type':'memory'},extract={'extract_graph':{},'extract_entity':{},'fabri_text':{}},
        web_search={},im={},agent={})


def manifests(ns,images,ca,files,ips,tolerations,gw,secrets_data):
    need(RUNTIME_SOURCE is not None and SAMPLE_B64 is not None,'packaged_payload_missing')
    meta=lambda name:dict(name=name,namespace=ns,labels=LABEL)
    policy=dict(apiVersion='networking.k8s.io/v1',kind='NetworkPolicy',metadata=meta('isolation'),spec={
        'podSelector':{},'policyTypes':['Ingress','Egress'],'ingress':[], 'egress':[
            {'to':[{'ipBlock':{'cidr':ip+'/32'}} for ip in ips], 'ports':[{'protocol':'TCP','port':443}]},
            {'to':[{'ipBlock':{'cidr':ip+'/32'}} for ip in sorted({gw['service_ip']}|{p['ip'] for p in gw['pods']})],
             'ports':[{'protocol':'TCP','port':8001}]}]})
    cm=dict(apiVersion='v1',kind='ConfigMap',metadata=meta('probe'),immutable=True,
        data={'runtime.py':RUNTIME_SOURCE,'config.json':json.dumps(clean_config()),
              'expected.json':json.dumps({'ca_sha256':native.CA_SHA,'files':files})},
        binaryData={'ca.bin':base64.b64encode(ca).decode(),'sample.png':SAMPLE_B64})
    secret=dict(apiVersion='v1',kind='Secret',metadata=meta('fixture-only'),immutable=True,type='Opaque',
        data={k:base64.b64encode(v.encode()).decode() for k,v in secrets_data.items()})
    restricted=dict(allowPrivilegeEscalation=False,readOnlyRootFilesystem=True,capabilities={'drop':['ALL']},
        runAsNonRoot=True,runAsUser=1000,runAsGroup=1000)
    containers=[dict(name='app',image=images['app'],imagePullPolicy='Never',
        command=['python3','-I','-B','/probe/runtime.py','idle'],securityContext=restricted,
        resources={'requests':{'cpu':'1','memory':'2Gi','ephemeral-storage':'1Gi'},
                   'limits':{'cpu':'2','memory':'4Gi','ephemeral-storage':'2Gi'}},
        volumeMounts=[{'name':'probe','mountPath':'/probe','readOnly':True},
            {'name':'test-secrets','mountPath':'/test-secrets','readOnly':True},
            {'name':'work','mountPath':'/work'},{'name':'tmp','mountPath':'/tmp'}]),
        dict(name='postgres',image=images['postgres'],imagePullPolicy='Never',
            # Reset inherited image environment; mask initdb scripts with an empty volume.
            command=['/usr/bin/env','-i','PATH=/usr/lib/postgresql/16/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                'LANG=C.UTF-8','PGDATA=/var/lib/postgresql/data/pgdata','POSTGRES_USER=fixture_bootstrap',
                'POSTGRES_DB=fixture','POSTGRES_PASSWORD_FILE=/test-secrets/bootstrap',
                '/usr/local/bin/docker-entrypoint.sh','postgres','-c','listen_addresses=127.0.0.1',
                '-c','max_connections=40','-c','shared_buffers=128MB','-c','work_mem=4MB',
                '-c','shared_preload_libraries=pg_search','-c','log_statement=none'],
            securityContext={'runAsUser':0,'allowPrivilegeEscalation':False,
                'capabilities':{'drop':['ALL'],'add':['CHOWN','FOWNER','DAC_OVERRIDE','SETUID','SETGID']}},
            resources={'requests':{'cpu':'500m','memory':'1Gi','ephemeral-storage':'3Gi'},
                'limits':{'cpu':'1','memory':'2Gi','ephemeral-storage':'5Gi'}},
            volumeMounts=[{'name':'pgdata','mountPath':'/var/lib/postgresql/data'},
                {'name':'empty-init','mountPath':'/docker-entrypoint-initdb.d','readOnly':True},
                {'name':'test-secrets','mountPath':'/test-secrets','readOnly':True}]),
        dict(name='redis',image=images['redis'],imagePullPolicy='Never',command=[
            '/usr/bin/env','-i','PATH=/usr/local/bin:/usr/bin:/bin','redis-server','--bind','127.0.0.1',
            '--protected-mode','yes','--save','','--appendonly','no','--maxmemory','128mb','--maxmemory-policy','noeviction'],
            securityContext=restricted,resources={'requests':{'cpu':'100m','memory':'128Mi'},
                'limits':{'cpu':'250m','memory':'256Mi'}},volumeMounts=[{'name':'redis','mountPath':'/data'}])]
    pod=dict(apiVersion='v1',kind='Pod',metadata=meta('fixture'),spec=dict(
        nodeSelector={'kubernetes.io/hostname':'datax-01'},restartPolicy='Never',activeDeadlineSeconds=1200,
        terminationGracePeriodSeconds=20,automountServiceAccountToken=False,enableServiceLinks=False,
        dnsPolicy='None',dnsConfig={'nameservers':['127.0.0.1']},tolerations=tolerations,
        hostAliases=[{'ip':ips[0],'hostnames':[native.HOST]},
            {'ip':gw['service_ip'],'hostnames':['gateway','gateway.deer-flow.svc.cluster.local']}],
        securityContext={'fsGroup':1000,'seccompProfile':{'type':'RuntimeDefault'}},containers=containers,
        volumes=[{'name':'probe','configMap':{'name':'probe','defaultMode':0o444}},
                 {'name':'test-secrets','secret':{'secretName':'fixture-only','defaultMode':0o440}},
                 {'name':'work','emptyDir':{'medium':'Memory','sizeLimit':'512Mi'}},
                 {'name':'tmp','emptyDir':{'medium':'Memory','sizeLimit':'128Mi'}},
                 {'name':'pgdata','emptyDir':{'sizeLimit':'4Gi'}},
                 {'name':'empty-init','emptyDir':{}},{'name':'redis','emptyDir':{'sizeLimit':'128Mi'}}]))
    return policy,cm,secret,pod


def normalized(obj):
    obj=json.loads(json.dumps(obj))
    if obj['kind']=='NetworkPolicy': obj['spec'].setdefault('ingress',[])
    return obj


def validate(actual,expected,uid=None):
    actual=normalized(actual); expected=normalized(expected)
    comparable=json.loads(json.dumps(actual))
    if actual['kind']=='Pod':
        defaults=[dict(key='node.kubernetes.io/'+k,operator='Exists',effect='NoExecute',tolerationSeconds=300)
                  for k in ('not-ready','unreachable')]
        supplied=comparable['spec'].get('tolerations',[])
        comparable['spec']['tolerations']=[x for x in supplied if x not in defaults]
    need(native.subset(expected,comparable),'admission_changed_fixture')
    if uid: need(actual['metadata']['uid']==uid,'fixture_uid_changed')
    need(not actual['metadata'].get('deletionTimestamp'),'fixture_deleting')
    if actual['kind']=='NetworkPolicy':
        need(actual['spec']==expected['spec'],'network_policy_changed'); return
    if actual['kind'] in ('Secret','ConfigMap'):
        need(all(actual.get(k,{})==expected.get(k,{}) for k in ('data','binaryData')),'fixture_payload_changed')
        return
    if actual['kind']!='Pod': return
    s=actual['spec']; e=expected['spec']
    need(not any(s.get(k) for k in ('hostNetwork','hostPID','hostIPC','shareProcessNamespace',
         'initContainers','ephemeralContainers')),'fixture_privilege_changed')
    need(len(s['containers'])==3 and len(s['volumes'])==len(e['volumes']),'injected_container_or_volume')
    for a,b in zip(s['containers'],e['containers']):
        need(not any(a.get(k) for k in ('env','envFrom','args','ports','lifecycle')),'injected_container_settings')
        need(not a.get('securityContext',{}).get('privileged'),'privileged_fixture')
        need(a['securityContext'].get('capabilities',{})==b['securityContext']['capabilities'],'capabilities_changed')
        need(len(a['volumeMounts'])==len(b['volumeMounts']),'injected_volume_mount')
    need(not s.get('nodeName') or s['nodeName']=='datax-01','fixture_wrong_node')


def create(obj,label):
    record(label+'-REQUESTED.json',{'kind':obj['kind'],'name':obj['metadata']['name']})
    raw=native.call(['create','-f','-','-o','json'],json.dumps(obj).encode())[1]
    live=json.loads(raw); record(label+'-ACK.json',{'name':live['metadata']['name'],'uid':live['metadata']['uid']})
    validate(live,obj); return live


def remote(ns,mode,data=None,checked=True,timeout=40):
    bind_pod(ns)
    args=['exec']+(['-i'] if data is not None else [])+['-n',ns,'fixture','-c','app','--',
        'python3','-I','-B','/probe/runtime.py',mode]
    rc,raw=native.call(args,None if data is None else json.dumps(data).encode(),
        checked=False,timeout=timeout,limit=262144)
    try: value=json.loads(raw)
    except ValueError: value={'transport_exit':rc}
    if checked and rc:
        label=value.get('fixture_error','') if isinstance(value,dict) else ''
        need(False,'fixture_'+label if re.fullmatch('[a-z0-9_]{1,100}',label) else 'fixture_exec_failed_USE_STATUS')
    return rc,value


def bind_pod(ns):
    need(re.fullmatch('weknora-enrich-check-[a-f0-9]{16}',ns),'namespace_shape')
    ack=json.loads(audit.read(STATE/'POD-ACK.json'))
    expected=json.loads(audit.read(STATE/'POD-EXPECTED.json'))
    validate(native.get('pod','fixture',ns),expected,ack['uid'])


def sql(ns,statement):
    bind_pod(ns)
    # Only the exact isolated Pod's loopback Unix socket; no production DSN.
    raw=native.call(['exec','-i','-n',ns,'fixture','-c','postgres','--',
        '/usr/bin/env','-i','PATH=/usr/lib/postgresql/16/bin:/usr/local/bin:/usr/bin:/bin','PGCONNECT_TIMEOUT=5',
        'psql','-h','/var/run/postgresql','-p','5432','-X','-q','-t','-A',
        '-v','ON_ERROR_STOP=1','-U','fixture_bootstrap','-d','fixture'],
        statement.encode(),timeout=40,limit=262144)[1]
    return raw.decode().strip()


def wait_for(predicate,seconds,label):
    start=time.monotonic()
    while True:
        value=predicate()
        if value: return value
        need(time.monotonic()-start<seconds,label+'_deadline')
        emit({'full_enrichment':'WAIT','stage':label,'elapsed_seconds':int(time.monotonic()-start)})
        time.sleep(5)


def schema_receipt(ns):
    return json.loads(sql(ns,"SELECT json_build_object('version',version,'dirty',dirty,"+
        "'extensions',(SELECT json_agg(extname) FROM pg_extension),"+
        "'knowledge_rows',(SELECT count(*) FROM knowledges),'model_rows',(SELECT count(*) FROM models)) FROM schema_migrations;"))


def grants(ns,password):
    need(re.fullmatch('[a-z0-9]{64}',password) is not None,'test_password_shape')
    sql(ns,"CREATE ROLE fixture_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 24 PASSWORD '"+password+"';\n"
        "REVOKE CONNECT ON DATABASE fixture FROM PUBLIC; GRANT CONNECT ON DATABASE fixture TO fixture_app;\n"
        "GRANT USAGE ON SCHEMA public TO fixture_app;\n"
        "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO fixture_app;\n"
        "GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA public TO fixture_app;\n"
        "REVOKE ALL ON schema_migrations FROM fixture_app;\n")
    r=json.loads(sql(ns,"SELECT json_build_object('superuser',rolsuper,'createdb',rolcreatedb,'createrole',rolcreaterole,"+
        "'connection_limit',rolconnlimit) FROM pg_roles WHERE rolname='fixture_app';"))
    need(r=={'superuser':False,'createdb':False,'createrole':False,'connection_limit':24},'app_role_not_least_privilege')
    return r


def healthy(ns,role):
    current=remote(ns,'status',checked=False)[1]
    need('STOPPED-'+role+'.json' not in current,'isolated_'+role+'_exited_before_health')
    return remote(ns,'health',checked=False)[0]==0


def index_receipt(ns,kid,kb):
    need(all(re.fullmatch('[a-f0-9-]{36}',v) for v in (kid,kb)),'test_record_id_shape')
    query="""SELECT json_build_object(
      'knowledge_id', k.id, 'kb_id', k.knowledge_base_id, 'parse_status',k.parse_status,
      'chunks',COALESCE((SELECT json_agg(json_build_object('id',c.id,'kind',c.chunk_type,
        'parent_id',c.parent_chunk_id,'characters',length(c.content),
        'has_orbit',position('ORBIT' in upper(c.content))>0,'has_number',position('7426' in c.content)>0,
        'has_blue',position('blue' in lower(c.content))>0,
        'parent_bound',EXISTS(SELECT 1 FROM chunks p WHERE p.id=c.parent_chunk_id AND p.knowledge_id=k.id),
        'vectors',(SELECT count(*) FROM embeddings e WHERE e.chunk_id=c.id AND e.knowledge_id=k.id
          AND e.knowledge_base_id=k.knowledge_base_id AND e.dimension=1024 AND vector_dims(e.embedding)=1024
          AND e.embedding IS NOT NULL))) FROM chunks c WHERE c.knowledge_id=k.id
          AND c.knowledge_base_id=k.knowledge_base_id AND c.deleted_at IS NULL),'[]'::json))
      FROM knowledges k WHERE k.id='%s' AND k.knowledge_base_id='%s' AND k.deleted_at IS NULL;"""%(kid,kb)
    return json.loads(sql(ns,query))


def index_pass(r):
    rows=r.get('chunks',[]); children=[x for x in rows if x.get('kind') in ('image_ocr','image_caption')]
    return (r.get('parse_status')=='completed' and len(children)==2 and
        {c['kind'] for c in children}=={'image_ocr','image_caption'} and
        all(c['characters']>0 and c['parent_bound'] and c['vectors']>=1 for c in children) and
        any(c['kind']=='image_ocr' and c['has_orbit'] and c['has_number'] for c in children) and
        any(c['kind']=='image_caption' and c['has_blue'] for c in children))


def search_pass(result,index):
    children={x['id'] for x in index['chunks'] if x['kind'] in ('image_ocr','image_caption')}
    return any(x.get('knowledge_id')==index['knowledge_id'] and
        (x.get('id') in children or bool(children.intersection(x.get('sub_chunk_id') or [])))
        for x in result.get('matches',[]))


def cleanup(ns):
    need(re.fullmatch('weknora-enrich-check-[a-f0-9]{16}',ns),'namespace_shape')
    ack=json.loads(audit.read(STATE/'NAMESPACE-ACK.json'))
    need(ack['name']==ns,'cleanup_name_changed')
    live=native.get('namespace',ns,missing=True)
    if live:
        need(live['metadata']['uid']==ack['uid'],'cleanup_uid_changed')
        if not (STATE/'CLEANUP-REQUESTED.json').exists():
            record('CLEANUP-REQUESTED.json',{'namespace':ns,'uid':ack['uid']})
        if not live['metadata'].get('deletionTimestamp'):
            raw={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':ack['uid']},'propagationPolicy':'Foreground'}
            native.call(['delete','--raw','/api/v1/namespaces/'+ns,'-f','-'],json.dumps(raw).encode())
            if not (STATE/'CLEANUP-ACK.json').exists(): record('CLEANUP-ACK.json',{'namespace':ns,'uid':ack['uid']})
        def absent():
            n=native.get('namespace',ns,missing=True)
            if n: need(n['metadata']['uid']==ack['uid'],'cleanup_uid_changed')
            return n is None
        wait_for(absent,90,'TEST_NAMESPACE_CLEANUP')
    if not (STATE/'CLEANUP-VERIFIED.json').exists(): record('CLEANUP-VERIFIED.json',{'namespace':ns,'uid':ack['uid'],'absent':True})
    return 'NAMESPACE_ABSENT_VERIFIED'


def capture_failure(ns):
    # Capture safe aggregates before deleting the disposable resources, even for
    # early startup failures. Do not print raw PostgreSQL or provider stderr.
    captured={}
    try:
        p=native.get('pod','fixture',ns); ack=json.loads(audit.read(STATE/'POD-ACK.json'))
        need(p['metadata']['uid']==ack['uid'],'fixture_uid_changed')
        captured['containers']=[{'name':c['name'],'restarts':c.get('restartCount',0),
            'states':list(c.get('state',{})),
            'exit_code':c.get('state',{}).get('terminated',{}).get('exitCode')}
            for c in p.get('status',{}).get('containerStatuses',[])]
        captured['runtime']=remote(ns,'status',checked=False)[1]
        remote(ns,'stop-all',checked=False)
        time.sleep(3)
        captured['runtime_after_stop']=remote(ns,'status',checked=False)[1]
    except Exception as exc: captured['capture_error_type']=type(exc).__name__
    for container in ('postgres','app','redis'):
        try:
            rc,raw=native.call(['logs','-n',ns,'fixture','-c',container,'--tail=100','--limit-bytes=32768'],checked=False,limit=65536)
            text=raw.decode('utf-8','replace')
            captured[container+'_startup']={'exit_code':rc,'bytes':len(raw),'raw_saved':False,
                'missing_library':bool(re.search('could not access file|cannot open shared object',text)),
                'permission_denied':bool(re.search('Permission denied|permission denied',text)),
                'fixture_error':re.findall(r'"fixture_error": "([A-Za-z0-9_]{1,100})"',text),
                'database_ready':bool(re.search('database system is ready to accept connections',text))}
        except Exception as exc: captured[container+'_startup']={'error_type':type(exc).__name__}
    record('FAILURE-DIAGNOSTICS.json',captured)


def run():
    need(not os.path.lexists(str(STATE)),'previous_attempt_exists_USE_STATUS')
    audit.directory(STATE.parent); STATE.mkdir(mode=0o700)
    report=dict(native_enrichment_acceptance='UNCONFIRMED',ready_for_rollout=False,
        production_writes=0,production_workloads_mutated=False,production_database_connections=0,
        production_redis_connections=0,model_changed=False,consumer_slots_changed=False,
        build_started=False,images_imported=False,document_submission_attempted=False,
        provider_request_budget=REQUEST_BUDGET,model_credentials_scope='DISPOSABLE_TEST_DB_ONLY_UNTIL_CLEANUP',
        upstream_embedding_key_read=False,automatic_resubmission=False,cleanup='NOT_REQUESTED',
        network_policy_enforcement='NOT_INDEPENDENTLY_VERIFIED',raw_logs_saved=False)
    ns=None; guard=None; plan=None; gw=None
    try:
        phase('BIND_COMPLETED_BYTE_NATIVE_AND_PRELOAD_RECEIPTS')
        b=read_json(BYTE_REPORT,BYTE_SHA)
        need(b.get('classification',{}).get('byte_differences_explained') is True,'runtime_byte_receipt')
        need(read_json(audit.NATIVE,audit.NATIVE_SHA).get('native_glm_acceptance')=='PASS','native_receipt')
        need(read_json(audit.STORE,audit.STORE_SHA).get('enrichment_store_preload_recovery')=='PASS','store_receipt')
        p,q=native.initialize(); guard,plan,ca,files,ips,_=native.preflight(p,q)
        stores=frozen.load(); images={'app':p.IMAGE}
        for item in stores.ITEMS:
            stores.image_check(item,pinned=True); images[item['role']]=item['image']
        phase('FRESH_RESOURCE_AND_COMPLETED_GATEWAY_ROLLOUT_REVIEW')
        resources,tolerations=capacity(); gw=gateway_baseline()
        record('PREPARED.json',{'resources':resources,'gateway':gw,'request_budget':REQUEST_BUDGET})
        ns='weknora-enrich-check-'+uuid.uuid4().hex[:16]
        record('STARTED.json',{'namespace':ns,'images':images})
        passwords={k:secrets.token_hex(32) for k in ('bootstrap','app','jwt')}; passwords['aes']=secrets.token_hex(16)
        policy,cm,sec,pod=manifests(ns,images,ca,files,ips,tolerations,gw,passwords)
        record('POD-EXPECTED.json',pod)
        phase('CREATE_DISPOSABLE_STORES_AND_APP_NO_PROVIDER_KEYS')
        create({'apiVersion':'v1','kind':'Namespace','metadata':{'name':ns,'labels':LABEL}},'NAMESPACE')
        for obj,label in ((policy,'POLICY'),(cm,'CONFIG'),(sec,'TEST-SECRET'),(pod,'POD')):
            dry=json.loads(native.call(['create','--dry-run=server','-f','-','-o','json'],json.dumps(obj).encode())[1])
            validate(dry,obj); created=create(obj,label)
        pod_uid=created['metadata']['uid']
        def running():
            live=native.get('pod','fixture',ns); validate(live,pod,pod_uid)
            need(live.get('status',{}).get('phase') not in ('Failed','Succeeded'),'fixture_terminated')
            statuses=live.get('status',{}).get('containerStatuses',[])
            need(not any(c.get('state',{}).get('terminated') for c in statuses),'fixture_container_exited')
            if len(statuses)!=3 or not all(c.get('state',{}).get('running') for c in statuses): return False
            need(all(c.get('restartCount')==0 for c in statuses),'fixture_restarted')
            actual={c['name']:c['imageID'] for c in statuses}
            expected={'app':p.CONFIG,'postgres':'sha256:5656e1d666225b9ee09c83684b3f1363d6b216bab498d11e0a186a21e175b85b',
                'redis':'sha256:755105238729ef40393b21c3e7e2fb6ee886fc504e04718a4133ca2bc1aa294a'}
            # CRI may expose a pullable manifest reference rather than the config ID.
            for name,digest in expected.items():
                refs={digest,images[name],images[name]+'@'+(p.MANIFEST if name=='app' else
                    'sha256:f094a1fe0d28a507315e482498087e8a6934e666fecaa7cfeb041ee79d84811d' if name=='postgres' else
                    'sha256:4c609b1ccae44c2a0f7b3eca475af5d0f1f02f8fb7df3970ee4d7ab6a5590f56')}
                need(actual.get(name) in refs,'fixture_image_identity_changed')
            return remote(ns,'status',checked=False)[1].get('READY.json',{}).get('runtime_files')=='PASS'
        wait_for(running,180,'FIXTURE_READY')
        # pg_isready is a connection attempt ONLY to the disposable loopback DB.
        wait_for(lambda:native.call(['exec','-n',ns,'fixture','-c','postgres','--','pg_isready',
            '-h','127.0.0.1','-p','5432','-U','fixture_bootstrap','-d','fixture'],checked=False)[0]==0,120,'TEST_POSTGRES_READY')
        phase('NATIVE_FRESH_SCHEMA_MIGRATION')
        remote(ns,'bootstrap')
        wait_for(lambda:healthy(ns,'bootstrap'),180,'BOOTSTRAP_APP_HEALTH')
        schema=schema_receipt(ns); record('SCHEMA.json',schema)
        need(schema.get('version')==90 and schema.get('dirty') is False and schema.get('knowledge_rows')==0
            and schema.get('model_rows')==0 and {'vector','pg_search','pg_trgm','uuid-ossp'}<=set(schema['extensions']),
            'fresh_schema_not_accepted')
        remote(ns,'stop-bootstrap')
        wait_for(lambda:remote(ns,'status')[1].get('STOPPED-bootstrap.json'),40,'BOOTSTRAP_STOP')
        record('APP-ROLE.json',grants(ns,passwords['app'])); del passwords
        phase('START_NON_SUPERUSER_FULL_APP')
        remote(ns,'app'); wait_for(lambda:healthy(ns,'app'),150,'APP_HEALTH')
        need(gateway_baseline()==gw,'gateway_changed_before_provider_boundary'); guard.live(plan,binary=True)
        # Only this scoped relay key is read, never CNOOC_RETRIEVAL_API_KEY or whole env/Secret.
        key_script='import os,sys;sys.stdout.write(os.environ["WEKNORA_INTERNAL_MODEL_RELAY_KEY"].strip())'
        relay=native.call(['exec','-n','deer-flow',gw['pods'][0]['name'],'-c','gateway','--',
            'python3','-I','-B','-c',key_script],limit=4096)[1].decode()
        need(re.fullmatch(r'[!-~]{8,4096}',relay) is not None,'relay_credential_shape')
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            glm=getpass.getpass('GLM API key (hidden; temporary isolated test DB only; removed with fixture): ')
        phase('CONFIGURE_TEST_MODELS_AND_UPLOAD_ONE_SYNTHETIC_IMAGE')
        setup=remote(ns,'setup',{'glm_key':glm,'relay_key':relay},timeout=60)[1]; del glm,relay
        record('TEST-MODELS.json',setup)
        record('PROVIDER-REQUESTED.json',{'pod_uid':pod_uid,'budget':REQUEST_BUDGET})
        report['document_submission_attempted']=True
        uploaded=remote(ns,'upload',timeout=80)[1]; record('UPLOAD.json',uploaded)
        phase('WAIT_REAL_ASYNQ_GLM_CHUNKS_AND_1024D_INDEX')
        last={}
        def indexed():
            last.clear(); last.update(index_receipt(ns,uploaded['knowledge_id'],uploaded['kb_id']))
            return index_pass(last)
        try: wait_for(indexed,330,'REAL_INDEX_ACCEPTANCE')
        finally: record('INDEX.json',last)
        phase('REAL_QUERY_EMBEDDING_AND_VECTOR_ONLY_RETRIEVAL')
        result=remote(ns,'search',timeout=110)[1]; record('SEARCH.json',result)
        need(search_pass(result,last),'real_vector_search_missed_enriched_chunks')
        remote(ns,'stop-app')
        stopped=wait_for(lambda:remote(ns,'status')[1].get('STOPPED-app.json'),40,'TEST_APP_STOP')
        record('RUNTIME.json',stopped)
        need(not stopped.get('logs_truncated') and stopped['classes']['multimodal_enqueued']==1 and
             stopped['classes']['multimodal_started']==1 and stopped['classes']['ocr_chunk_created']==1 and
             stopped['classes']['caption_chunk_created']==1 and not stopped['classes']['panic'],
             'asynq_runtime_evidence_incomplete')
        phase('POST_ACCEPTANCE_PRODUCTION_BASELINE')
        guard.live(plan,binary=True); need(gateway_baseline()==gw,'gateway_changed_during_acceptance')
        report.update(native_enrichment_acceptance='PASS',native_schema_migration='PASS',
            real_asynq_glm_enrichment='PASS',real_embedding_1024d_index='PASS',vector_only_retrieval='PASS',
            production_baseline='UNCHANGED',synthetic_sample_only=True,business_documents='NOT_TESTED')
    except BaseException as exc:
        report.update(failed_stage=PHASE,error_type=type(exc).__name__,failed_check=str(exc)
            if isinstance(exc,native.AcceptanceFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            last_operation=dict(native.LAST),raw_error_printed=False,
            code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                         for x in traceback.extract_tb(exc.__traceback__)[-6:]])
        if ns and (STATE/'POD-ACK.json').exists():
            try: capture_failure(ns)
            except Exception: report['safe_diagnostic_capture']='UNAVAILABLE'
    finally:
        # Test-only, UID-bound cleanup also stops any unresolved test request.
        # No retry/rollback/import/production mutation is performed.
        if ns and (STATE/'NAMESPACE-ACK.json').exists():
            try: report['cleanup']=cleanup(ns)
            except Exception: report['cleanup']='UNCONFIRMED_USE_STATUS'; report['native_enrichment_acceptance']='UNCONFIRMED'
        if guard is not None and gw is not None and report.get('production_baseline')!='UNCHANGED':
            try:
                guard.live(plan,binary=True); need(gateway_baseline()==gw,'gateway_changed')
                report['production_baseline']='UNCHANGED'
            except Exception: report['production_baseline']='RECHECK_UNCONFIRMED'
    record('RESULT.json',report)
    emit(dict(report,report=str(STATE/'RESULT.json'),report_sha256=spec_file_digest(STATE/'RESULT.json')))
    return 0 if report['native_enrichment_acceptance']=='PASS' else 1


def spec_file_digest(p): return hashlib.sha256(audit.read(p)).hexdigest()


def status():
    output=dict(full_enrichment_status='READ_ONLY',production_writes=0,provider_requests=0,files_modified=False,
        automatic_retry=False,ready_for_rollout=False,state=str(STATE))
    if STATE.exists():
        audit.directory(STATE)
        for name in ('RESULT.json','STARTED.json','PROVIDER-REQUESTED.json','CLEANUP-ACK.json','CLEANUP-VERIFIED.json','FAILURE-DIAGNOSTICS.json'):
            if (STATE/name).exists(): output[name]=json.loads(audit.read(STATE/name))
        if 'STARTED.json' in output:
            ns=output['STARTED.json']['namespace']; need(re.fullmatch('weknora-enrich-check-[a-f0-9]{16}',ns),'namespace_shape')
            n=native.get('namespace',ns,missing=True)
            output['namespace_present']=n is not None
            if n and (STATE/'NAMESPACE-ACK.json').exists():
                need(n['metadata']['uid']==json.loads(audit.read(STATE/'NAMESPACE-ACK.json'))['uid'],'namespace_uid_changed')
                output['namespace_terminating']=bool(n['metadata'].get('deletionTimestamp'))
    emit(output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--accept-once',action='store_true'); group.add_argument('--status',action='store_true')
    group.add_argument('--cleanup-only',action='store_true',help='UID-bound deletion of this attempt only; no requests or retry')
    args=parser.parse_args()
    need(os.name=='posix' and os.geteuid()==0 and platform.machine()=='aarch64' and
         socket.gethostname().split('.')[0]=='datax-01','root_arm64_datax01_required')
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    def interrupt(signum,frame): raise native.AcceptanceFailure('interrupted_USE_STATUS')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,interrupt)
    if args.status: status(); return 0
    if args.cleanup_only:
        ns=json.loads(audit.read(STATE/'STARTED.json'))['namespace']
        emit({'cleanup':cleanup(ns),'production_writes':0,'provider_requests':0,'original_result_modified':False})
        return 0
    return run()


if __name__=='__main__': sys.exit(main())
