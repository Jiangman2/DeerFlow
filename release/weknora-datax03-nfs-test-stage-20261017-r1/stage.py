#!/usr/bin/env python3
"""Add a datax-03 synthetic NFS export via pinned XFS directory FD; Python3.7+."""
import argparse
import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
import sys
import traceback
import types

HOST = 'datax-03'
SERVER = '192.168.111.18'
CLIENTS = ('192.168.111.2', '192.168.111.18')
AUDIT_CONTROL = Path('/var/lib/weknora-datax03-share-audit-dLRuiSI0')
AUDIT_SHA = 'ff2d7bbfe829e1cc03305bd5e9e5856462703c9aed2b888414deff2667d7c2bc'
AUDIT_UTC = '2026-10-17T11:43:55.621063+00:00'
PINS = (
    ('/', 0, 0, 0o555, 64512, 128),
    ('/opt', 1000, 1000, 0o755, 64785, 128),
    ('/opt/deerflow-storage', 0, 0, 0o755, 64785, 4325326400),
    ('/opt/deerflow-storage/weknora-files', 1000, 1000, 0o770, 64785, 2149230571),
    ('/opt/deerflow-storage/weknora-docreader', 1000, 1000, 0o770, 64785, 4325326401),
)
PARENT = Path('/opt/deerflow-storage')
TEST_NAME = 'weknora-nfs-test-20261017-r1'
STATE = Path('/var/lib/weknora-datax03-nfs-test-20261017-r1')
ANCHOR = Path('/var/lib/weknora-datax03-nfs-export-20261017-r1')
TARGET = ANCHOR/'probe'
EXPORTS = Path('/etc/exports')
EXPORT_DIR = Path('/etc/exports.d')
CONFIG = EXPORT_DIR/'weknora-datax03-test-20261017-r1.exports'
OPTIONS = 'rw,sync,root_squash,no_all_squash,no_subtree_check,secure,sec=sys,mountpoint'
ENV = dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C')
H = None
OWNED = False
PHASE = 'PREFLIGHT'
LAST = {}


class Failure(RuntimeError):
    pass


def need(ok, check):
    if not ok:
        raise Failure(check)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def emit(value):
    print(json.dumps(value, sort_keys=True, separators=(',', ':')), flush=True)


def phase(value):
    global PHASE
    PHASE = value
    emit(dict(datax03_nfs_test='RUNNING', phase=value))


def present(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def safe_dir(path):
    for p in (path,) + tuple(path.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
             'unsafe_root_managed_directory')


def read_safe(path, limit=1048576):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
             and not s.st_mode & 0o022 and s.st_size <= limit, 'unsafe_private_file')
        raw = f.read(limit+1)
    need(len(raw) <= limit, 'file_limit')
    return raw


def sync(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def write_new(path, raw, mode=0o600):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as f:
        os.fchmod(f.fileno(), mode)
        f.write(raw); f.flush(); os.fsync(f.fileno())
    sync(path.parent)


def receipt(name, value):
    need(re.fullmatch('[A-Z0-9-]+\\.json', name), 'receipt_name')
    write_new(STATE/name, canonical(value)+b'\n')


def load_helper():
    path = STATE/'audit.py' if present(STATE/'audit.py') else AUDIT_CONTROL/'audit.py'
    raw = read_safe(path)
    need(sha(raw) == AUDIT_SHA, 'audit_helper_changed')
    module = types.ModuleType('pinned_readonly_audit')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module, raw


def command(args, timeout=20):
    global LAST
    LAST = dict(program=Path(args[0]).name, exit_code=None)
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, close_fds=True, env=ENV)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        LAST.update(timeout=True, pid=p.pid)
        raise Failure('command_timeout_outcome_uncertain_no_retry')
    LAST.update(exit_code=p.returncode, stdout_bytes=len(out), stderr_bytes=len(err),
                stderr_sha256=sha(err), raw_error_printed=False)
    need(len(out) <= 1048576 and len(err) <= 65536, 'command_output_limit')
    need(p.returncode == 0, 'command_failed_no_retry')
    return out


