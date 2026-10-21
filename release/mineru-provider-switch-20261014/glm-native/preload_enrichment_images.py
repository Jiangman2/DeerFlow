#!/usr/bin/env python3
"""Pinned PG/Redis image transfer + local preload; no stores or Pods started. Py3.7+."""
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
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import threading
import time
import warnings

import export_weknora_base as io
import export_enrichment_images as inventory
import preload_native_candidate as cli
import transfer_base as transfer
import verify_weknora_base as verify

require, emit = verify.require, io.emit
ROOT = Path('/opt/weknora-enrichment-preload-20261017-r1')
LOCK = Path('/var/lib/weknora-enrichment-preload-20261017-r1.lock')
MAGIC = b'WEKNORA-STORE-TRANSFER-v1\n'
PHASE = 'PREFLIGHT'
IMPORTED = []
ITEMS = (
    dict(role='postgres', node='datax-02', host='192.168.111.37',
         fingerprint='SHA256:VIGU1xEuM2WSdXfS2dCu0qqZjSgbl1UeIXDjZEOPWMI',
         image='deerflow.local/weknora-postgres:pg16-search-arm64-20260722-1',
         root_digest='sha256:f094a1fe0d28a507315e482498087e8a6934e666fecaa7cfeb041ee79d84811d',
         root_media_type='application/vnd.oci.image.manifest.v1+json',
         manifest_digest='sha256:f094a1fe0d28a507315e482498087e8a6934e666fecaa7cfeb041ee79d84811d',
         config_digest='sha256:5656e1d666225b9ee09c83684b3f1363d6b216bab498d11e0a186a21e175b85b',
         report_sha256='27ac4b9ff6096b47a4aa1cc9ac07a0a6a0b444934ade337fb9bcdc27fcd3b854',
         archive_sha256='61a43e362909e1597330364a1afcfd6e037fe97a22ab41beba1147404682d24c',
         archive_bytes=636895232, layers=17, compressed_layer_bytes=636866173,
         uncompressed_layer_bytes=713822208),
    dict(role='redis', node='datax-03', host='192.168.111.18',
         fingerprint='SHA256:w+TfO7/FUed/JhJVBHY/ms4FqSx1cj0CVOXdJK3GRl8',
         image='m.daocloud.io/docker.io/library/redis@sha256:c9d92d840fd011c908f040592857c724ae6d877f2aba5c40ad963276507386b2',
         root_digest='sha256:c9d92d840fd011c908f040592857c724ae6d877f2aba5c40ad963276507386b2',
         root_media_type='application/vnd.oci.image.index.v1+json',
         manifest_digest='sha256:4c609b1ccae44c2a0f7b3eca475af5d0f1f02f8fb7df3970ee4d7ab6a5590f56',
         config_digest='sha256:755105238729ef40393b21c3e7e2fb6ee886fc504e04718a4133ca2bc1aa294a',
         report_sha256='5c3b8b501e20f4ef6bcf3f4fe2045dd2a2f550318afea29d746d9fbece10454d',
         archive_sha256='0159f9dd3aabb24f64f3005a9d508c3ae1a021d2665baaa76cb2b79d37e9f290',
         archive_bytes=14084608, layers=8, compressed_layer_bytes=14050131,
         uncompressed_layer_bytes=34874368),
)


def source_root(item):
    return '/var/lib/weknora-enrichment-image-export-'+item['role']+'-20261017-r1'


def phase(name, **fields):
    global PHASE
    PHASE = name
    cli.PHASE = 'ENRICHMENT_STORES_'+name
    emit(dict(fields, enrichment_images='RUNNING', phase=name))


def record(path, value):
    transfer.private_write(path, (json.dumps(value, sort_keys=True, indent=2)+'\n').encode())
    fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def known_host(item, raw):
    rows = set()
    for line in raw.decode('ascii').splitlines():
        p = line.split()
        if len(p) != 3 or p[1] != 'ecdsa-sha2-nistp256': continue
        key = base64.b64decode(p[2], validate=True)
        require(16 < len(key) < 4096, 'invalid_host_key')
        fp = 'SHA256:'+base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip('=')
        require(fp == item['fingerprint'], 'previously_verified_ssh_fingerprint_mismatch')
        rows.add(item['node']+' '+p[1]+' '+p[2]+'\n')
    require(len(rows) == 1, 'host_key_not_unique')
    return next(iter(rows)).encode('ascii')


