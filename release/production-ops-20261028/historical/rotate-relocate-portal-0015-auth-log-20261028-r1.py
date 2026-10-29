#!/usr/bin/env python3
"""One auth.log rotation and verified closed-file relocation. Python 3.7+."""
import hashlib
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import syslog
import time

SOURCE='/var/log/auth.log'
OLD_DIR='/var/log/portal-auth-log-rotation-20261028-r1'
DEST='/mnt/data/portal-auth-log-archive-0015-20261028-r1'
ROOT_DEVICE=64771
DATA_DEVICE=64785
INODE=1441870
MAX_BYTES=8*1024**3
DEADLINE=0
RATE=16*1024**2


class Refused(Exception):
    pass


def require(ok,code):
    if not ok: raise Refused(code)


def budget():
    require(not DEADLINE or time.monotonic()<DEADLINE,'TIME_LIMIT')


def parent(path):
    parts=Path(path).parts
    require(parts[0]=='/' and '..' not in parts,'INVALID_PATH')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for p in parts[1:-1]:
            new=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=new
        st=os.fstat(fd)
        require(st.st_uid==0 and not st.st_mode&0o022,'PARENT_NOT_PRIVATE_TO_ROOT')
        return fd,parts[-1]
    except BaseException:
        os.close(fd);raise


def identity(s):
    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_uid,s.st_gid,s.st_nlink)


def attrs(fd):
    return {k:os.getxattr(fd,k) for k in os.listxattr(fd)}


def copy_metadata(src,dst):
    before=os.fstat(src)
    os.fchown(dst,before.st_uid,before.st_gid)
    os.fchmod(dst,stat.S_IMODE(before.st_mode))
    expected=attrs(src)
    for name,value in expected.items(): os.setxattr(dst,name,value)
    for name in set(attrs(dst))-set(expected): os.removexattr(dst,name)
    require(attrs(dst)==expected,'XATTR_NOT_PRESERVED')
    after=os.fstat(dst)
    require((after.st_uid,after.st_gid,stat.S_IMODE(after.st_mode))==
            (before.st_uid,before.st_gid,stat.S_IMODE(before.st_mode)),'FILE_PERMISSIONS_CHANGED')
    os.fsync(dst)


def safe_binary(path):
    require(path in ('/usr/sbin/logrotate','/usr/bin/logrotate','/usr/bin/systemctl','/usr/bin/ionice','/bin/ionice'),
            'BINARY_NOT_ALLOWED')
    st=os.lstat(path)
    require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022,'BINARY_PERMISSIONS')
    return path


def command(args,timeout=20):
    budget()
    safe_binary(args[0])
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,
                     env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
    require(len(p.stdout)+len(p.stderr)<=262144,'COMMAND_OUTPUT_LIMIT')
    return p


def logger_identity():
    p=command(['/usr/bin/systemctl','show','rsyslog.service','--no-pager','--property=MainPID,ActiveState'])
    require(p.returncode==0,'LOGGER_STATE_UNAVAILABLE')
    fields=dict(x.split('=',1) for x in p.stdout.decode().splitlines() if '=' in x)
    require(fields.get('ActiveState')=='active' and re.fullmatch('[1-9][0-9]*',fields.get('MainPID','')),'LOGGER_NOT_ACTIVE')
    pid=int(fields['MainPID'])
    require(Path('/proc/%d/comm'%pid).read_text().strip()=='rsyslogd','LOGGER_PROCESS_CHANGED')
    data=Path('/proc/%d/stat'%pid).read_text().rsplit(')',1)[1].split()
    return (pid,data[19])


def handles(key):
    rows=[];checked=0;end=time.monotonic()+35
    for name in os.listdir('/proc'):
        if not name.isdigit() or int(name)==os.getpid(): continue
        try:
            with os.scandir('/proc/'+name+'/fd') as entries:
                for e in entries:
                    checked+=1
                    require(checked<=200000 and time.monotonic()<end,'FD_SCAN_LIMIT')
                    try: s=e.stat()
                    except FileNotFoundError: continue
                    if (s.st_dev,s.st_ino)==key:
                        info=Path('/proc/'+name+'/fdinfo/'+e.name).read_text()[:4096]
                        m=re.search(r'^flags:\s*([0-7]+)',info,re.M)
                        mode=int(m.group(1),8)&os.O_ACCMODE if m else None
                        rows.append(dict(pid=int(name),fd=int(e.name),writable=mode in (os.O_WRONLY,os.O_RDWR)))
        except (FileNotFoundError,ProcessLookupError): continue
    return rows


