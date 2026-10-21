#!/usr/bin/env python3
"""Read-only migration preparation on datax-01 / datax-03; Python3.7+ stdlib."""
import argparse
from collections import Counter
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

NS = 'deer-flow'
PREVIOUS = Path('/var/lib/weknora-rwx-crossnode-20261017-r1')
PIN = '8384148615788cfee1a8db011921e19de5dc752bdb32479bfab8cbf04495f756'
BASE = Path('/var/lib')
PREFIX = 'weknora-rwx-migration-audit-'
ROOTS = {'files': Path('/opt/deerflow-storage/weknora-files'),
         'docreader': Path('/opt/deerflow-storage/weknora-docreader')}
OLD = {'files': ('deerflow-weknora-files-pv-datax-03', 'weknora-data-files'),
       'docreader': ('deerflow-weknora-docreader-pv-datax-03', 'weknora-docreader-tmp')}
NEW = {'files': ('deerflow-weknora-files-pv-nfs-r2', 'weknora-data-files-rwx-r2'),
       'docreader': ('deerflow-weknora-docreader-pv-nfs-r2', 'weknora-docreader-tmp-rwx-r2')}
GET_KINDS = {'pv', 'pvc', 'pods', 'deployments', 'statefulsets', 'daemonsets', 'jobs', 'cronjobs'}
SCAN_SECONDS = 120
SCAN_ENTRIES = 1500000
LAST = {}


class Failure(RuntimeError):
    pass


def need(ok, check):
    if not ok:
        raise Failure(check)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def safe_dir(path):
    for p in (path,) + tuple(path.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_private_directory')


def read_private(path):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
             and not s.st_mode & 0o022 and s.st_size <= 1048576, 'unsafe_private_file')
        raw = f.read(1048577)
        need(len(raw) <= 1048576, 'private_size_limit')
    return raw


def save(path, value):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(canonical(value) + b'\n')
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def executable(name, optional=False):
    value = shutil.which(name, path='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
    if optional and not value:
        return None
    need(value, 'missing_tool_' + name)
    p = Path(value).resolve(strict=True)
    safe_dir(p.parent)
    s = p.stat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_tool')
    return str(p)


def run(argv, timeout=25, limit=16777216):
    global LAST
    LAST = dict(program=Path(argv[0]).name)
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, close_fds=True)
    buffers = [bytearray(), bytearray()]
    bad = threading.Event()

    def drain(pipe, index):
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                if len(buffers[index]) + len(chunk) > (limit if index == 0 else 65536):
                    bad.set()
                    break
                buffers[index].extend(chunk)
        except Exception:
            bad.set()
        finally:
            pipe.close()

    threads = [threading.Thread(target=drain, args=(proc.stdout, 0), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, 1), daemon=True)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    expired = False
    try:
        while proc.poll() is None and not bad.is_set():
            if time.monotonic() >= deadline:
                expired = True
                break
            time.sleep(.03)
    finally:
        if proc.poll() is None:
            proc.kill()  # Our reader CLI only, never an application or daemon.
        proc.wait(timeout=5)
        for t in threads:
            t.join(timeout=2)
    LAST.update(exit_code=proc.returncode, timed_out=expired,
                stdout_bytes=len(buffers[0]), stderr_bytes=len(buffers[1]))
    need(not expired and not bad.is_set() and not any(t.is_alive() for t in threads), 'read_incomplete')
    need(proc.returncode == 0, 'read_command_failed')
    return bytes(buffers[0])


class Kube:
    def __init__(self):
        self.exe = executable('kubectl')

    def get(self, kind, name=None):
        need(kind in GET_KINDS, 'get_not_allowed')
        need(name is None or re.fullmatch('[a-z0-9][a-z0-9.-]{0,251}', name), 'bad_object_name')
        args = [self.exe, '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', 'get', kind]
        if name:
            args += [name]
        if kind != 'pv':
            args += ['-n', NS] if name else ['-A']
        args += ['-o', 'json', '--chunk-size=200']
        emit(dict(migration_audit='GET_ONLY', resource=kind, named=name is not None))
        obj = json.loads(run(args))
        need(isinstance(obj, dict), 'bad_kube_json')
        if name:
            need(obj.get('metadata', {}).get('name') == name, 'wrong_object')
        else:
            need(isinstance(obj.get('items'), list) and len(obj['items']) <= 2048, 'list_too_large_or_invalid')
        return obj


def overlap(a, b):
    if not isinstance(a, str) or not a.startswith('/') or '..' in a.split('/'):
        return False
    a, b = a.rstrip('/') or '/', b.rstrip('/') or '/'
    return a == b or a == '/' or b == '/' or a.startswith(b + '/') or b.startswith(a + '/')


