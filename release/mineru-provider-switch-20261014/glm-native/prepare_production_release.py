"""Receipt-bound review + private candidate push to datax-03. No rollout."""
import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import warnings

import recover_enrichment_gateway as frozen
import production_release_review as review
import transfer_base as trust

ROOT=Path('/var/lib/weknora-glm-release-prepare-20261017-r1')
REMOTE_CODE=None  # exact qualified bundled receiver, assigned by packager
PHASE='PREFLIGHT'


def emit(row): print(json.dumps(row,sort_keys=True),flush=True)


def need(ok,label):
    if not ok: raise RuntimeError(label)


def phase(name):
    global PHASE
    PHASE=name; emit(dict(weknora_release_prepare='RUNNING',phase=name,production_writes=0))


def save(name,value): trust.private_write(ROOT/name,(json.dumps(value,sort_keys=True)+'\n').encode())


def remote_command():
    need(REMOTE_CODE is not None,'packaged_receiver_missing')
    code='import base64;exec(compile(base64.b64decode('+repr(base64.b64encode(REMOTE_CODE.encode()).decode())+'),"receiver.py","exec"))'
    command="sudo -S -p '' -- python3 -I -B -c "+shlex.quote(code)
    need(len(command.encode())<120000,'remote_command_size_bound')
    return ['ssh','-T','-F','/dev/null','-o','StrictHostKeyChecking=yes',
        '-o','UserKnownHostsFile='+str(ROOT/'known_hosts'),'-o','GlobalKnownHostsFile=/dev/null',
        '-o','HostKeyAlias=datax-03','-o','HostKeyAlgorithms=ecdsa-sha2-nistp256',
        '-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
        '-o','NumberOfPasswordPrompts=1','-o','LogLevel=ERROR','cnooc@'+trust.HOST,command]