def unused(s):
    require(not handles((s.st_dev,s.st_ino)),'OLD_LOG_STILL_OPEN_RETAINED')
    # A mapped file also keeps blocks alive; never remove an actively mapped log.
    end=time.monotonic()+35
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or int(pid)==os.getpid(): continue
        require(time.monotonic()<end,'MAP_SCAN_LIMIT')
        try:
            with open('/proc/'+pid+'/maps') as f:
                for i,line in enumerate(f):
                    require(i<100000,'MAP_ENTRY_LIMIT')
                    fields=line.split(None,5)
                    if len(fields)>=5 and int(fields[4])==s.st_ino:
                        major,minor=[int(x,16) for x in fields[3].split(':')]
                        require(os.makedev(major,minor)!=s.st_dev,'OLD_LOG_MAPPED_RETAINED')
        except (FileNotFoundError,ProcessLookupError): continue


def transfer(src,dst,size,rate,phase):
    h=hashlib.sha256();done=0;start=last=time.monotonic()
    print(json.dumps(dict(phase=phase,bytes_done=0,total_bytes=size)),flush=True)
    while True:
        budget();b=os.read(src,1024**2)
        if not b: break
        done+=len(b);require(done<=size,'CLOSED_LOG_GREW')
        h.update(b)
        if dst is not None:
            view=memoryview(b)
            while view:
                n=os.write(dst,view);require(n>0,'SHORT_WRITE');view=view[n:]
        if time.monotonic()-last>=10:
            print(json.dumps(dict(phase=phase,bytes_done=done,total_bytes=size)),flush=True);last=time.monotonic()
        if rate:
            delay=done/rate-(time.monotonic()-start)
            if delay>0:time.sleep(min(delay,1))
    require(done==size,'COPY_LENGTH_MISMATCH')
    return h.hexdigest()


def relocate_closed(path,destdir,journal,check_unused=unused,before_release=lambda:None):
    srcparent,name=parent(path);dstparent,target=parent(destdir+'/auth.log.20261028-r1')
    src=dst=None
    try:
        src=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=srcparent);initial=os.fstat(src)
        require(stat.S_ISREG(initial.st_mode) and initial.st_nlink==1 and initial.st_size<=MAX_BYTES,'OLD_FILE_INVALID')
        require(os.fstat(dstparent).st_dev!=initial.st_dev,'DESTINATION_MUST_DIFFER')
        check_unused(initial)
        dst=os.open(target,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=dstparent)
        digest=transfer(src,dst,initial.st_size,RATE,'COPY_CLOSED_AUTH_LOG')
        copy_metadata(src,dst)
        os.utime(dst,ns=(initial.st_atime_ns,initial.st_mtime_ns));os.fsync(dst);os.fsync(dstparent)
        copied=identity(os.fstat(dst));os.lseek(dst,0,os.SEEK_SET)
        require(transfer(dst,None,initial.st_size,RATE*2,'VERIFY_RETAINED_AUTH_LOG')==digest,'ARCHIVE_HASH_MISMATCH')
        require(identity(os.fstat(src))==identity(initial)==identity(os.stat(name,dir_fd=srcparent,follow_symlinks=False)),
                'OLD_LOG_CHANGED_RETAINED')
        require(identity(os.fstat(dst))==copied==identity(os.stat(target,dir_fd=dstparent,follow_symlinks=False)),
                'ARCHIVE_CHANGED_RETAINED')
        require(attrs(src)==attrs(dst),'ARCHIVE_XATTR_MISMATCH')
        check_unused(initial)
        before_release()
        journal(dict(phase='VERIFIED_ARCHIVE_BEFORE_ROOT_UNLINK',bytes=initial.st_size,sha256=digest,
                     destination=destdir+'/'+target,source=path,allocated_bytes=initial.st_blocks*512))
        os.unlink(name,dir_fd=srcparent);os.fsync(srcparent)
        return dict(bytes=initial.st_size,sha256=digest,destination=destdir+'/'+target,
                    estimated_reclaimed_bytes=initial.st_blocks*512,old_root_copy_removed=True)
    finally:
        for fd in (src,dst,srcparent,dstparent):
            if fd is not None:os.close(fd)


