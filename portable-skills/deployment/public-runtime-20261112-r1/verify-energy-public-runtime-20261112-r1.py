"""Read-only postcheck of one accepted public browser runtime repair.

No installation, workload change, browser launch, credential or business-file read.
Uses one existing ordinary sandbox, only its public Skill files and /tmp metadata.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

RECEIPT = Path('/var/lib/portal-energy-public-runtime-repair-20261112-r1/REPORT.json')
RECEIPT_SHA = '8a36ab323f83d7bc1fba16ef7eac06faa07520fa109efb86ffe087af4d733ce0'
RECEIPT_BYTES = 6038
OPERATION = '2677be7ae78e4339b7df3628e0ffa1c9'
TREE = 'f9865f7b1fd1745c471bb808f081bf3e86f2170c0c9c22df20e776c0a56ffcf9'
RUNTIME_SHA = 'cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598'
PNG_SHA = 'bedd5af036641c695510cca936b1e530ed7600238a8892fe88b5d7d5953bdd9b'
IMAGE_ID = 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
CLAIM = 'deer-flow-skills-rwx-pvc'
CLAIM_UID = '5bd3d4ed-54df-45c7-8954-d32ff8e53ec9'
KUBE = ['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=15s','-n','deer-flow']
COLUMNS = 'NAME:.metadata.name,UID:.metadata.uid,NODE:.spec.nodeName,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp,READY:.status.containerStatuses[?(@.name=="sandbox")].ready,IMAGE_ID:.status.containerStatuses[?(@.name=="sandbox")].imageID'
LIST = ['get','pods','-l','app.kubernetes.io/component=sandbox','--field-selector=status.phase=Running','--no-headers','-o','custom-columns='+COLUMNS]
VISIBILITY_CHILD = ''


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')


def receipt():
    for part in (RECEIPT,)+tuple(RECEIPT.parents):
        need(not part.is_symlink(),'RECEIPT_LINK')
    descriptor=os.open(str(RECEIPT),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        before=os.fstat(descriptor)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size==RECEIPT_BYTES,'RECEIPT_SHAPE')
        need(before.st_uid==0 and stat.S_IMODE(before.st_mode)&0o077==0,'ROOT_PRIVATE_RECEIPT_REQUIRED')
        raw=os.read(descriptor,RECEIPT_BYTES+1)
        after=os.fstat(descriptor)
        stamp=lambda row:(row.st_dev,row.st_ino,row.st_size,row.st_mtime_ns,row.st_ctime_ns)
        need(len(raw)==RECEIPT_BYTES and stamp(before)==stamp(after)==stamp(RECEIPT.stat()),'RECEIPT_CHANGED')
    finally:
        os.close(descriptor)
    need(sha(raw)==RECEIPT_SHA,'ACCEPTED_REPAIR_RECEIPT_CHANGED')
    report=json.loads(raw)
    need(report.get('operation')==OPERATION and report.get('skill_updated') is True
         and report.get('status') in ('ENERGY_PUBLIC_RUNTIME_REPAIR_INSTALLED','ENERGY_PUBLIC_RUNTIME_REPAIR_INSTALLED_VISIBILITY_PENDING'),
         'SUCCESSFUL_REPAIR_REQUIRED')
    publication=report.get('publication',{})
    qualification=report.get('shared_runtime_qualification',{})
    need(publication.get('after_files')==107 and publication.get('after_tree_sha256')==TREE,'ACCEPTED_SKILL_TREE_CHANGED')
    need(qualification.get('uid')==1000 and qualification.get('gid')==1000 and qualification.get('png_sha256')==PNG_SHA
         and qualification.get('runtime_manifest_sha256')==RUNTIME_SHA,'ACCEPTED_UID1000_RENDER_CHANGED')
    return {'path':str(RECEIPT),'bytes':len(raw),'sha256':sha(raw),'operation':OPERATION,'after_tree_sha256':TREE,'qualified_png_sha256':PNG_SHA}


def candidates(raw):
    rows=[]
    counts={'rows':0,'ready_expected_image':0,'not_ready':0,'image_mismatch':0,'deleting':0}
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields=line.split();need(len(fields)==7,'ORDINARY_POD_LIST_SHAPE')
        name,uid,node,phase,deleting,ready,image=fields
        counts['rows']+=1
        if phase!='Running' or ready!='true':
            counts['not_ready']+=1;continue
        if deleting!='<none>':
            counts['deleting']+=1;continue
        if image!=IMAGE_ID:
            counts['image_mismatch']+=1;continue
        need(re.fullmatch('sandbox-[a-z0-9-]{1,100}',name) and re.fullmatch('[0-9a-f-]{36}',uid)
             and re.fullmatch('[a-z0-9.-]{1,253}',node),'ORDINARY_POD_IDENTITY')
        rows.append({'pod':name,'uid':uid,'node':node});counts['ready_expected_image']+=1
    return sorted(rows,key=lambda row:row['pod']),counts


def identity(pod,sample):
    metadata=pod.get('metadata',{});spec=pod.get('spec',{});status=pod.get('status',{})
    need(metadata.get('name')==sample['pod'] and metadata.get('uid')==sample['uid']
         and metadata.get('labels',{}).get('app.kubernetes.io/component')=='sandbox'
         and not metadata.get('deletionTimestamp') and spec.get('nodeName')==sample['node'],'ORDINARY_POD_REPLACED')
    containers=[row for row in spec.get('containers',[]) if row.get('name')=='sandbox']
    states=[row for row in status.get('containerStatuses',[]) if row.get('name')=='sandbox']
    need(len(containers)==1 and len(states)==1,'ORDINARY_CONTAINER_SHAPE')
    state=states[0]
    need(status.get('phase')=='Running' and state.get('ready') is True and state.get('imageID')==IMAGE_ID
         and bool(state.get('state',{}).get('running')) and isinstance(state.get('containerID'),str),'ORDINARY_CONTAINER_NOT_READY')
    return {'uid':metadata['uid'],'node':spec['nodeName'],'spec_sha256':sha(canonical(spec)),
            'container_id_sha256':sha(state['containerID'].encode()),'restart_count':state.get('restartCount',0),'image_id':state['imageID']}


def mount_summary(pod):
    container=[row for row in pod['spec']['containers'] if row['name']=='sandbox'][0]
    mounts=container.get('volumeMounts',[])
    category=[row for row in mounts if row.get('mountPath')=='/mnt/skills/public']
    need(len(category)==1,'ORDINARY_PUBLIC_MOUNT_REQUIRED')
    mount=category[0]
    volumes={row['name']:row for row in pod['spec'].get('volumes',[])}
    volume=volumes.get(mount.get('name'),{})
    source=volume.get('persistentVolumeClaim',{})
    return {'mount_path':mount.get('mountPath'),'subpath_is_public':mount.get('subPath')=='public',
            'subpath_expression_present':bool(mount.get('subPathExpr')),'read_only':mount.get('readOnly',False),
            'claim_is_expected':source.get('claimName')==CLAIM,'volume_read_only':source.get('readOnly',False)},mount,mounts


def require_mount(summary,mount,mounts):
    need(summary['subpath_is_public'] and not summary['subpath_expression_present'],'ORDINARY_PUBLIC_SUBPATH_CHANGED')
    need(summary['claim_is_expected'],'ORDINARY_SKILLS_CLAIM_CHANGED')
    public=PurePosixPath('/mnt/skills/public')
    for other in mounts:
        if other is mount:
            continue
        path=other.get('mountPath');need(isinstance(path,str) and path.startswith('/'),'ORDINARY_MOUNT_PATH_INVALID')
        candidate=PurePosixPath(path)
        need(public!=candidate and public not in candidate.parents and candidate not in public.parents,'ORDINARY_PUBLIC_SHADOW_MOUNT')


class Operation:
    def __init__(self):
        self.sample=None;self.queries=0;self.started=time.monotonic();self.step='accepted_repair_receipt'
        self.out={'schema':'energy-public-runtime-visibility-postcheck.v1','status':'INCOMPLETE','workload_modified':False,
                  'files_modified':False,'persistent_files_modified':False,'browser_launched':False,'credentials_read':False,
                  'credentials_exported':False,'secret_api_reads':0,'provider_requests':0,'database_access':False,
                  'business_requests':0,'work_messages_sent':0,'repeat_readonly_check_safe':True,'original_receipt_modified':False}

    def command(self,args,limit=524288):
        fixed=[LIST,['get','pvc',CLAIM,'-o','json']]
        if self.sample:
            fixed.append(['get','pod',self.sample['pod'],'-o','json'])
            fixed.append(['exec',self.sample['pod'],'-c','sandbox','--','python3','-I','-B','-c',VISIBILITY_CHILD,TREE])
        need(args in fixed,'FIXED_READONLY_COMMAND_REQUIRED')
        need(self.queries<10 and time.monotonic()-self.started<90,'READONLY_OPERATION_BOUND')
        self.queries+=1
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            process=subprocess.Popen(KUBE+args,stdin=subprocess.DEVNULL,stdout=output,stderr=errors,
                                     env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'},start_new_session=True)
            until=time.monotonic()+(35 if args[0]=='exec' else 22)
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size<=limit and os.fstat(errors.fileno()).st_size<=16384,'COMMAND_OUTPUT_BOUND')
                    need(time.monotonic()<until,'COMMAND_DEADLINE');time.sleep(.05)
                self.out['last_command_exitcode']=process.returncode
                need(process.returncode in ((0,1) if args[0]=='exec' else (0,)),'READONLY_COMMAND_FAILED_REDACTED')
                need(os.fstat(output.fileno()).st_size<=limit and os.fstat(errors.fileno()).st_size<=16384,'COMMAND_OUTPUT_BOUND')
                output.seek(0);raw=output.read(limit+1)
                return raw.decode('utf-8','replace') if args==LIST else json.loads(raw)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

    def run(self):
        self.out['accepted_repair']=receipt()
        self.step='list_existing_ordinary_sandboxes'
        rows,counts=candidates(self.command(LIST,limit=1048576));self.out['inventory']=counts
        need(bool(rows),'NO_READY_ORDINARY_SANDBOX_WITH_FIXED_IMAGE')
        self.sample=rows[0];self.out['sample']=self.sample
        self.step='read_selected_ordinary_pod'
        pod=self.command(['get','pod',self.sample['pod'],'-o','json']);before=identity(pod,self.sample)
        self.out['sample_before']=before
        self.step='verify_actual_public_category_mount'
        summary,mount,mounts=mount_summary(pod);self.out['public_category_mount']=summary;require_mount(summary,mount,mounts)
        self.step='verify_existing_skills_pvc'
        pvc=self.command(['get','pvc',CLAIM,'-o','json'])
        need(pvc.get('metadata',{}).get('uid')==CLAIM_UID and pvc.get('status',{}).get('phase')=='Bound'
             and not pvc.get('metadata',{}).get('deletionTimestamp'),'SKILLS_PVC_IDENTITY_CHANGED')
        self.step='read_public_skill_and_runtime_as_uid1000'
        observation=self.command(['exec',self.sample['pod'],'-c','sandbox','--','python3','-I','-B','-c',VISIBILITY_CHILD,TREE],limit=65536)
        need(isinstance(observation,dict),'VISIBILITY_CHILD_RESPONSE_INVALID')
        self.out['observation']=observation
        self.step='recheck_selected_pod_identity'
        fresh=self.command(['get','pod',self.sample['pod'],'-o','json']);after=identity(fresh,self.sample)
        need(before==after,'ORDINARY_POD_OR_CONTAINER_CHANGED')
        self.step='recheck_accepted_receipt'
        need(receipt()==self.out['accepted_repair'],'ACCEPTED_REPAIR_CHANGED_DURING_INSPECTION')
        need(observation.get('status')=='ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE',
             observation.get('error_code') if isinstance(observation.get('error_code'),str) and re.fullmatch('[A-Z0-9_]{1,100}',observation['error_code']) else 'ORDINARY_VISIBILITY_CHILD_INCOMPLETE')
        need(observation.get('uid')==1000 and observation.get('gid')==1000 and observation.get('skill_tree_sha256')==TREE
             and observation.get('runtime_manifest_sha256')==RUNTIME_SHA,'ORDINARY_VISIBILITY_RESULT_MISMATCH')
        self.out.update(status='ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE_READONLY_ACCEPTED',stable_readback=True,
                        browser_business_acceptance_pending=True,last_command_exitcode=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify-readonly',required=True,action='store_true');parser.parse_args()
    operation=Operation();out=operation.out
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        need(bool(VISIBILITY_CHILD),'VISIBILITY_CHILD_NOT_SEALED');compile(VISIBILITY_CHILD,'<fixed-visibility>','exec')
        operation.run()
    except Exception as exc:
        out.update(status='INCOMPLETE',error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_POSTCHECK_FAILED_REDACTED')
    out.update(last_step=operation.step,queries=operation.queries,elapsed_seconds=round(time.monotonic()-operation.started,3),
               observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')


if __name__=='__main__':
    sys.exit(main())
