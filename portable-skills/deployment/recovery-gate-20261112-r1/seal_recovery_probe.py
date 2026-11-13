"""Build a new fixed-thread readonly operator from already audited transport helpers."""
import ast
import base64
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PREVIOUS=HERE.parent/'public-runtime-20261112-r1'

HOST_EXTRA=r'''
def skill_roots(pod):
    volumes={row['name']:row for row in pod['spec'].get('volumes',[])}
    mains=[row for row in pod['spec'].get('containers',[]) if row.get('name')=='gateway'];need(len(mains)==1,'ONE_GATEWAY_CONTAINER_REQUIRED')
    roots=[]
    for mount in mains[0].get('volumeMounts',[]):
        claim=volumes.get(mount.get('name'),{}).get('persistentVolumeClaim',{}).get('claimName','')
        if 'skill' not in claim:continue
        path=mount.get('mountPath');sub=mount.get('subPath','')
        need(isinstance(path,str) and path.startswith('/') and str(PurePosixPath(path))==path and '..' not in PurePosixPath(path).parts and not mount.get('subPathExpr'),'SKILLS_PVC_MOUNT_SHAPE')
        if sub=='public':candidate=PurePosixPath(path)/'energy-market-daily'
        elif sub=='public/energy-market-daily':candidate=PurePosixPath(path)
        elif not sub:candidate=PurePosixPath(path)/'public/energy-market-daily'
        else:continue
        # Never accept a deeper overlay for the selected official Skill subtree.
        for other in mains[0].get('volumeMounts',[]):
            target=PurePosixPath(other['mountPath'])
            need(not (target!=PurePosixPath(path) and (target==candidate or candidate in target.parents or (PurePosixPath(path) in target.parents and target in candidate.parents))),'SKILL_SOURCE_SHADOW_MOUNT')
        roots.append(str(candidate))
    need(len(roots)<=4,'SKILL_ROOT_BOUND');return sorted(set(roots))

class Operation:
    def __init__(self,child):self.child,self.pod,self.rs,self.payload,self.queries=child,None,None,None,0
    def command(self,args,body=None):
        allowed=args==LIST or args==['get','deployment','gateway','-o','json']
        if self.pod:
            allowed=allowed or args==['get','pod',self.pod,'-o','json']
            if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
            if args==['exec','-i',self.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.child]:
                allowed=self.payload is not None and body==canonical(self.payload)
        need(allowed and (args[0]=='exec' or body is None),'READONLY_FIXED_COMMAND_REQUIRED');need(self.queries<7,'QUERY_BOUND');self.queries+=1
        timeout=55 if args[0]=='exec' else 25;prefix=list(KUBE)
        if args[0]=='exec':prefix[2]='--request-timeout=50s'
        with tempfile.TemporaryFile() as incoming,tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            if body is not None:incoming.write(body);incoming.seek(0)
            process=subprocess.Popen(prefix+args,stdin=incoming if body is not None else subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,
                env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+timeout
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
    out={'schema':'energy-recovery-gate-inspection.v1','status':'INCOMPLETE','thread_id':THREAD,'workload_modified':False,'database_readonly':True,
        'database_modified':False,'database_credentials_used':False,'credentials_exported':False,'provider_requests':0,'files_modified':False,'secret_api_reads':0,
        'new_sandboxes_created':0,'task_retry_requested':False,'gate_modified':False,'raw_messages_or_news_exported':False}
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        op=Operation(assets());dep=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(dep['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_DEPLOYMENT_CHANGED')
        dep_identity={'uid':GATEWAY_UID,'generation':dep['metadata'].get('generation'),'spec_sha256':digest(canonical(dep['spec']))}
        chosen=candidates(op.command(LIST))[0];op.pod=chosen[0]
        pod=json.loads(op.command(['get','pod',op.pod,'-o','json']));before=identity(pod,chosen[1])
        controllers=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
        need(len(controllers)==1 and controllers[0].get('kind')=='ReplicaSet' and re.fullmatch('gateway-[a-z0-9-]+',controllers[0].get('name','')),'GATEWAY_REPLICASET_REQUIRED')
        op.rs=controllers[0]['name'];rs=json.loads(op.command(['get','replicaset',op.rs,'-o','json']))
        op.payload={'skill_roots':skill_roots(pod)}
        out.update(gateway=dep_identity,sample=before,replica_set=ownership(pod,rs,GATEWAY_UID),skill_source_candidate_count=len(op.payload['skill_roots']))
        result=json.loads(op.command(['exec','-i',op.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',op.child],canonical(op.payload)))
        out.update(observation=result,database_credentials_used=result.get('database_credentials_used') is True)
        final=json.loads(op.command(['get','pod',op.pod,'-o','json']));dep_after=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(before==identity(final,chosen[1]) and pod['metadata'].get('ownerReferences')==final['metadata'].get('ownerReferences') and digest(canonical(dep_after['spec']))==dep_identity['spec_sha256'] and dep_after['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_CHANGED_DURING_READ')
        need(result.get('status')=='NAMED_THREAD_RECOVERY_EVIDENCE_OBSERVED_NO_CHANGE','RECOVERY_CHILD_INCOMPLETE')
        out.update(status='NAMED_THREAD_RECOVERY_EVIDENCE_OBSERVED_NO_CHANGE',stable_gateway_readback=True)
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_INSPECTION_FAILED_REDACTED')
    out.update(queries=op.queries if op else 0,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
'''

