#!/usr/bin/env python3
"""Add only the reviewed new NFS exports. Python 3.7+, stdlib, Linux root."""
import argparse
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time

HOST = 'ecs-zdz-pro-0000'
SERVER = '192.168.111.189'
CLIENTS = ('192.168.111.2', '192.168.111.18')
MOUNT = Path('/mnt/data')
ROOT = MOUNT / 'weknora-rwx-r1'
CHILDREN = ('files', 'docreader')
EXPORTS = Path('/etc/exports')
EXPORT_DIR = Path('/etc/exports.d')
CONFIG = EXPORT_DIR / 'weknora-rwx-20261017-r1.exports'
STATE = Path('/var/lib/weknora-rwx-nfs-20261017-r1')
OPTIONS = 'rw,sync,root_squash,no_all_squash,no_subtree_check,secure,sec=sys'
MIN_FREE = 600 * 1024**3
MIN_INODES = 1000000
LAST = {}


class Failure(RuntimeError):
    pass


def need(ok, check):
    if not ok:
        raise Failure(check)


def emit(value):
    print(json.dumps(value, sort_keys=True, ensure_ascii=True), flush=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def safe_dir(path):
    """No symlink anywhere, root owner, no group/other write. Never repair."""
    for item in reversed((path,) + tuple(path.parents)):
        s = item.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
             'unsafe_private_ancestor')


def read_safe(path, limit=1048576):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        s = os.fstat(stream.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
             and not s.st_mode & 0o022 and s.st_size <= limit, 'unsafe_input_file')
        raw = stream.read(limit + 1)
        need(len(raw) <= limit, 'file_size_limit')
        return raw


def sync_dir(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, raw, mode=0o600):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def receipt(name, value):
    need(re.fullmatch(r'[A-Z0-9-]+\.json', name), 'receipt_name')
    write_new(STATE / name, canonical(value) + b'\n')


def bounded_run(args, timeout=25, limit=1048576):
    """No shell, bound both pipes while draining, never print raw command output."""
    global LAST
    LAST = dict(program=Path(args[0]).name, exit_code=None, command_started=False)
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, close_fds=True)
    LAST['command_started'] = True
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    errors = threading.Event()

    def reader(pipe, index):
        try:
            while True:
                block = pipe.read(4096)
                if not block:
                    break
                if len(buffers[index]) + len(block) > limit:
                    overflow.set()
                    break
                buffers[index].extend(block)
        except Exception:
            errors.set()
        finally:
            pipe.close()

    threads = [threading.Thread(target=reader, args=(proc.stdout, 0), daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, 1), daemon=True)]
    for thread in threads:
        thread.start()
    end = time.monotonic() + timeout
    expired = False
    while proc.poll() is None:
        if overflow.is_set() or errors.is_set():
            break
        if time.monotonic() > end:
            expired = True
            break
        time.sleep(.02)
    if proc.poll() is None:
        proc.kill()  # Our own CLI only. Export outcome remains uncertain.
    proc.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=2)
    LAST.update(exit_code=proc.returncode, stdout_bytes=len(buffers[0]),
                stderr_bytes=len(buffers[1]), timeout=expired)
    need(not expired, 'command_timeout_outcome_must_be_reconciled')
    need(not overflow.is_set() and not errors.is_set()
         and not any(t.is_alive() for t in threads), 'command_output_unavailable')
    need(proc.returncode == 0, 'command_failed_no_retry')
    return bytes(buffers[0])


def executable(name):
    value = shutil.which(name, path='/usr/sbin:/usr/bin:/sbin:/bin')
    need(value is not None, 'required_tool_missing_' + name)
    # System symlinks (/bin -> /usr/bin) are allowed only for executable discovery.
    value = Path(value).resolve(strict=True)
    safe_dir(value.parent)
    s = value.stat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
         'unsafe_system_executable')
    return str(value)


def parse_exports(raw):
    result = {}
    for line in raw.decode('utf-8', 'strict').splitlines():
        if not line.strip():
            continue
        parts = shlex.split(line, comments=True)
        need(len(parts) >= 2 and parts[0].startswith('/'), 'export_table_shape')
        path = parts[0]
        need(str(Path(path)) == path and '..' not in Path(path).parts, 'export_path_shape')
        for entry in parts[1:]:
            match = re.fullmatch(r'([^\s()]+)\(([^()]*)\)', entry)
            need(match is not None, 'export_client_shape')
            key = (path, match.group(1))
            need(key not in result, 'duplicate_export_record')
            options = match.group(2).split(',')
            need(all(options), 'empty_export_option')
            result[key] = tuple(sorted(options))
    need(len(result) <= 10000, 'too_many_exports')
    return result


