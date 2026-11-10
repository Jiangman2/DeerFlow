"""Export the observed SkillHub program image for a narrowly scoped auth rebuild.

Reads Kubernetes and local containerd. Creates one new private export directory;
never imports an image, patches a workload, reads Secrets/volumes or calls APIs.
The resulting image contains program files, not mounted production user data.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile

ROOT = Path('/var/lib/portal-skillhub-auth-build-input-20261105-r2')
DEPLOYMENT_UID = '009babb5-b084-4397-a5ee-56a4b4b91032'
DEPLOYMENT_SHA = 'dc58fef71aaf1f8dc3b1dcc5b12ab6d376ed25208cf58fd3c511dcd7f4685b6f'
POD = 'skillhub-server-86c7d457bb-7vvbw'
POD_UID = '04e4804a-f04e-49e2-b181-bbdfb276245f'
IMAGE = 'deerflow.local/skillhub-server:20260908-enterprise-arm64-rc15-skillhubsession2'
IMAGE_ID = 'docker.io/library/import-2026-10-09@sha256:71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2'
MANIFEST = 'sha256:71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2'
JAR_SHA = '6fc23f8056b763a1590a9953a83e6bee873e7bb2e935b2dddf93589585500363'
KUBE = ['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=15s','-n','deer-flow']
MAX_ARCHIVE = 1073741824

class Refused(Exception):
    pass

def require(ok, code):
    if not ok:
        raise Refused(code)

def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read_command(argv, limit=1048576, timeout=30):
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result=subprocess.run(argv,stdout=output,stderr=errors,timeout=timeout,check=False)
        except subprocess.TimeoutExpired:
            raise Refused('READ_TIMEOUT')
        require(result.returncode==0,'READ_FAILED')
        require(output.tell()<=limit,'READ_OUTPUT_BOUND')
        output.seek(0)
        return output.read(limit+1)

def get(kind,name):
    return json.loads(read_command(KUBE+['get',kind,name,'-o','json']))

def identity(obj):
    return {'name':obj['metadata']['name'],'uid':obj['metadata']['uid'],
            'generation':obj['metadata'].get('generation'),'spec_sha256':sha(canonical(obj['spec']))}

def snapshot():
    deployment=get('deployment','skillhub-server')
    pod=get('pod',POD)
    d=identity(deployment)
    require(d['uid']==DEPLOYMENT_UID and d['generation']==14 and d['spec_sha256']==DEPLOYMENT_SHA,'SERVER_DEPLOYMENT_CHANGED')
    require(pod['metadata']['uid']==POD_UID and pod['spec']['nodeName']=='datax-01' and not pod['metadata'].get('deletionTimestamp'),'SERVER_POD_CHANGED')
    require(any(c.get('type')=='Ready' and c.get('status')=='True' for c in pod.get('status',{}).get('conditions',[])),'SERVER_NOT_READY')
    containers=pod['spec']['containers']
    statuses=pod['status']['containerStatuses']
    require(len(containers)==len(statuses)==1 and containers[0]['name']=='server' and containers[0]['image']==IMAGE and statuses[0]['imageID']==IMAGE_ID,'SERVER_IMAGE_CHANGED')
    template=deployment['spec']['template']['spec']
    container=next(c for c in template['containers'] if c['name']=='server')
    scheduling={k:template.get(k) for k in ('nodeName','nodeSelector','affinity','tolerations','topologySpreadConstraints','schedulerName','priorityClassName')}
    metadata={'deployment':d,'pod':identity(pod),'runtime':{'node':'datax-01','image_id':statuses[0]['imageID'],'restart_count':statuses[0]['restartCount'],'container_id_sha256':sha(statuses[0].get('containerID','').encode())},
        'scheduling':scheduling,'strategy':deployment['spec'].get('strategy'),
        'resources':container.get('resources',{}),'image_pull_policy':container.get('imagePullPolicy'),
        'termination_grace_seconds':template.get('terminationGracePeriodSeconds'),
        'native_auth_flag':next((e.get('value') for e in container.get('env',[]) if e.get('name')=='SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED'),None),
        'mounted_volumes_in_export':False,'environment_values_exported':False,'credentials_exported':False}
    require(metadata['native_auth_flag']=='true','ENTERPRISE_AUTH_FLAG_CHANGED')
    # Only digest/size of fixed program file; no app execution or application configuration.
    jar=read_command(KUBE+['exec',POD,'-c','server','--','sh','-c','stat -c %s /app/app.jar && sha256sum /app/app.jar'],2048).decode('ascii').splitlines()
    require(len(jar)==2 and jar[0]=='103450647' and jar[1].split()[0]==JAR_SHA,'PROGRAM_JAR_CHANGED')
    metadata['program_jar']={'bytes':103450647,'sha256':JAR_SHA}
    return metadata

def hash_file(path):
    digest=hashlib.sha256()
    size=0
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576),b''):
            digest.update(chunk);size+=len(chunk)
            require(size<=MAX_ARCHIVE,'ARCHIVE_SIZE_BOUND')
    return {'bytes':size,'sha256':digest.hexdigest()}

def valid_digest(value):
    return isinstance(value,str) and value.startswith('sha256:') and len(value)==71 and all(c in '0123456789abcdef' for c in value[7:])

def resolve_program_manifest(read_blob, root_digest, expected_size=None):
    """Resolve one ARM64 program manifest without changing the pinned root index."""
    digest=root_digest
    chain=[]
    for depth in range(4):
        require(valid_digest(digest),'DESCRIPTOR_DIGEST')
        raw=read_blob(digest,262144)
        require(len(raw)<=262144 and sha(raw)==digest[7:] and (expected_size is None or len(raw)==expected_size),'MANIFEST_HASH_MISMATCH')
        value=json.loads(raw)
        chain.append({'digest':digest,'size':len(raw)})
        if 'config' in value and isinstance(value.get('layers'),list):
            require(1<=len(value['layers'])<=40,'LAYER_COUNT_BOUND')
            return value,chain
        children=value.get('manifests')
        require(isinstance(children,list) and 1<=len(children)<=16,'IMAGE_INDEX_SHAPE')
        candidates=[d for d in children if isinstance(d,dict) and isinstance(d.get('platform'),dict) and d['platform'].get('os')=='linux' and d['platform'].get('architecture')=='arm64' and d['platform'].get('variant','v8') in ('','v8')]
        if not candidates and len(children)==1 and isinstance(children[0],dict) and children[0].get('platform') is None:
            candidates=children
        require(len(candidates)==1,'ONE_ARM64_MANIFEST_REQUIRED')
        selected=candidates[0]
        require(valid_digest(selected.get('digest')) and isinstance(selected.get('size'),int) and 0<selected['size']<=262144,'INDEX_CHILD_DESCRIPTOR')
        digest=selected['digest'];expected_size=selected['size']
    raise Refused('IMAGE_INDEX_DEPTH_BOUND')

def validate_platform_config(read_blob,manifest):
    item=manifest['config']
    require(valid_digest(item.get('digest')) and isinstance(item.get('size'),int) and 0<item['size']<=262144,'CONFIG_DESCRIPTOR')
    raw=read_blob(item['digest'],262144)
    require(len(raw)==item['size'] and sha(raw)==item['digest'][7:],'CONFIG_HASH_MISMATCH')
    config=json.loads(raw)
    require(config.get('architecture')=='arm64' and config.get('os')=='linux','PLATFORM_MISMATCH')
    require(config.get('rootfs',{}).get('type')=='layers' and len(config['rootfs']['diff_ids'])==len(manifest['layers']),'LAYER_CONFIG_COUNT_MISMATCH')
    return config

def audit_oci(path, expected_manifest):
    require(path.is_file() and not path.is_symlink() and 0<path.stat().st_size<=MAX_ARCHIVE,'INVALID_EXPORT_FILE')
    with tarfile.open(str(path),'r:') as archive:
        members=archive.getmembers()
        require(len(members)<=256,'OCI_MEMBER_BOUND')
        files={}
        for member in members:
            require(member.isfile() or member.isdir(),'OCI_LINK_OR_SPECIAL_FILE')
            if member.isdir():
                require(member.name.rstrip('/') in ('blobs','blobs/sha256'),'OCI_UNEXPECTED_DIRECTORY')
                continue
            name=member.name
            require(name not in files and (name in ('index.json','oci-layout') or name.startswith('blobs/sha256/') and len(name)==77 and all(c in '0123456789abcdef' for c in name[13:])),'OCI_UNEXPECTED_MEMBER')
            require(0<=member.size<=MAX_ARCHIVE,'OCI_MEMBER_SIZE_BOUND')
            files[name]=member
        def raw(name,limit):
            require(name in files and files[name].size<=limit,'OCI_DESCRIPTOR_MISSING_OR_OVERSIZE')
            return archive.extractfile(files[name]).read(limit+1)
        def read_blob(digest,limit):
            require(valid_digest(digest),'DESCRIPTOR_DIGEST')
            return raw('blobs/sha256/'+digest[7:],limit)
        require(json.loads(raw('oci-layout',4096)).get('imageLayoutVersion')=='1.0.0','OCI_LAYOUT')
        index=json.loads(raw('index.json',262144))
        require(isinstance(index.get('manifests'),list) and len(index['manifests'])==1,'ONE_MANIFEST_REQUIRED')
        descriptor=index['manifests'][0]
        require(descriptor.get('digest')==expected_manifest,'RUNTIME_MANIFEST_MISMATCH')
        manifest,chain=resolve_program_manifest(read_blob,expected_manifest,descriptor['size'])
        descriptors=[manifest['config']]+manifest['layers']
        for item in descriptors:
            digest=item['digest']
            require(valid_digest(digest),'DESCRIPTOR_DIGEST')
            name='blobs/sha256/'+digest[7:]
            require(name in files and files[name].size==item['size'],'BLOB_MISSING')
            h=hashlib.sha256()
            with archive.extractfile(files[name]) as stream:
                for chunk in iter(lambda:stream.read(1048576),b''):
                    h.update(chunk)
            require(h.hexdigest()==digest[7:],'BLOB_HASH_MISMATCH')
        allowed={'index.json','oci-layout'} | {'blobs/sha256/'+item['digest'][7:] for item in chain+descriptors}
        require(set(files)==allowed,'UNREFERENCED_IMAGE_CONTENT')
        validate_platform_config(read_blob,manifest)
        return {'root_manifest':expected_manifest,'manifest':chain[-1]['digest'],'config':manifest['config']['digest'],'platform':'linux/arm64','layers':len(manifest['layers']),'index_depth':len(chain)-1,'complete_blob_hashes_verified':True,'raw_image_configuration_exported_to_stdout':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-current-program-image-once',required=True,action='store_true')
    parser.parse_args()
    out={'schema':'portal-skillhub-auth-build-input.v2','status':'INCOMPLETE','automatic_retry':False,
         'workload_modified':False,'configuration_modified':False,'database_access':False,'secret_api_reads':0,
         'image_import_requested':False,'provider_requests':0,'business_requests':0,'export_directory_created':False,
         'export_may_be_partial':False,'ready_for_activation':False,'raw_errors_exported':False}
    step='host_guard'
    try:
        require(os.name=='posix' and os.geteuid()==0 and os.uname().nodename.split('.')[0]=='datax-01','DATAX01_ROOT_REQUIRED')
        step='snapshot_before'
        before=snapshot()
        ctr=shutil.which('ctr')
        require(ctr is not None,'CTR_REQUIRED')
        prefix=[ctr,'--address','/run/containerd/containerd.sock','--namespace','k8s.io']
        step='resolve_cached_platform'
        def read_cached_blob(digest,limit):
            return read_command(prefix+['content','get',digest],limit)
        manifest,chain=resolve_program_manifest(read_cached_blob,MANIFEST)
        validate_platform_config(read_cached_blob,manifest)
        require(all(valid_digest(d.get('digest')) and isinstance(d.get('size'),int) and d['size']>0 for d in manifest['layers']),'LAYER_DESCRIPTOR')
        require(sum(d['size'] for d in manifest['layers'])+manifest['config']['size']<805306368,'EXPORT_CAPACITY_BOUND')
        out['resolved_image']={'root_manifest':MANIFEST,'manifest':chain[-1]['digest'],'index_depth':len(chain)-1,'platform':'linux/arm64'}
        import pwd
        account=pwd.getpwnam('cnooc')
        parent=ROOT.parent.lstat()
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid==0 and parent.st_mode & 0o022 == 0 and not ROOT.parent.is_symlink(),'PRIVATE_EXPORT_PARENT_REQUIRED')
        require(shutil.disk_usage(str(ROOT.parent)).free>=2147483648,'EXPORT_SPACE_REQUIRED')
        require(not os.path.lexists(str(ROOT)),'EXPORT_EXISTS_REVIEW_REQUIRED')
        step='create_private_export'
        ROOT.mkdir(mode=0o700)
        out['export_directory_created']=True
        archive=ROOT/'skillhub.auth-baseline-20261105-r2.oci.tar'
        step='export_cached_program_image'
        print(json.dumps({'phase':'EXPORT_CURRENT_CACHED_SKILLHUB_PROGRAM_NO_DEPLOYMENT_CHANGE'}),flush=True)
        out['export_may_be_partial']=True
        # Export cached program layers; mounted volumes and Secret objects are not read.
        # The tag is verified against the exact observed manifest after export.
        with tempfile.TemporaryFile() as log:
            try:
                result=subprocess.run(prefix+['images','export','--platform','linux/arm64','--skip-manifest-json',str(archive),IMAGE],stdout=log,stderr=log,timeout=180,check=False)
            except subprocess.TimeoutExpired:
                raise Refused('IMAGE_EXPORT_TIMEOUT')
            require(result.returncode==0,'IMAGE_EXPORT_FAILED')
        step='audit_export'
        integrity=audit_oci(archive,MANIFEST)
        transport=hash_file(archive)
        step='snapshot_after'
        after=snapshot()
        require(before==after,'BASELINE_CHANGED_DURING_EXPORT')
        metadata={'schema':'portal-skillhub-auth-build-metadata.v1','source':'fixed_operator_observed_runtime','observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'production':after,'archive':dict(transport,filename=archive.name),'image':integrity,
                  'source_jar_hash_requires_reconciliation_after_local_unpack':JAR_SHA,'stable_readback':True}
        metadata_file=ROOT/'METADATA.json'
        with metadata_file.open('x',encoding='utf-8') as stream:
            json.dump(metadata,stream,sort_keys=True,indent=2);stream.write('\n')
        for file in (archive,metadata_file):
            os.chmod(str(file),0o600);os.chown(str(file),account.pw_uid,account.pw_gid)
        os.chown(str(ROOT),account.pw_uid,account.pw_gid)
        out.update(status='SKILLHUB_PROGRAM_IMAGE_EXPORTED_NO_SERVICE_CHANGE',export_may_be_partial=False,
                   archive_path=str(archive),archive=transport,metadata_path=str(metadata_file),metadata=hash_file(metadata_file),
                   image=integrity,stable_readback=True)
    except Exception as exc:
        out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'EXPORT_LOCAL_FAILURE')
    out.update(last_step=step,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out,sort_keys=True))
    return 0 if out['status']=='SKILLHUB_PROGRAM_IMAGE_EXPORTED_NO_SERVICE_CHANGE' else 1

if __name__=='__main__':
    raise SystemExit(main())