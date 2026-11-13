"""Seal a small readonly retention and node-0015 workload metadata inspector."""
import ast
import base64
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PREVIOUS=HERE.parent/'public-runtime-20261112-r1/inspect-energy-daily-persisted-thread-20261112-r1.py'

HOST_EXTRA=r'''
NODE_PODS=['get','pods','-A','--field-selector=spec.nodeName=ecs-zdz-pro-0015,status.phase!=Succeeded,status.phase!=Failed','--no-headers','-o',
    'custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,OWNER_KIND:.metadata.ownerReferences[?(@.controller==true)].kind,OWNER:.metadata.ownerReferences[?(@.controller==true)].name,CPU:.spec.containers[*].resources.requests.cpu,MEMORY:.spec.containers[*].resources.requests.memory,COMPONENT:.metadata.labels.app\\.kubernetes\\.io/component']

def identifier(value):
    need(isinstance(value,str) and (value=='<none>' or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,252}',value)),'METADATA_IDENTIFIER_SHAPE')
    return re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}','[id]',value)

def quantities(value):
    if value=='<none>':return []
    need(len(value)<=1024,'RESOURCE_COLUMN_BOUND');parts=value.split(',')
    need(len(parts)<=16 and all(re.fullmatch(r'[0-9][0-9.eE+-]*(?:[A-Za-z]{1,3})?',part) for part in parts),'RESOURCE_QUANTITY_SHAPE')
    return parts

def workload_groups(raw):
    need(isinstance(raw,str) and len(raw.encode())<=262144,'NODE_POD_PROJECTION_BOUND')
    groups={};total=0
    for line in raw.splitlines():
        fields=line.split();need(len(fields)==7,'NODE_POD_ROW_SHAPE');total+=1;need(total<=256,'NODE_POD_COUNT_BOUND')
        namespace,name,kind,owner,cpu,memory,component=fields
        cpu,memory=quantities(cpu),quantities(memory)
        if name.startswith('sandbox-') or component=='sandbox':
            row={'workload_type':'sandbox','container_cpu_requests':cpu,'container_memory_requests':memory}
        else:
            namespace,name,owner,component=map(identifier,(namespace,name,owner,component))
            need(kind in ('<none>','ReplicaSet','DaemonSet','StatefulSet','Job','CronJob','ReplicationController','Pod'),'OWNER_KIND_SHAPE')
            # Non-sandbox standalone maintenance names may contain operation IDs.
            if owner=='<none>':owner=re.sub(r'(?<=-)[0-9a-f]{8,64}$','[id]',name)
            row={'workload_type':'non_sandbox','namespace':namespace,'owner_kind':kind if kind!='<none>' else 'StandalonePod',
                'owner':owner,'component':component if component!='<none>' else None,'container_cpu_requests':cpu,'container_memory_requests':memory}
        key=canonical(row);groups.setdefault(key,dict(row,pod_count=0))['pod_count']+=1
    return {'node':'ecs-zdz-pro-0015','nonterminated_pod_count':total,'groups':sorted(groups.values(),key=lambda row:(row['workload_type'],row.get('namespace',''),row.get('owner',''),str(row['container_cpu_requests']))),
        'resource_basis':'REGULAR_CONTAINER_REQUEST_COLUMNS_ONLY_NOT_SCHEDULER_EFFECTIVE_TOTAL','init_overhead_and_pod_level_not_in_projection':True,
        'sandbox_names_threads_or_owner_exported':False,'idle_or_cleanup_eligibility_not_inferred':True}

class Operation:
    def __init__(self,child):self.child,self.pod,self.rs,self.queries=child,None,None,0
    def command(self,args):
        allowed=args==LIST or args==['get','deployment','gateway','-o','json'] or args==NODE_PODS
        if self.pod:allowed=allowed or args==['get','pod',self.pod,'-o','json'] or args==['exec',self.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.child]
        if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
        need(allowed,'FIXED_READONLY_COMMAND_REQUIRED');need(self.queries<8,'QUERY_BOUND');self.queries+=1
        prefix=KUBE[:-2] if args==NODE_PODS else KUBE
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            process=subprocess.Popen(prefix+args,stdin=subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,
                env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+25
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size<=1048576 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                    need(time.monotonic()<until,'READ_DEADLINE');time.sleep(.1)
                need(process.returncode in ((0,1) if args[0]=='exec' else (0,)),'READ_COMMAND_FAILED')
                need(os.fstat(output.fileno()).st_size<=1048576 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                output.seek(0);return output.read(1048577).decode()
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspect-readonly',required=True,action='store_true');parser.parse_args()
    op=None
    out={'schema':'sandbox-retention-and-node-occupants.v1','status':'INCOMPLETE','workload_modified':False,'files_modified':False,'database_access':False,
        'secret_api_reads':0,'credentials_exported':False,'provider_requests':0,'lease_files_read_or_acquired':False,'cleanup_requested':False,
        'sandboxes_created':0,'sandboxes_deleted':0,'policy_is_fresh_config_not_live_provider_state':True}
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        op=Operation(assets());dep=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(dep['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_DEPLOYMENT_CHANGED')
        dep_identity={'uid':GATEWAY_UID,'generation':dep['metadata'].get('generation'),'spec_sha256':digest(canonical(dep['spec']))}
        chosen=candidates(op.command(LIST))[0];op.pod=chosen[0]
        pod=json.loads(op.command(['get','pod',op.pod,'-o','json']));before=identity(pod,chosen[1])
        owners=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
        need(len(owners)==1 and owners[0].get('kind')=='ReplicaSet' and re.fullmatch('gateway-[a-z0-9-]+',owners[0].get('name','')),'GATEWAY_REPLICASET_REQUIRED')
        op.rs=owners[0]['name'];rs=json.loads(op.command(['get','replicaset',op.rs,'-o','json']))
        out.update(gateway=dep_identity,sample=before,replica_set=ownership(pod,rs,GATEWAY_UID))
        policy=json.loads(op.command(['exec',op.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',op.child]));out['retention_policy']=policy
        need(policy.get('status')=='SANDBOX_RETENTION_CONFIGURATION_OBSERVED_NO_CHANGE','FRESH_CONFIG_READ_INCOMPLETE')
        out['node_occupants']=workload_groups(op.command(NODE_PODS))
        final=json.loads(op.command(['get','pod',op.pod,'-o','json']));dep_after=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(before==identity(final,chosen[1]) and pod['metadata'].get('ownerReferences')==final['metadata'].get('ownerReferences') and digest(canonical(dep_after['spec']))==dep_identity['spec_sha256'] and dep_after['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_CHANGED_DURING_READ')
        out.update(status='SANDBOX_RETENTION_AND_NODE_OCCUPANTS_OBSERVED_NO_CHANGE',stable_gateway_readback=True)
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_INSPECTION_FAILURE_REDACTED')
    out.update(queries=op.queries if op else 0,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat());print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
'''