def pv_roles(pv):
    s = pv.get('spec', {})
    hits = []
    for role, path in ROOTS.items():
        local = s.get('local', s.get('hostPath', {})).get('path')
        if overlap(local, str(path)):
            hits.append('source_' + role)
        nfs = s.get('nfs', {})
        if nfs.get('server') == '192.168.111.189' and overlap(nfs.get('path'), '/mnt/data/weknora-rwx-r2/' + role):
            hits.append('target_' + role)
    return hits


def pod_spec(obj):
    s = obj.get('spec', {})
    if obj['kind'] == 'Pod':
        return s
    if obj['kind'] == 'CronJob':
        return s.get('jobTemplate', {}).get('spec', {}).get('template', {}).get('spec', {})
    return s.get('template', {}).get('spec', {})


def consumers(objects, pvs):
    aliases = {}
    for pv in pvs:
        hits = pv_roles(pv)
        ref = pv.get('spec', {}).get('claimRef', {})
        if hits and ref.get('namespace') and ref.get('name'):
            aliases[(ref['namespace'], ref['name'])] = hits
    rows = []
    for obj in objects:
        s, meta = pod_spec(obj), obj['metadata']
        namespace = meta.get('namespace', NS)
        volumes = {}
        for v in s.get('volumes', []):
            hits = list(aliases.get((namespace, v.get('persistentVolumeClaim', {}).get('claimName')), []))
            for role, path in ROOTS.items():
                if overlap(v.get('hostPath', {}).get('path'), str(path)):
                    hits.append('source_' + role + '_hostpath')
            nfs = v.get('nfs', {})
            for role in ROOTS:
                if nfs.get('server') == '192.168.111.189' and overlap(nfs.get('path'), '/mnt/data/weknora-rwx-r2/' + role):
                    hits.append('target_' + role + '_direct_nfs')
            if hits:
                volumes[v['name']] = (sorted(set(hits)), bool(v.get('persistentVolumeClaim', {}).get('readOnly')
                                                             or nfs.get('readOnly')))
        mounts = []
        for section in ('containers', 'initContainers', 'ephemeralContainers'):
            for c in s.get(section, []):
                for mount in c.get('volumeMounts', []):
                    if mount['name'] in volumes:
                        roles, volume_ro = volumes[mount['name']]
                        mounts.append(dict(container=c['name'], container_type=section, roles=roles,
                            read_only_declared=bool(volume_ro or mount.get('readOnly')),
                            subpath_present=bool(mount.get('subPath') or mount.get('subPathExpr'))))
        if mounts:
            row = dict(kind=obj['kind'], namespace=namespace, name=meta['name'], uid=meta['uid'],
                       node=s.get('nodeName'), phase=obj.get('status', {}).get('phase'),
                       deleting=bool(meta.get('deletionTimestamp')), mounts=mounts,
                       spec_sha256=sha(canonical(s)))
            if obj['kind'] != 'Pod':
                row.update(replicas=obj.get('spec', {}).get('replicas'), suspended=obj.get('spec', {}).get('suspend'))
            rows.append(row)
    return sorted(rows, key=lambda r: (r['kind'], r['namespace'], r['name']))