def tool(name):
    value = H.tool(name)
    need(value is not None, 'tool_missing_'+name)
    return value


def signature(s):
    return (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode), s.st_dev, s.st_ino)


def open_chain(path):
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def pin_paths():
    result = []
    for name, uid, gid, mode, dev, ino in PINS:
        fd = open_chain(name)
        try:
            s = os.fstat(fd)
            need(stat.S_ISDIR(s.st_mode) and signature(s) == (uid, gid, mode, dev, ino),
                 'operator_pinned_path_identity_changed')
            result.append(dict(path=name, uid=uid, gid=gid, mode=oct(mode), device=dev, inode=ino))
        finally: os.close(fd)
    return result


def audit_receipt():
    raw = read_safe(AUDIT_CONTROL/'audit.log', 262144)
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    summaries = [r for r in rows if 'storage_share_audit' in r]
    need(len(summaries) == 1, 'audit_summary_ambiguous')
    summary = summaries[0]
    need(summary.get('utc') == AUDIT_UTC and summary.get('node') == HOST
         and summary.get('unavailable_sections') == ['paths']
         and summary['results']['paths'].get('reason') == 'unsafe_ancestor', 'audit_receipt_changed')
    need(sha(read_safe(AUDIT_CONTROL/'audit.py')) == AUDIT_SHA, 'original_audit_code_changed')
    return dict(log_sha256=sha(raw), summary_sha256=sha(canonical(summary)), utc=AUDIT_UTC)


def mounts():
    text = Path('/proc/self/mountinfo').read_text()
    need(len(text) <= 4194304, 'mount_inventory_limit')
    rows = []
    for line in text.splitlines():
        a, sep, b = line.partition(' - ')
        left, right = a.split(), b.split()
        need(sep and len(left) >= 6 and len(right) >= 3, 'mount_inventory_shape')
        unescape = lambda text: re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1),8)), text)
        rows.append(dict(point=unescape(left[4]), root=unescape(left[3]), device=left[2],
            fs=right[0], source=unescape(right[1]),
            options=set(left[5].split(',')+right[2].split(',')), propagation=left[6:]))
    return rows


def services():
    value = H.Audit().services()
    need(value['nfs-server.service'].get('ActiveState') == 'active'
         and value['rpcbind.service'].get('ActiveState') == 'active', 'existing_nfs_service_not_active')
    versions = read_safe(Path('/proc/fs/nfsd/versions'),4096).decode().split()
    threads = int(read_safe(Path('/proc/fs/nfsd/threads'),4096).strip())
    need('+4.1' in versions and threads > 0, 'existing_nfs_v41_not_active')
    return dict(nfs_server=value['nfs-server.service'], rpcbind=value['rpcbind.service'],
                nfs_threads=threads, versions=versions)


def parse_exports(raw):
    rows = {}
    for line in raw.decode('utf-8','strict').splitlines():
        parts = shlex.split(line,comments=True)
        if not parts: continue
        need(len(parts) >= 2 and parts[0].startswith('/') and '..' not in Path(parts[0]).parts,
             'export_table_shape')
        for item in parts[1:]:
            match = re.fullmatch(r'([^()\s]+)\(([^()]*)\)',item)
            need(match is not None, 'export_client_shape')
            key = (parts[0],match.group(1))
            need(key not in rows, 'duplicate_export')
            rows[key] = tuple(sorted(match.group(2).split(',')))
    need(len(rows) <= 10000, 'export_table_limit')
    return rows


def exports():
    return parse_exports(command([tool('exportfs'),'-s']))


def configs():
    rows = {}
    if present(EXPORTS): rows[str(EXPORTS)] = sha(read_safe(EXPORTS))
    if present(EXPORT_DIR):
        safe_dir(EXPORT_DIR)
        names = [p for p in EXPORT_DIR.iterdir() if p.name.endswith('.exports') and not p.name.startswith('.')]
        need(len(names) <= 128, 'export_config_limit')
        for p in names: rows[str(p)] = sha(read_safe(p))
    return rows


def config_bytes():
    return (str(TARGET)+' '+ ' '.join(client+'('+OPTIONS+')' for client in CLIENTS)+'\n').encode()


