#!/usr/bin/env python3
"""Validate a dedicated auth.log rotation in native logrotate debug mode only."""
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
import sys
import tempfile

TARGET = '/var/log/auth.log'


def require(ok, code):
    if not ok:
        raise ValueError(code)


def metadata(path):
    s = os.lstat(path)
    return dict(device=s.st_dev, inode=s.st_ino, bytes=s.st_size, allocated_bytes=s.st_blocks*512,
                uid=s.st_uid, gid=s.st_gid, mode=oct(stat.S_IMODE(s.st_mode)), links=s.st_nlink,
                regular_file=stat.S_ISREG(s.st_mode), symlink=stat.S_ISLNK(s.st_mode))


def policy(st):
    # Never delete prior rotations. The production transfer tool must separately
    # verify a closed old inode before releasing its root-disk allocation.
    owner, group = pwd.getpwuid(st.st_uid).pw_name, grp.getgrgid(st.st_gid).gr_name
    require(re.fullmatch(r'[A-Za-z0-9_-]+', owner) and re.fullmatch(r'[A-Za-z0-9_-]+', group), 'OWNER_NAME_UNSUPPORTED')
    return ('''/var/log/auth.log {
    size 100M
    rotate -1
    missingok
    notifempty
    nocompress
    nodateext
    nocopytruncate
    create %04o %s %s
    postrotate
        /usr/bin/systemctl kill --kill-who=main --signal=HUP rsyslog.service
    endscript
}
''' % (stat.S_IMODE(st.st_mode), owner, group)).encode()


def native_debug(binary, raw, scratch):
    # Anonymous/unlinked temporary config; never install a rule or write state.
    with tempfile.TemporaryFile(dir=scratch) as f:
        f.write(raw)
        f.flush()
        f.seek(0)
        p = subprocess.run([binary, '--debug', '--state', '/dev/null', '/proc/self/fd/%d' % f.fileno()],
                           pass_fds=(f.fileno(),), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=20, env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL':'C'})
    output = p.stdout+p.stderr
    require(len(output) <= 262144, 'DEBUG_OUTPUT_LIMIT')
    text = output.decode('utf-8', 'replace')
    return dict(returncode=p.returncode, output_sha256=hashlib.sha256(output).hexdigest(),
                raw_output_exported=False, configuration_bytes=len(raw), configuration_sha256=hashlib.sha256(raw).hexdigest(),
                auth_target_observed=TARGET in text,
                error_line_count=sum(bool(re.match(r'^\s*error:', x, re.I)) for x in text.splitlines()),
                directive_unknown=bool(re.search(r'unknown (?:option|directive)', text, re.I)),
                postrotate_executed=False)


def main():
    out = dict(schema='portal-0015-auth-log-rotation-dryrun.v1', production_configuration_modified=False,
               log_contents_read=False, credentials_exported=False, rotation_requests=0,
               service_signal_requests=0, service_restart_requests=0, cleanup_requests=0,
               temporary_anonymous_configuration_only=True)
    try:
        require(os.name=='posix' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='ecs-zdz-pro-0015'
                and sys.flags.isolated and sys.flags.dont_write_bytecode
                and sys.argv[1:]==['--verify-rotation-dryrun'], 'WRONG_HOST_OR_ENTRY')
        out['before'] = metadata(TARGET)
        require(out['before']['regular_file'] and not out['before']['symlink'] and out['before']['links']==1
                and out['before']['device']==64771 and out['before']['inode']==1441870,
                'OBSERVED_AUTH_LOG_CHANGED')
        require(not os.path.islink('/mnt/data') and os.stat('/mnt/data').st_dev==64785, 'DATA_MOUNT_CHANGED')
        binary = next((p for p in ('/usr/sbin/logrotate','/usr/bin/logrotate') if os.path.isfile(p)), None)
        require(binary is not None, 'LOGROTATE_MISSING')
        s=os.lstat(binary)
        require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022, 'LOGROTATE_PERMISSIONS')
        p=subprocess.run([binary,'--version'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=5,
                         env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
        require(p.returncode==0 and len(p.stdout)<16384, 'VERSION_UNAVAILABLE')
        m=re.search(rb'logrotate\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)',p.stdout)
        require(m is not None, 'VERSION_UNRECOGNIZED')
        out['logrotate_version']=m.group(1).decode()
        out['native_debug']=native_debug(binary, policy(os.lstat(TARGET)), '/mnt/data')
        out['after']=metadata(TARGET)
        out['file_identity_preserved']=all(out['before'][k]==out['after'][k] for k in ('device','inode','uid','gid','mode','links'))
        require(out['file_identity_preserved'] and out['native_debug']['returncode']==0
                and out['native_debug']['auth_target_observed'] and out['native_debug']['error_line_count']==0
                and not out['native_debug']['directive_unknown'], 'NATIVE_DEBUG_NOT_ACCEPTED')
        # Metadata needed to preserve ACL/SELinux on the eventual new active file.
        out['xattr_names'] = sorted(os.listxattr(TARGET, follow_symlinks=False))
        out['selinux_enforcing'] = Path('/sys/fs/selinux/enforce').read_text().strip()=='1' if Path('/sys/fs/selinux/enforce').is_file() else False
        out['status']='AUTH_LOG_NATIVE_ROTATION_DRYRUN_ACCEPTED_NOT_ROTATED'
    except Exception as error:
        out.update(status='INCOMPLETE',error_class=type(error).__name__,
                   error_code=str(error) if isinstance(error,ValueError) else 'DETAILS_WITHHELD')
    print(json.dumps(out,sort_keys=True),flush=True)
    return 0 if out['status']!='INCOMPLETE' else 1


if __name__=='__main__':
    sys.exit(main())