def cluster_snapshot(k, created):
    pvs = k.get('pv')['items']
    by_name = {p['metadata']['name']: p for p in pvs}
    volumes = []
    for group, pairs in (('source', OLD), ('target', NEW)):
        for role, (pv_name, pvc_name) in pairs.items():
            need(pv_name in by_name, 'reviewed_pv_missing')
            pv, pvc = by_name[pv_name], k.get('pvc', pvc_name)
            need(pv['status'].get('phase') == 'Bound' and pvc['status'].get('phase') == 'Bound'
                 and pvc['spec'].get('volumeName') == pv_name
                 and pv['spec'].get('claimRef', {}).get('uid') == pvc['metadata']['uid'], 'storage_binding_changed')
            need(pv['spec']['claimRef'].get('namespace') == NS and pv['spec']['claimRef'].get('name') == pvc_name,
                 'claim_reference_changed')
            if group == 'target':
                for obj in (pv, pvc):
                    need(obj['metadata']['uid'] == created[obj['kind'] + '/' + obj['metadata']['name']], 'target_uid_changed')
                need(pv['spec'].get('nfs', {}).get('server') == '192.168.111.189'
                     and pv['spec']['nfs']['path'] == '/mnt/data/weknora-rwx-r2/' + role
                     and not pv['spec']['nfs'].get('readOnly')
                     and pv['spec'].get('accessModes') == ['ReadWriteMany']
                     and {'nfsvers=4.1', 'hard'} <= set(pv['spec'].get('mountOptions', []))
                     and pv['spec'].get('persistentVolumeReclaimPolicy') == 'Retain', 'target_nfs_changed')
            else:
                need(pv['spec'].get('local', {}).get('path') == str(ROOTS[role]), 'source_path_changed')
            volumes.append(dict(group=group, role=role, pv=pv_name, pvc=pvc_name,
                                pv_uid=pv['metadata']['uid'], pvc_uid=pvc['metadata']['uid'],
                                spec_sha256=sha(canonical([pv['spec'], pvc['spec']]))))
    objects = []
    for kind in ('pods', 'deployments', 'statefulsets', 'daemonsets', 'jobs', 'cronjobs'):
        objects += k.get(kind)['items']
    rows = consumers(objects, pvs)
    app = [r for r in rows if r['kind'] == 'Pod' and r['namespace'] == NS and r['name'] == 'weknora-app-c467d64fc-2zbls']
    need(len(app) == 1 and app[0]['uid'] == '2fb12206-2d91-4b12-806b-471e1ca19c61'
         and app[0]['node'] == 'datax-03' and app[0]['phase'] == 'Running' and not app[0]['deleting'], 'app_identity_changed')
    return dict(volumes=volumes, declared_consumers=rows,
                relevant_pv_aliases=[dict(name=p['metadata']['name'], roles=pv_roles(p)) for p in pvs if pv_roles(p)])


def cluster_audit():
    raw = read_private(PREVIOUS/'RESULT.json')
    need(sha(raw) == PIN, 'crossnode_result_hash_changed')
    result = json.loads(raw)
    need(result.get('cross_node_io') == 'PASS' and result.get('production_baseline') == 'UNCHANGED', 'crossnode_not_pass')
    started = json.loads(read_private(PREVIOUS/'STARTED.json'))
    created = {}
    for obj in started['plan']:
        if obj['kind'] not in ('PersistentVolume', 'PersistentVolumeClaim'):
            continue
        key = (obj['kind'] + '-' + obj['metadata']['name']).upper() + '-CREATED.json'
        created[obj['kind'] + '/' + obj['metadata']['name']] = json.loads(read_private(PREVIOUS/key))['uid']
    k = Kube()
    before = cluster_snapshot(k, created)
    after = cluster_snapshot(k, created)
    stable = before == after
    return dict(cluster_inventory=after, baseline_stable=stable,
                actual_write_activity='NOT_MEASURED', outside_kubernetes_writers='NOT_EXCLUDED',
                write_quiescence='NOT_PROVEN', crossnode_receipt_sha256=PIN,
                collection='COMPLETE' if stable else 'BASELINE_CHANGED_REVIEW_REQUIRED')


def mount_metadata(path):
    raw = run([executable('findmnt'), '-J', '-T', str(path), '-o', 'TARGET,SOURCE,FSTYPE'], limit=65536)
    value = json.loads(raw)
    rows = value.get('filesystems', [])
    need(len(rows) == 1 and rows[0].get('fstype') in ('xfs', 'ext4'), 'source_not_reviewed_local_filesystem')
    return dict(filesystem=rows[0]['fstype'], mount_target=rows[0]['target'])


def open_directory(path):
    for p in (path,) + tuple(path.parents):
        need(stat.S_ISDIR(p.lstat().st_mode), 'source_symlink_or_not_directory')
    return os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_NOATIME', 0))


def descendant_mounts(path):
    # Also reject same-device bind mounts, which st_dev alone cannot detect.
    with open('/proc/self/mountinfo', 'rb') as f:
        raw = f.read(2097153)
    need(len(raw) <= 2097152, 'mountinfo_too_large')
    result = set()
    prefix = str(path) + '/'
    for line in raw.decode('utf-8', 'strict').splitlines():
        fields = line.split()
        need(len(fields) >= 6, 'bad_mountinfo')
        point = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), fields[4])
        if point.startswith(prefix):
            result.add(tuple(point[len(prefix):].split('/')))
    return result


def fd_mount_id(fd):
    with open('/proc/self/fdinfo/' + str(fd), 'r') as f:
        text = f.read(8193)
    found = re.search(r'(?m)^mnt_id:\s*(\d+)\s*$', text)
    need(len(text) <= 8192 and found, 'mount_identity_unavailable')
    return int(found.group(1))