def remote_source(item):
    constants = '\n'.join(k+'='+repr(v) for k, v in dict(
        ROOT=source_root(item), NODE=item['node'], REPORT_SHA=item['report_sha256'],
        ARCHIVE_SHA=item['archive_sha256'], SIZE=item['archive_bytes'], MAGIC=MAGIC).items())
    return constants+'\n'+r'''
import hashlib,os,socket,stat,struct,sys,time
from pathlib import Path
def check(ok):
 if not ok: raise RuntimeError('private_source_guard')
def ident(s): return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def opened(p,maximum):
 fd=os.open(str(p),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  s=os.fstat(fd)
  check(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1
        and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=maximum)
  return os.fdopen(fd,'rb'),ident(s)
 except BaseException:
  os.close(fd);raise
try:
 check(os.geteuid()==0 and socket.gethostname().split('.')[0]==NODE)
 root=Path(ROOT)
 for p in reversed([root]+list(root.parents)):
  s=p.lstat();check(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022)
 f,before=opened(root/'RESULT.json',1048576)
 with f:
  report=f.read(1048577)
  check(hashlib.sha256(report).hexdigest()==REPORT_SHA and ident(os.fstat(f.fileno()))==before)
 f,before=opened(root/'store.oci.tar',SIZE)
 with f:
  check(before[2]==SIZE)
  out=sys.stdout.buffer;out.write(MAGIC+struct.pack('>I',len(report))+report);out.flush()
  h=hashlib.sha256();count=0;start=time.monotonic()
  while True:
   check(time.monotonic()-start<900)
   block=f.read(1048576)
   if not block:break
   count+=len(block);check(count<=SIZE);h.update(block);out.write(block)
   delay=count/(16*1048576)-(time.monotonic()-start)
   if delay>0:time.sleep(min(delay,1))
  out.flush()
  check(count==SIZE and h.hexdigest()==ARCHIVE_SHA and ident(os.fstat(f.fileno()))==before
        and ident((root/'store.oci.tar').lstat())==before)
except BaseException:
 sys.stderr.write('private_source_check=FAILED raw_error_printed=no\n');sys.exit(1)
'''