def send(candidate,q,password,tty):
    child=None; stopped=threading.Event(); rows=queue.Queue(maxsize=8); writer_result=[]
    writer=None; ready=False; skipped=False; result=None; output_bytes=0
    def enqueue(value):
        while not stopped.is_set():
            try: rows.put(value,timeout=.2); return
            except queue.Full: pass
    try:
        child=subprocess.Popen(remote_command(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=tty,bufsize=0)
        def write_all(data):
            sent=0
            while sent<len(data):
                need(not stopped.is_set(),'transfer_cancelled')
                n=child.stdin.write(memoryview(data)[sent:]); need(n is not None and n>0,'ssh_pipe_closed'); sent+=n
        write_all(password)
        for i in range(len(password)): password[i]=0
        def reader():
            try:
                while not stopped.is_set():
                    line=child.stdout.readline(65537)
                    if len(line)>65536: enqueue(('error','remote_line_limit')); return
                    if not line: enqueue(('end',None)); return
                    enqueue(('line',line))
            except Exception: enqueue(('error','ssh_stdout_failure'))
        threading.Thread(target=reader,daemon=True).start()
        def stream():
            try:
                count=0; start=time.monotonic(); h=hashlib.sha256()
                with q.opened(candidate,trust_cli.ARCHIVE_BYTES) as f:
                    while True:
                        block=f.read(65536)
                        if not block: break
                        count+=len(block); need(count<=trust_cli.ARCHIVE_BYTES,'source_grew'); h.update(block); write_all(block)
                        delay=count/(16*1024**2)-(time.monotonic()-start)
                        if delay>0: time.sleep(min(delay,.5))
                need(count==trust_cli.ARCHIVE_BYTES and h.hexdigest()==trust_cli.ARCHIVE_SHA,'source_changed_during_transfer')
                child.stdin.close(); writer_result.append('PASS')
            except BaseException:
                writer_result.append('FAIL'); enqueue(('error','source_transfer_failed_USE_STATUS'))
        start=time.monotonic(); last=start
        while True:
            now=time.monotonic(); need(now-start<1800,'remote_operation_deadline_USE_STATUS')
            if now-last>=20:
                emit(dict(private_preload='RUNNING',elapsed_seconds=int(now-start),phase=PHASE)); last=now
            try: kind,value=rows.get(timeout=.2)
            except queue.Empty: continue
            need(kind!='error',value or 'remote_output_failure')
            if kind=='end': break
            output_bytes+=len(value); need(output_bytes<=1048576,'remote_output_bound')
            row=json.loads(value); need(isinstance(row,dict),'remote_output_not_object')
            if row.get('receiver')=='READY':
                need(not ready and not skipped and row.get('bytes')==trust_cli.ARCHIVE_BYTES,'receiver_handshake')
                ready=True; writer=threading.Thread(target=stream,daemon=True); writer.start()
            elif row.get('receiver')=='SKIP_TRANSFER_COMPLETE_IMAGE_PRESENT':
                need(not ready and not skipped,'receiver_handshake'); skipped=True; child.stdin.close()
            if row.get('production_node_preload') in ('PASS','UNCONFIRMED'): result=row
            emit(row)
        code=child.wait(timeout=10)
        if writer: writer.join(timeout=5)
        need(code==0 and result is not None and result.get('production_node_preload')=='PASS','remote_preload_not_passed_USE_STATUS')
        need((ready and writer_result==['PASS']) or skipped,'transfer_completion_unconfirmed')
        return result
    finally:
        stopped.set()
        for i in range(len(password)): password[i]=0
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
            if writer: writer.join(timeout=5)
            if child.stdin and not child.stdin.closed: child.stdin.close()
            child.stdout.close()


def safe_error(exc):
    v=str(exc)
    return v if type(exc).__name__ in ('RuntimeError','CheckFailure','RecoveryFailure','AcceptanceFailure','PreloadFailure','AuditFailure') and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',v) else 'SAFE_DIAGNOSTIC_REQUIRED'


def run():
    need(not os.path.lexists(str(ROOT)),'previous_attempt_USE_STATUS')
    a=frozen.load_original(); review.accepted(a)
    a.audit.directory(ROOT.parent); ROOT.mkdir(mode=0o700)
    save('STARTED.json',dict(full_acceptance_sha256=review.PASS_SHA,production_writes=0,automatic_retry=False))
    report=dict(weknora_release_prepare='UNCONFIRMED',production_writes=0,production_workloads_mutated=False,
        provider_requests=0,model_changed=False,consumer_slots_changed=False,build_started=False,
        rollout='NOT_PERFORMED',ready_for_rollout=False,automatic_retry=False,remote_operation_attempted=False)
    try:
        phase('PRODUCTION_MODEL_SCOPE_AND_BASELINE')
        details,p,q,guard,plan=review.collect(a,save)
        save('REVIEW.json',details); emit(details)
        phase('VERIFY_SOURCE_ARCHIVE')
        candidate,_=p.source(q)
        phase('VERIFY_PINNED_DATAX03_HOST_KEY')
        raw=trust_cli.command(['ssh-keyscan','-T','5','-t','ecdsa',trust.HOST],timeout=15,limit=65536)
        trust.private_write(ROOT/'known_hosts',trust.known_host(raw))
        guard.live(plan,binary=True)
        phase('PRELOAD_DATAX03_KEEP_CURRENT_APP_RUNNING')
        with open('/dev/tty','w',encoding='utf-8') as tty,warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            secret=getpass.getpass('datax-03 cnooc sudo password (hidden; never saved): ',stream=tty)
            need(0<len(secret)<=2048 and '\n' not in secret and '\r' not in secret,'invalid_password')
            password=bytearray(secret.encode()+b'\n'); del secret
            save('REMOTE-REQUESTED.json',dict(node='datax-03',image=p.IMAGE,archive_sha256=p.ARCHIVE_SHA,
                remote_state='/var/lib/weknora-glm-production-preload-20261017-r1'))
            report['remote_operation_attempted']=True
            result=send(candidate,q,password,tty)
        save('REMOTE-RESULT.json',result)
        phase('FINAL_PRODUCTION_BASELINE')
        guard.live(plan,binary=True)
        report.update(weknora_release_prepare='PASS',production_baseline='UNCHANGED',
            candidate_preloaded_node='datax-03',candidate=result['candidate'],rollback_image=result['rollback_image'],
            production_model_scope_review='COLLECTED_NOT_MODIFIED',gateway_provisioning_review='COLLECTED_NOT_MODIFIED',
            remaining_gates=['REVIEW_MODEL_PROVISIONING_AND_ROLLBACK_PLAN','CURRENT_CLAIMED_WORK_DRAIN_AND_CONTROLLED_IMAGE_SWITCH',
                'PRODUCTION_GLM_MODEL_ACTIVATION','POST_SWITCH_BUSINESS_DOCUMENT_ACCEPTANCE','FRESH88_REVIEW_AFTER_NEW_POD'])
    except BaseException as exc:
        report.update(phase=PHASE,error_type=type(exc).__name__,failed_check=safe_error(exc),raw_error_printed=False)
    save('RESULT.json',report)
    emit(dict(report,report=str(ROOT/'RESULT.json'),report_sha256=hashlib.sha256((ROOT/'RESULT.json').read_bytes()).hexdigest(),
        instruction='USE_STATUS_NO_REPEAT_PREPARE_NO_ROLLOUT'))
    return 0 if report['weknora_release_prepare']=='PASS' else 1


def status():
    out=dict(release_preparation_status='READ_ONLY',files_modified=False,production_writes=0,provider_requests=0,
        ssh_connections=0,remote_state_not_requeried=True,records={})
    if ROOT.exists():
        trust.base.safe_dir(ROOT)
        for name in ('STARTED.json','REMOTE-REQUESTED.json','REMOTE-RESULT.json','RESULT.json'):
            if not (ROOT/name).exists(): continue
            before=trust.base.private_meta(ROOT/name); need(before[2]<1048576,'status_size')
            raw=(ROOT/name).read_bytes(); need(trust.base.private_meta(ROOT/name)==before,'status_changed')
            row=json.loads(raw)
            out['records'][name]=dict(sha256=hashlib.sha256(raw).hexdigest(),fields={k:row.get(k) for k in
                ('weknora_release_prepare','production_node_preload','phase','failed_check','remote_operation_attempted')})
    emit(out); return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__); group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare-once',action='store_true'); group.add_argument('--status',action='store_true'); args=parser.parse_args()
    need(os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01' and platform.machine()=='aarch64','root_arm64_datax01_required')
    os.umask(0o077)
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    if args.status: return status()
    def interrupted(sig,frame): raise RuntimeError('interrupted_USE_STATUS')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,interrupted)
    import fcntl
    trust.base.safe_dir(ROOT.parent)
    fd=os.open(str(ROOT)+'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno()); need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022,'unsafe_lock')
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); return run()


import preload_native_candidate as trust_cli

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        emit(dict(weknora_release_prepare='UNCONFIRMED',phase=PHASE,failed_check=safe_error(exc),
            error_type=type(exc).__name__,production_writes=0,automatic_retry=False,ready_for_rollout=False)); sys.exit(1)
