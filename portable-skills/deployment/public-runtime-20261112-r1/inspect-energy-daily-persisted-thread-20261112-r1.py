"""Read one thread's persistent stored market inputs without creating a sandbox.

Uses only an existing Ready Gateway and indexed readonly owner queries.
The configured database credential is used internally, never printed/exported.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import signal
import subprocess
import sys
import tempfile
import time

THREAD='201fb3d5-49c0-4276-9e3f-3db67f8edfe7'
GATEWAY_UID='a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
ASSETS={}
KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=15s','-n','deer-flow']
LIST=['get','pods','-l','app=gateway','--no-headers','-o','custom-columns=NAME:.metadata.name,UID:.metadata.uid,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp,READY:.status.containerStatuses[?(@.name=="gateway")].ready']


class Refused(ValueError):pass


def need(ok,code):
    if not ok:raise Refused(code)


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def digest(raw):return hashlib.sha256(raw).hexdigest()


def assets():
    need(set(ASSETS)=={'child'},'ASSETS_NOT_SEALED')
    item=ASSETS['child'];raw=base64.b64decode(item['base64'],validate=True)
    need(len(raw)<=262144 and digest(raw)==item['sha256'],'CHILD_ASSET_CHANGED')
    compile(raw,'<fixed-persisted-child>','exec');return raw.decode()


def candidates(raw):
    need(isinstance(raw,str) and len(raw)<=131072,'GATEWAY_LIST_BOUND')
    selected=[]
    for line in raw.splitlines():
        parts=line.split();need(len(parts)==5,'GATEWAY_LIST_SHAPE')
        name,uid,phase,deleting,ready=parts
        need(re.fullmatch('gateway-[a-z0-9-]+',name) and re.fullmatch('[0-9a-f-]{36}',uid),'GATEWAY_POD_IDENTITY')
        if phase=='Running' and deleting=='<none>' and ready=='true':selected.append(parts)
    need(bool(selected) and len(selected)<=100,'READY_GATEWAY_REQUIRED')
    return sorted(selected)


def identity(pod,uid):
    meta,spec,status=pod['metadata'],pod['spec'],pod.get('status',{})
    need(meta.get('uid')==uid and not meta.get('deletionTimestamp') and meta.get('labels',{}).get('app')=='gateway','GATEWAY_IDENTITY_CHANGED')
    states=[row for row in status.get('containerStatuses',[]) if row.get('name')=='gateway']
    need(status.get('phase')=='Running' and len(states)==1 and states[0].get('ready') is True,'GATEWAY_NOT_READY')
    return {'name':meta['name'],'uid':uid,'spec_sha256':digest(canonical(spec)),'image_id_sha256':digest(str(states[0].get('imageID','')).encode()),
            'container_id_sha256':digest(str(states[0].get('containerID','')).encode()),'restart_count':states[0].get('restartCount')}


def userdata_mounts(pod):
    volumes={row['name']:row for row in pod['spec'].get('volumes',[])}
    mains=[row for row in pod['spec'].get('containers',[]) if row.get('name')=='gateway'];need(len(mains)==1,'ONE_GATEWAY_CONTAINER_REQUIRED')
    result=[]
    for mount in mains[0].get('volumeMounts',[]):
        claim=volumes.get(mount.get('name'),{}).get('persistentVolumeClaim',{}).get('claimName')
        if not claim or 'skills' in claim:continue
        root=mount.get('mountPath')
        need(isinstance(root,str) and root.startswith('/') and root!='/' and str(PurePosixPath(root))==root and '..' not in PurePosixPath(root).parts and '\\' not in root and not mount.get('subPathExpr'),'USERDATA_MOUNT_PATH')
        result.append({'mount_path':root,'claim':claim,'subpath_sha256':digest(str(mount.get('subPath','')).encode())})
    need(0<len(result)<=8,'USERDATA_PVC_MOUNT_REQUIRED');return result


def verify_root(value,mounts):
    need(isinstance(value,str) and '\\' not in value and '\x00' not in value and value.startswith('/'),'TRUSTED_ROOT_SHAPE')
    path=PurePosixPath(value)
    need(str(path)==value and '..' not in path.parts and len(path.parts)>=7,'TRUSTED_ROOT_SHAPE')
    need(path.parts[-5]=='users' and re.fullmatch('[A-Za-z0-9_-]{1,64}',path.parts[-4]) and path.parts[-3:]==('threads',THREAD,'user-data'),'TRUSTED_THREAD_ROOT_REQUIRED')
    need(any(PurePosixPath(row['mount_path']) in path.parents for row in mounts),'TRUSTED_ROOT_NOT_ON_USERDATA_PVC')
    return value


def reject_shadow_mounts(root,pod,mounts):
    path=PurePosixPath(root);base=path.parents[4]
    covering=[PurePosixPath(row['mount_path']) for row in mounts if PurePosixPath(row['mount_path'])==base or PurePosixPath(row['mount_path']) in base.parents]
    need(bool(covering),'BASE_USERDATA_PVC_MOUNT_REQUIRED')
    owning=max(covering,key=lambda item:len(item.parts))
    main=next(row for row in pod['spec']['containers'] if row['name']=='gateway')
    for mount in main.get('volumeMounts',[]):
        target=PurePosixPath(mount['mountPath'])
        if target==owning:
            need(not mount.get('subPathExpr'),'THREAD_STORAGE_SHADOW_MOUNT_REFUSED')
        elif owning in target.parents and (target==path or path in target.parents or target in path.parents):
            raise Refused('THREAD_STORAGE_SHADOW_MOUNT_REFUSED')


def ownership(pod,replicaset,dep_uid):
    owners=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
    need(len(owners)==1 and owners[0].get('kind')=='ReplicaSet','GATEWAY_REPLICASET_REQUIRED')
    need(replicaset['metadata'].get('uid')==owners[0].get('uid') and replicaset['metadata'].get('name')==owners[0].get('name'),'GATEWAY_REPLICASET_CHANGED')
    parents=[row for row in replicaset['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
    need(len(parents)==1 and parents[0].get('kind')=='Deployment' and parents[0].get('name')=='gateway' and parents[0].get('uid')==dep_uid,'GATEWAY_DEPLOYMENT_OWNERSHIP_REQUIRED')
    return {'name':replicaset['metadata']['name'],'uid':replicaset['metadata']['uid']}


class Operation:
    def __init__(self,child):self.child,self.pod,self.queries,self.rs,self.mounts,self.scope=child,None,0,None,None,None
    def command(self,args,body=None):
        allowed=args==LIST or args==['get','deployment','gateway','-o','json']
        if self.pod:
            allowed=allowed or args==['get','pod',self.pod,'-o','json']
            if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
            if len(args)==12 and args[:6]==['exec','-i',self.pod,'-c','gateway','--'] and args[6:11]==['/app/backend/.venv/bin/python','-I','-B','-c',self.child]:
                mode=args[11]
                allowed=mode in ('resolve','inspect') and self.mounts is not None and body==canonical({'mounts':self.mounts} if mode=='resolve' else {'mounts':self.mounts,'scope':self.scope})
        need(allowed and (args[0]=='exec' or body is None),'READONLY_FIXED_COMMAND_REQUIRED');need(self.queries<8,'QUERY_BOUND');self.queries+=1
        timeout=55 if args[0]=='exec' else 25;prefix=list(KUBE)
        if args[0]=='exec':prefix[2]='--request-timeout=50s'
        with tempfile.TemporaryFile() as incoming,tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            if body is not None:incoming.write(body);incoming.seek(0)
            process=subprocess.Popen(prefix+args,stdin=incoming if body is not None else subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,
                                     env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size<=2097152 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                    need(time.monotonic()<until,'READ_DEADLINE');time.sleep(.1)
                need(process.returncode in ((0,1) if args[0]=='exec' else (0,)),'READ_COMMAND_FAILED')
                need(os.fstat(output.fileno()).st_size<=2097152 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                output.seek(0);return output.read(2097153).decode()
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspect-readonly',required=True,action='store_true');parser.parse_args()
    op=None
    out={'schema':'energy-persisted-thread-inspection.v1','status':'INCOMPLETE','thread_id':THREAD,'workload_modified':False,'database_readonly':True,
         'database_modified':False,'database_credentials_used':False,'credentials_exported':False,'provider_requests':0,'files_modified':False,
         'secret_api_reads':0,'new_sandboxes_created':0,'raw_messages_or_news_exported':False,
         'path_resolution_basis':'FRESH_CONFIG_AND_PATHS_SOURCE_WITH_ACTUAL_GATEWAY_PVC_MOUNTS'}
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        op=Operation(assets())
        dep=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(dep['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_DEPLOYMENT_CHANGED')
        dep_identity={'uid':GATEWAY_UID,'generation':dep['metadata'].get('generation'),'spec_sha256':digest(canonical(dep['spec']))}
        out['gateway_replicas']={'desired':dep['spec'].get('replicas',1),**{key:dep.get('status',{}).get(source,0) for key,source in [('total','replicas'),('updated','updatedReplicas'),('ready','readyReplicas'),('available','availableReplicas'),('observed_generation','observedGeneration'),('terminating','terminatingReplicas')]}}
        chosen=candidates(op.command(LIST))[0];op.pod=chosen[0]
        pod=json.loads(op.command(['get','pod',op.pod,'-o','json']));before=identity(pod,chosen[1]);op.mounts=userdata_mounts(pod)
        controllers=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
        need(len(controllers)==1 and controllers[0].get('kind')=='ReplicaSet' and re.fullmatch('gateway-[a-z0-9-]+',controllers[0].get('name','')),'GATEWAY_REPLICASET_REQUIRED')
        op.rs=controllers[0]['name'];rs=json.loads(op.command(['get','replicaset',op.rs,'-o','json']))
        out.update(gateway=dep_identity,sample=before,replica_set=ownership(pod,rs,GATEWAY_UID),userdata_pvc_mounts=op.mounts)
        prefix=['exec','-i',op.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',op.child]
        resolved=json.loads(op.command(prefix+['resolve'],canonical({'mounts':op.mounts})))
        out['database_credentials_used']=resolved.get('database_credentials_used') is True
        root=resolved.pop('trusted_root',None)
        out['owner_resolution']=resolved
        need(resolved.get('status')=='NAMED_THREAD_OWNER_ROOT_RESOLVED_READONLY','OWNER_RESOLUTION_INCOMPLETE')
        verify_root(root,op.mounts)
        reject_shadow_mounts(root,pod,op.mounts)
        op.scope={'root':root,'owner_fingerprint':resolved['owner_fingerprint'],'path_resolver_sha256':resolved['path_resolver_source']['sha256']}
        out['storage_root_sha256']=digest(root.encode())
        result=json.loads(op.command(prefix+['inspect'],canonical({'mounts':op.mounts,'scope':op.scope})))
        out['observation']=result;out['database_credentials_used']=out['database_credentials_used'] or result.get('database_credentials_used') is True
        final=json.loads(op.command(['get','pod',op.pod,'-o','json']))
        dep_after=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(before==identity(final,chosen[1]) and pod['metadata'].get('ownerReferences')==final['metadata'].get('ownerReferences') and digest(canonical(dep_after['spec']))==dep_identity['spec_sha256'] and dep_after['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_CHANGED_DURING_READ')
        need(result.get('status')=='NAMED_THREAD_PERSISTED_MARKET_OBSERVED_NO_CHANGE','PERSISTED_CHILD_INCOMPLETE')
        out.update(status='NAMED_THREAD_PERSISTED_MARKET_OBSERVED_NO_CHANGE',stable_gateway_readback=True)
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'PERSISTED_INSPECTION_FAILED_REDACTED')
    out.update(queries=op.queries if op else 0,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')


if __name__=='__main__':sys.exit(main())