def table(tool):
    return parse_exports(bounded_run([tool, '-s']))


def keys():
    return {(str(ROOT / child), client) for child in CHILDREN for client in CLIENTS}


def config_bytes():
    entries = ' '.join(client + '(' + OPTIONS + ')' for client in CLIENTS)
    return ''.join(str(ROOT / child) + ' ' + entries + '\n' for child in CHILDREN).encode()


def config_inventory():
    """Hash config only; never print its contents or silently ignore symlinks."""
    rows = {}
    if os.path.lexists(str(EXPORTS)):
        rows[str(EXPORTS)] = sha(read_safe(EXPORTS))
    if os.path.lexists(str(EXPORT_DIR)):
        safe_dir(EXPORT_DIR)
        with os.scandir(str(EXPORT_DIR)) as entries:
            names = sorted(e.name for e in entries if not e.name.startswith('.')
                           and e.name.endswith('.exports'))
        need(len(names) <= 256, 'too_many_export_configs')
        for name in names:
            path = EXPORT_DIR / name
            rows[str(path)] = sha(read_safe(path))
    return rows


def reject_overlaps(rows):
    for path, client in rows:
        p = Path(path)
        need(not (p == ROOT or ROOT in p.parents or p in ROOT.parents),
             'new_path_overlaps_existing_export')


def config_overlap_check():
    """Catch configured-but-not-active paths without interpreting their options."""
    for name in config_inventory():
        raw = read_safe(Path(name)).decode('utf-8', 'strict').replace('\\\n', ' ')
        for line in raw.splitlines():
            parts = shlex.split(line, comments=True)
            if not parts:
                continue
            need(parts[0].startswith('/'), 'export_config_requires_review')
            reject_overlaps({(parts[0], ''): ()})


def check_export_options(options):
    opts = set(options)
    required = set(OPTIONS.split(','))
    extra = opts - required
    need(required <= opts, 'new_export_options_missing')
    need(extra <= {'wdelay', 'hide', 'nohide', 'no_crossmnt', 'anonuid=65534',
                   'anongid=65534', 'no_acl', 'no_pnfs'}, 'new_export_options_unreviewed')
    # nohide is not requested and must never broaden the subtree exposure.
    need('nohide' not in opts, 'new_export_nohide_unexpected')


def preserve_old(before, after, allowed_new):
    need({k: v for k, v in after.items() if k not in allowed_new} == before,
         'unrelated_exports_changed')
    need(set(after) == set(before) | allowed_new, 'new_export_membership_mismatch')
    for key in allowed_new:
        check_export_options(after[key])


def environment():
    need(sys.platform == 'linux' and os.geteuid() == 0, 'linux_root_required')
    need(socket.gethostname().split('.')[0] == HOST, 'wrong_host')
    for client in CLIENTS:
        ip = ipaddress.ip_address(client)
        need(ip in ipaddress.ip_network('192.168.111.0/24') and str(ip) != SERVER,
             'client_not_reviewed')
    safe_dir(MOUNT)
    safe_dir(STATE.parent)
    safe_dir(EXPORT_DIR.parent)
    tools = {name: executable(name) for name in ('exportfs', 'ip', 'findmnt')}
    addresses = bounded_run([tools['ip'], '-o', '-4', 'addr', 'show']).decode('ascii', 'strict')
    need(SERVER in re.findall(r'\binet ([0-9.]+)/[0-9]+\b', addresses), 'server_ip_not_present')
    mount = json.loads(bounded_run([tools['findmnt'], '--json', '--target', str(MOUNT),
                                  '--output', 'TARGET,FSTYPE,OPTIONS']))
    fs = mount.get('filesystems', [])
    need(len(fs) == 1 and fs[0].get('target') == str(MOUNT)
         and fs[0].get('fstype') == 'xfs' and 'rw' in fs[0].get('options', '').split(','),
         'reviewed_data_mount_not_available')
    space = os.statvfs(str(MOUNT))
    free = space.f_bavail * space.f_frsize
    need(free >= MIN_FREE and space.f_favail >= MIN_INODES, 'nfs_capacity_reserve')
    return tools, dict(available_bytes=free, available_inodes=space.f_favail,
                       minimum_available_bytes=MIN_FREE)


def preflight():
    tools, capacity = environment()
    need(not os.path.lexists(str(ROOT)), 'new_directory_already_exists_use_status')
    need(not os.path.lexists(str(CONFIG)), 'new_export_config_already_exists_use_status')
    inventory = config_inventory()
    config_overlap_check()
    before = table(tools['exportfs'])
    reject_overlaps(before)
    return tools, capacity, inventory, before