def validate_exports(actual, added):
    need(set(actual) == {(str(TARGET),client) for client in added}, 'test_export_membership_changed')
    required = set(OPTIONS.split(','))
    defaults = {'wdelay','hide','no_pnfs','no_crossmnt','anonuid=65534','anongid=65534'}
    for options in actual.values():
        opts = set(options)
        need(required <= opts and opts-required <= defaults, 'test_export_options_changed')


def preflight():
    need(os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt')
         and os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'), 'host_namespaces_required')
    for path in (STATE.parent,ANCHOR.parent,EXPORT_DIR.parent): safe_dir(path)
    need(not present(ANCHOR) and not present(CONFIG), 'test_target_exists_use_status')
    pins = pin_paths()
    need(not present(PARENT/TEST_NAME), 'test_source_exists_use_status')
    rows = [r for r in mounts() if r['point'] == '/opt']
    need(len(rows) == 1 and rows[0]['fs'] == 'xfs' and rows[0]['source'] == '/dev/vdb1'
         and 'rw' in rows[0]['options'], 'pinned_xfs_mount_changed')
    space = os.statvfs(str(PARENT))
    need(space.f_bavail*space.f_frsize >= 1024**3 and space.f_favail >= 10000, 'synthetic_test_headroom_low')
    addresses = command([tool('ip'),'-o','-4','addr','show']).decode('ascii','strict')
    need(SERVER in re.findall(r'\binet ([0-9.]+)/[0-9]+',addresses), 'server_address_changed')
    service_state = services()
    inventory = configs()
    need(all(value == sha(b'') for value in inventory.values()), 'reviewed_empty_export_config_changed')
    need(not exports(), 'reviewed_empty_active_exports_changed')
    return dict(audit=audit_receipt(), paths=pins, services=service_state, configs=inventory,
                available_bytes=space.f_bavail*space.f_frsize)


def new_dir(path, mode, uid=0, gid=0):
    safe_dir(path.parent)
    os.mkdir(str(path),0o700)
    fd = os.open(str(path),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fchown(fd,uid,gid); os.fchmod(fd,mode); os.fsync(fd)
    finally: os.close(fd)
    sync(path.parent)


def mount_call(source, target, flags):
    global LAST
    LAST = dict(program='mount(2)',flags=flags,exit_code=None)
    lib = ctypes.CDLL(None,use_errno=True)
    lib.mount.argtypes = (ctypes.c_char_p,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_ulong,ctypes.c_void_p)
    lib.mount.restype = ctypes.c_int
    rc = lib.mount(None if source is None else os.fsencode(source),os.fsencode(target),None,flags,None)
    LAST.update(exit_code=rc,errno=ctypes.get_errno() if rc else 0)
    need(rc == 0, 'mount_syscall_failed_no_retry')


def check_bound(source_stat):
    safe_dir(ANCHOR)
    rows = [r for r in mounts() if r['point'] == str(TARGET)]
    need(len(rows) == 1 and rows[0]['fs'] == 'xfs'
         and {'rw','nosuid','nodev','noexec'} <= rows[0]['options']
         and not any(x.startswith(('shared:','master:')) for x in rows[0]['propagation']),
         'test_bind_mount_identity_or_flags')
    need(not any(r['point'].startswith(str(TARGET)+'/') for r in mounts()), 'test_bind_submount_present')
    fd = os.open(str(TARGET),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        need(signature(os.fstat(fd)) == signature(source_stat), 'test_bind_source_inode_mismatch')
        need(not os.listdir(fd), 'synthetic_test_directory_not_empty')
    finally: os.close(fd)


def guard(original, expected_configs, added, source_stat=None):
    need(pin_paths() == original['paths'], 'source_permission_or_identity_drift')
    need(configs() == expected_configs, 'export_configuration_drift')
    validate_exports(exports(),added)
    if source_stat is not None: check_bound(source_stat)


def once(helper_bytes):
    global OWNED
    need(not present(STATE), 'attempt_exists_use_status_no_retry')
    phase('BIND_OPERATOR_PATH_IDENTITIES_AND_RUNNING_NFS')
    original = preflight()
    driver = read_safe(Path(__file__).resolve())
    os.mkdir(str(STATE),0o700); sync(STATE.parent); OWNED = True
    write_new(STATE/'stage.py',driver); write_new(STATE/'audit.py',helper_bytes)
    receipt('STARTED.json',dict(utc=utc(),baseline=original,script_sha256=sha(driver),
        server=SERVER,clients=list(CLIENTS),source=str(PARENT/TEST_NAME),target=str(TARGET)))
    parent = source = None
    try:
        guard(original,original['configs'],set())
        parent = open_chain(PARENT)
        expected = next(row for row in PINS if row[0] == str(PARENT))
        need(signature(os.fstat(parent)) == expected[1:], 'source_parent_changed_before_create')
        phase('CREATE_ONE_NEW_SYNTHETIC_DIRECTORY_ON_DATAX03_DISK')
        receipt('SOURCE-REQUESTED.json',dict(name=TEST_NAME,parent_device=expected[-2],parent_inode=expected[-1]))
        os.mkdir(TEST_NAME,0o700,dir_fd=parent)
        source = os.open(TEST_NAME,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        os.fchown(source,1000,1000); os.fchmod(source,0o770); os.fsync(source); os.fsync(parent)
        source_stat = os.fstat(source)
        need(source_stat.st_dev == expected[-2] and not os.listdir(source), 'new_source_identity')
        receipt('SOURCE-ACK.json',dict(device=source_stat.st_dev,inode=source_stat.st_ino,uid=1000,gid=1000,mode='0o770'))
        guard(original,original['configs'],set())
        receipt('ANCHOR-REQUESTED.json',dict(anchor=str(ANCHOR),target=str(TARGET)))
        new_dir(ANCHOR,0o755); new_dir(TARGET,0o700)
        receipt('ANCHOR-ACK.json',dict(root_owned=True))
        phase('BIND_OPEN_TEST_DIRECTORY_FD_TO_ROOT_MANAGED_ANCHOR')
        receipt('BIND-REQUESTED.json',dict(device=source_stat.st_dev,inode=source_stat.st_ino,target=str(TARGET)))
        mount_call('/proc/self/fd/'+str(source),str(TARGET),4096)
        mount_call(None,str(TARGET),4096|32|2|4|8)
        mount_call(None,str(TARGET),1<<18)
        check_bound(source_stat)
        receipt('BIND-ACK.json',dict(device=source_stat.st_dev,inode=source_stat.st_ino,
                                  mountpoint=True,flags=['rw','nosuid','nodev','noexec','private']))
        guard(original,original['configs'],set(),source_stat)
        if not present(EXPORT_DIR):
            receipt('CONFIGDIR-REQUESTED.json',dict(path=str(EXPORT_DIR)))
            new_dir(EXPORT_DIR,0o755)
            receipt('CONFIGDIR-ACK.json',dict(root_owned=True))
        receipt('CONFIG-REQUESTED.json',dict(path=str(CONFIG),sha256=sha(config_bytes())))
        write_new(CONFIG,config_bytes(),0o644)
        receipt('CONFIG-ACK.json',dict(sha256=sha(read_safe(CONFIG))))
        expected_configs = dict(original['configs']); expected_configs[str(CONFIG)] = sha(config_bytes())
        added = set()
        for index,client in enumerate(CLIENTS,1):
            guard(original,expected_configs,added,source_stat)
            phase('TARGETED_TEST_EXPORT_'+str(index))
            receipt('EXPORT-{}-REQUESTED.json'.format(index),dict(client=client,target=str(TARGET)))
            command([tool('exportfs'),'-i','-o',OPTIONS,client+':'+str(TARGET)])
            added.add(client)
            guard(original,expected_configs,added,source_stat)
            receipt('EXPORT-{}-ACK.json'.format(index),dict(client=client,target=str(TARGET)))
        services()
        result = dict(datax03_test_export_stage='PASS',server=SERVER,clients=list(CLIENTS),
            export=str(TARGET),source=str(PARENT/TEST_NAME),source_device=source_stat.st_dev,
            source_inode=source_stat.st_ino,exports_created=2,root_squash=True,mountpoint_guard=True,
            bind_mounts_created=1,source_permissions_unchanged=True,existing_exports_unchanged=True,
            existing_export_configs_unchanged=True,business_directories_exported=False,
            business_data_copied=False,production_mounts_changed=False,production_workloads_mutated=False,
            services_restarted=0,kubernetes_writes=0,provider_requests=0,cross_node_io='NOT_TESTED',
            reboot_persistence='NOT_CONFIGURED_TEST_BIND_ONLY',ready_for_crossnode_test=True,
            ready_for_production_switch=False,ready_to_scale=False,automatic_retry=False,
            next_gate='RECEIPT_BOUND_TWO_NODE_UID1000_SYNTHETIC_IO',utc_finished=utc())
        receipt('RESULT.json',result)
        emit(dict(result,report=str(STATE/'RESULT.json'),report_sha256=sha(read_safe(STATE/'RESULT.json')),
                  status_command='sudo python3 -I -B '+str(STATE/'stage.py')+' --status'))
        return 0
    finally:
        for fd in (source,parent):
            if fd is not None: os.close(fd)


def metadata(path):
    if not present(path): return dict(present=False)
    s = path.lstat()
    return dict(present=True,uid=s.st_uid,gid=s.st_gid,mode=oct(stat.S_IMODE(s.st_mode)),
                directory=stat.S_ISDIR(s.st_mode),symlink=stat.S_ISLNK(s.st_mode),device=s.st_dev,inode=s.st_ino)


def status():
    safe_dir(STATE.parent);safe_dir(ANCHOR.parent)
    value = dict(datax03_nfs_test_status='READ_ONLY',files_modified=False,mounts_created=False,
                 exports_changed=False,automatic_retry=False,state=str(STATE))
    if present(STATE):
        safe_dir(STATE)
        names = sorted(p.name for p in STATE.iterdir() if p.name.endswith('.json'))
        need(len(names) <= 40,'receipt_count_limit')
        value['records'] = {}
        for name in names:
            raw = read_safe(STATE/name)
            value['records'][name] = dict(sha256=sha(raw))
            if name in ('RESULT.json','FAILED.json'):value['records'][name]['fields'] = json.loads(raw)
    value['anchor'] = metadata(ANCHOR)
    if value['anchor'].get('directory') and not value['anchor'].get('symlink'):
        safe_dir(ANCHOR)
        value['target'] = metadata(TARGET)
    value['test_mounts'] = [dict(point=r['point'],root=r['root'],filesystem=r['fs'],options=sorted(r['options']))
                            for r in mounts() if r['point'] == str(TARGET)]
    value['test_exports'] = [dict(path=p,client=c,options=list(o)) for (p,c),o in exports().items() if p == str(TARGET)]
    value['config'] = dict(present=present(CONFIG))
    if present(CONFIG):value['config']['sha256'] = sha(read_safe(CONFIG))
    emit(value)


def main():
    global H
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--stage-once',action='store_true');g.add_argument('--status',action='store_true')
    args = p.parse_args()
    try:
        need(sys.platform == 'linux' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == HOST,
             'root_on_datax03_required')
        H,helper_bytes = load_helper()
        if args.status:status();return 0
        return once(helper_bytes)
    except BaseException as exc:
        result = dict(datax03_test_export_stage='UNCONFIRMED',phase=PHASE,error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc,Failure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            errno=getattr(exc,'errno',None),last_operation=LAST,raw_error_printed=False,
            code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                         for x in traceback.extract_tb(exc.__traceback__)[-10:]],
            automatic_retry=False,instruction='USE_STATUS_NO_REPEAT_STAGE_NO_DELETE_STATE',
            business_data_copied=False,production_mounts_changed=False,ready_to_scale=False)
        if OWNED and not args.status and not present(STATE/'FAILED.json'):
            receipt('FAILED.json',result)
        emit(result)
        return 1


if __name__ == '__main__':sys.exit(main())