def config_changed():
    expected={'/etc/logrotate.conf':'f0a00814bbf47af061041b7f4f3cb61a8a349a59011e1f49f9e37a9427df2ef7',
              '/etc/logrotate.d/rsyslog':'7ae3684d3c5936f2c72f67a6d21f3f81aeee4cff5628e8dc44990aa75d19802e',
              '/etc/rsyslog.conf':'f96e7cd75e6e026c8768a3aca235fdfc352f736e50d95b5ed0fbd7d1866ebb54'}
    for path,digest in expected.items():
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            require(os.fstat(fd).st_size<=262144,'CONFIG_SIZE')
            require(hashlib.sha256(os.read(fd,262145)).hexdigest()==digest,'OBSERVED_CONFIG_CHANGED')
        finally:os.close(fd)
    paths=list(Path('/etc/logrotate.d').iterdir());require(len(paths)<=128,'CONFIG_COUNT')
    for p in paths:
        require(p.is_file() and not p.is_symlink() and p.stat().st_size<=262144,'CONFIG_LAYOUT_CHANGED')
        for line in p.read_text(errors='replace').splitlines():
            if line.lstrip().startswith('#'):continue
            patterns=re.findall(r'/var/log/[A-Za-z0-9_./*?\[\]-]+',line)
            require(not any(fnmatch.fnmatchcase(SOURCE,p) for p in patterns),
                    'POSSIBLE_AUTH_ROTATION_ALREADY_CONFIGURED')


def rotation_policy():
    return (SOURCE+' {\n    size 100M\n    rotate -1\n    missingok\n    notifempty\n    nocompress\n'
            '    nodateext\n    nocopytruncate\n    create 0644 root root\n    olddir '+OLD_DIR+'\n}\n').encode()


def probe_metadata(path,directories):
    src=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        for directory in directories:
            pfd,name=parent(directory+'/.metadata-probe')
            dst=None
            try:
                dst=os.open(name,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=pfd)
                copy_metadata(src,dst)
                require(os.fstat(dst).st_size==0,'METADATA_PROBE_NOT_EMPTY')
                os.unlink(name,dir_fd=pfd);os.fsync(pfd)
            finally:
                if dst is not None:os.close(dst)
                os.close(pfd)
        return sorted(attrs(src))
    finally:os.close(src)


def reopen(oldfd,newfd,initial_logger,journal,signal_logger=None,emit=None):
    copy_metadata(oldfd,newfd)
    require(logger_identity()==initial_logger,'LOGGER_CHANGED_BEFORE_SIGNAL')
    journal(dict(phase='NEW_LOG_METADATA_VERIFIED_BEFORE_HUP',xattr_names=sorted(attrs(newfd))))
    if signal_logger is None:
        p=command(['/usr/bin/systemctl','kill','--kill-who=main','--signal=HUP','rsyslog.service'])
        require(p.returncode==0,'LOGGER_HUP_NOT_ACKNOWLEDGED')
    else:signal_logger()
    if emit is None:
        syslog.openlog('portal-auth-log-rotation',0,syslog.LOG_AUTHPRIV)
        syslog.syslog(syslog.LOG_NOTICE,'Auth log rotation verification 20261028-r1; no business data.')
        syslog.closelog()
    else:emit()
    old=os.fstat(oldfd);new=os.fstat(newfd);until=time.monotonic()+30
    while True:
        require(logger_identity()==initial_logger,'LOGGER_RESTARTED_OR_CHANGED')
        active=handles((new.st_dev,new.st_ino));prior=handles((old.st_dev,old.st_ino))
        if any(r['pid']==initial_logger[0] and r['writable'] for r in active) and not prior:break
        require(time.monotonic()<until,'LOGGER_REOPEN_NOT_CONFIRMED_OLD_LOG_RETAINED')
        time.sleep(1)
    journal(dict(phase='LOGGER_REOPEN_ACCEPTED',pid=initial_logger[0],new_inode=new.st_ino,old_inode=old.st_ino))