def ssh_command(item):
    command = "sudo -S -p '' -- python3 -I -B -c "+shlex.quote(remote_source(item))
    return ['ssh', '-T', '-F', '/dev/null', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile='+str(ROOT/'known_hosts'), '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'HostKeyAlias='+item['node'], '-o', 'HostKeyAlgorithms=ecdsa-sha2-nistp256',
            '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            '-o', 'NumberOfPasswordPrompts=1', '-o', 'LogLevel=ERROR', 'cnooc@'+item['host'], command]


def check_report(item, raw):
    require(hashlib.sha256(raw).hexdigest() == item['report_sha256'], 'export_report_hash')
    r = json.loads(raw, object_pairs_hook=verify.unique)
    require(r.get('store_image_export') == 'PASS' and r.get('uncompressed_diffids') == 'PASS'
            and r.get('base_layer_verification') == 'PASS' and r.get('production_writes') == 0
            and r.get('runtime_volumes_exported') is False, 'export_report_not_accepted')
    for k in ('archive_bytes', 'archive_sha256', 'manifest_digest', 'config_digest',
              'compressed_layer_bytes', 'uncompressed_layer_bytes'):
        require(r.get(k) == item[k], 'export_report_identity:'+k)
    for k in ('image', 'root_digest', 'root_media_type', 'manifest_digest', 'config_digest', 'layers'):
        require(r.get('identity', {}).get(k) == item[k], 'export_source_identity:'+k)
    require(r['identity'].get('source_node') == item['node']
            and r.get('archive') == source_root(item)+'/store.oci.tar', 'export_source_path_or_node')


class Receiver:
    def __init__(self, item, output, receipt):
        self.item, self.output, self.receipt = item, output, receipt
        self.buffer, self.length, self.report = bytearray(), None, False
        self.count, self.digest = 0, hashlib.sha256()

    def feed(self, data):
        if not self.report:
            self.buffer.extend(data)
            require(len(self.buffer) <= 1048576+65536+len(MAGIC)+4, 'transfer_header_bound')
            if self.length is None:
                if len(self.buffer) < len(MAGIC)+4: return
                require(bytes(self.buffer[:len(MAGIC)]) == MAGIC, 'transfer_protocol')
                self.length = struct.unpack('>I', self.buffer[len(MAGIC):len(MAGIC)+4])[0]
                require(0 < self.length <= 1048576, 'transfer_receipt_bound')
                del self.buffer[:len(MAGIC)+4]
            if len(self.buffer) < self.length: return
            raw = bytes(self.buffer[:self.length]); del self.buffer[:self.length]
            check_report(self.item, raw); self.receipt(raw)
            data = bytes(self.buffer); self.buffer.clear(); self.report = True
        self.count += len(data)
        require(self.count <= self.item['archive_bytes'], 'transfer_archive_bound')
        self.digest.update(data); self.output(data)

    def finish(self):
        require(self.report and self.count == self.item['archive_bytes']
                and self.digest.hexdigest() == self.item['archive_sha256'], 'transfer_truncated_or_corrupt')


def receive(item, directory, password, tty):
    path = directory/'store.oci.tar.partial'
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    messages, stopped = queue.Queue(maxsize=4), threading.Event()
    proc = None
    with os.fdopen(fd, 'wb') as output:
        os.fchmod(output.fileno(), 0o600)
        receiver = Receiver(item, output.write, lambda raw: transfer.private_write(directory/'EXPORT.json', raw))
        try:
            proc = subprocess.Popen(ssh_command(item), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=tty, bufsize=0)
            sent = 0
            while sent < len(password):
                count = proc.stdin.write(memoryview(password)[sent:])
                require(count is not None and count > 0, 'password_pipe_closed'); sent += count
            proc.stdin.flush(); proc.stdin.close()
            for i in range(len(password)): password[i] = 0
            def reader():
                try:
                    while not stopped.is_set():
                        data = proc.stdout.read(65536)
                        while not stopped.is_set():
                            try: messages.put(data, timeout=.1); break
                            except queue.Full: pass
                        if not data: return
                except Exception:
                    try: messages.put(None, timeout=.1)
                    except queue.Full: pass
            threading.Thread(target=reader, daemon=True).start()
            start, beat = time.monotonic(), time.monotonic()
            while True:
                now = time.monotonic()
                require(now-start < 1200, 'transfer_deadline_USE_STATUS')
                if now-beat >= 15:
                    io.check_space(ROOT, max(0, item['archive_bytes']-receiver.count))
                    emit(dict(enrichment_transfer='RUNNING', role=item['role'], bytes_received=receiver.count,
                              elapsed_seconds=int(now-start), source_receipt_verified=receiver.report)); beat = now
                try: data = messages.get(timeout=.2)
                except queue.Empty: continue
                require(data is not None, 'ssh_stream_failed')
                if not data: break
                receiver.feed(data)
            require(proc.wait(timeout=10) == 0, 'ssh_or_sudo_failed_NO_RETRY')
            receiver.finish(); output.flush(); os.fsync(output.fileno())
        finally:
            stopped.set()
            for i in range(len(password)): password[i] = 0
            if proc is not None:
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=3)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
                proc.stdout.close()
                if proc.stdin and not proc.stdin.closed: proc.stdin.close()
    return path


def archive_reference(item, path):
    # containerd's io.containerd.image.name annotation wins over OCI ref.name.
    # Permit exactly the pinned name; do not let an archive overwrite other tags.
    with tarfile.open(str(path), 'r:') as archive:
        matches = [m for m in archive if (m.name[2:] if m.name.startswith('./') else m.name) == 'index.json']
        require(len(matches) == 1 and matches[0].isfile() and matches[0].size <= 1024**2,
                'archive_index_invalid')
        with archive.extractfile(matches[0]) as f: index = json.loads(f.read(), object_pairs_hook=verify.unique)
    rows = index.get('manifests', [])
    require(len(rows) == 1 and rows[0].get('digest') == item['root_digest'], 'archive_target_changed')
    annotations = rows[0].get('annotations', {})
    name = annotations.get('io.containerd.image.name') or annotations.get('org.opencontainers.image.ref.name')
    require(name == item['image'], 'archive_import_reference_not_exact')


