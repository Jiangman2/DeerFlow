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

BASE_SOURCE='#!/usr/bin/env python3\n"""One auth.log rotation and verified closed-file relocation. Python 3.7+."""\nimport hashlib\nimport fnmatch\nimport json\nimport os\nfrom pathlib import Path\nimport re\nimport shutil\nimport socket\nimport stat\nimport subprocess\nimport sys\nimport syslog\nimport time\n\nSOURCE=\'/var/log/auth.log\'\nOLD_DIR=\'/var/log/portal-auth-log-rotation-20261028-r1\'\nDEST=\'/mnt/data/portal-auth-log-archive-0015-20261028-r1\'\nROOT_DEVICE=64771\nDATA_DEVICE=64785\nINODE=1441870\nMAX_BYTES=8*1024**3\nDEADLINE=0\nRATE=16*1024**2\n\n\nclass Refused(Exception):\n    pass\n\n\ndef require(ok,code):\n    if not ok: raise Refused(code)\n\n\ndef budget():\n    require(not DEADLINE or time.monotonic()<DEADLINE,\'TIME_LIMIT\')\n\n\ndef parent(path):\n    parts=Path(path).parts\n    require(parts[0]==\'/\' and \'..\' not in parts,\'INVALID_PATH\')\n    fd=os.open(\'/\',os.O_RDONLY|os.O_DIRECTORY)\n    try:\n        for p in parts[1:-1]:\n            new=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)\n            os.close(fd);fd=new\n        st=os.fstat(fd)\n        require(st.st_uid==0 and not st.st_mode&0o022,\'PARENT_NOT_PRIVATE_TO_ROOT\')\n        return fd,parts[-1]\n    except BaseException:\n        os.close(fd);raise\n\n\ndef identity(s):\n    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_uid,s.st_gid,s.st_nlink)\n\n\ndef attrs(fd):\n    return {k:os.getxattr(fd,k) for k in os.listxattr(fd)}\n\n\ndef copy_metadata(src,dst):\n    before=os.fstat(src)\n    os.fchown(dst,before.st_uid,before.st_gid)\n    os.fchmod(dst,stat.S_IMODE(before.st_mode))\n    expected=attrs(src)\n    for name,value in expected.items(): os.setxattr(dst,name,value)\n    for name in set(attrs(dst))-set(expected): os.removexattr(dst,name)\n    require(attrs(dst)==expected,\'XATTR_NOT_PRESERVED\')\n    after=os.fstat(dst)\n    require((after.st_uid,after.st_gid,stat.S_IMODE(after.st_mode))==\n            (before.st_uid,before.st_gid,stat.S_IMODE(before.st_mode)),\'FILE_PERMISSIONS_CHANGED\')\n    os.fsync(dst)\n\n\ndef safe_binary(path):\n    require(path in (\'/usr/sbin/logrotate\',\'/usr/bin/logrotate\',\'/usr/bin/systemctl\',\'/usr/bin/ionice\',\'/bin/ionice\'),\n            \'BINARY_NOT_ALLOWED\')\n    st=os.lstat(path)\n    require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022,\'BINARY_PERMISSIONS\')\n    return path\n\n\ndef command(args,timeout=20):\n    budget()\n    safe_binary(args[0])\n    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,\n                     env={\'PATH\':\'/usr/sbin:/usr/bin:/sbin:/bin\',\'LC_ALL\':\'C\'})\n    require(len(p.stdout)+len(p.stderr)<=262144,\'COMMAND_OUTPUT_LIMIT\')\n    return p\n\n\ndef logger_identity():\n    p=command([\'/usr/bin/systemctl\',\'show\',\'rsyslog.service\',\'--no-pager\',\'--property=MainPID,ActiveState\'])\n    require(p.returncode==0,\'LOGGER_STATE_UNAVAILABLE\')\n    fields=dict(x.split(\'=\',1) for x in p.stdout.decode().splitlines() if \'=\' in x)\n    require(fields.get(\'ActiveState\')==\'active\' and re.fullmatch(\'[1-9][0-9]*\',fields.get(\'MainPID\',\'\')),\'LOGGER_NOT_ACTIVE\')\n    pid=int(fields[\'MainPID\'])\n    require(Path(\'/proc/%d/comm\'%pid).read_text().strip()==\'rsyslogd\',\'LOGGER_PROCESS_CHANGED\')\n    data=Path(\'/proc/%d/stat\'%pid).read_text().rsplit(\')\',1)[1].split()\n    return (pid,data[19])\n\n\ndef handles(key):\n    rows=[];checked=0;end=time.monotonic()+35\n    for name in os.listdir(\'/proc\'):\n        if not name.isdigit() or int(name)==os.getpid(): continue\n        try:\n            with os.scandir(\'/proc/\'+name+\'/fd\') as entries:\n                for e in entries:\n                    checked+=1\n                    require(checked<=200000 and time.monotonic()<end,\'FD_SCAN_LIMIT\')\n                    try: s=e.stat()\n                    except FileNotFoundError: continue\n                    if (s.st_dev,s.st_ino)==key:\n                        info=Path(\'/proc/\'+name+\'/fdinfo/\'+e.name).read_text()[:4096]\n                        m=re.search(r\'^flags:\\s*([0-7]+)\',info,re.M)\n                        mode=int(m.group(1),8)&os.O_ACCMODE if m else None\n                        rows.append(dict(pid=int(name),fd=int(e.name),writable=mode in (os.O_WRONLY,os.O_RDWR)))\n        except (FileNotFoundError,ProcessLookupError): continue\n    return rows\n\n\ndef unused(s):\n    require(not handles((s.st_dev,s.st_ino)),\'OLD_LOG_STILL_OPEN_RETAINED\')\n    # A mapped file also keeps blocks alive; never remove an actively mapped log.\n    end=time.monotonic()+35\n    for pid in os.listdir(\'/proc\'):\n        if not pid.isdigit() or int(pid)==os.getpid(): continue\n        require(time.monotonic()<end,\'MAP_SCAN_LIMIT\')\n        try:\n            with open(\'/proc/\'+pid+\'/maps\') as f:\n                for i,line in enumerate(f):\n                    require(i<100000,\'MAP_ENTRY_LIMIT\')\n                    fields=line.split(None,5)\n                    if len(fields)>=5 and int(fields[4])==s.st_ino:\n                        major,minor=[int(x,16) for x in fields[3].split(\':\')]\n                        require(os.makedev(major,minor)!=s.st_dev,\'OLD_LOG_MAPPED_RETAINED\')\n        except (FileNotFoundError,ProcessLookupError): continue\n\n\ndef transfer(src,dst,size,rate,phase):\n    h=hashlib.sha256();done=0;start=last=time.monotonic()\n    print(json.dumps(dict(phase=phase,bytes_done=0,total_bytes=size)),flush=True)\n    while True:\n        budget();b=os.read(src,1024**2)\n        if not b: break\n        done+=len(b);require(done<=size,\'CLOSED_LOG_GREW\')\n        h.update(b)\n        if dst is not None:\n            view=memoryview(b)\n            while view:\n                n=os.write(dst,view);require(n>0,\'SHORT_WRITE\');view=view[n:]\n        if time.monotonic()-last>=10:\n            print(json.dumps(dict(phase=phase,bytes_done=done,total_bytes=size)),flush=True);last=time.monotonic()\n        if rate:\n            delay=done/rate-(time.monotonic()-start)\n            if delay>0:time.sleep(min(delay,1))\n    require(done==size,\'COPY_LENGTH_MISMATCH\')\n    return h.hexdigest()\n\n\ndef relocate_closed(path,destdir,journal,check_unused=unused,before_release=lambda:None):\n    srcparent,name=parent(path);dstparent,target=parent(destdir+\'/auth.log.20261028-r1\')\n    src=dst=None\n    try:\n        src=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=srcparent);initial=os.fstat(src)\n        require(stat.S_ISREG(initial.st_mode) and initial.st_nlink==1 and initial.st_size<=MAX_BYTES,\'OLD_FILE_INVALID\')\n        require(os.fstat(dstparent).st_dev!=initial.st_dev,\'DESTINATION_MUST_DIFFER\')\n        check_unused(initial)\n        dst=os.open(target,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=dstparent)\n        digest=transfer(src,dst,initial.st_size,RATE,\'COPY_CLOSED_AUTH_LOG\')\n        copy_metadata(src,dst)\n        os.utime(dst,ns=(initial.st_atime_ns,initial.st_mtime_ns));os.fsync(dst);os.fsync(dstparent)\n        copied=identity(os.fstat(dst));os.lseek(dst,0,os.SEEK_SET)\n        require(transfer(dst,None,initial.st_size,RATE*2,\'VERIFY_RETAINED_AUTH_LOG\')==digest,\'ARCHIVE_HASH_MISMATCH\')\n        require(identity(os.fstat(src))==identity(initial)==identity(os.stat(name,dir_fd=srcparent,follow_symlinks=False)),\n                \'OLD_LOG_CHANGED_RETAINED\')\n        require(identity(os.fstat(dst))==copied==identity(os.stat(target,dir_fd=dstparent,follow_symlinks=False)),\n                \'ARCHIVE_CHANGED_RETAINED\')\n        require(attrs(src)==attrs(dst),\'ARCHIVE_XATTR_MISMATCH\')\n        check_unused(initial)\n        before_release()\n        journal(dict(phase=\'VERIFIED_ARCHIVE_BEFORE_ROOT_UNLINK\',bytes=initial.st_size,sha256=digest,\n                     destination=destdir+\'/\'+target,source=path,allocated_bytes=initial.st_blocks*512))\n        os.unlink(name,dir_fd=srcparent);os.fsync(srcparent)\n        return dict(bytes=initial.st_size,sha256=digest,destination=destdir+\'/\'+target,\n                    estimated_reclaimed_bytes=initial.st_blocks*512,old_root_copy_removed=True)\n    finally:\n        for fd in (src,dst,srcparent,dstparent):\n            if fd is not None:os.close(fd)\n\n\ndef config_changed():\n    expected={\'/etc/logrotate.conf\':\'f0a00814bbf47af061041b7f4f3cb61a8a349a59011e1f49f9e37a9427df2ef7\',\n              \'/etc/logrotate.d/rsyslog\':\'7ae3684d3c5936f2c72f67a6d21f3f81aeee4cff5628e8dc44990aa75d19802e\',\n              \'/etc/rsyslog.conf\':\'f96e7cd75e6e026c8768a3aca235fdfc352f736e50d95b5ed0fbd7d1866ebb54\'}\n    for path,digest in expected.items():\n        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)\n        try:\n            require(os.fstat(fd).st_size<=262144,\'CONFIG_SIZE\')\n            require(hashlib.sha256(os.read(fd,262145)).hexdigest()==digest,\'OBSERVED_CONFIG_CHANGED\')\n        finally:os.close(fd)\n    paths=list(Path(\'/etc/logrotate.d\').iterdir());require(len(paths)<=128,\'CONFIG_COUNT\')\n    for p in paths:\n        require(p.is_file() and not p.is_symlink() and p.stat().st_size<=262144,\'CONFIG_LAYOUT_CHANGED\')\n        for line in p.read_text(errors=\'replace\').splitlines():\n            if line.lstrip().startswith(\'#\'):continue\n            patterns=re.findall(r\'/var/log/[A-Za-z0-9_./*?\\[\\]-]+\',line)\n            require(not any(fnmatch.fnmatchcase(SOURCE,p) for p in patterns),\n                    \'POSSIBLE_AUTH_ROTATION_ALREADY_CONFIGURED\')\n\n\ndef rotation_policy():\n    return (SOURCE+\' {\\n    size 100M\\n    rotate -1\\n    missingok\\n    notifempty\\n    nocompress\\n\'\n            \'    nodateext\\n    nocopytruncate\\n    create 0644 root root\\n    olddir \'+OLD_DIR+\'\\n}\\n\').encode()\n\n\ndef probe_metadata(path,directories):\n    src=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)\n    try:\n        for directory in directories:\n            pfd,name=parent(directory+\'/.metadata-probe\')\n            dst=None\n            try:\n                dst=os.open(name,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=pfd)\n                copy_metadata(src,dst)\n                require(os.fstat(dst).st_size==0,\'METADATA_PROBE_NOT_EMPTY\')\n                os.unlink(name,dir_fd=pfd);os.fsync(pfd)\n            finally:\n                if dst is not None:os.close(dst)\n                os.close(pfd)\n        return sorted(attrs(src))\n    finally:os.close(src)\n\n\ndef reopen(oldfd,newfd,initial_logger,journal,signal_logger=None,emit=None):\n    copy_metadata(oldfd,newfd)\n    require(logger_identity()==initial_logger,\'LOGGER_CHANGED_BEFORE_SIGNAL\')\n    journal(dict(phase=\'NEW_LOG_METADATA_VERIFIED_BEFORE_HUP\',xattr_names=sorted(attrs(newfd))))\n    if signal_logger is None:\n        p=command([\'/usr/bin/systemctl\',\'kill\',\'--kill-who=main\',\'--signal=HUP\',\'rsyslog.service\'])\n        require(p.returncode==0,\'LOGGER_HUP_NOT_ACKNOWLEDGED\')\n    else:signal_logger()\n    if emit is None:\n        syslog.openlog(\'portal-auth-log-rotation\',0,syslog.LOG_AUTHPRIV)\n        syslog.syslog(syslog.LOG_NOTICE,\'Auth log rotation verification 20261028-r1; no business data.\')\n        syslog.closelog()\n    else:emit()\n    old=os.fstat(oldfd);new=os.fstat(newfd);until=time.monotonic()+30\n    while True:\n        require(logger_identity()==initial_logger,\'LOGGER_RESTARTED_OR_CHANGED\')\n        active=handles((new.st_dev,new.st_ino));prior=handles((old.st_dev,old.st_ino))\n        if any(r[\'pid\']==initial_logger[0] and r[\'writable\'] for r in active) and not prior:break\n        require(time.monotonic()<until,\'LOGGER_REOPEN_NOT_CONFIRMED_OLD_LOG_RETAINED\')\n        time.sleep(1)\n    journal(dict(phase=\'LOGGER_REOPEN_ACCEPTED\',pid=initial_logger[0],new_inode=new.st_ino,old_inode=old.st_ino))\n\n\ndef main():\n    global DEADLINE\n    out=dict(schema=\'portal-0015-auth-log-relocation.v1\',status=\'INCOMPLETE\',once_claimed=False,\n             rotation_requests=0,service_signal_requests=0,service_restart_requests=0,\n             container_runtime_commands=0,workload_patch_requests=0,automatic_retry=False,\n             old_root_copy_removed=False,credentials_exported=False,log_content_exported=False,\n             permanent_rotation_policy_installed=False)\n    started=time.monotonic();d=None\n    try:\n        require(os.name==\'posix\' and os.geteuid()==0 and socket.gethostname().split(\'.\')[0]==\'ecs-zdz-pro-0015\'\n                and sys.flags.isolated and sys.flags.dont_write_bytecode\n                and sys.argv[1:]==[\'--rotate-relocate-auth-log-once\'],\'WRONG_HOST_OR_ENTRY\')\n        DEADLINE=time.monotonic()+2400\n        s=os.lstat(SOURCE)\n        require(stat.S_ISREG(s.st_mode) and s.st_ino==INODE and s.st_dev==ROOT_DEVICE and s.st_nlink==1\n                and s.st_uid==s.st_gid==0 and stat.S_IMODE(s.st_mode)==0o644 and 1024**3<s.st_size<=MAX_BYTES,\n                \'OBSERVED_AUTH_LOG_CHANGED\')\n        require(os.stat(\'/\').st_dev==ROOT_DEVICE and os.path.ismount(\'/mnt/data\')\n                and os.stat(\'/mnt/data\').st_dev==DATA_DEVICE,\'FILESYSTEM_CHANGED\')\n        v=os.statvfs(\'/mnt/data\');require(v.f_bavail*v.f_frsize>s.st_size+20*1024**3,\'DATA_HEADROOM\')\n        config_changed();initial_logger=logger_identity()\n        writers=handles((s.st_dev,s.st_ino))\n        require(writers and all(r[\'pid\']==initial_logger[0] for r in writers)\n                and any(r[\'writable\'] for r in writers),\'OTHER_LOG_USERS_PRESENT\')\n        binary=next((p for p in (\'/usr/sbin/logrotate\',\'/usr/bin/logrotate\') if os.path.isfile(p)),None)\n        require(binary is not None,\'LOGROTATE_MISSING\')\n        p=command([binary,\'--version\']);require(p.returncode==0 and b\'logrotate 3.15.1\' in p.stdout+p.stderr,\'LOGROTATE_VERSION_CHANGED\')\n        os.nice(19);ionice=shutil.which(\'ionice\',path=\'/usr/bin:/bin\')\n        require(ionice is not None and command([ionice,\'-c\',\'3\',\'-p\',str(os.getpid())]).returncode==0,\'IONICE_REQUIRED\')\n        for path,device in ((DEST,DATA_DEVICE),(OLD_DIR,ROOT_DEVICE)):\n            pfd,name=parent(path)\n            try:\n                require(os.fstat(pfd).st_dev==device,\'PARENT_DEVICE_CHANGED\')\n                os.mkdir(name,0o700,dir_fd=pfd);os.fsync(pfd)\n                if path==DEST:\n                    out[\'once_claimed\']=True\n                    d=os.open(DEST,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)\n            finally:os.close(pfd)\n        def journal(row):\n            fd=os.open(\'JOURNAL.jsonl\',os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600,dir_fd=d)\n            with os.fdopen(fd,\'a\') as f:f.write(json.dumps(row,sort_keys=True)+\'\\n\');f.flush();os.fsync(f.fileno())\n            os.fsync(d)\n        out[\'security_attribute_probe\']=dict(status=\'PASS\',xattr_names=probe_metadata(SOURCE,(OLD_DIR,DEST)))\n        out[\'root_available_before\']=os.statvfs(\'/\').f_bavail*os.statvfs(\'/\').f_frsize\n        raw=rotation_policy();fd=os.open(\'ROTATION.conf\',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=d)\n        with os.fdopen(fd,\'wb\') as f:f.write(raw);f.flush();os.fsync(f.fileno())\n        config=DEST+\'/ROTATION.conf\'\n        p=command([binary,\'--debug\',\'--state\',\'/dev/null\',config])\n        output=p.stdout+p.stderr\n        require(p.returncode==0 and SOURCE.encode() in output and not re.search(rb\'^\\s*error:\',output,re.M|re.I),\n                \'NATIVE_PREFLIGHT_REJECTED\')\n        journal(dict(phase=\'ROTATION_INTENT\',old_inode=INODE,old_dir=OLD_DIR,logger_pid=initial_logger[0],\n                     configuration_sha256=hashlib.sha256(raw).hexdigest()))\n        out[\'rotation_requests\']=1\n        print(json.dumps(dict(phase=\'ROTATE_AUTH_LOG_WITHOUT_SERVICE_RESTART\')),flush=True)\n        p=command([binary,\'--state\',DEST+\'/logrotate.state\',config])\n        out[\'rotation_command_returncode\']=p.returncode\n        require(p.returncode==0,\'ROTATION_NOT_ACCEPTED_REVIEW_RETAINED_FILES\')\n        old=OLD_DIR+\'/auth.log.1\'\n        a=os.open(old,os.O_RDONLY|os.O_NOFOLLOW);b=os.open(SOURCE,os.O_RDWR|os.O_NOFOLLOW)\n        try:\n            oldst,newst=os.fstat(a),os.fstat(b)\n            require(oldst.st_ino==INODE and oldst.st_dev==ROOT_DEVICE and newst.st_ino!=INODE\n                    and stat.S_ISREG(newst.st_mode) and newst.st_dev==ROOT_DEVICE and newst.st_nlink==1,\n                    \'ROTATED_FILE_IDENTITIES_UNEXPECTED\')\n            journal(dict(phase=\'ROTATED_OLD_LOG_RETAINED\',old_inode=oldst.st_ino,new_inode=newst.st_ino))\n            # Metadata is restored before the only service signal is permitted.\n            def signal_logger():\n                journal(dict(phase=\'HUP_INTENT\'));out[\'service_signal_requests\']=1\n                p=command([\'/usr/bin/systemctl\',\'kill\',\'--kill-who=main\',\'--signal=HUP\',\'rsyslog.service\'])\n                require(p.returncode==0,\'LOGGER_HUP_NOT_ACKNOWLEDGED\')\n            reopen(a,b,initial_logger,journal,signal_logger=signal_logger)\n            out[\'logger_reopen_accepted\']=True\n            out[\'security_attributes_preserved\']=True\n            current_key=(newst.st_dev,newst.st_ino)\n        finally:os.close(a);os.close(b)\n        def verify_live_before_release():\n            require(logger_identity()==initial_logger,\'LOGGER_CHANGED_BEFORE_ROOT_RELEASE\')\n            current=os.lstat(SOURCE)\n            require(stat.S_ISREG(current.st_mode) and (current.st_dev,current.st_ino)==current_key,\n                    \'ACTIVE_LOG_CHANGED_BEFORE_ROOT_RELEASE\')\n            require(any(r[\'pid\']==initial_logger[0] and r[\'writable\'] for r in handles(current_key)),\n                    \'ACTIVE_LOG_WRITER_NOT_CONFIRMED\')\n        out.update(relocate_closed(old,DEST,journal,before_release=verify_live_before_release))\n        require(logger_identity()==initial_logger,\'LOGGER_CHANGED_AFTER_TRANSFER\')\n        out[\'root_available_after\']=os.statvfs(\'/\').f_bavail*os.statvfs(\'/\').f_frsize\n        out[\'actual_root_free_increase_bytes\']=out[\'root_available_after\']-out[\'root_available_before\']\n        out[\'status\']=\'AUTH_LOG_ROTATED_ARCHIVED_VERIFIED_ROOT_RELEASED\'\n    except BaseException as error:\n        out.update(status=\'INCOMPLETE_REVIEW_JOURNAL_NO_RETRY\',error_class=type(error).__name__,\n                   error_code=str(error) if isinstance(error,Refused) else \'DETAILS_WITHHELD\')\n    finally:\n        out[\'elapsed_seconds\']=round(time.monotonic()-started,3)\n        if d is not None:\n            raw=(json.dumps(out,sort_keys=True)+\'\\n\').encode()\n            try:\n                fd=os.open(\'REPORT.json\',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=d)\n                with os.fdopen(fd,\'wb\') as f:f.write(raw);f.flush();os.fsync(f.fileno())\n                os.fsync(d)\n                out.update(report_path=DEST+\'/REPORT.json\',report_bytes=len(raw),report_sha256=hashlib.sha256(raw).hexdigest())\n            except BaseException:out.update(status=\'INCOMPLETE_REPORT_WRITE_REVIEW_JOURNAL\')\n            os.close(d)\n    print(json.dumps(out,sort_keys=True),flush=True)\n    return 0 if out[\'status\']==\'AUTH_LOG_ROTATED_ARCHIVED_VERIFIED_ROOT_RELEASED\' else 1\n\n\nif __name__==\'__main__\':sys.exit(main())\n'  # Filled by the packager from the verified one-time implementation.
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
