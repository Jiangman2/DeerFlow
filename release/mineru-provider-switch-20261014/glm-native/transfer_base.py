#!/usr/bin/env python3
"""Private datax-03 -> datax-01 transfer; no image import or workload change."""
import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import queue
import shlex
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import warnings

import export_weknora_base as base
import verify_weknora_base as verify

require, emit = base.require, base.emit
ROOT = Path('/opt/weknora-glm-base-input-20261016-r1')
SOURCE = '/var/lib/weknora-glm-base-export-20261016-r1'
HOST = '192.168.111.18'
FINGERPRINT = 'SHA256:w+TfO7/FUed/JhJVBHY/ms4FqSx1cj0CVOXdJK3GRl8'
REPORT_SHA = '173b6facbb42d00df9be07ff1865a2834c31a86b1347d5231b5b190e0229ab6c'
ARCHIVE_SHA = 'd5bc9a5235a99d2d37218cc0b20a65ad650ce44d0764c2bdf6fb2cefab7943d7'
ARCHIVE_BYTES = 624964608
MANIFEST = 'sha256:579198308537bd130e9daa79173a3ae56897cdd520abc58d88df1efe043095ef'
COMPRESSED_BYTES = 624931352
MAGIC = b'WEKNORA-BASE-TRANSFER-v1\n'
STAGE = 'PREFLIGHT'