def source_host():
    original=(PREVIOUS/'inspect-energy-daily-persisted-thread-20261112-r1.py').read_text(encoding='utf-8')
    tree=ast.parse(original);nodes=[]
    wanted={'Refused','need','canonical','digest','assets','candidates','identity','ownership'}
    for node in tree.body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in wanted:
            nodes.append(ast.get_source_segment(original,node))
        elif isinstance(node,ast.Assign) and any(isinstance(name,ast.Name) and name.id in {'THREAD','GATEWAY_UID','ASSETS','KUBE','LIST'} for name in node.targets):
            nodes.append(ast.get_source_segment(original,node))
    raw=('"""Read fixed-thread recovery evidence without retrying or changing a task."""\n'+'\n\n'.join(nodes)+HOST_EXTRA).encode()
    return raw.replace(b"THREAD='201fb3d5-49c0-4276-9e3f-3db67f8edfe7'",b"THREAD='0fb37c34-d996-4d8b-aeeb-ea9dcd252a28'")

def source_safety():
    original=(PREVIOUS/'inspect_persisted_child.py').read_text(encoding='utf-8');nodes=[]
    for node in ast.parse(original).body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in {'Refused','need','audit','runtime_modules'}:
            nodes.append(ast.get_source_segment(original,node))
        elif isinstance(node,ast.Assign) and any(isinstance(name,ast.Name) and name.id in {'DB_HOST','DB_PORT','DB_ADDRESSES','ALLOW_DB_DNS'} for name in node.targets):
            nodes.append(ast.get_source_segment(original,node))
    return ('\n\n'.join(nodes)+'\n').encode()

def main():
    raw=source_host();host=HERE/'inspect-energy-daily-recovery-gate-20261112-r1.py'
    with host.open('xb') as stream:stream.write(raw)
    safety=source_safety()
    child=(HERE/'inspect_recovery_child.py').read_bytes()
    for name,value in [('SAFETY_B64',base64.b64encode(safety).decode()),('SAFETY_SHA',hashlib.sha256(safety).hexdigest())]:
        needle=(name+"=''\n").encode()
        if child.count(needle)!=1:raise ValueError('UNSEALED_CHILD_REQUIRED')
        child=child.replace(needle,(name+'='+repr(value)+'\n').encode())
    compile(child,'<fixed-recovery-child>','exec')
    assets={'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    if raw.count(b'ASSETS={}'):sealed=raw.replace(b'ASSETS={}',b'ASSETS='+repr(assets).encode())
    else:raise ValueError('UNSEALED_HOST_REQUIRED')
    compile(sealed,str(host),'exec')
    directory=ROOT/'release/energy-daily-recovery-gate-20261112-r1';directory.mkdir(parents=True,exist_ok=True)
    target=directory/host.name
    with target.open('xb') as stream:stream.write(sealed)
    meta={'schema':'energy-recovery-probe-package.v1','filename':target.name,'bytes':len(sealed),'sha256':hashlib.sha256(sealed).hexdigest(),'child_sha256':hashlib.sha256(child).hexdigest(),'production_executed':False}
    with target.with_suffix('.package.json').open('x',encoding='utf-8') as stream:json.dump(meta,stream,indent=2);stream.write('\n')
    print(json.dumps(meta,sort_keys=True))

if __name__=='__main__':main()