def pull(item):
    directory = ROOT/item['role']; directory.mkdir(mode=0o700)
    phase('PRIVATE_TRANSFER', role=item['role'], source=item['node'])
    with open('/dev/tty', 'w', encoding='utf-8') as tty, warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        secret = getpass.getpass(item['node']+' cnooc sudo password (hidden; not saved): ', stream=tty)
        require(0 < len(secret) <= 4095 and '\n' not in secret and '\r' not in secret, 'invalid_hidden_password')
        password = bytearray(secret.encode()+b'\n'); del secret
        require(len(password) <= 4096, 'hidden_password_bound')
        path = receive(item, directory, password, tty)
    phase('VERIFY_ARCHIVE_AND_ALL_LAYERS', role=item['role'])
    before = io.private_meta(path)
    checked = verify.verify(path, emit, item['manifest_digest'], item['config_digest'], item['layers'],
                            item['compressed_layer_bytes'], item['root_digest'])
    require(checked['uncompressed_layer_bytes'] == item['uncompressed_layer_bytes'], 'expansion_changed')
    archive_reference(item, path)
    require(io.private_meta(path) == before, 'archive_changed_during_verification')
    final = directory/'store.oci.tar'; require(not os.path.lexists(str(final)), 'archive_destination_exists')
    path.rename(final)
    record(directory/'TRANSFER-PASS.json', dict(item, private_transfer='PASS', archive=str(final)))
    return final, io.private_meta(final)


def reference_present(item):
    rows = cli.command(io.CTR+['images', 'ls', '--quiet', 'name=='+item['image']], limit=65536).decode().split()
    require(rows in ([], [item['image']]), 'unexpected_reference_listing')
    return bool(rows)


def image_check(item, pinned=False):
    inventory.ROLE = dict(item, node='datax-01')
    got = inventory.identity()
    for key in ('image', 'root_digest', 'root_media_type', 'manifest_digest', 'config_digest',
                'layers', 'compressed_layer_bytes'):
        require(got[key] == item[key], 'imported_identity:'+key)
    complete = cli.command(io.CTR+['images', 'check', '--snapshotter', 'overlayfs', '--quiet',
                                    'name=='+item['image']], timeout=60, limit=65536).decode().split()
    require(complete == [item['image']], 'content_or_unpack_incomplete')
    status = cli.cri('inspecti', item['image']).get('status', {})
    require(status.get('id') == item['config_digest'], 'cri_config_changed')
    require(not pinned or status.get('pinned') is True, 'pin_unconfirmed')
    return dict(got, content_and_unpack='PASS', pinned=status.get('pinned') is True)


def processes_clear():
    raw = cli.command(['ps', '-eo', 'comm=,args='], limit=8*1024**2).decode('utf-8', 'replace')
    for row in raw.splitlines():
        require(not re.search(r'^\s*(buildctl|nerdctl)\s|^\s*ctr\s.*\bimages\s+(import|export)\b', row),
                'other_image_operation_active_NO_PROCESS_STOPPED')