def metadata(path):
    if not os.path.lexists(str(path)):
        return dict(present=False)
    s = path.lstat()
    return dict(present=True, directory=stat.S_ISDIR(s.st_mode),
                symlink=stat.S_ISLNK(s.st_mode), uid=s.st_uid, gid=s.st_gid,
                mode=oct(stat.S_IMODE(s.st_mode)), device=s.st_dev, inode=s.st_ino)


def check_directories():
    safe_dir(ROOT)
    need(stat.S_IMODE(ROOT.lstat().st_mode) == 0o755, 'new_parent_mode_drift')
    for child in CHILDREN:
        p = ROOT / child
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 1000 and s.st_gid == 1000
             and stat.S_IMODE(s.st_mode) == 0o770 and s.st_dev == ROOT.lstat().st_dev,
             'new_child_metadata_drift')
        need(not any(p.iterdir()), 'new_child_not_empty')


def run_once():
    # Operator changed storage direction before qualification or delivery.
    raise Failure('withdrawn_draft_data_must_remain_on_datax03')


def _withdrawn_run_once_not_authorized():
    raise Failure('withdrawn_draft_data_must_remain_on_datax03')
    need(not os.path.lexists(str(STATE)), 'previous_attempt_exists_use_status')
    tools, capacity, inventory, before = preflight()
    # Exclusive mkdir is the operation lock and a permanent once-only marker.
    os.mkdir(str(STATE), 0o700)
    sync_dir(STATE.parent)
    started = dict(utc=utc(), host=HOST, server=SERVER, clients=list(CLIENTS),
                   paths=[str(ROOT / c) for c in CHILDREN], options=OPTIONS,
                   capacity=capacity, old_config_hashes=inventory,
                   old_exports=[[p, c, list(o)] for (p, c), o in sorted(before.items())],
                   automatic_retry=False)
    receipt('STARTED.json', started)
    try:
        need(config_inventory() == inventory and table(tools['exportfs']) == before,
             'baseline_changed_before_write')
        emit(dict(nfs_stage='RUNNING', phase='CREATE_NEW_DIRECTORIES_ONLY'))
        receipt('MKDIR-REQUESTED.json', dict(root=str(ROOT), children=list(CHILDREN)))
        os.mkdir(str(ROOT), 0o755)
        os.chmod(str(ROOT), 0o755, follow_symlinks=False)
        sync_dir(ROOT.parent)
        for child in CHILDREN:
            p = ROOT / child
            os.mkdir(str(p), 0o700)
            fd = os.open(str(p), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fchown(fd, 1000, 1000)
                os.fchmod(fd, 0o770)
                os.fsync(fd)
            finally:
                os.close(fd)
        sync_dir(ROOT)
        check_directories()
        receipt('MKDIR-ACK.json', dict(paths={str(p): metadata(p)
                                            for p in [ROOT] + [ROOT / c for c in CHILDREN]}))
        need(config_inventory() == inventory and table(tools['exportfs']) == before,
             'baseline_changed_before_config')
        if not os.path.lexists(str(EXPORT_DIR)):
            receipt('CONFIGDIR-REQUESTED.json', dict(path=str(EXPORT_DIR)))
            os.mkdir(str(EXPORT_DIR), 0o755)
            os.chmod(str(EXPORT_DIR), 0o755, follow_symlinks=False)
            sync_dir(EXPORT_DIR.parent)
            receipt('CONFIGDIR-ACK.json', metadata(EXPORT_DIR))
        receipt('CONFIG-REQUESTED.json', dict(path=str(CONFIG), sha256=sha(config_bytes())))
        write_new(CONFIG, config_bytes(), 0o644)
        receipt('CONFIG-ACK.json', dict(sha256=sha(read_safe(CONFIG))))
        expected_config = dict(inventory, **{str(CONFIG): sha(config_bytes())})
        added = set()
        for index, key in enumerate(sorted(keys()), 1):
            need(config_inventory() == expected_config, 'export_config_drift')
            preserve_old(before, table(tools['exportfs']), added)
            check_directories()
            emit(dict(nfs_stage='RUNNING', phase='TARGETED_EXPORT', of=4, number=index))
            receipt('EXPORT-{}-REQUESTED.json'.format(index), dict(path=key[0], client=key[1]))
            bounded_run([tools['exportfs'], '-i', '-o', OPTIONS, key[1] + ':' + key[0]])
            added.add(key)
            preserve_old(before, table(tools['exportfs']), added)
            receipt('EXPORT-{}-ACK.json'.format(index), dict(path=key[0], client=key[1]))
        check_directories()
        need(config_inventory() == expected_config, 'final_export_config_drift')
        preserve_old(before, table(tools['exportfs']), keys())
        result = dict(nfs_directory_export_stage='PASS', utc_finished=utc(), server=SERVER,
                      clients=list(CLIENTS), directories=[str(ROOT / c) for c in CHILDREN],
                      exports_created=4, existing_exports_unchanged=True,
                      existing_export_configs_unchanged=True, root_squash=True,
                      business_data_copied=False, production_mounts_changed=False,
                      kubernetes_writes=0, provider_requests=0, processes_restarted=0,
                      pv_pvc_created=False, cross_node_io='NOT_TESTED',
                      ready_to_migrate=False, ready_to_scale=False, automatic_retry=False)
        receipt('RESULT.json', result)
        emit(dict(result, report=str(STATE / 'RESULT.json'),
                  report_sha256=sha(read_safe(STATE / 'RESULT.json'))))
    except BaseException as exc:
        # Even Ctrl+C keeps the original intent and partial objects. Never undo.
        failure = dict(nfs_directory_export_stage='UNCONFIRMED', error_type=type(exc).__name__,
                       failed_check=str(exc) if isinstance(exc, Failure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                       last_operation=LAST, raw_error_printed=False,
                       instruction='USE_STATUS_NO_REPEAT_CREATE_NO_DELETE_STATE',
                       production_mounts_changed=False, business_data_copied=False,
                       kubernetes_writes=0, provider_requests=0, ready_to_scale=False)
        if not os.path.lexists(str(STATE / 'FAILED.json')):
            receipt('FAILED.json', failure)
        emit(failure)
        return 1
    return 0


def status():
    need(sys.platform == 'linux' and os.geteuid() == 0, 'linux_root_required')
    need(socket.gethostname().split('.')[0] == HOST, 'wrong_host')
    safe_dir(ROOT.parent)
    safe_dir(STATE.parent)
    observed = table(executable('exportfs'))
    receipts = {}
    allowed = {'STARTED.json', 'RESULT.json', 'FAILED.json', 'MKDIR-ACK.json',
               'MKDIR-REQUESTED.json', 'CONFIG-ACK.json', 'CONFIG-REQUESTED.json',
               'CONFIGDIR-REQUESTED.json', 'CONFIGDIR-ACK.json'}
    allowed |= {'EXPORT-{}-{}.json'.format(i, tag) for i in range(1, 5)
                for tag in ('REQUESTED', 'ACK')}
    if os.path.lexists(str(STATE)):
        safe_dir(STATE)
        for name in sorted(allowed):
            p = STATE / name
            if os.path.lexists(str(p)):
                raw = read_safe(p)
                receipts[name] = dict(sha256=sha(raw), bytes=len(raw))
                if name in ('RESULT.json', 'FAILED.json'):
                    value = json.loads(raw)
                    receipts[name]['outcome'] = value.get('nfs_directory_export_stage')
                    receipts[name]['failed_check'] = value.get('failed_check')
    paths = {str(ROOT): metadata(ROOT)}
    # Never traverse a replaced root or unsafe ancestor in status.
    if paths[str(ROOT)].get('directory') and not paths[str(ROOT)].get('symlink'):
        safe_dir(ROOT)
        paths.update({str(ROOT / c): metadata(ROOT / c) for c in CHILDREN})
    config = dict(present=os.path.lexists(str(CONFIG)))
    if config['present']:
        config['matches_expected_bytes'] = read_safe(CONFIG) == config_bytes()
    matching = []
    for (path, client), options in sorted(observed.items()):
        if (path, client) in keys():
            try:
                check_export_options(options)
                valid = True
            except Failure:
                valid = False
            matching.append(dict(path=path, client=client, options_match=valid))
    emit(dict(nfs_stage_status='READ_ONLY', state=str(STATE), paths=paths,
              config=config, target_exports=matching, receipts=receipts,
              files_modified=False, exports_changed=False, automatic_retry=False,
              ready_to_scale=False, cross_node_io='NOT_TESTED'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check-only', action='store_true')
    modes.add_argument('--create-once', action='store_true')
    modes.add_argument('--status', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.status:
            status()
        elif args.check_only:
            _, capacity, inventory, before = preflight()
            emit(dict(nfs_preflight='PASS', capacity=capacity, existing_exports=len(before),
                      existing_config_files=len(inventory), files_modified=False,
                      directories_created=0, ready_to_scale=False))
        else:
            return run_once()
    except BaseException as exc:
        emit(dict(nfs_stage='UNCONFIRMED', error_type=type(exc).__name__,
                  failed_check=str(exc) if isinstance(exc, Failure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                  raw_error_printed=False, automatic_retry=False,
                  instruction='USE_STATUS_NO_REPEAT_CREATE_NO_DELETE_STATE',
                  kubernetes_writes=0, provider_requests=0, ready_to_scale=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