def private_write(path, data):
    base.safe_dir(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(data); f.flush(); os.fsync(f.fileno())


def record(name, value):
    private_write(ROOT/name, (json.dumps(value, sort_keys=True, indent=2)+'\n').encode())


def known_host(raw):
    # ssh-keyscan is discovery only; trust comes from the independently supplied
    # datax-03 SHA256 fingerprint, never from the scan itself.
    rows = set()
    for line in raw.decode('ascii').splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[1] != 'ecdsa-sha2-nistp256': continue
        key = base64.b64decode(parts[2], validate=True)
        require(16 < len(key) < 4096, 'invalid_scanned_key')
        fp = 'SHA256:'+base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip('=')
        require(fp == FINGERPRINT, 'datax03_host_fingerprint_mismatch')
        rows.add('datax-03 '+parts[1]+' '+parts[2]+'\n')
    require(len(rows) == 1, 'datax03_host_key_not_unique')
    return next(iter(rows)).encode('ascii')


def remote_source():
    # Embedded code is constant and contains no credential. sudo reads its
    # hidden password from stdin; Python never reads or writes that credential.
    constants = '\n'.join(k+'='+repr(v) for k, v in {
        'ROOT': SOURCE, 'REPORT_SHA': REPORT_SHA, 'ARCHIVE_SHA': ARCHIVE_SHA,
        'SIZE': ARCHIVE_BYTES, 'MAGIC': MAGIC}.items())
    return constants+'\n'+r'''
import hashlib,json,os,socket,stat,struct,sys,time
from pathlib import Path
def check(ok):
 if not ok: raise RuntimeError('source_guard_failed')
def identity(s):
 return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def open_private(path, maximum):
 fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  s=os.fstat(fd)
  check(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1
        and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=maximum)
  return os.fdopen(fd,'rb'),identity(s)
 except BaseException:
  os.close(fd);raise
try:
 check(os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-03')
 root=Path(ROOT)
 for p in reversed([root]+list(root.parents)):
  s=p.lstat();check(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022)
 f,meta=open_private(root/'BASE-EXPORT.json',1048576)
 with f:
  report=f.read(1048577)
  check(hashlib.sha256(report).hexdigest()==REPORT_SHA and identity(os.fstat(f.fileno()))==meta)
 f,meta=open_private(root/'weknora.base.oci.tar',SIZE)
 with f:
  check(meta[2]==SIZE)
  out=sys.stdout.buffer
  out.write(MAGIC+struct.pack('>I',len(report))+report);out.flush()
  digest=hashlib.sha256();count=0;start=time.monotonic()
  while True:
   check(time.monotonic()-start<600)
   block=f.read(1048576)
   if not block:break
   count+=len(block);check(count<=SIZE);digest.update(block);out.write(block)
   delay=count/(16*1048576)-(time.monotonic()-start)
   if delay>0:time.sleep(min(delay,1))
  out.flush()
  check(count==SIZE and digest.hexdigest()==ARCHIVE_SHA and identity(os.fstat(f.fileno()))==meta
        and identity((root/'weknora.base.oci.tar').lstat())==meta)
except BaseException:
 sys.stderr.write('private_source_check=FAILED raw_error_printed=no\n')
 sys.exit(1)
'''


def ssh_command():
    command = 'sudo -S -p '+shlex.quote('')+' -- python3 -I -B -c '+shlex.quote(remote_source())
    return ['ssh', '-T', '-F', '/dev/null', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile='+str(ROOT/'known_hosts'), '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'HostKeyAlias=datax-03', '-o', 'HostKeyAlgorithms=ecdsa-sha2-nistp256',
            '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            '-o', 'NumberOfPasswordPrompts=1', '-o', 'LogLevel=ERROR', 'cnooc@'+HOST, command]


class Receiver:
    """Incremental, bounded protocol; no archive write before pinned report."""
    def __init__(self, write_archive, save_report):
        self.write_archive, self.save_report = write_archive, save_report
        self.buffer, self.header, self.report = bytearray(), False, False
        self.length, self.count, self.digest = None, 0, hashlib.sha256()

    def feed(self, data):
        if not self.report:
            self.buffer.extend(data)
            require(len(self.buffer) <= 1048576+65536+len(MAGIC)+4, 'transfer_header_oversized')
            if not self.header:
                if len(self.buffer) < len(MAGIC)+4: return
                require(bytes(self.buffer[:len(MAGIC)]) == MAGIC, 'transfer_protocol_mismatch')
                self.length = struct.unpack('>I', self.buffer[len(MAGIC):len(MAGIC)+4])[0]
                require(0 < self.length <= 1048576, 'transfer_report_size_invalid')
                del self.buffer[:len(MAGIC)+4]; self.header = True
            if len(self.buffer) < self.length: return
            report = bytes(self.buffer[:self.length]); del self.buffer[:self.length]
            require(hashlib.sha256(report).hexdigest() == REPORT_SHA, 'source_receipt_hash_mismatch')
            obj = json.loads(report, object_pairs_hook=verify.unique)
            require(obj.get('weknora_base_export') == 'PASS' and obj.get('bytes') == ARCHIVE_BYTES
                    and obj.get('archive_sha256') == ARCHIVE_SHA and obj.get('config_digest') == base.CONFIG
                    and obj.get('manifest_digest') == MANIFEST, 'source_receipt_identity_mismatch')
            self.save_report(report)
            data = bytes(self.buffer); self.buffer.clear(); self.report = True
        self.count += len(data)
        require(self.count <= ARCHIVE_BYTES, 'received_archive_oversized')
        self.digest.update(data); self.write_archive(data)

    def finish(self):
        require(self.report and self.count == ARCHIVE_BYTES
                and self.digest.hexdigest() == ARCHIVE_SHA, 'received_archive_truncated_or_hash_mismatch')


def receive(password, tty):
    path = ROOT/'weknora.base.oci.tar.partial'
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    messages, stopped = queue.Queue(maxsize=4), threading.Event()
    p = None
    with os.fdopen(fd, 'wb') as out:
        os.fchmod(out.fileno(), 0o600)
        receiver = Receiver(out.write, lambda raw: private_write(ROOT/'BASE-EXPORT.json', raw))
        try:
            p = subprocess.Popen(ssh_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=tty, bufsize=0)
            sent = 0
            while sent < len(password):
                written = p.stdin.write(memoryview(password)[sent:])
                require(written is not None and written > 0, 'ssh_password_pipe_closed')
                sent += written
            p.stdin.flush(); p.stdin.close()
            for i in range(len(password)): password[i] = 0
            def reader():
                try:
                    while not stopped.is_set():
                        block = p.stdout.read(65536)
                        while not stopped.is_set():
                            try: messages.put(block, timeout=.1); break
                            except queue.Full: pass
                        if not block: return
                except Exception:
                    try: messages.put(None, timeout=.1)
                    except queue.Full: pass
            worker = threading.Thread(target=reader, daemon=True); worker.start()
            start, last = time.monotonic(), time.monotonic()
            while True:
                now = time.monotonic()
                require(now-start < 900, 'private_transfer_deadline')
                if now-last >= 15:
                    base.check_space(ROOT, max(0, ARCHIVE_BYTES-receiver.count))
                    emit({'private_transfer': 'RUNNING', 'bytes_received': receiver.count,
                          'elapsed_seconds': round(now-start), 'source_report_verified': receiver.report})
                    last = now
                try: block = messages.get(timeout=.2)
                except queue.Empty: continue
                require(block is not None, 'ssh_pipe_read_failed')
                if not block: break
                receiver.feed(block)
            require(p.wait(timeout=10) == 0, 'ssh_or_remote_sudo_failed_no_retry')
            receiver.finish()
            out.flush(); os.fsync(out.fileno())
        finally:
            stopped.set()
            for i in range(len(password)): password[i] = 0
            if p is not None:
                if p.poll() is None:
                    p.terminate()
                    try: p.wait(timeout=3)
                    except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=3)
                p.stdout.close()
                if p.stdin and not p.stdin.closed: p.stdin.close()
    return path


def status():
    base.safe_dir(ROOT)
    records = []
    for name in ('STARTED.json', 'BASE-EXPORT.json', 'TRANSFER-PASS.json', 'FAILED.json',
                 'weknora.base.oci.tar.partial', 'weknora.base.oci.tar'):
        path = ROOT/name
        if os.path.lexists(str(path)):
            meta = base.private_meta(path)
            row = {'name': name, 'bytes': meta[2]}
            if name.endswith('.json'):
                require(meta[2] <= 1048576, 'status_record_oversized')
                row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            records.append(row)
    emit({'private_transfer_status': 'READ_ONLY', 'evidence': str(ROOT), 'records': records,
          'network_connections': 0, 'files_modified': False, 'archive_rehashed': False})


def main(argv=None):
    global STAGE
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--pull-once', action='store_true'); mode.add_argument('--status', action='store_true')
    args = parser.parse_args(argv)
    created = False
    try:
        require(sys.platform == 'linux' and os.geteuid() == 0
                and socket.gethostname().split('.')[0] == 'datax-01', 'requires_root_datax01')
        os.umask(0o077)
        if args.status: status(); return 0
        require(not os.path.lexists(str(ROOT)), 'operation_exists_use_status')
        base.check_space(ROOT.parent, ARCHIVE_BYTES+2*1024**2)
        # Exclusive new private directory. No existing permissions are altered.
        ROOT.mkdir(mode=0o700); created = True; base.safe_dir(ROOT)
        record('STARTED.json', {'source_node': 'datax-03', 'source_report_sha256': REPORT_SHA,
                               'source_archive_sha256': ARCHIVE_SHA, 'production_writes': 0})
        STAGE = 'VERIFY_PINNED_HOST_KEY'
        chunks = []
        base.pipe_command(['ssh-keyscan', '-T', '5', '-t', 'ecdsa', HOST], chunks.append, 65536, 15)
        private_write(ROOT/'known_hosts', known_host(b''.join(chunks)))
        emit({'ssh_trust': 'MATCH_PREVIOUSLY_VERIFIED_DATAX03_FINGERPRINT',
              'system_known_hosts_modified': False, 'evidence': str(ROOT)})
        STAGE = 'PRIVATE_SSH_TRANSFER'
        # A terminal is not seekable: do not use buffered r+/BufferedRandom.
        # getpass independently opens its read terminal with echo disabled.
        with open('/dev/tty', 'w', encoding='utf-8') as tty, warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            secret = getpass.getpass('datax-03 cnooc sudo password (hidden; not saved): ', stream=tty)
            require(0 < len(secret) <= 4096 and '\n' not in secret and '\r' not in secret,
                    'invalid_hidden_password')
            password = bytearray(secret.encode()+b'\n'); del secret
            require(len(password) <= 4096, 'hidden_password_size_bound')
            path = receive(password, tty)
        STAGE = 'OFFLINE_LAYER_VERIFICATION'
        before = base.private_meta(path)
        checked = verify.verify(path, emit, MANIFEST, base.CONFIG, 20, COMPRESSED_BYTES)
        require(before == base.private_meta(path), 'received_archive_changed')
        base.check_space(ROOT, 0)
        final = ROOT/'weknora.base.oci.tar'
        require(not os.path.lexists(str(final)), 'final_file_exists')
        path.rename(final)
        result = {'private_base_transfer': 'PASS', 'archive': str(final), 'archive_sha256': ARCHIVE_SHA,
                  'bytes': ARCHIVE_BYTES, 'source_report_sha256': REPORT_SHA,
                  'base_layer_verification': checked['base_layer_verification'],
                  'uncompressed_diffids': checked['uncompressed_diffids'], 'layers_verified': 20,
                  'credentials_saved': False, 'production_workloads_mutated': False,
                  'images_imported_or_pulled': False, 'build_started': False, 'ready_for_build': False,
                  'ready_for_rollout': False, 'provider_requests': 0}
        record('TRANSFER-PASS.json', result); emit(result)
        emit({'report': str(ROOT/'TRANSFER-PASS.json'),
              'report_sha256': hashlib.sha256((ROOT/'TRANSFER-PASS.json').read_bytes()).hexdigest()})
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        failure = {'private_base_transfer': 'UNCONFIRMED', 'stage': STAGE,
                   'failed_check': str(exc) if type(exc) is verify.CheckFailure else 'SAFE_DIAGNOSTIC_REQUIRED',
                   'error_type': type(exc).__name__, 'raw_error_printed': False, 'automatic_retry': False,
                   'production_workloads_mutated': False, 'images_imported_or_pulled': False,
                   'credentials_saved': False, 'provider_requests': 0, 'ready_for_rollout': False,
                   'evidence': str(ROOT), 'instruction': 'USE_STATUS; DO_NOT_REPEAT_PULL'}
        if created:
            try: record('FAILED.json', failure)
            except Exception: failure['failure_receipt_saved'] = False
        emit(failure); return 1


if __name__ == '__main__': sys.exit(main())
