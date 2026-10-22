#!/usr/bin/env python3
"""Once-only internal NFS business sample. Linux Python3.7+ standard library."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import re
import select
import shutil
import socket
import stat
import subprocess
import sys
import time
import uuid

STATE = Path('/var/lib/weknora-rwx-copy-pilot-20261017-r1')
AUDIT = Path('/var/lib/weknora-rwx-migration-audit-6kp1760v/RESULT.json')
AUDIT_SHA = '5220a8c5b3829bc65b93c4c0bb76a2be8270d0f449a9e78aaae52a61353eb68d'
CLUSTER_SHA = '04362498e4a62ff1c20747a5db2430d0611d9bd5c78bd78827d51a1a56cfc49c'
CROSSNODE_SHA = '8384148615788cfee1a8db011921e19de5dc752bdb32479bfab8cbf04495f756'
SOURCE = Path('/opt/deerflow-storage/weknora-files')
EXPORT = '192.168.111.189:/mnt/data/weknora-rwx-r2/files'
UNIT = 'weknora-rwx-copy-pilot-20261017-r1.service'
MIB = 1024**2
MAX_BYTES = 1024 * MIB
MAX_FILES = 5000
RATE = 10 * MIB
SCAN_SECONDS = 180
SCAN_ENTRIES = 1500000
COPY_SECONDS = 1200
MIN_AGE = 300
# Per-band file limits sum to5000; payload budgets sum to1GiB.
BANDS = ('empty', 'under64KiB', 'under1MiB', 'under16MiB', '16to64MiB')
QUOTAS = (20, 2300, 2400, 260, 20)
BUDGETS = (0, 64*MIB, 384*MIB, 320*MIB, 256*MIB)
ENV = dict(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C')
LAST = {}


class Failure(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Failure(code)


def raw(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def emit(value):
    print(raw(value).decode().rstrip(), flush=True)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def failure(exc):
    return dict(error_type=type(exc).__name__, failed_check=str(exc) if isinstance(exc, Failure)
                else 'SAFE_DIAGNOSTIC_REQUIRED', errno=getattr(exc, 'errno', None), raw_error_printed=False)


def safe_dir(path):
    for p in (path,) + tuple(path.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
             'unsafe_private_directory')


def private_read(path):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
             and not s.st_mode & 0o022 and s.st_size <= 1048576, 'unsafe_private_file')
        data = f.read(1048577)
    need(len(data) <= 1048576, 'private_file_oversize')
    return data


def save(path, value, replace=False):
    safe_dir(path.parent)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex) if replace else path
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw(value)); f.flush(); os.fsync(f.fileno())
    if replace:
        os.replace(str(tmp), str(path))  # Only this tool's private progress file.
    fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def executable(name):
    p = shutil.which(name, path=ENV['PATH'])
    need(p, 'missing_tool_' + name)
    p = Path(p).resolve(strict=True)
    safe_dir(p.parent)
    s = p.stat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_executable')
    return str(p)


def command(args, timeout=30):
    global LAST
    LAST = dict(program=Path(args[0]).name, exit_code=None)
    # Commands are a fixed allowlist with small outputs. No business paths in errors.
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=ENV, close_fds=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Do not kill/replay uncertain mount or service-start operations.
        LAST.update(timed_out=True, pid=p.pid)
        raise Failure('command_timeout_outcome_uncertain_no_retry')
    LAST.update(exit_code=p.returncode, stdout_bytes=len(out), stderr_bytes=len(err))
    need(len(out) <= 65536 and len(err) <= 65536, 'command_output_limit')
    need(p.returncode == 0, 'local_command_failed_no_retry')
    return out


def verify_audit():
    data = private_read(AUDIT)
    need(sha(data) == AUDIT_SHA, 'source_receipt_hash')
    r = json.loads(data)
    need(r['collection'] == 'COMPLETE' and r['host'] == 'datax-03', 'source_receipt_incomplete')
    row = next(x for x in r['sources'] if x['role'] == 'files')
    need(row['traversal'] == 'COMPLETE' and row['counters']['errors'] == 0, 'source_scan_incomplete')
    return dict(source_audit_sha256=AUDIT_SHA, operator_cluster_inventory_sha256=CLUSTER_SHA,
                operator_crossnode_sha256=CROSSNODE_SHA, fresh_cluster_check=False)


def resources():
    text = Path('/proc/meminfo').read_text()
    m = re.search(r'(?m)^MemAvailable:\s+(\d+) kB$', text)
    need(m is not None, 'memory_counter_missing')
    available = int(m.group(1)) * 1024
    load = os.getloadavg()[0]
    cpus = os.cpu_count() or 1
    need(available >= 8*1024**3 and load <= 1.5*cpus, 'host_resource_pressure')
    return dict(available_memory_bytes=available, load1=load, logical_cpus=cpus)


def fd_mount_id(fd):
    t = Path('/proc/self/fdinfo/' + str(fd)).read_text()
    m = re.search(r'(?m)^mnt_id:\s*(\d+)\s*$', t)
    need(m is not None, 'fd_mount_identity_unavailable')
    return int(m.group(1))


def open_dir(path, final_path_only=False):
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = Path(path).parts[1:]
        for index, name in enumerate(parts):
            flags = os.O_PATH if final_path_only and index == len(parts)-1 else os.O_RDONLY
            nxt = os.open(name, flags | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = nxt
        return fd
    except BaseException:
        os.close(fd); raise


def mount_rows(text=None):
    if text is None:
        text = Path('/proc/self/mountinfo').read_text()
    need(len(text) < 2097152, 'mountinfo_oversize')
    result = []
    for line in text.splitlines():
        left, sep, right = line.partition(' - ')
        a, b = left.split(), right.split()
        need(sep and len(a) >= 6 and len(b) >= 3, 'mountinfo_shape')
        point = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), a[4])
        result.append(dict(point=point, fstype=b[0], source=b[1],
                           options=set(a[5].split(',') + b[2].split(',')), propagation=a[6:]))
    return result


def isolated_namespace():
    need(os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'), 'private_namespace_required')
    need(all(not any(x.startswith(('shared:', 'master:')) for x in r['propagation'])
             for r in mount_rows()), 'mount_propagation_not_private')


def check_mounts(rows, src, dest):
    a = [r for r in rows if r['point'] == str(src)]
    b = [r for r in rows if r['point'] == str(dest)]
    need(len(a) == len(b) == 1, 'exact_private_mounts_missing')
    need('ro' in a[0]['options'] and a[0]['fstype'] == 'xfs', 'source_bind_not_readonly_xfs')
    need(b[0]['source'] == EXPORT and b[0]['fstype'] in ('nfs', 'nfs4'), 'nfs_source_mismatch')
    need({'rw', 'hard', 'vers=4.1', 'nodev', 'nosuid', 'noexec'} <= b[0]['options']
         and not ({'soft', 'softerr'} & b[0]['options']), 'nfs_options_mismatch')
    need(not any(r['point'].startswith(str(src) + '/') or r['point'].startswith(str(dest) + '/')
                 for r in rows), 'unexpected_descendant_mount')


def identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
            s.st_uid, s.st_gid, s.st_mode, s.st_nlink)


def ordinary(s):
    return (stat.S_ISREG(s.st_mode) and s.st_uid == 1000 and s.st_gid == 1000
            and stat.S_IMODE(s.st_mode) == 0o644 and s.st_nlink == 1)


def band(size):
    if size == 0: return 0
    if size < 65536: return 1
    if size < MIB: return 2
    if size < 16*MIB: return 3
    if size <= 64*MIB: return 4
    return None


def component(name):
    need(isinstance(name, str) and name not in ('', '.', '..') and '/' not in name and '\x00' not in name,
         'unsafe_relative_component')


def sample(root, report):
    start = tick = time.monotonic()
    cutoff = time.time() - MIN_AGE
    rootstat, mount = os.fstat(root), fd_mount_id(root)
    reservoirs, seen = [[] for _ in BANDS], [0 for _ in BANDS]
    rng = random.Random(20261017)
    count, skipped, changed = 0, 0, 0
    fd = os.dup(root)
    stack = [(fd, os.scandir(fd), (), os.fstat(fd))]
    try:
        while stack:
            need(time.monotonic()-start < SCAN_SECONDS and count < SCAN_ENTRIES, 'sample_scan_limit')
            if time.monotonic()-tick >= 15:
                report(dict(phase='SAMPLE_METADATA', entries=count, elapsed_seconds=int(time.monotonic()-start)))
                tick = time.monotonic()
            parent, it, parts, original = stack[-1]
            try:
                entry = next(it)
            except StopIteration:
                current = os.fstat(parent)
                changed += (current.st_mtime_ns, current.st_ctime_ns) != (original.st_mtime_ns, original.st_ctime_ns)
                it.close(); os.close(parent); stack.pop(); continue
            count += 1
            component(entry.name)
            try:
                s = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                skipped += 1; continue
            need(s.st_dev == rootstat.st_dev, 'source_device_changed')
            rel = parts + (entry.name,)
            if stat.S_ISDIR(s.st_mode):
                need(len(rel) < 96, 'source_tree_too_deep')
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=parent)
                try:
                    cs = os.fstat(child)
                    need((cs.st_dev, cs.st_ino) == (s.st_dev, s.st_ino) and fd_mount_id(child) == mount,
                         'source_directory_changed_or_mount')
                    iterator = os.scandir(child)
                except BaseException:
                    os.close(child); raise
                stack.append((child, iterator, rel, cs))
            elif ordinary(s) and max(s.st_mtime, s.st_ctime) <= cutoff and band(s.st_size) is not None:
                idx = band(s.st_size)
                seen[idx] += 1
                item = (rel, identity(s))
                if len(reservoirs[idx]) < QUOTAS[idx]:
                    reservoirs[idx].append(item)
                else:
                    choice = rng.randrange(seen[idx])
                    if choice < QUOTAS[idx]: reservoirs[idx][choice] = item
            else:
                skipped += 1
    finally:
        for fd, it, _, _ in stack:
            it.close(); os.close(fd)
    chosen, sizes = [], []
    for idx, reservoir in enumerate(reservoirs):
        used, n = 0, 0
        rng.shuffle(reservoir)
        for rel, ident in reservoir:
            if used + ident[2] <= BUDGETS[idx]:
                chosen.append((rel, ident, idx)); used += ident[2]; n += 1
        sizes.append(dict(band=BANDS[idx], files=n, bytes=used, eligible_files=seen[idx]))
    need(0 < len(chosen) <= MAX_FILES and 0 < sum(x[1][2] for x in chosen) <= MAX_BYTES, 'empty_or_oversize_sample')
    return chosen, dict(entries_scanned=count, skipped_ineligible_or_vanished=skipped,
                        directory_changes=changed, bands=sizes, scan_seconds=round(time.monotonic()-start, 3))


def walk_fd(root, parts, create=False):
    fd, mount = os.dup(root), fd_mount_id(root)
    try:
        for name in parts:
            component(name)
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=fd)
            os.close(fd); fd = nxt
            need(fd_mount_id(fd) == mount, 'descendant_mount_rejected')
            if create:
                s = os.fstat(fd)
                need(s.st_uid == s.st_gid == 1000 and stat.S_IMODE(s.st_mode) == 0o700,
                     'pilot_directory_identity')
        return fd
    except BaseException:
        os.close(fd); raise


class Pacer:
    def __init__(self, rate=RATE, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep, self.rate = clock, sleep, rate
        self.due, self.total = clock(), 0

    def consume(self, count):
        # No accumulated idle credit: every read/write payload block is paced.
        self.due = max(self.due, self.clock()) + count/self.rate
        self.total += count
        delay = self.due - self.clock()
        if delay > 0: self.sleep(delay)


def copy_one(src, dst, item, pacer, deadline):
    parts, expected, idx = item
    need(parts and expected[2] <= MAX_BYTES, 'invalid_sample_entry')
    for p in parts: component(p)
    parent = walk_fd(src, parts[:-1])
    sfd = dparent = dfd = vfd = None
    try:
        sfd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=parent)
        before = os.fstat(sfd)
        need(ordinary(before) and identity(before) == tuple(expected) and fd_mount_id(sfd) == fd_mount_id(src),
             'sample_source_changed_before_copy')
        dparent = walk_fd(dst, parts[:-1], create=True)
        dfd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dparent)
        written, digest = 0, hashlib.sha256()
        while written < before.st_size:
            need(time.monotonic() < deadline, 'copy_deadline')
            chunk = os.read(sfd, min(262144, before.st_size-written))
            need(chunk, 'source_short_read')
            pacer.consume(len(chunk))
            view = memoryview(chunk)
            while view:
                n = os.write(dfd, view); need(n > 0, 'destination_short_write'); view = view[n:]
            digest.update(chunk); written += len(chunk)
        need(not os.read(sfd, 1) and identity(os.fstat(sfd)) == tuple(expected), 'sample_source_changed_during_copy')
        os.fsync(dfd)
        ds = os.fstat(dfd)
        need(stat.S_ISREG(ds.st_mode) and ds.st_nlink == 1 and ds.st_size == written
             and ds.st_uid == ds.st_gid == 1000 and stat.S_IMODE(ds.st_mode) == 0o600, 'destination_identity')
        os.close(dfd); dfd = None
        os.fsync(dparent)
        vfd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME, dir_fd=dparent)
        need(identity(os.fstat(vfd)) == identity(ds), 'destination_replaced')
        actual, read = hashlib.sha256(), 0
        while read < written:
            need(time.monotonic() < deadline, 'verify_deadline')
            pacer.consume(min(262144, written-read))
            chunk = os.read(vfd, min(262144, written-read)); need(chunk, 'destination_short_read')
            actual.update(chunk); read += len(chunk)
        need(not os.read(vfd, 1) and actual.digest() == digest.digest()
             and identity(os.fstat(vfd)) == identity(ds), 'destination_checksum_or_identity')
        need(identity(os.fstat(sfd)) == tuple(expected)
             and identity(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)) == tuple(expected),
             'source_changed_before_readback_complete')
        return written
    finally:
        for fd in (vfd, dfd, dparent, sfd, parent):
            if fd is not None: os.close(fd)


def worker(src, dst, token, report):
    need(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 1000 and os.getgroups() == [],
         'business_io_requires_uid1000_no_extra_groups')
    os.umask(0o077)
    for fd in (src, dst):
        s = os.fstat(fd)
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == s.st_gid == 1000 and stat.S_IMODE(s.st_mode) == 0o770,
             'source_or_export_root_identity')
    need(os.fstatvfs(src).f_flag & os.ST_RDONLY, 'business_source_not_readonly')
    space = os.fstatvfs(dst)
    need(space.f_bavail*space.f_frsize >= 100*1024**3 and space.f_favail >= 100000, 'nfs_headroom_low')
    with os.scandir(dst) as it:
        need(next(it, None) is None, 'target_export_not_empty_review_required')
    report(dict(phase='SAMPLE_METADATA', uid=1000, gid=1000, nfs_available_bytes=space.f_bavail*space.f_frsize))
    chosen, summary = sample(src, report)
    need(re.fullmatch('[0-9a-f]{32}', token), 'bad_token')
    directory = '.weknora-copy-pilot-' + token
    report(dict(phase='CREATE_PILOT_DIRECTORY', sample=summary, selected_files=len(chosen),
                selected_bytes=sum(x[1][2] for x in chosen), pilot_directory=directory))
    # Recheck root emptiness after scanning; exclusive mkdir is the write boundary.
    with os.scandir(dst) as it:
        need(next(it, None) is None, 'target_changed_before_copy')
    os.mkdir(directory, 0o700, dir_fd=dst); os.fsync(dst)
    pilot = walk_fd(dst, (directory,))
    counters = [dict(band=name, files=0, bytes=0, seconds=0.0) for name in BANDS]
    start = tick = time.monotonic()
    files = total = 0
    pacer = Pacer()
    try:
        for item in chosen:
            need(files + 1 <= MAX_FILES and total + item[1][2] <= MAX_BYTES, 'hard_copy_budget')
            before = time.monotonic()
            copied = copy_one(src, pilot, item, pacer, start+COPY_SECONDS)
            files += 1; total += copied
            c = counters[item[2]]; c['files'] += 1; c['bytes'] += copied; c['seconds'] += time.monotonic()-before
            if time.monotonic()-tick >= 10:
                report(dict(phase='COPY_AND_SHA256_READBACK', verified_files=files, verified_bytes=total,
                            elapsed_seconds=int(time.monotonic()-start)))
                tick = time.monotonic()
        os.fsync(pilot)
    finally:
        os.close(pilot)
    elapsed = time.monotonic()-start
    for c in counters: c['seconds'] = round(c['seconds'], 3)
    return dict(pilot='PASS', verified_files=files, verified_bytes=total, bands=counters,
                copy_verify_seconds=round(elapsed, 3), logical_MiB_per_second=round(total/MIB/max(elapsed, .001), 3),
                files_per_second=round(files/max(elapsed, .001), 3), payload_io_bytes=pacer.total,
                pilot_directory=directory, sample=summary, sample_checksum_readback='PASS',
                source_stable_during_each_selected_copy=True, effective_uid1000_readability='PASS',
                source_contents_read=True, full_source_snapshot=False, pilot_retained=True, cross_node_readback='NOT_TESTED',
                server_failure_durability='NOT_TESTED', acl_xattr_preservation='NOT_TESTED',
                throughput_scope='THROTTLED_STDLIB_COPY_FSYNC_CACHED_READBACK_NOT_RSYNC_OR_MAX_THROUGHPUT')


def run_worker(src, dst, token):
    readfd, writefd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(readfd)
        def send(value):
            data = raw(value)
            while data:
                n = os.write(writefd, data); data = data[n:]
        try:
            os.setgroups([]); os.setgid(1000); os.setuid(1000)
            # Root-squashed NFS root is0770/1000:1000. Root holds only O_PATH;
            # acquire real directory-read access only after dropping identity.
            readable = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=dst)
            os.close(dst); dst = readable
            result = worker(src, dst, token, lambda v: send(dict(progress=v)))
        except BaseException as exc:
            result = dict(pilot='UNCONFIRMED', **failure(exc))
        send(dict(result=result))
        os.close(writefd)
        os._exit(0 if result['pilot'] == 'PASS' else 1)
    os.close(writefd)
    data, result, progress = b'', None, dict(phase='WORKER_STARTING')
    try:
        while True:
            ready, _, _ = select.select([readfd], [], [], 15)
            if not ready:
                save(STATE/'PROGRESS.json', dict(utc=utc(), worker_pid=pid, last_progress=progress,
                     heartbeat=True, hard_nfs_io_may_wait=True), replace=True)
                continue
            chunk = os.read(readfd, 16384)
            if not chunk: break
            data += chunk
            need(len(data) <= 131072, 'worker_message_limit')
            while b'\n' in data:
                line, data = data.split(b'\n', 1)
                value = json.loads(line)
                if 'progress' in value:
                    progress = value['progress']
                    save(STATE/'PROGRESS.json', dict(utc=utc(), worker_pid=pid, last_progress=progress), replace=True)
                    emit(progress)
                if 'result' in value:
                    need(result is None, 'duplicate_worker_result')
                    result = value['result']
        _, status = os.waitpid(pid, 0)
        need(result is not None and not data and os.WIFEXITED(status), 'worker_result_missing')
        need(os.WEXITSTATUS(status) == (0 if result['pilot'] == 'PASS' else 1), 'worker_exit_mismatch')
        return result
    finally:
        os.close(readfd)


def internal_run(token):
    base = dict(production_mounts_changed=False, production_configuration_changed=False,
                production_workloads_mutated=False, provider_requests=0, source_files_modified=False,
                automatic_retry=False, ready_to_migrate=False, ready_to_scale=False,
                business_copy_limit_bytes=MAX_BYTES, business_copy_limit_files=MAX_FILES,
                max_combined_copy_readback_payload_bytes_per_second=RATE,
                mounts_scope='PRIVATE_NAMESPACE_ONLY', source_contents_read='NOT_CONFIRMED')
    src = dst = None
    try:
        record = json.loads(private_read(STATE/'STARTED.json'))
        need(record['token'] == token and record['script_sha256'] == sha(private_read(Path(__file__))),
             'run_identity_changed')
        need(not (STATE/'RUN-ENTERED.json').exists(), 'internal_run_already_entered_no_retry')
        save(STATE/'RUN-ENTERED.json', dict(utc=utc()))
        isolated_namespace()
        base.update(verify_audit())
        base['resources_before'] = resources()
        check = open_dir(SOURCE)
        try:
            s = os.fstat(check)
            need(s.st_uid == s.st_gid == 1000 and stat.S_IMODE(s.st_mode) == 0o770, 'source_root_changed')
            need(not any(x['point'].startswith(str(SOURCE)+'/') for x in mount_rows()), 'source_submount_present')
        finally: os.close(check)
        mount = executable('mount')
        srcpath, dstpath = STATE/'source-ro', STATE/'nfs'
        srcpath.mkdir(mode=0o700); dstpath.mkdir(mode=0o700)
        save(STATE/'MOUNT-REQUESTED.json', dict(utc=utc(), scope='PRIVATE_NAMESPACE_ONLY'))
        command([mount, '--bind', str(SOURCE), str(srcpath)])
        command([mount, '-o', 'remount,bind,ro,nosuid,nodev,noexec,noatime', str(srcpath)])
        command([mount, '-t', 'nfs', '-o', 'vers=4.1,proto=tcp,hard,timeo=600,retrans=2,retry=0,nosuid,nodev,noexec',
                 EXPORT, str(dstpath)], timeout=45)
        isolated_namespace(); check_mounts(mount_rows(), srcpath, dstpath)
        src, dst = open_dir(srcpath), open_dir(dstpath, final_path_only=True)
        save(STATE/'WORKER-REQUESTED.json', dict(utc=utc(), pilot_directory='.weknora-copy-pilot-'+token))
        base.update(run_worker(src, dst, token))
        base['resources_after'] = resources()
    except BaseException as exc:
        base.update(pilot='UNCONFIRMED', **failure(exc), last_operation=LAST)
    finally:
        for fd in (src, dst):
            if fd is not None: os.close(fd)
    base['utc_finished'] = utc()
    # No forced unmount: these non-shared mounts disappear with the unit namespace.
    save(STATE/'RESULT.json', base)
    emit(base)
    return 0 if base['pilot'] == 'PASS' else 1


def start():
    need(not STATE.exists(), 'previous_attempt_exists_use_status_no_retry')
    evidence = verify_audit()
    resources()
    for name in ('systemd-run', 'systemctl', 'unshare', 'mount', 'mount.nfs'):
        executable(name)
    source = private_read(Path(__file__).resolve())
    safe_dir(STATE.parent)
    STATE.mkdir(mode=0o700)  # Exclusive one-operation boundary, including concurrent launchers.
    tool = STATE/'pilot.py'
    fd = os.open(str(tool), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(source); f.flush(); os.fsync(f.fileno())
    token = uuid.uuid4().hex
    save(STATE/'STARTED.json', dict(token=token, script_sha256=sha(source), utc=utc(), evidence=evidence))
    save(STATE/'START-REQUESTED.json', dict(unit=UNIT, utc=utc()))
    args = [executable('systemd-run'), '--unit='+UNIT, '--property=Type=oneshot',
            '--property=Restart=no', '--property=TimeoutStartSec=0', '--property=Nice=10',
            '--property=IOSchedulingClass=idle', executable('unshare'), '--mount', '--propagation', 'private',
            str(Path(sys.executable).resolve()), '-I', '-B', '-u', str(tool), '--run', token]
    command(args, timeout=20)
    save(STATE/'START-ACK.json', dict(unit=UNIT, utc=utc()))
    emit(dict(pilot='BACKGROUND_STARTED_NOT_COMPLETED', state=str(STATE), unit=UNIT,
              status_command='sudo python3 -I -B '+str(tool)+' --status',
              terminal_disconnect_does_not_cancel_unit=True, automatic_retry=False))


def status():
    need(STATE.is_dir(), 'state_not_present')
    value = dict(pilot_status='READ_ONLY_STORED_EVIDENCE', files_modified=False,
                 mounts_created=False, copy_started_by_status=False, automatic_retry=False)
    for name in ('STARTED.json', 'PROGRESS.json', 'RESULT.json'):
        if (STATE/name).exists():
            data = private_read(STATE/name)
            value[name] = dict(sha256=sha(data), fields=json.loads(data))
    try:
        text = command([executable('systemctl'), 'show', UNIT, '--no-pager',
                        '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result', '-p', 'ExecMainStatus']).decode()
        value['unit'] = {k: v for k, v in (line.split('=', 1) for line in text.splitlines() if '=' in line)
                         if k in ('ActiveState', 'SubState', 'Result', 'ExecMainStatus')}
    except BaseException as exc:
        value['unit_status'] = failure(exc)
    emit(value)


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--start-once', action='store_true'); g.add_argument('--status', action='store_true')
    g.add_argument('--run', help=argparse.SUPPRESS)
    args = p.parse_args()
    try:
        need(sys.platform == 'linux' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-03',
             'datax03_root_required')
        if args.status: status()
        elif args.run: return internal_run(args.run)
        else: start()
        return 0
    except BaseException as exc:
        emit(dict(pilot='UNCONFIRMED', **failure(exc), last_operation=LAST,
                  instruction='USE_STATUS_NO_REPEAT_START_NO_DELETE_STATE', automatic_retry=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
