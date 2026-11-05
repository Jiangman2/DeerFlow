#!/usr/bin/env python3
"""Install/check/run a narrowly scoped auth.log policy. Python 3.7+."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
import types
import uuid

BASE_SOURCE=None  # Filled by the packager from the verified one-time implementation.
BASE_SHA='c4bb6ad4c5b39bb646f207c4c00e7d4ccd7e03067311f4c49f51d23cbd14e546'
OPT='/opt/portal-auth-log-policy-0015-20261028-r1'
RUNNER=OPT+'/maintenance.py'
MANIFEST=OPT+'/INSTALL.json'
CONFIG='/etc/portal-auth-log-0015-20261028-r1.conf'
CRON='/etc/cron.d/portal-auth-log-0015'
STATE='/var/lib/portal-auth-log-policy-0015-20261028-r1'
PENDING='/var/log/portal-auth-log-pending-0015-20261028-r1'
ARCHIVES='/mnt/data/portal-auth-log-archives-0015'
SOURCE='/var/log/auth.log'
THRESHOLD=100*1024**2
ROOT_DEVICE=64771
DATA_DEVICE=64785
MAX_BYTES=8*1024**3
ACCEPTED_REPORT='/mnt/data/portal-auth-log-archive-0015-20261028-r1/REPORT.json'
ACCEPTED_ARCHIVE='/mnt/data/portal-auth-log-archive-0015-20261028-r1/auth.log.20261028-r1'


def require(ok,code):
    if not ok:raise ValueError(code)


def engine():
    require(isinstance(BASE_SOURCE,str) and hashlib.sha256(BASE_SOURCE.encode()).hexdigest()==BASE_SHA,'BASE_SOURCE_CHANGED')
    m=types.ModuleType('verified_auth_log_transfer')
    exec(compile(BASE_SOURCE,'<verified-auth-transfer>','exec'),m.__dict__)
    m.DEADLINE=time.monotonic()+1800
    return m


def read_private(path,limit=262144):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022 and s.st_nlink==1
                and s.st_size<=limit,'PRIVATE_FILE_INVALID')
        data=os.read(fd,limit+1);require(len(data)<=limit,'PRIVATE_FILE_LIMIT')
        return data
    finally:os.close(fd)


def write_json(path,value):
    raw=(json.dumps(value,sort_keys=True)+'\n').encode()
    folder=os.path.dirname(path);temporary=folder+'/.write-'+uuid.uuid4().hex
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        if os.path.lexists(path):read_private(path)
        os.replace(temporary,path)
        d=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(d)
        finally:os.close(d)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def new_file(base,path,data,mode):
    pfd,name=base.parent(path)
    try:
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode,dir_fd=pfd)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.fsync(pfd)
    finally:os.close(pfd)


def new_directory(base,path):
    pfd,name=base.parent(path)
    try:os.mkdir(name,0o700,dir_fd=pfd);os.fsync(pfd)
    finally:os.close(pfd)


def private_directory(path,device):
    s=os.lstat(path)
    require(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o700 and s.st_dev==device,
            'PRIVATE_DIRECTORY_CHANGED')


def rule():
    return (SOURCE+' {\n    size 100M\n    rotate -1\n    missingok\n    notifempty\n'
            '    nocompress\n    nodateext\n    nocopytruncate\n    create 0644 root root\n    olddir '+PENDING+'\n}\n').encode()


def interpreter():
    p=os.path.realpath(sys.executable)
    require(re.fullmatch(r'/(?:usr/)?bin/python3(?:\.[0-9]+)?',p),'TRUSTED_SYSTEM_PYTHON_REQUIRED')
    s=os.lstat(p)
    require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022,'PYTHON_PERMISSIONS')
    return p


def cron(python):
    return ('SHELL=/bin/sh\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\n'
            '17 * * * * root '+python+' -I -B '+RUNNER+' --maintain\n').encode()


def source_stat():
    s=os.lstat(SOURCE)
    require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_dev==ROOT_DEVICE
            and s.st_uid==s.st_gid==0 and stat.S_IMODE(s.st_mode)==0o644 and s.st_size<=MAX_BYTES,'AUTH_LOG_METADATA_CHANGED')
    require('security.kysec' in os.listxattr(SOURCE,follow_symlinks=False),'KYSEC_LABEL_MISSING')
    return s


def installed():
    manifest=json.loads(read_private(MANIFEST))
    require(manifest.get('schema')=='portal-0015-auth-policy-install.v1','INSTALL_MANIFEST_INVALID')
    for path in (RUNNER,CONFIG,CRON):
        require(hashlib.sha256(read_private(path)).hexdigest()==manifest['files'][path],'INSTALLED_FILE_CHANGED')
    require(read_private(CONFIG)==rule() and read_private(CRON)==cron(manifest['python']),'INSTALLED_POLICY_CHANGED')
    return manifest


def preflight(base):
    require(os.stat('/').st_dev==ROOT_DEVICE and os.path.ismount('/mnt/data')
            and os.stat('/mnt/data').st_dev==DATA_DEVICE,'FILESYSTEM_CHANGED')
    s=source_stat();base.config_changed()
    for path,dev in ((STATE,ROOT_DEVICE),(OPT,ROOT_DEVICE),(PENDING,ROOT_DEVICE),(ARCHIVES,DATA_DEVICE)):
        private_directory(path,dev)
    logrotate=next((p for p in ('/usr/sbin/logrotate','/usr/bin/logrotate') if os.path.isfile(p)),None)
    require(logrotate is not None,'LOGROTATE_MISSING')
    p=base.command([logrotate,'--version'])
    require(p.returncode==0 and b'logrotate 3.15.1' in p.stdout+p.stderr,'LOGROTATE_VERSION_CHANGED')
    p=base.command([logrotate,'--debug','--state','/dev/null',CONFIG])
    require(p.returncode==0 and SOURCE.encode() in p.stdout+p.stderr
            and not re.search(rb'^\s*error:',p.stdout+p.stderr,re.M|re.I),'NATIVE_POLICY_DEBUG_REJECTED')
    logger=base.logger_identity()
    writers=base.handles((s.st_dev,s.st_ino))
    require(all(r['pid']==logger[0] for r in writers),'OTHER_AUTH_LOG_USERS_PRESENT')
    return s,logger,logrotate


def lock():
    fd=os.open(STATE+'/LOCK',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    s=os.fstat(fd)
    require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1,'LOCK_FILE_INVALID')
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:os.close(fd);return None
    return fd


def live(base,logger,key):
    require(base.logger_identity()==logger,'LOGGER_CHANGED_BEFORE_ARCHIVE_RELEASE')
    s=os.lstat(SOURCE)
    require(stat.S_ISREG(s.st_mode) and (s.st_dev,s.st_ino)==key,'LIVE_AUTH_LOG_CHANGED')
    require(any(r['pid']==logger[0] and r['writable'] for r in base.handles(key)),'LIVE_AUTH_LOG_WRITER_MISSING')


def journal(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a') as f:f.write(json.dumps(value,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())


def finish_existing(base,old,target,destination,record,before_release):
    """Recover a fully copied file after a crash; never overwrite partial evidence."""
    a=os.open(old,os.O_RDONLY|os.O_NOFOLLOW);b=os.open(target,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        sa,sb=os.fstat(a),os.fstat(b)
        require(stat.S_ISREG(sb.st_mode) and sb.st_uid==0 and sb.st_nlink==1
                and sb.st_size==sa.st_size,'PARTIAL_ARCHIVE_RETAINED_REVIEW_REQUIRED')
        require(sa.st_dev!=sb.st_dev and base.attrs(a)==base.attrs(b)
                and (sa.st_uid,sa.st_gid,stat.S_IMODE(sa.st_mode))==(sb.st_uid,sb.st_gid,stat.S_IMODE(sb.st_mode)),
                'EXISTING_ARCHIVE_METADATA_MISMATCH')
        before=base.identity(sa);copy_before=base.identity(sb)
        h=base.transfer(a,None,sa.st_size,base.RATE*2,'VERIFY_CLOSED_LOG_RECOVERY')
        require(base.transfer(b,None,sa.st_size,base.RATE*2,'VERIFY_ARCHIVE_RECOVERY')==h,'EXISTING_ARCHIVE_HASH_MISMATCH')
        require(base.identity(os.fstat(a))==before==base.identity(os.lstat(old))
                and base.identity(os.fstat(b))==copy_before==base.identity(os.lstat(target)),'RECOVERY_FILE_CHANGED')
        base.unused(sa);before_release()
        record(dict(phase='VERIFIED_ARCHIVE_BEFORE_ROOT_UNLINK',source=old,destination=target,sha256=h,bytes=sa.st_size))
        pfd,name=base.parent(old)
        try:os.unlink(name,dir_fd=pfd);os.fsync(pfd)
        finally:os.close(pfd)
        return dict(destination=target,sha256=h,bytes=sa.st_size,old_root_copy_removed=True,recovered_existing_archive=True)
    finally:os.close(a);os.close(b)


def maintain(base,out,initial,logger,logrotate):
    statepath=STATE+'/CYCLE.json';old=PENDING+'/auth.log.1'
    if os.path.exists(statepath):
        cycle=json.loads(read_private(statepath))
        require(cycle.get('schema')=='portal-0015-auth-cycle.v1'
                and re.fullmatch(r'[0-9a-f]{32}',cycle.get('id',''))
                and type(cycle.get('old_inode')) is int,'PENDING_STATE_INVALID')
    else:
        require(not os.path.lexists(old),'UNTRACKED_OLD_LOG_RETAINED')
        if initial.st_size<THRESHOLD:
            out.update(status='AUTH_LOG_BELOW_100M_NO_ROTATION',current_bytes=initial.st_size);return
        v=os.statvfs(ARCHIVES)
        require(v.f_bavail*v.f_frsize>initial.st_size+20*1024**3,'DATA_HEADROOM')
        cycle=dict(schema='portal-0015-auth-cycle.v1',id=uuid.uuid4().hex,old_inode=initial.st_ino)
        new_directory(base,ARCHIVES+'/'+cycle['id'])
        write_json(statepath,cycle)
    directory=ARCHIVES+'/'+cycle['id'];private_directory(directory,DATA_DEVICE)
    def record(value):
        journal(directory+'/JOURNAL.jsonl',value)
        if value.get('phase')=='VERIFIED_ARCHIVE_BEFORE_ROOT_UNLINK':
            cycle['verified']=value;write_json(statepath,cycle)
    if not os.path.lexists(old) and cycle.get('verified'):
        # The only uncertain remaining action is a local unlink already performed.
        evidence=cycle['verified'];target=directory+'/auth.log.20261028-r1'
        require(evidence.get('destination')==target,'RECOVERY_ARCHIVE_PATH_CHANGED')
        fd=os.open(target,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            s=os.fstat(fd)
            require(stat.S_ISREG(s.st_mode) and s.st_size==evidence['bytes'] and s.st_nlink==1
                    and s.st_uid==s.st_gid==0 and s.st_dev==DATA_DEVICE,'RECOVERY_ARCHIVE_INVALID')
            require(base.transfer(fd,None,s.st_size,base.RATE*2,'VERIFY_COMPLETED_ARCHIVE')==evidence['sha256'],
                    'COMPLETED_ARCHIVE_HASH_MISMATCH')
        finally:os.close(fd)
        live(base,logger,(initial.st_dev,initial.st_ino))
        result=dict(destination=target,sha256=evidence['sha256'],bytes=evidence['bytes'],old_root_copy_removed=True)
    else:
        if not os.path.lexists(old):
            require(initial.st_ino==cycle['old_inode'],'SOURCE_CHANGED_BEFORE_ROTATION')
            require(not list(Path(PENDING).iterdir()),'PENDING_DIRECTORY_NOT_EMPTY')
            base.probe_metadata(SOURCE,(PENDING,directory))
            record(dict(phase='ROTATION_INTENT',old_inode=initial.st_ino))
            out['rotation_requests']=1
            p=base.command([logrotate,'--state',STATE+'/logrotate.state',CONFIG])
            require(p.returncode==0,'ROTATION_OUTCOME_REQUIRES_REVIEW')
        a=os.open(old,os.O_RDONLY|os.O_NOFOLLOW);b=os.open(SOURCE,os.O_RDWR|os.O_NOFOLLOW)
        try:
            sa,sb=os.fstat(a),os.fstat(b)
            require(stat.S_ISREG(sa.st_mode) and sa.st_dev==ROOT_DEVICE and sa.st_ino==cycle['old_inode']
                    and sa.st_nlink==1 and sa.st_size<=MAX_BYTES and sb.st_dev==ROOT_DEVICE
                    and sb.st_ino!=sa.st_ino and stat.S_ISREG(sb.st_mode) and sb.st_nlink==1,'PENDING_FILE_IDENTITIES_CHANGED')
            base.copy_metadata(a,b)
            key=(sb.st_dev,sb.st_ino)
            active=base.handles(key);prior=base.handles((sa.st_dev,sa.st_ino))
            if not any(r['pid']==logger[0] and r['writable'] for r in active) or any(r['pid']==logger[0] for r in prior):
                def signal_logger():
                    record(dict(phase='HUP_INTENT'));out['service_signal_requests']+=1
                    p=base.command(['/usr/bin/systemctl','kill','--kill-who=main','--signal=HUP','rsyslog.service'])
                    require(p.returncode==0,'LOGGER_HUP_NOT_ACKNOWLEDGED')
                base.reopen(a,b,logger,record,signal_logger=signal_logger)
            out['logger_reopen_accepted']=True
        finally:os.close(a);os.close(b)
        v=os.statvfs(ARCHIVES)
        require(v.f_bavail*v.f_frsize>sa.st_size+20*1024**3,'DATA_HEADROOM')
        before_release=lambda:live(base,logger,key)
        target=directory+'/auth.log.20261028-r1'
        if os.path.lexists(target):result=finish_existing(base,old,target,directory,record,before_release)
        else:result=base.relocate_closed(old,directory,record,before_release=before_release)
    write_json(directory+'/RECEIPT.json',dict(cycle_id=cycle['id'],result=result,completed_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
    os.unlink(statepath)
    d=os.open(STATE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(d)
    finally:os.close(d)
    out.update(status='AUTH_LOG_ROTATED_AND_ARCHIVED',cycle_id=cycle['id'],result=result)


def check(base):
    manifest=installed();s,logger,binary=preflight(base)
    return dict(status='AUTH_LOG_PERMANENT_POLICY_CHECKED_NO_ROTATION',current_bytes=s.st_size,
                logger_pid=logger[0],hourly_minute=17,threshold_bytes=THRESHOLD,
                rotation_requests=0,service_signal_requests=0,service_restart_requests=0,
                archive_directory=ARCHIVES,archive_auto_deletion=False,files_verified=len(manifest['files']))


def install(base):
    proof=ACCEPTED_REPORT
    raw=read_private(proof)
    require(len(raw)==1013 and hashlib.sha256(raw).hexdigest()=='3ba110937068b73d50991b60fe417197c37823af4d3370f9f13f12bd90f78d90',
            'ACCEPTED_TRANSFER_RECEIPT_REQUIRED')
    r=json.loads(raw)
    require(r['status']=='AUTH_LOG_ROTATED_ARCHIVED_VERIFIED_ROOT_RELEASED' and r['logger_reopen_accepted']
            and r['security_attributes_preserved'] and r['old_root_copy_removed'],'TRANSFER_NOT_ACCEPTED')
    original=ACCEPTED_ARCHIVE
    s=os.lstat(original)
    require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid==0 and s.st_dev==DATA_DEVICE
            and s.st_size==r['bytes'],'ORIGINAL_RETAINED_ARCHIVE_CHANGED')
    source_stat();base.config_changed();before=base.logger_identity()
    p=base.command(['/usr/bin/systemctl','show','crond.service','--no-pager','--property=ActiveState'])
    require(p.returncode==0 and p.stdout.strip()==b'ActiveState=active','EXISTING_CROND_NOT_ACTIVE')
    require(not os.path.lexists(CONFIG) and not os.path.lexists(CRON),'POLICY_ALREADY_PRESENT_REVIEW_REQUIRED')
    py=interpreter();code=Path(__file__).read_bytes()
    for path in (OPT,STATE,PENDING,ARCHIVES):new_directory(base,path)
    new_file(base,RUNNER,code,0o600);new_file(base,CONFIG,rule(),0o600)
    manifest=dict(schema='portal-0015-auth-policy-install.v1',python=py,
                  files={RUNNER:hashlib.sha256(code).hexdigest(),CONFIG:hashlib.sha256(rule()).hexdigest(),
                         CRON:hashlib.sha256(cron(py)).hexdigest()})
    write_json(MANIFEST,manifest)
    # Verify the root-owned installed program and policy before enabling cron.
    s,logger,binary=preflight(base)
    require(logger==before,'LOGGER_CHANGED_DURING_INSTALL')
    pending_cron=OPT+'/CRON.pending';new_file(base,pending_cron,cron(py),0o600)
    # The final publication enables future runs; no maintenance is invoked here.
    new_file(base,CRON,cron(py),0o644)
    result=check(base)
    result.update(status='AUTH_LOG_PERMANENT_POLICY_INSTALLED_CHECKED_NO_ROTATION',accepted_transfer_report_sha256=hashlib.sha256(raw).hexdigest())
    write_json(STATE+'/INSTALL-REPORT.json',result)
    return result


def run():
    out=dict(status='INCOMPLETE',schema='portal-0015-auth-policy.v1',rotation_requests=0,service_signal_requests=0,
             service_restart_requests=0,workload_patch_requests=0,container_runtime_commands=0,
             credentials_exported=False,log_content_exported=False)
    held=None;mode=sys.argv[1:] or ['INVALID']
    try:
        require(os.name=='posix' and os.geteuid()==0 and sys.flags.isolated and sys.flags.dont_write_bytecode
                and socket.gethostname().split('.')[0]=='ecs-zdz-pro-0015','WRONG_HOST_OR_ENTRY')
        require(mode in (['--install-auth-log-policy-once'],['--check-readonly'],['--maintain']),'EXPLICIT_MODE_REQUIRED')
        base=engine()
        if mode==['--install-auth-log-policy-once']:out.update(install(base))
        elif mode==['--check-readonly']:out.update(check(base))
        else:
            installed()
            held=lock()
            if held is None:out.update(status='AUTH_LOG_MAINTENANCE_ALREADY_RUNNING')
            else:
                s,logger,binary=preflight(base)
                os.nice(19)
                ionice=next((p for p in ('/usr/bin/ionice','/bin/ionice') if os.path.isfile(p)),None)
                require(ionice and base.command([ionice,'-c','3','-p',str(os.getpid())]).returncode==0,'IONICE_REQUIRED')
                maintain(base,out,s,logger,binary)
                write_json(STATE+'/LAST-REPORT.json',out)
    except BaseException as error:
        out.update(status='INCOMPLETE_RETAINED_FILES_REVIEW_REQUIRED',error_class=type(error).__name__,
                   error_code=str(error) if isinstance(error,(ValueError,)) or type(error).__name__=='Refused' else 'DETAILS_WITHHELD')
        if mode==['--maintain'] and os.path.isdir(STATE) and not os.path.islink(STATE):
            try:write_json(STATE+'/LAST-REPORT.json',out)
            except BaseException:pass
    finally:
        if held is not None:os.close(held)
    if mode!=['--maintain'] or out['status']!='AUTH_LOG_BELOW_100M_NO_ROTATION':print(json.dumps(out,sort_keys=True),flush=True)
    return 1 if out['status'].startswith('INCOMPLETE') else 0


if __name__=='__main__':sys.exit(run())