def scan(path, role):
    start, progress = time.monotonic(), time.monotonic()
    mounts = descendant_mounts(path)
    root = open_directory(path)
    try:
        before = os.fstat(root)
        need(before.st_uid == 1000 and before.st_gid == 1000 and stat.S_IMODE(before.st_mode) == 0o770,
             'source_root_identity_changed')
        vfs = os.fstatvfs(root)
        mount_id = fd_mount_id(root)
        iterator_root = os.scandir(root)
    except BaseException:
        os.close(root)
        raise
    counts = Counter(entries=0, files=0, directories=1, symlinks=0, special=0, errors=0,
                     apparent_bytes=0, allocated_file_bytes=0, hardlinked_entries=0, sparse_entries=0,
                     non_1000_owner=0, non_1000_group=0, posix_unreadable_for_uid1000=0,
                     untraversable_directories=0, crossed_device_or_mount=0, directory_changes_during_scan=0)
    sizes = Counter(empty=0, under_64KiB=0, under_1MiB=0, under_16MiB=0, at_least_16MiB=0)
    modes = Counter()
    stack = [(root, iterator_root, before, ())]
    stop = None
    try:
        while stack:
            now = time.monotonic()
            if now - start >= SCAN_SECONDS or counts['entries'] >= SCAN_ENTRIES:
                stop = 'TIME_OR_ENTRY_LIMIT'
                break
            if now - progress >= 15:
                emit(dict(source_metadata_scan='RUNNING', role=role, entries=counts['entries'],
                          elapsed_seconds=int(now - start)))
                progress = now
            fd, iterator, opened, relative = stack[-1]
            try:
                entry = next(iterator)
            except StopIteration:
                current = os.fstat(fd)
                if (current.st_mtime_ns, current.st_ctime_ns) != (opened.st_mtime_ns, opened.st_ctime_ns):
                    counts['directory_changes_during_scan'] += 1
                iterator.close()
                os.close(fd)
                stack.pop()
                continue
            counts['entries'] += 1
            try:
                child_relative = relative + (entry.name,)
                if child_relative in mounts:
                    counts['crossed_device_or_mount'] += 1
                    continue
                s = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                if s.st_dev != before.st_dev:
                    counts['crossed_device_or_mount'] += 1
                    continue
                mode = stat.S_IMODE(s.st_mode)
                bits = (mode >> 6) & 7 if s.st_uid == 1000 else ((mode >> 3) & 7 if s.st_gid == 1000 else mode & 7)
                counts['non_1000_owner'] += s.st_uid != 1000
                counts['non_1000_group'] += s.st_gid != 1000
                if stat.S_ISDIR(s.st_mode):
                    counts['directories'] += 1
                    counts['untraversable_directories'] += bits & 5 != 5
                    if len(stack) >= 128:
                        counts['errors'] += 1
                        continue
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_NOATIME', 0), dir_fd=fd)
                    try:
                        actual = os.fstat(child)
                        need((s.st_dev, s.st_ino) == (actual.st_dev, actual.st_ino), 'directory_changed_during_open')
                        if fd_mount_id(child) != mount_id:
                            counts['crossed_device_or_mount'] += 1
                            os.close(child)
                            continue
                        iterator_child = os.scandir(child)
                    except BaseException:
                        os.close(child)
                        raise
                    stack.append((child, iterator_child, actual, child_relative))
                elif stat.S_ISREG(s.st_mode):
                    counts['files'] += 1
                    counts['apparent_bytes'] += s.st_size
                    counts['allocated_file_bytes'] += s.st_blocks * 512
                    counts['hardlinked_entries'] += s.st_nlink > 1
                    counts['sparse_entries'] += s.st_blocks * 512 < s.st_size
                    counts['posix_unreadable_for_uid1000'] += not bool(bits & 4)
                    modes[oct(mode)] += 1
                    band = 'empty' if s.st_size == 0 else ('under_64KiB' if s.st_size < 65536 else
                           ('under_1MiB' if s.st_size < 1048576 else ('under_16MiB' if s.st_size < 16777216 else 'at_least_16MiB')))
                    sizes[band] += 1
                elif stat.S_ISLNK(s.st_mode):
                    counts['symlinks'] += 1  # Do not read the target or follow it.
                else:
                    counts['special'] += 1
            except (OSError, Failure):
                counts['errors'] += 1
        need(not stack or stop, 'scan_state')
    finally:
        for fd, iterator, _, _ in reversed(stack):
            iterator.close()
            os.close(fd)
    mount_table_stable = descendant_mounts(path) == mounts
    if not mount_table_stable:
        stop = 'MOUNT_TABLE_CHANGED'
    complete = stop is None and counts['errors'] == 0 and counts['crossed_device_or_mount'] == 0
    return dict(role=role, traversal='COMPLETE' if complete else 'INCOMPLETE', stop_reason=stop,
                counters=dict(counts), file_size_bands=dict(sizes), top_file_modes=dict(modes.most_common(12)),
                hardlink_bytes_may_be_counted_more_than_once=True, acl_xattr_compatibility='NOT_TESTED',
                effective_uid1000_readability='NOT_TESTED_POSIX_MODE_ESTIMATE_ONLY',
                point_in_time_snapshot=False, available_bytes=vfs.f_bavail * vfs.f_frsize,
                elapsed_seconds=round(time.monotonic() - start, 2), business_file_contents_read=False,
                business_filenames_exported=False, descendant_mount_table_stable=mount_table_stable)