def disk_budget(paths, remaining):
    rows, devices = [], set()
    extra = 2*sum(i['compressed_layer_bytes']+i['uncompressed_layer_bytes'] for i in remaining)+2*io.GIB
    for path in paths:
        io.safe_dir(path); devices.add(path.stat().st_dev)
        v = os.statvfs(str(path)); size, free = v.f_blocks*v.f_frsize, v.f_bavail*v.f_frsize
        reserve = max(20*io.GIB, (size+4)//5)
        require(not v.f_flag & os.ST_RDONLY and v.f_favail >= 100000, 'unusable_imagefs')
        require(free >= reserve+extra, 'insufficient_imagefs_reserve_NO_AUTO_CLEANUP')
        rows.append(dict(path=str(path), available_GiB=round(free/io.GIB, 2),
                         required_GiB=round((reserve+extra)/io.GIB, 2)))
    require(devices == {Path('/var/lib/containerd').stat().st_dev}, 'split_imagefs_requires_review')
    return rows


def preflight():
    require(not os.path.lexists(str(ROOT)), 'previous_attempt_exists_USE_STATUS')
    io.safe_dir(ROOT.parent)
    for program in ('ssh', 'ssh-keyscan', 'ctr', 'crictl', 'ps', 'nice', 'ionice'):
        require(shutil.which(program) is not None, 'missing_tool:'+program)
    require(stat.S_ISSOCK(Path(io.SOCKET).lstat().st_mode), 'containerd_socket_missing')
    mem = re.search(r'(?m)^MemAvailable:\s+(\d+)\s+kB$', Path('/proc/meminfo').read_text())
    require(mem is not None and int(mem.group(1))*1024 >= 4*io.GIB, 'host_memory_reserve')
    paths = cli.image_fs_paths(cli.cri('imagefsinfo', '--output', 'json'))
    present = {i['role']: reference_present(i) for i in ITEMS}
    for item in ITEMS:
        if present[item['role']]: image_check(item)
    budget = disk_budget(paths, [i for i in ITEMS if not present[i['role']]])
    io.check_space(ROOT.parent, sum(i['archive_bytes'] for i in ITEMS)+4*1024**2)
    help_text = cli.command(io.CTR+['images', 'import', '--help'], limit=65536)
    for flag in (b'--local', b'--platform', b'--label', b'--snapshotter'):
        require(flag in help_text, 'required_import_flags_unavailable')
    processes_clear()
    return paths, budget


def preload_one(item, archive, metadata, paths, remaining):
    directory = ROOT/item['role']
    io.safe_dir(directory)
    require(io.private_meta(archive) == metadata, 'sealed_archive_changed')
    phase('LOCAL_IMAGE_IMPORT', role=item['role'])
    present = reference_present(item)
    if present:
        image_check(item)  # Conflicting or incomplete preexisting images are never overwritten.
    else:
        disk_budget(paths, remaining); processes_clear()
        require(not reference_present(item), 'reference_appeared_before_import')
        record(directory/'IMPORT-REQUESTED.json', dict(image=item['image'], root_digest=item['root_digest']))
        IMPORTED.append(item['role'])
        cli.command(cli.import_args(archive), timeout=1200, limit=1024**2,
                    monitor=lambda: disk_budget(paths, []))
        record(directory/'IMPORT-ACK.json', {'client_exit_code': 0})
    phase('VERIFY_LOCAL_CONTENT_UNPACK_CRI_PIN', role=item['role'])
    checked = image_check(item)
    if not checked['pinned']:
        record(directory/'PIN-REQUESTED.json', {'image': item['image']})
        cli.command(io.CTR+['images', 'label', item['image'], cli.PIN], limit=65536)
    for n in range(15):
        checked = image_check(item)
        if checked['pinned']: break
        time.sleep(1)
    require(checked['pinned'], 'pin_unconfirmed_USE_STATUS')
    record(directory/'PRELOAD-PASS.json', dict(checked, reused_complete_image=present))
    return checked


def common():
    return dict(production_writes=0, production_workloads_mutated=False, production_database_connections=0,
                production_redis_connections=0, runtime_volumes_copied=False, pods_created=0,
                provider_requests=0, model_changed=False, consumer_slots_changed=False,
                build_started=False, credentials_saved=False, automatic_retry=False,
                ready_for_rollout=False, native_enrichment_acceptance='NOT_TESTED', state=str(ROOT))


def prepare_once():
    paths, budget = preflight()
    ROOT.mkdir(mode=0o700)
    record(ROOT/'STARTED.json', dict(common(), targets=list(ITEMS), imagefs=budget))
    try:
        phase('VERIFY_BOTH_PREVIOUSLY_PINNED_SSH_HOSTS')
        hosts = []
        for item in ITEMS:
            raw = cli.command(['ssh-keyscan', '-T', '5', '-t', 'ecdsa', item['host']], timeout=15, limit=65536)
            hosts.append(known_host(item, raw))
        transfer.private_write(ROOT/'known_hosts', b''.join(hosts))
        archives = [(item,)+pull(item) for item in ITEMS]
        accepted = []
        for n, (item, archive, metadata) in enumerate(archives):
            accepted.append(preload_one(item, archive, metadata, paths, ITEMS[n:]))
        phase('FINAL_IMAGE_RECHECK')
        accepted = [image_check(item, pinned=True) for item in ITEMS]
        disk_budget(paths, []); io.check_space(ROOT, 0)
        result = dict(common(), enrichment_store_preload='PASS', images=accepted,
                      imports_attempted=list(IMPORTED), next_gate='ISOLATED_FULL_APP_ENRICHMENT_FIXTURE')
        record(ROOT/'RESULT.json', result); emit(result)
        emit(dict(report=str(ROOT/'RESULT.json'), report_sha256=hashlib.sha256((ROOT/'RESULT.json').read_bytes()).hexdigest()))
        return 0
    except BaseException as exc:
        result = failure(exc)
        try: record(ROOT/'FAILED.json', result)
        except Exception: result['failure_receipt_saved'] = False
        emit(result); return 1


def failure(exc):
    return dict(common(), enrichment_store_preload='UNCONFIRMED', failed_stage=PHASE,
                error_type=type(exc).__name__, failed_check=cli.safe_detail(exc),
                last_operation=dict(cli.LAST), imports_attempted=list(IMPORTED),
                image_import_may_be_partial=bool(IMPORTED), raw_error_printed=False,
                instruction='USE_STATUS_NO_REPEAT_PREPARE_NO_DELETE_STATE')


def status():
    rows = []
    if os.path.lexists(str(ROOT)):
        io.safe_dir(ROOT)
        for directory in (ROOT,)+(tuple(ROOT/i['role'] for i in ITEMS)):
            if not os.path.lexists(str(directory)): continue
            io.safe_dir(directory)
            for name in ('STARTED.json', 'EXPORT.json', 'TRANSFER-PASS.json', 'IMPORT-REQUESTED.json',
                         'IMPORT-ACK.json', 'PIN-REQUESTED.json', 'PRELOAD-PASS.json', 'RESULT.json',
                         'FAILED.json', 'store.oci.tar', 'store.oci.tar.partial'):
                path = directory/name
                if not os.path.lexists(str(path)): continue
                before = io.private_meta(path); row = dict(path=str(path), bytes=before[2])
                if name.endswith('.json'):
                    require(before[2] <= 2*1024**2, 'receipt_oversized')
                    raw = path.read_bytes(); require(io.private_meta(path) == before, 'receipt_changed')
                    row['sha256'] = hashlib.sha256(raw).hexdigest()
                    if name in ('RESULT.json', 'FAILED.json'):
                        doc = json.loads(raw, object_pairs_hook=verify.unique)
                        row['fields'] = {k: doc.get(k) for k in ('enrichment_store_preload', 'failed_stage', 'failed_check')}
                rows.append(row)
    images = []
    for item in ITEMS:
        try: images.append(image_check(item) if reference_present(item) else dict(role=item['role'], present=False))
        except Exception as exc: images.append(dict(role=item['role'], status='NOT_VERIFIED', failed_check=cli.safe_detail(exc)))
    emit(dict(common(), enrichment_preload_status='READ_ONLY', files_modified=False,
              images_imported_by_status=False, ssh_connections=0, records=rows, images=images))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--prepare-once', action='store_true'); modes.add_argument('--status', action='store_true')
    args = parser.parse_args(argv)
    try:
        require(platform.system() == 'Linux' and platform.machine() in ('aarch64', 'arm64')
                and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_arm64_datax01_required')
        os.umask(0o077)
        if args.status: return status()
        import fcntl
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        def interrupted(signum, frame): raise verify.CheckFailure('interrupted_USE_STATUS')
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT, signal.SIGALRM): signal.signal(sig, interrupted)
        signal.alarm(3600); os.nice(10)
        io.safe_dir(LOCK.parent)
        fd = os.open(str(LOCK), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'rb') as lock:
            s = os.fstat(lock.fileno())
            require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                    and stat.S_IMODE(s.st_mode) == 0o600, 'unsafe_operation_lock')
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return prepare_once()
    except (Exception, KeyboardInterrupt) as exc:
        emit(failure(exc)); return 1
    finally:
        if sys.platform == 'linux': signal.alarm(0)


if __name__ == '__main__': sys.exit(main())
