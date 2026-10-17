"""Real Linux kernel fixture on a new internal Docker bridge; exact-owned cleanup."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import time
import traceback
import uuid

HERE=Path(__file__).resolve().parent
IMAGE='sha256:1353c9a1723ad9a8f05be830d951311623e153488500b1e22b3bcb059e676630'
DOCKER=['docker','--context','desktop-linux']
OWNER=uuid.uuid4().hex
LABEL='portal-ppt-link-guard-fixture-owner'
POD_UID='12345678-1234-1234-1234-123456789abc'
records=[]; containers=[]; network=None; volume=None


def call(args,timeout=30,allowed=(0,)):
    begun=time.monotonic()
    result=subprocess.run(DOCKER+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    records.append(dict(argv_prefix=args[:2],returncode=result.returncode,elapsed_seconds=round(time.monotonic()-begun,3),
                        stdout_bytes=len(result.stdout),stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
                        stderr_bytes=len(result.stderr),stderr_sha256=hashlib.sha256(result.stderr).hexdigest()))
    if result.returncode not in allowed:
        raise RuntimeError('Docker command failed: '+str(args[:2])+' '+result.stderr.decode(errors='replace')[:2048])
    return result


def inspection(kind,name):
    rows=json.loads(call([kind,'inspect',name]).stdout)
    assert len(rows)==1
    return rows[0]


def owned_container(cid):
    obj=inspection('container',cid)
    assert obj['Id']==cid and obj['Config']['Labels'].get(LABEL)==OWNER and obj['Image']==IMAGE
    return obj


def create(name,net,args,netadmin=False,receipt=False,extra_env=None):
    command=['container','create','--pull','never','--name','portal-link-'+OWNER[:16]+'-'+name,
             '--label',LABEL+'='+OWNER,'--network',net,'--read-only','--cap-drop','ALL','--user','0:0',
             '--security-opt','no-new-privileges','--cpus','1','--memory','256m','--pids-limit','64',
             '--tmpfs','/tmp:rw,size=16m,mode=1777','--entrypoint','/bin/sh']
    if netadmin: command += ['--cap-add','NET_ADMIN']
    if receipt: command += ['--mount','type=volume,source='+volume+',target=/portal-ppt-link-guard'+('' if netadmin else ',readonly')]
    for key,value in (extra_env or {}).items(): command += ['--env',key+'='+value]
    result=call(command+[IMAGE]+args)
    cid=result.stdout.decode().strip(); assert re.fullmatch('[a-f0-9]{64}',cid)
    containers.append(cid)
    obj=owned_container(cid)
    shape={key:obj['HostConfig'].get(key) for key in ['ReadonlyRootfs','NetworkMode','CapDrop','CapAdd','Privileged','PidMode']}
    assert obj['HostConfig']['ReadonlyRootfs'] is True and obj['HostConfig']['NetworkMode']==net,shape
    cap_add=[value.removeprefix('CAP_') for value in (obj['HostConfig']['CapAdd'] or [])]
    assert obj['HostConfig']['CapDrop']==['ALL'] and cap_add==(['NET_ADMIN'] if netadmin else []),shape
    assert obj['HostConfig']['Privileged'] is False and obj['HostConfig']['PidMode']!='host',shape
    assert all(m['Type']=='volume' and m['Name']==volume for m in obj.get('Mounts',[])),obj.get('Mounts',[])
    return cid


def exec_python(cid,source,*args,allowed=(0,)):
    owned_container(cid)
    return call(['exec',cid,'python','-I','-S','-B','-u','-c',source]+list(args),allowed=allowed)


def connection(cid,address):
    source='import json,socket,sys\ntry:\n s=socket.create_connection((sys.argv[1],18777),2);s.close();print(json.dumps({"connected":True}))\nexcept OSError as e:print(json.dumps({"connected":False,"errno":e.errno}))'
    return json.loads(exec_python(cid,source,address).stdout)


def business(name,args,extra_env=None,netadmin=False):
    env=dict(PPT_GUARD_POD_UID=POD_UID);env.update(extra_env or {})
    cid=create(name,'container:'+subject,args,netadmin=netadmin,receipt=True,extra_env=env)
    result=call(['start','--attach',cid],allowed=(0,73,91),timeout=30)
    state=owned_container(cid)['State']
    assert state['Running'] is False and state['ExitCode']==result.returncode
    return dict(exit_code=result.returncode,stdout=result.stdout.decode(),stderr=result.stderr.decode())


summary=dict(schema='portal-owned-pod-link-guard-kernel-fixture.v1',owner=OWNER,image=IMAGE,
             status='INCOMPLETE',host_network=False,remote_execution=False,production_mutation=False,
             candidate_image_executed=False,native_arm64_qualified=False,ready_for_rollout=False)
try:
    spec=importlib.util.spec_from_file_location('guard',HERE/'link_guard.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    assert inspection('image',IMAGE)['Id']==IMAGE
    name='portal-link-'+OWNER
    network=call(['network','create','--driver','bridge','--internal','--label',LABEL+'='+OWNER,name]).stdout.decode().strip()
    net=inspection('network',network)
    assert net['Id']==network and net['Internal'] is True and net['Labels'][LABEL]==OWNER
    volume=call(['volume','create','--driver','local','--opt','type=tmpfs','--opt','device=tmpfs','--opt','o=size=1m,mode=0755','--label',LABEL+'='+OWNER,name]).stdout.decode().strip()
    vol=inspection('volume',volume)
    assert vol['Name']==volume and vol['Labels'][LABEL]==OWNER and vol['Options']['type']=='tmpfs'
    daemon='import socket,time\ns=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(("0.0.0.0",18777));s.listen(10)\nwhile True:\n c,a=s.accept();c.close()'
    subject=create('subject',network,['-eu','-c','exec python -I -S -B -u -c "$1"','fixture',daemon],netadmin=True,receipt=True,
                   extra_env={'PROOF_POD_UID':POD_UID})
    peer=create('peer',network,['-eu','-c','exec python -I -S -B -u -c "$1"','fixture',daemon])
    call(['start',subject,peer])
    subject_ip=next(iter(owned_container(subject)['NetworkSettings']['Networks'].values()))['IPAddress']
    peer_ip=next(iter(owned_container(peer)['NetworkSettings']['Networks'].values()))['IPAddress']
    baseline=dict(subject_to_peer=connection(subject,peer_ip),peer_to_subject=connection(peer,subject_ip),
                  subject_loopback=connection(subject,'127.0.0.1'),peer_loopback=connection(peer,'127.0.0.1'))
    assert all(row['connected'] for row in baseline.values()),baseline
    wrong_proof_uid=call(['exec',subject]+helper.proof_guard_argv(OWNER,'abcdef12-1234-1234-1234-123456789abc'),allowed=(92,))
    assert not wrong_proof_uid.stdout
    result=exec_python(subject,helper.guard_source(),'--owner',OWNER,'--pod-uid',POD_UID,
                       '--code-sha',helper.GUARD_CODE_SHA,'--receipt-directory',helper.RECEIPT_PATH)
    guard=json.loads(result.stdout)
    (HERE/'KERNEL-GUARD-RECEIPT.json').write_bytes(result.stdout)
    after=dict(subject_to_peer=connection(subject,peer_ip),peer_to_subject=connection(peer,subject_ip),
               subject_loopback=connection(subject,'127.0.0.1'),peer_loopback=connection(peer,'127.0.0.1'))
    assert not after['subject_to_peer']['connected'] and not after['peer_to_subject']['connected'],after
    assert after['subject_loopback']['connected'] and after['peer_loopback']['connected'],after
    wrap=['-eu','-c',helper.wrapper_source(),'portal-ppt-link-preexec',OWNER,helper.GUARD_CODE_SHA]
    business_code='''import errno,fcntl,json,socket,struct,sys
try:
 open('/portal-ppt-link-guard/owner','w').write('changed'); raise AssertionError('receipt writable')
except OSError as e: assert e.errno==errno.EROFS
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
 try:
  fcntl.ioctl(s.fileno(),0x8914,struct.pack('16sH22x',b'eth0',1)); raise AssertionError('link re-enabled')
 except OSError as e: assert e.errno==errno.EPERM
print(json.dumps(dict(argv=sys.argv[1:],receipt_readonly=True,link_reenable_denied=True)))
sys.exit(73)
'''
    original=['python','-I','-S','-B','-c',business_code,'space value','$(must-stay-literal)','semi;colon']
    accepted=business('business',wrap+original)
    assert accepted['exit_code']==73 and json.loads(accepted['stdout'])==dict(argv=original[-3:],receipt_readonly=True,link_reenable_denied=True),accepted
    wrong_uid=business('wronguid',wrap+['python','-c','print("UNEXPECTED")'],{'PPT_GUARD_POD_UID':'different'})
    assert wrong_uid['exit_code']==91 and not wrong_uid['stdout'],wrong_uid
    caps=business('caps',wrap+['python','-c','print("UNEXPECTED")'],netadmin=True)
    assert caps['exit_code']==91 and not caps['stdout'],caps
    poison_setup='/bin/mkdir -p /tmp/evil; printf "#!/bin/sh\\nexit 99\\n" > /tmp/evil/cat; /bin/chmod 755 /tmp/evil/cat; PATH=/tmp/evil; LC_ALL=C.UTF-8; export PATH LC_ALL; exec /bin/sh "$@"'
    preserve_code='import json,os; assert os.environ["PATH"]=="/tmp/evil"; assert os.environ["LC_ALL"]=="C.UTF-8"; print(json.dumps({"safe_tools_and_original_environment":True}))'
    protected=business('poison-path',['-eu','-c',poison_setup,'setup']+wrap+['/usr/local/bin/python','-I','-S','-B','-c',preserve_code])
    assert protected['exit_code']==0 and json.loads(protected['stdout'])==dict(safe_tools_and_original_environment=True),protected
    assert connection(subject,'127.0.0.1')['connected'] and connection(peer,'127.0.0.1')['connected']
    summary.update(status='PASS',baseline=baseline,after_guard=after,guard_receipt=guard,
                   wrapper_original_argv_and_exit=accepted,wrong_pod_uid_rejected=wrong_uid,
                   nonzero_capability_sets_rejected=caps,interfaces_complete=True,
                   wrong_live_proof_pod_uid_rejected=True,loopback_health_after_business=True,
                   malicious_path_cannot_replace_tools_and_original_environment_restored=protected,
                   receipt_tmpfs_shared_readonly_to_business=True,
                   mechanics_scope='LOCAL_LINUX_KERNEL_FIXTURE_IMAGE_ONLY_NOT_ARM64_CANDIDATE_ACCEPTANCE')
except BaseException as exc:
    summary['error']=str(exc)[:4096]
    summary['exception_class']=type(exc).__name__
    summary['traceback']=traceback.format_exc()
finally:
    cleanup=[]
    for cid in reversed(containers):
        try:
            owned_container(cid);call(['rm','--force',cid]);cleanup.append(dict(kind='container',id=cid,status='EXACT_OWNED_ID_REMOVED'))
        except BaseException as exc:cleanup.append(dict(kind='container',id=cid,status='REVIEW_REQUIRED',error=str(exc)[:300]))
    if volume:
        try:
            obj=inspection('volume',volume);assert obj['Labels'][LABEL]==OWNER
            call(['volume','rm',volume]);cleanup.append(dict(kind='volume',id=volume,status='EXACT_OWNED_NAME_REMOVED'))
        except BaseException as exc:cleanup.append(dict(kind='volume',id=volume,status='REVIEW_REQUIRED',error=str(exc)[:300]))
    if network:
        try:
            obj=inspection('network',network);assert obj['Id']==network and obj['Labels'][LABEL]==OWNER and not obj['Containers']
            call(['network','rm',network]);cleanup.append(dict(kind='network',id=network,status='EXACT_OWNED_ID_REMOVED'))
        except BaseException as exc:cleanup.append(dict(kind='network',id=network,status='REVIEW_REQUIRED',error=str(exc)[:300]))
    summary['cleanup']=cleanup;summary['commands']=records
    if any(row['status']=='REVIEW_REQUIRED' for row in cleanup):summary['status']='INCOMPLETE'
    summary['files']={name:dict(bytes=(HERE/name).stat().st_size,sha256=hashlib.sha256((HERE/name).read_bytes()).hexdigest()) for name in ['guard_kernel.py','preexec.sh','link_guard.py','run_kernel_fixture.py']}
    (HERE/'KERNEL-QUALIFICATION.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=summary['status'],error=summary.get('error'),cleanup=cleanup),sort_keys=True))
    if summary['status']!='PASS':raise SystemExit(1)