def source_host():
    original=PREVIOUS.read_text(encoding='utf-8');nodes=[]
    for node in ast.parse(original).body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in {'Refused','need','canonical','digest','assets','candidates','identity','ownership'}:nodes.append(ast.get_source_segment(original,node))
        elif isinstance(node,ast.Assign) and any(isinstance(name,ast.Name) and name.id in {'GATEWAY_UID','ASSETS','KUBE','LIST'} for name in node.targets):nodes.append(ast.get_source_segment(original,node))
    return ('"""Readonly fresh sandbox retention policy and node-0015 workload metadata."""\n'+'\n\n'.join(nodes)+HOST_EXTRA).encode()

def main():
    raw=source_host();host=HERE/'inspect-sandbox-retention-20261112-r1.py'
    with host.open('xb') as stream:stream.write(raw)
    child=(HERE/'inspect_retention_child.py').read_bytes();compile(child,'<fixed-retention-reader>','exec')
    assets={'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    if raw.count(b'ASSETS={}')!=1:raise ValueError('UNSEALED_HOST_REQUIRED')
    sealed=raw.replace(b'ASSETS={}',b'ASSETS='+repr(assets).encode());compile(sealed,str(host),'exec')
    target=ROOT/'release/sandbox-retention-20261112-r1'/host.name;target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as stream:stream.write(sealed)
    meta={'schema':'sandbox-retention-package.v1','filename':target.name,'bytes':len(sealed),'sha256':hashlib.sha256(sealed).hexdigest(),'child_sha256':hashlib.sha256(child).hexdigest(),'production_executed':False}
    with target.with_suffix('.package.json').open('x',encoding='utf-8') as stream:json.dump(meta,stream,indent=2);stream.write('\n')
    print(json.dumps(meta,sort_keys=True))

if __name__=='__main__':main()