def main():
    global DEADLINE
    out=dict(schema='portal-0015-auth-log-relocation.v1',status='INCOMPLETE',once_claimed=False,
             rotation_requests=0,service_signal_requests=0,service_restart_requests=0,
             container_runtime_commands=0,workload_patch_requests=0,automatic_retry=False,
             old_root_copy_removed=False,credentials_exported=False,log_content_exported=False,
             permanent_rotation_policy_installed=False)
    started=time.monotonic();d=None
    try:
        require(os.name=='posix' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='ecs-zdz-pro-0015'
                and sys.flags.isolated and sys.flags.dont_write_bytecode
                and sys.argv[1:]==['--rotate-relocate-auth-log-once'],'WRONG_HOST_OR_ENTRY')
        DEADLINE=time.monotonic()+2400
        s=os.lstat(SOURCE)
        require(stat.S_ISREG(s.st_mode) and s.st_ino==INODE and s.st_dev==ROOT_DEVICE and s.st_nlink==1
                and s.st_uid==s.st_gid==0 and stat.S_IMODE(s.st_mode)==0o644 and 1024**3<s.st_size<=MAX_BYTES,
                'OBSERVED_AUTH_LOG_CHANGED')
        require(os.stat('/').st_dev==ROOT_DEVICE and os.path.ismount('/mnt/data')
                and os.stat('/mnt/data').st_dev==DATA_DEVICE,'FILESYSTEM_CHANGED')
        v=os.statvfs('/mnt/data');require(v.f_bavail*v.f_frsize>s.st_size+20*1024**3,'DATA_HEADROOM')
        config_changed();initial_logger=logger_identity()
        writers=handles((s.st_dev,s.st_ino))
        require(writers and all(r['pid']==initial_logger[0] for r in writers)
                and any(r['writable'] for r in writers),'OTHER_LOG_USERS_PRESENT')
        binary=next((p for p in ('/usr/sbin/logrotate','/usr/bin/logrotate') if os.path.isfile(p)),None)
        require(binary is not None,'LOGROTATE_MISSING')
        p=command([binary,'--version']);require(p.returncode==0 and b'logrotate 3.15.1' in p.stdout+p.stderr,'LOGROTATE_VERSION_CHANGED')
        os.nice(19);ionice=shutil.which('ionice',path='/usr/bin:/bin')
        require(ionice is not None and command([ionice,'-c','3','-p',str(os.getpid())]).returncode==0,'IONICE_REQUIRED')
        for path,device in ((DEST,DATA_DEVICE),(OLD_DIR,ROOT_DEVICE)):
            pfd,name=parent(path)
            try:
                require(os.fstat(pfd).st_dev==device,'PARENT_DEVICE_CHANGED')
                os.mkdir(name,0o700,dir_fd=pfd);os.fsync(pfd)
                if path==DEST:
                    out['once_claimed']=True
                    d=os.open(DEST,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            finally:os.close(pfd)
        def journal(row):
            fd=os.open('JOURNAL.jsonl',os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600,dir_fd=d)
            with os.fdopen(fd,'a') as f:f.write(json.dumps(row,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
            os.fsync(d)
        out['security_attribute_probe']=dict(status='PASS',xattr_names=probe_metadata(SOURCE,(OLD_DIR,DEST)))
        out['root_available_before']=os.statvfs('/').f_bavail*os.statvfs('/').f_frsize
        raw=rotation_policy();fd=os.open('ROTATION.conf',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=d)
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        config=DEST+'/ROTATION.conf'
        p=command([binary,'--debug','--state','/dev/null',config])
        output=p.stdout+p.stderr
        require(p.returncode==0 and SOURCE.encode() in output and not re.search(rb'^\s*error:',output,re.M|re.I),
                'NATIVE_PREFLIGHT_REJECTED')
        journal(dict(phase='ROTATION_INTENT',old_inode=INODE,old_dir=OLD_DIR,logger_pid=initial_logger[0],
                     configuration_sha256=hashlib.sha256(raw).hexdigest()))
        out['rotation_requests']=1
        print(json.dumps(dict(phase='ROTATE_AUTH_LOG_WITHOUT_SERVICE_RESTART')),flush=True)
        p=command([binary,'--state',DEST+'/logrotate.state',config])
        out['rotation_command_returncode']=p.returncode
        require(p.returncode==0,'ROTATION_NOT_ACCEPTED_REVIEW_RETAINED_FILES')
        old=OLD_DIR+'/auth.log.1'
        a=os.open(old,os.O_RDONLY|os.O_NOFOLLOW);b=os.open(SOURCE,os.O_RDWR|os.O_NOFOLLOW)
        try:
            oldst,newst=os.fstat(a),os.fstat(b)
            require(oldst.st_ino==INODE and oldst.st_dev==ROOT_DEVICE and newst.st_ino!=INODE
                    and stat.S_ISREG(newst.st_mode) and newst.st_dev==ROOT_DEVICE and newst.st_nlink==1,
                    'ROTATED_FILE_IDENTITIES_UNEXPECTED')
            journal(dict(phase='ROTATED_OLD_LOG_RETAINED',old_inode=oldst.st_ino,new_inode=newst.st_ino))
            # Metadata is restored before the only service signal is permitted.
            def signal_logger():
                journal(dict(phase='HUP_INTENT'));out['service_signal_requests']=1
                p=command(['/usr/bin/systemctl','kill','--kill-who=main','--signal=HUP','rsyslog.service'])
                require(p.returncode==0,'LOGGER_HUP_NOT_ACKNOWLEDGED')
            reopen(a,b,initial_logger,journal,signal_logger=signal_logger)
            out['logger_reopen_accepted']=True
            out['security_attributes_preserved']=True
            current_key=(newst.st_dev,newst.st_ino)
        finally:os.close(a);os.close(b)
        def verify_live_before_release():
            require(logger_identity()==initial_logger,'LOGGER_CHANGED_BEFORE_ROOT_RELEASE')
            current=os.lstat(SOURCE)
            require(stat.S_ISREG(current.st_mode) and (current.st_dev,current.st_ino)==current_key,
                    'ACTIVE_LOG_CHANGED_BEFORE_ROOT_RELEASE')
            require(any(r['pid']==initial_logger[0] and r['writable'] for r in handles(current_key)),
                    'ACTIVE_LOG_WRITER_NOT_CONFIRMED')
        out.update(relocate_closed(old,DEST,journal,before_release=verify_live_before_release))
        require(logger_identity()==initial_logger,'LOGGER_CHANGED_AFTER_TRANSFER')
        out['root_available_after']=os.statvfs('/').f_bavail*os.statvfs('/').f_frsize
        out['actual_root_free_increase_bytes']=out['root_available_after']-out['root_available_before']
        out['status']='AUTH_LOG_ROTATED_ARCHIVED_VERIFIED_ROOT_RELEASED'
    except BaseException as error:
        out.update(status='INCOMPLETE_REVIEW_JOURNAL_NO_RETRY',error_class=type(error).__name__,
                   error_code=str(error) if isinstance(error,Refused) else 'DETAILS_WITHHELD')
    finally:
        out['elapsed_seconds']=round(time.monotonic()-started,3)
        if d is not None:
            raw=(json.dumps(out,sort_keys=True)+'\n').encode()
            try:
                fd=os.open('REPORT.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=d)
                with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
                os.fsync(d)
                out.update(report_path=DEST+'/REPORT.json',report_bytes=len(raw),report_sha256=hashlib.sha256(raw).hexdigest())
            except BaseException:out.update(status='INCOMPLETE_REPORT_WRITE_REVIEW_JOURNAL')
            os.close(d)
    print(json.dumps(out,sort_keys=True),flush=True)
    return 0 if out['status']=='AUTH_LOG_ROTATED_ARCHIVED_VERIFIED_ROOT_RELEASED' else 1


if __name__=='__main__':sys.exit(main())
