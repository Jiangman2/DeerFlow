"""Exact datax-03 candidate receiver; no Kubernetes/model/queue mutations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import stat
import sys
import time
from types import SimpleNamespace

import verify_weknora_base as verify
import export_weknora_base as io
import preload_native_candidate as cli

ROOT=Path('/var/lib/weknora-glm-production-preload-20261017-r1')
PHASE='PREFLIGHT'
MODULE_SOURCES=None
require=verify.require


def record(name,row):
    io.safe_dir(ROOT)
    raw=(json.dumps(row,sort_keys=True)+'\n').encode()
    write(name,raw)


def write(name,raw):
    require(Path(name).name==name and name not in ('.','..'),'record_name')
    fd=os.open(str(ROOT/name),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    fd=os.open(str(ROOT),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def boot(sources):
    code='import sys,types\n'
    for name,source in sources.items():
        code+='_m=types.ModuleType('+repr(name)+'); _m.__file__='+repr(name+'.py')+'\n'
        code+='sys.modules['+repr(name)+']=_m\nexec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
    return code


def status_program():
    require(MODULE_SOURCES is not None,'packaged_sources_missing')
    return (boot(MODULE_SOURCES)+'sys.exit(sys.modules["production_node_preload"].main(["--status"]))\n').encode()


def phase(name):
    global PHASE
    PHASE=name; cli.PHASE='DATAX03_'+name
    io.emit({'production_node_preload':'RUNNING','node':'datax-03','phase':name})


def clear_operations():
    raw=cli.command(['ps','-eo','comm=,args='],limit=4*1024**2)
    for line in raw.decode('utf-8','replace').splitlines():
        parts=line.split(None,1)
        if not parts: continue
        name=parts[0]; args=parts[1] if len(parts)>1 else ''
        require(not (name in ('buildctl','nerdctl') or name=='ctr' and
            re.search(r'\bimages\s+(import|export|pull)\b',args)),'other_image_operation_active')


def storage(initial):
    paths=cli.image_fs_paths(cli.cri('imagefsinfo','--output','json'))
    require(Path('/var/lib').stat().st_dev==Path('/var/lib/containerd').stat().st_dev,'archive_imagefs_device_review')
    # Includes a whole cold import, extra copy of every layer, and private archive.
    out=cli.disk_budget(SimpleNamespace(directory=io.safe_dir),paths,initial=initial)
    if initial:
        for p in paths:
            v=os.statvfs(str(p)); _,needed=cli.required_bytes(v.f_blocks*v.f_frsize)
            require(v.f_bavail*v.f_frsize>=needed+cli.ARCHIVE_BYTES,'archive_plus_import_space')
    rows=dict(x.split(':',1) for x in Path('/proc/meminfo').read_text().splitlines())
    require(int(rows['MemAvailable'].split()[0])*1024>=4*cli.GIB,'node_memory_reserve')
    return dict(out,private_archive_on_rootfs=True,archive_bytes=cli.ARCHIVE_BYTES)


def rollback():
    identity=io.current_identity()  # exact live container UID/ID/image, never env output
    complete=cli.command(cli.CTR+['images','check','--snapshotter','overlayfs','--quiet','name=='+io.IMAGE],timeout=60,limit=65536).decode().split()
    require(complete==[io.IMAGE],'rollback_content_or_unpack_incomplete')
    pin=cli.cri('inspecti',io.IMAGE)['status'].get('pinned') is True
    return dict(identity,content_and_unpack='PASS',pinned=pin)


def receive_archive(stream):
    phase('RECEIVE_VERIFIED_ARCHIVE')
    io.emit({'receiver':'READY','bytes':cli.ARCHIVE_BYTES})
    path=ROOT/'candidate.oci.tar.partial'; h=hashlib.sha256(); total=0; start=time.monotonic(); last=start
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        while total<cli.ARCHIVE_BYTES:
            block=stream.read(min(1048576,cli.ARCHIVE_BYTES-total))
            require(block,'archive_truncated_NO_IMPORT'); total+=len(block); h.update(block); f.write(block)
            now=time.monotonic(); require(now-start<300,'archive_transfer_deadline')
            if now-last>=15:
                storage(False); io.emit({'archive_bytes_received':total,'node':'datax-03'}); last=now
        require(stream.read(1)==b'','archive_excess_bytes_NO_IMPORT')
        f.flush(); os.fsync(f.fileno())
    require(h.hexdigest()==cli.ARCHIVE_SHA,'archive_hash_NO_IMPORT')
    phase('VERIFY_ALL_LAYERS_AND_DIFFIDS')
    before=io.private_meta(path)
    r=verify.verify(path,io.emit,cli.MANIFEST,cli.CONFIG,24,cli.COMPRESSED)
    require(r['uncompressed_layer_bytes']==cli.EXPANDED and io.private_meta(path)==before,'archive_layer_verification')
    final=ROOT/'candidate.oci.tar'; require(not os.path.lexists(str(final)),'archive_destination_exists')
    path.rename(final); record('ARCHIVE-VERIFIED.json',dict(sha256=cli.ARCHIVE_SHA,bytes=total,layers=24))
    return final


def common():
    return dict(node='datax-03',production_workloads_mutated=False,production_configuration_changed=False,
        model_changed=False,consumer_slots_changed=False,provider_requests=0,pods_created=0,
        ready_for_rollout=False,automatic_retry=False,production_writes=0)


def accept():
    require(not os.path.lexists(str(ROOT)),'previous_remote_attempt_USE_STATUS')
    io.safe_dir(ROOT.parent); clear_operations(); old=rollback(); budget=storage(True)
    present=cli.reference_present()
    if present: cli.image_check()
    ROOT.mkdir(mode=0o700); write('status.py',status_program())
    record('STARTED.json',dict(old_image=old,budget=budget,candidate=cli.IMAGE,already_present=present))
    requested=False
    try:
        if not present:
            archive=receive_archive(sys.stdin.buffer)
            require(rollback()==old,'old_runtime_changed_before_import'); storage(True); clear_operations()
            require(not cli.reference_present(),'candidate_appeared_before_import')
            phase('IMPORT_CANDIDATE_ONCE')
            record('IMPORT-REQUESTED.json',dict(image=cli.IMAGE,sha256=cli.ARCHIVE_SHA)); requested=True
            cli.command(cli.import_args(archive),timeout=1200,limit=1048576,monitor=lambda:storage(False))
            record('IMPORT-ACK.json',dict(exit_code=0))
        else:
            io.emit({'receiver':'SKIP_TRANSFER_COMPLETE_IMAGE_PRESENT'})
        phase('VERIFY_CRI_CONTENT_AND_PIN')
        current=cli.image_check()
        if not current['pinned']:
            record('PIN-REQUESTED.json',dict(image=cli.IMAGE))
            cli.command(cli.CTR+['images','label',cli.IMAGE,cli.PIN],limit=65536)
            for _ in range(15):
                if cli.image_check()['pinned']: break
                time.sleep(1)
        candidate=cli.image_check(require_pin=True)
        require(rollback()==old,'old_runtime_changed_after_import'); storage(False)
        result=dict(common(),production_node_preload='PASS',candidate=candidate,rollback_image=old,
            imports_attempted=int(requested),reused_existing_image=present,archive_extracted=False)
    except BaseException as exc:
        result=dict(common(),production_node_preload='UNCONFIRMED',phase=PHASE,imports_attempted=int(requested),
            failed_check=safe_error(exc),error_type=type(exc).__name__,last_operation=dict(cli.LAST),raw_error_printed=False)
    record('RESULT.json',result); io.emit(result)
    return 0 if result['production_node_preload']=='PASS' else 1


def safe_error(exc):
    text=str(exc)
    return text if isinstance(exc,(verify.CheckFailure,cli.PreloadFailure)) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',text) else 'SAFE_DIAGNOSTIC_REQUIRED'


def status():
    out=dict(common(),production_node_status='READ_ONLY',state=str(ROOT),files_modified=False)
    out['records']={}
    if ROOT.exists():
        io.safe_dir(ROOT); previous=io.ROOT; io.ROOT=ROOT
        try:
            for name in ('STARTED.json','IMPORT-REQUESTED.json','IMPORT-ACK.json','PIN-REQUESTED.json','RESULT.json'):
                if (ROOT/name).exists():
                    raw,r=io.read_receipt(name)
                    out['records'][name]={'sha256':hashlib.sha256(raw).hexdigest(),'state':r.get('production_node_preload')}
        finally: io.ROOT=previous
    out['candidate']=cli.image_check() if cli.reference_present() else {'present':False}
    io.emit(out); return 0


def main(argv=None):
    p=argparse.ArgumentParser(); group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--receive-once',action='store_true'); group.add_argument('--status',action='store_true'); args=p.parse_args(argv)
    try:
        require(os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-03' and
            platform.machine()=='aarch64','root_arm64_datax03_required')
        os.umask(0o077)
        import resource
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        def interrupted(sig,frame): raise verify.CheckFailure('interrupted_USE_STATUS')
        for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP,signal.SIGALRM): signal.signal(sig,interrupted)
        signal.alarm(1800)
        if args.status: return status()
        import fcntl
        io.safe_dir(ROOT.parent)
        fd=os.open(str(ROOT)+'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'rb') as f:
            s=os.fstat(f.fileno()); require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022,'unsafe_lock')
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return accept()
    except BaseException as exc:
        if isinstance(exc,SystemExit): raise
        io.emit(dict(common(),production_node_preload='UNCONFIRMED',phase=PHASE,failed_check=safe_error(exc),
            error_type=type(exc).__name__,raw_error_printed=False)); return 1
    finally: signal.alarm(0)


if __name__=='__main__': sys.exit(main())
