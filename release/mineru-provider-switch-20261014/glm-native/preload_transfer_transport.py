"""Prompt-gated sudo stdin and framed archive transport. Python 3.7+."""
import base64
import getpass
import hashlib
import json
import os
import queue
import re
import shlex
import subprocess
import threading
import time
import uuid
import warnings
import zlib

FRAME = b'WEKNORA-CANDIDATE-ARCHIVE-v2\n'


def need(ok, label):
    if not ok:
        raise RuntimeError(label)


class FramedInput:
    def __init__(self, stream):
        self.stream = stream
        self.checked = False

    def read(self, size):
        if not self.checked:
            # Never expose or persist a rejected prefix (it could be a password).
            prefix = self.stream.readline(len(FRAME) + 1)
            need(prefix == FRAME, 'archive_frame_rejected_NO_IMPORT')
            self.checked = True
        return self.stream.read(size)


def ssh_command(remote, known_hosts, host, prompt):
    need(re.fullmatch(r'WEKNORA-SUDO-[a-f0-9]{32}', prompt), 'invalid_prompt_marker')
    payload = base64.b64encode(zlib.compress(remote.encode(), 9)).decode()
    code = 'import base64,zlib;exec(compile(zlib.decompress(base64.b64decode(' + repr(payload) + ')),"receiver.py","exec"))'
    command = 'sudo -S -p ' + shlex.quote('\n' + prompt + '\n') + ' -- python3 -I -B -c ' + shlex.quote(code)
    need(len(command.encode()) < 120000, 'remote_command_bound')
    return ['ssh', '-T', '-F', '/dev/null', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + str(known_hosts), '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'HostKeyAlias=datax-03', '-o', 'HostKeyAlgorithms=ecdsa-sha2-nistp256',
            '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            '-o', 'NumberOfPasswordPrompts=1', '-o', 'LogLevel=ERROR', 'cnooc@' + host, command]


def hidden_password():
    with open('/dev/tty', 'w', encoding='utf-8') as tty, warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        text = getpass.getpass('datax-03 sudo password (hidden; requested by remote sudo): ', stream=tty)
    need(0 < len(text) <= 2048 and '\n' not in text and '\r' not in text and '\0' not in text, 'invalid_password')
    data = bytearray(text.encode() + b'\n')
    del text
    return data


def send(argv, candidate, opened, size, digest, prompt, emit, ask=hidden_password, deadline=1800):
    """SSH authentication retains its controlling TTY. Never forward raw stderr.

    No bytes are sent until either our exact sudo prompt or receiver READY.
    Passwords are sent once only on the former; archive framing only on the latter.
    Cached/NOPASSWD sudo therefore never places a password in the archive stream.
    """
    child = None
    stopped = threading.Event()
    messages = queue.Queue(maxsize=8)
    writer = None
    readers = []
    sent = []
    result = None
    ready = skipped = asked = False
    eof = set()
    totals = {'stdout': 0, 'stderr': 0}

    def put(item):
        while not stopped.is_set():
            try:
                messages.put(item, timeout=.2)
                return
            except queue.Full:
                pass

    def read_lines(name, stream):
        try:
            while not stopped.is_set():
                line = stream.readline(65537)
                if len(line) > 65536:
                    put(('error', 'remote_line_bound'))
                    return
                put((name, line))
                if not line:
                    return
        except Exception:
            put(('error', 'remote_pipe_read_failed'))

    try:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

        def write_all(data):
            count = 0
            while count < len(data):
                need(not stopped.is_set(), 'transfer_cancelled')
                n = child.stdin.write(memoryview(data)[count:])
                need(n is not None and n > 0, 'ssh_stdin_closed')
                count += n

        for name in ('stdout', 'stderr'):
            t = threading.Thread(target=read_lines, args=(name, getattr(child, name)), daemon=True)
            t.start()
            readers.append(t)

        def stream_archive():
            try:
                write_all(FRAME)
                h = hashlib.sha256()
                count = 0
                start = time.monotonic()
                with opened(candidate, size) as f:
                    while True:
                        block = f.read(65536)
                        if not block:
                            break
                        count += len(block)
                        need(count <= size, 'source_grew')
                        h.update(block)
                        write_all(block)
                        delay = count / (16 * 1024**2) - (time.monotonic() - start)
                        if delay > 0:
                            time.sleep(min(delay, .5))
                need(count == size and h.hexdigest() == digest, 'source_changed_during_transfer')
                child.stdin.close()
                sent.append('PASS')
            except BaseException:
                sent.append('FAIL')
                put(('error', 'source_transfer_failed_USE_STATUS'))

        start = beat = time.monotonic()
        while len(eof) < 2:
            now = time.monotonic()
            need(now - start < deadline, 'remote_transfer_deadline_USE_STATUS')
            if now - beat >= 20:
                emit(dict(private_preload='RUNNING', elapsed_seconds=int(now - start), production_writes=0))
                beat = now
            try:
                channel, data = messages.get(timeout=.2)
            except queue.Empty:
                continue
            need(channel != 'error', data)
            if not data:
                eof.add(channel)
                continue
            totals[channel] += len(data)
            need(totals[channel] <= (1048576 if channel == 'stdout' else 65536), 'remote_output_bound')
            if channel == 'stderr':
                if data.rstrip(b'\r\n') == prompt.encode():
                    need(not asked and not ready and not skipped, 'unexpected_sudo_prompt_NO_RETRY')
                    asked = True
                    password = ask()
                    try:
                        need(isinstance(password, bytearray) and 1 < len(password) <= 8193 and
                             password.endswith(b'\n') and b'\n' not in password[:-1] and b'\r' not in password,
                             'invalid_password_buffer')
                        write_all(password)
                    finally:
                        for i in range(len(password)):
                            password[i] = 0
                continue  # Do not print even authentication stderr.
            row = json.loads(data)
            need(isinstance(row, dict), 'remote_output_not_object')
            if row.get('receiver') == 'READY':
                need(not ready and not skipped and row.get('bytes') == size, 'receiver_handshake')
                ready = True
                writer = threading.Thread(target=stream_archive, daemon=True)
                writer.start()
            elif row.get('receiver') == 'SKIP_TRANSFER_COMPLETE_IMAGE_PRESENT':
                need(not ready and not skipped, 'receiver_handshake')
                skipped = True
                child.stdin.close()
            if row.get('production_node_preload') in ('PASS', 'UNCONFIRMED'):
                result = row
            emit(row)
        code = child.wait(timeout=10)
        if writer:
            writer.join(timeout=5)
        need(code == 0 and result is not None and result.get('production_node_preload') == 'PASS',
             'remote_preload_not_passed_USE_STATUS')
        need((ready and sent == ['PASS']) or skipped, 'transfer_completion_unconfirmed')
        return result
    finally:
        stopped.set()
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            if writer:
                writer.join(timeout=5)
            for t in readers:
                t.join(timeout=2)
            for name in ('stdin', 'stdout', 'stderr'):
                f = getattr(child, name)
                if f and not f.closed:
                    f.close()


def marker():
    return 'WEKNORA-SUDO-' + uuid.uuid4().hex