def tool_inventory():
    tools = {name: executable(name) for name in ('rsync', 'setpriv', 'mount.nfs', 'findmnt')}
    raw = run([tools['rsync'], '--version'], limit=65536).decode('utf-8', 'replace')
    match = re.search(r'rsync\s+version\s+([0-9.]+)\s+protocol version\s+([0-9]+)', raw)
    need(match, 'rsync_version_unrecognized')
    rpm = executable('rpm', optional=True)
    package = 'UNAVAILABLE'
    if rpm:
        try:
            value = run([rpm, '-qf', tools['rsync'], '--qf', '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n'], limit=8192).decode().strip()
            if re.fullmatch('[A-Za-z0-9._+~-]{1,200}', value):
                package = value
        except Failure:
            pass
    return dict(tools_available=sorted(tools), rsync_version=match.group(1), protocol=int(match.group(2)),
                rsync_os_package=package, vendor_security_backport_status='NOT_DETERMINED', rsync_copy_invoked=False)


def source_audit():
    os.nice(10)  # Lower only this inventory process's priority.
    info = tool_inventory()
    rows = []
    for role, path in ROOTS.items():
        emit(dict(source_metadata_scan='START', role=role, per_root_deadline_seconds=SCAN_SECONDS))
        mount = mount_metadata(path)
        row = scan(path, role)
        row.update(mount)
        rows.append(row)
        emit(row)
    return dict(tools=info, sources=rows,
                collection='COMPLETE' if all(r['traversal'] == 'COMPLETE' for r in rows) else 'INCOMPLETE',
                crossnode_receipt_sha256=PIN, receipt_binding='EXPECTED_REFERENCE; ACTUAL_BINDING_ON_DATAX01',
                write_quiescence='NOT_PROVEN')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--collect', action='store_true')
    group.add_argument('--status', metavar='REPORT_DIRECTORY')
    args = parser.parse_args()
    host = socket.gethostname().split('.')[0]
    try:
        need(sys.platform == 'linux' and os.geteuid() == 0 and host in ('datax-01', 'datax-03'), 'wrong_host_or_identity')
        safe_dir(BASE)
        if args.status:
            path = Path(args.status)
            need(path.parent == BASE and path.name.startswith(PREFIX), 'unexpected_status_directory')
            emit(dict(migration_audit_status='STORED_RECEIPT_ONLY', result=json.loads(read_private(path/'RESULT.json')),
                      files_modified=False, live_cluster_read=False))
            return 0
        folder = Path(tempfile.mkdtemp(prefix=PREFIX, dir=str(BASE)))
        report = dict(host=host, production_writes=0, provider_requests=0, mounts_changed=False,
                      business_data_copied=False, ready_to_copy=False, ready_to_migrate=False, ready_to_scale=False)
        emit(dict(migration_audit='RUNNING', host=host, state=str(folder)))
        rc = 1
        try:
            report.update(cluster_audit() if host == 'datax-01' else source_audit())
            rc = 0 if report['collection'] == 'COMPLETE' else 1
        except BaseException as exc:
            report.update(collection='INCOMPLETE', error_type=type(exc).__name__,
                          failed_check=str(exc) if isinstance(exc, Failure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                          last_operation=LAST, raw_error_printed=False)
        report['utc_finished'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(folder/'RESULT.json', report)
        emit(report)
        emit(dict(report=str(folder/'RESULT.json'), report_sha256=sha(canonical(report) + b'\n'),
                  status_command='sudo python3 -I -B ' + str(Path(__file__).resolve()) + ' --status ' + str(folder)))
        return rc
    except BaseException as exc:
        emit(dict(migration_audit='INCOMPLETE', error_type=type(exc).__name__,
                  failed_check=str(exc) if isinstance(exc, Failure) else 'SAFE_DIAGNOSTIC_REQUIRED', raw_error_printed=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
