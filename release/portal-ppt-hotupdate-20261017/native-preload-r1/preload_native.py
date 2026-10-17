#!/usr/bin/env python3
"""Six fixed candidates, datax-01 -> datax-02. No rollout. Python 3.7+."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import threading
import time
import uuid

PROFILE_SHA = 'fdeef64d8c1ced80251f733e0e83cad589032712c1b8a476affc1797105002d2'
GIB = 1024 ** 3
CTR = ['ctr', '--address', '/run/containerd/containerd.sock', '--namespace', 'k8s.io']
CRI = ['crictl', '--runtime-endpoint', 'unix:///run/containerd/containerd.sock', '--image-endpoint', 'unix:///run/containerd/containerd.sock']
META_LIMIT = 8 * 1024 ** 2
STATE = None
PHASE = 'START'
REPORT = None
DEADLINE = None
CODE_SNAPSHOT = None
PROFILE_SNAPSHOT = None
MARKER = 'PORTAL_NATIVE_RESULT='
SAFE_ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'}


class Failure(Exception):
    def __init__(self, code):
        self.code = code
        Exception.__init__(self, code)


def require(ok, code):
    if not ok:
        raise Failure(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode('utf-8')


def parse(raw):
    require(len(raw) <= META_LIMIT, 'metadata_limit')
    def pairs(rows):
        out = {}
        for k, v in rows:
            require(k not in out, 'duplicate_json_key')
            out[k] = v
        return out
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Failure('nonfinite_json')))
    except Failure:
        raise
    except Exception:
        raise Failure('invalid_json')


def progress(phase, **values):
    global PHASE
    PHASE = phase
    print(json.dumps(dict(phase=phase, **values), sort_keys=True), file=sys.stderr, flush=True)


def deadline():
    require(DEADLINE is None or time.monotonic() < DEADLINE, 'total_deadline')


def identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def safe_dir(path):
    path = Path(path)
    require(path.is_absolute(), 'directory_not_absolute')
    for item in (path,) + tuple(path.parents):
        s = item.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'directory_not_root_private')


def fresh(path):
    path = Path(path)
    safe_dir(path.parent)
    path.mkdir(mode=0o700)
    safe_dir(path)
    return path


def write_new(path, raw):
    path = Path(path)
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def read_file(path, limit=META_LIMIT, root_only=False):
    path = Path(path)
    if root_only:
        safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
                'unsafe_metadata_file')
        if root_only:
            require(before.st_uid == 0 and not before.st_mode & 0o022, 'metadata_not_root_owned')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()),
                'metadata_changed')
        return raw
    finally:
        os.close(fd)


def load_profile():
    raw = read_file(Path(__file__).resolve().parent / 'PROFILE.json', 65536)
    require(sha(raw) == PROFILE_SHA, 'profile_hash_mismatch')
    return parse(raw)


def token_ok(token):
    require(isinstance(token, str) and re.fullmatch('[a-f0-9]{32}', token), 'invalid_operation_token')
    return token


def paths(token):
    token_ok(token)
    return dict(source=Path('/var/lib/portal-ppt-native-preload-source-' + token),
                state=Path('/var/lib/portal-ppt-native-preload-target-' + token),
                control=Path('/var/lib/portal-ppt-native-preload-control-' + token),
                upload=Path('/opt/portal-ppt-native-upload-' + token))


def stop(proc, own_group):
    if proc is None:
        return
    if own_group:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        # The leader may have exited while its descendants still hold pipes.
        # Only this launch's PG is signalled; never the shared terminal group.
        until = time.monotonic() + 2
        while time.monotonic() < until:
            proc.poll()
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif proc.poll() is None:
        proc.kill()
    if proc.poll() is None:
        proc.wait(timeout=5)


def foreground(fd, pgid):
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        os.tcsetpgrp(fd, pgid)
    finally:
        signal.signal(signal.SIGTTOU, previous)


def restore_terminal(fd, pgid, attributes):
    import termios
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        if pgid is not None:
            os.tcsetpgrp(fd, pgid)
        if attributes is not None:
            termios.tcsetattr(fd, termios.TCSANOW, attributes)
    finally:
        signal.signal(signal.SIGTTOU, previous)


def run(argv, phase, timeout=30, limit=META_LIMIT, interactive=False, monitor=None, allow_failure=False):
    """Continuously drain both pipes; retain bounded stdout, never raw stderr."""
    deadline()
    progress(phase, password='ENTER_IN_TERMINAL_IF_PROMPTED' if interactive else 'NOT_REQUIRED')
    out, exceeded, failed = bytearray(), threading.Event(), threading.Event()
    counts = [0, 0]
    proc, terminal, workers, original_foreground, original_attributes = None, None, [], None, None
    def drain(stream, which):
        try:
            prompt_tail = b''
            while True:
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    break
                counts[which] += len(chunk)
                if which == 0 and len(out) < limit:
                    out.extend(chunk[:limit - len(out)])
                if interactive:
                    joined = prompt_tail + chunk
                    if b'[portal sudo password]' in joined:
                        print('[portal sudo password] ', file=sys.stderr, end='', flush=True)
                    prompt_tail = joined[-24:]
                if counts[which] > limit:
                    exceeded.set()
        except Exception:
            failed.set()
    started = last = watched = time.monotonic()
    try:
        terminal = open('/dev/tty', 'rb') if interactive else None
        if terminal:
            import termios
            original_foreground = os.tcgetpgrp(terminal.fileno())
            original_attributes = termios.tcgetattr(terminal.fileno())
        proc = subprocess.Popen(argv, stdin=terminal if terminal else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=SAFE_ENV,
                                start_new_session=not interactive,
                                preexec_fn=os.setpgrp if interactive else None)
        if terminal:
            foreground(terminal.fileno(), proc.pid)
            os.killpg(proc.pid, signal.SIGCONT)
        for which, stream in enumerate((proc.stdout, proc.stderr)):
            thread = threading.Thread(target=drain, args=(stream, which), daemon=True)
            workers.append(thread)
            thread.start()
        while proc.poll() is None:
            deadline()
            require(time.monotonic() - started < timeout, 'command_timeout_' + phase)
            require(not exceeded.is_set() and not failed.is_set(), 'command_output_limit_' + phase)
            if monitor and time.monotonic() - watched > 10:
                monitor()
                watched = time.monotonic()
            if time.monotonic() - last > 20:
                progress(phase, elapsed_seconds=int(time.monotonic() - started))
                last = time.monotonic()
            time.sleep(0.1)
        for thread in workers:
            thread.join(timeout=3)
        require(all(not t.is_alive() for t in workers) and not failed.is_set() and not exceeded.is_set(),
                'command_pipe_failure_' + phase)
        require(allow_failure or proc.returncode == 0, 'command_nonzero_' + phase)
        return proc.returncode, bytes(out)
    finally:
        try:
            stop(proc, True)
        finally:
            try:
                if terminal:
                    restore_terminal(terminal.fileno(), original_foreground, original_attributes)
            finally:
                if terminal:
                    terminal.close()
                if proc:
                    for stream in (proc.stdout, proc.stderr):
                        if stream:
                            stream.close()


def capture(argv, phase, **kwargs):
    return run(argv, phase, **kwargs)[1]


def disk(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'unsafe_filesystem_path')
    s, v = path.stat(), os.statvfs(str(path))
    kind = capture(['findmnt', '-n', '-o', 'FSTYPE', '--target', str(path)], 'FILESYSTEM', limit=4096).decode().strip()
    require(kind in ('ext4', 'xfs') and not v.f_flag & os.ST_RDONLY, 'unsupported_filesystem')
    return dict(path=str(path), device=s.st_dev, capacity_bytes=v.f_blocks * v.f_frsize,
                available_bytes=v.f_bavail * v.f_frsize, available_inodes=v.f_favail)


def reserve(row):
    return max(20 * GIB, (row['capacity_bytes'] + 4) // 5)


def image_fs_paths(value):
    if isinstance(value.get('status'), dict):
        require(not any(k in value for k in ('imageFilesystems', 'containerFilesystems')), 'ambiguous_imagefs_shape')
        value = value['status']
    rows = value.get('imageFilesystems', [])
    containers = value.get('containerFilesystems', [])
    require(isinstance(rows, list) and 0 < len(rows) <= 4 and isinstance(containers, list) and len(containers) <= 4,
            'imagefs_metadata_shape')
    paths_out = []
    for row in rows + containers:
        name = row.get('fsId', {}).get('mountpoint')
        require(safe_path(name), 'imagefs_path_invalid')
        paths_out.append(name)
    return sorted(set(paths_out))


def budget_plan(fs, archive_bytes, expanded, largest, sealed_present, uploaded):
    """Conservative per-device peak; credit no shared blobs or snapshots."""
    result = {}
    for role, row in fs.items():
        device = str(row['device'])
        if device not in result:
            result[device] = dict(row, required_bytes=reserve(row) + 2 * GIB, roles=[])
        result[device]['roles'].append(role)
    result[str(fs['upload']['device'])]['required_bytes'] += 0 if uploaded else archive_bytes
    result[str(fs['sealed']['device'])]['required_bytes'] += 0 if sealed_present else archive_bytes
    # Full compressed archive bytes upper-bound content-store ingestion. The
    # second expanded copy covers snapshot preparation/copy-up, plus scratch.
    result[str(fs['image']['device'])]['required_bytes'] += archive_bytes + 2 * expanded + largest
    return list(result.values())


def target_budget(profile, rows=None, sealed_present=False, uploaded=False):
    rows = profile['images'] if rows is None else rows
    images = [disk(Path(p)) for p in image_fs_paths(parse(capture(CRI + ['imagefsinfo', '--output', 'json'], 'IMAGEFS')))]
    require(len({r['device'] for r in images}) == 1, 'split_imagefs_not_supported')
    fs = dict(upload=disk('/opt'), sealed=disk('/var/lib'), image=images[0])
    amounts = budget_plan(fs, sum(r['archive_bytes'] for r in rows),
                          sum(r['expanded_bytes_upper_bound'] for r in rows),
                          max([r['archive_bytes'] for r in rows] or [0]), sealed_present, uploaded)
    for item in amounts:
        require(item['available_bytes'] >= item['required_bytes'] and item['available_inodes'] >= 100000,
                'storage_peak_budget_insufficient')
    return dict(policy='NO_SHARED_BLOB_CREDIT_FULL_CONTENT_PLUS_2X_EXPANDED_AND_SCRATCH', filesystems=amounts,
                resource_capacity_pass=False)


def monitor_paths(names):
    def check():
        for name in names:
            v = os.statvfs(str(name))
            require(v.f_bavail * v.f_frsize >= max(20 * GIB, (v.f_blocks * v.f_frsize + 4) // 5)
                    and v.f_favail >= 100000, 'live_storage_reserve_reached')
    return check


def open_anchored(path):
    """Anchor each component; never follow a user-controlled intermediate link."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'source_path_invalid')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)


def seal(source, destination, row, monitor=None):
    fd = open_anchored(source)
    safe_dir(Path(destination).parent)
    out_fd = None
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == row['archive_bytes'],
                'unsafe_archive_source')
        out_fd = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        count, digest, started, last = 0, hashlib.sha256(), time.monotonic(), time.monotonic()
        with os.fdopen(fd, 'rb', closefd=False) as src, os.fdopen(out_fd, 'wb', closefd=False) as dst:
            while True:
                deadline()
                data = src.read(4 * 1024 ** 2)
                if not data:
                    break
                count += len(data)
                require(count <= row['archive_bytes'] and time.monotonic() - started < 1800, 'seal_limit')
                digest.update(data)
                dst.write(data)
                if monitor:
                    monitor()
                if time.monotonic() - last > 20:
                    progress('SEAL_ARCHIVE', role=row['role'], bytes=count, total_bytes=row['archive_bytes'])
                    last = time.monotonic()
            dst.flush()
            os.fsync(dst.fileno())
        require(count == row['archive_bytes'] and digest.hexdigest() == row['archive_sha256'], 'archive_sha_or_size_mismatch')
        require(identity(before) == identity(os.fstat(fd)), 'source_changed_during_seal')
    finally:
        os.close(fd)
        if out_fd is not None:
            os.close(out_fd)


def digest(value):
    require(isinstance(value, str) and re.fullmatch('sha256:[a-f0-9]{64}', value), 'invalid_digest')
    return value


def refs(row):
    return (row['tag'], row['digest_reference'])


def listing(raw, ref):
    lines = raw.decode('utf-8').splitlines()
    rows = [line.split() for line in lines if line.split() and line.split()[0] == ref]
    require(len(rows) <= 1, 'duplicate_image_reference')
    if not rows:
        # ctr outputs a header for no rows. Any other row is an invalid filter.
        require(all(not line.strip() or line.split()[0] == 'REF' for line in lines), 'unexpected_reference_listing')
        return None
    require(len(rows[0]) >= 3, 'invalid_reference_listing')
    return digest(rows[0][2])


def reference(ref):
    return listing(capture(CTR + ['images', 'ls', 'name==' + ref], 'REFERENCE_LOOKUP', limit=65536), ref)


def collision_check(profile):
    for row in profile['images']:
        for ref in refs(row):
            found = reference(ref)
            require(found is None or found == row['manifest_digest'], 'existing_candidate_reference_conflict')


def blob_json(dig, expected_size=None):
    digest(dig)
    raw = capture(CTR + ['content', 'get', dig], 'CONTENT_METADATA', limit=META_LIMIT)
    require('sha256:' + sha(raw) == dig and (expected_size is None or len(raw) == expected_size),
            'content_metadata_digest_or_size_mismatch')
    return parse(raw)


def descriptor(value):
    require(isinstance(value, dict) and type(value.get('size')) is int and 0 < value['size'] <= META_LIMIT,
            'invalid_metadata_descriptor')
    return digest(value.get('digest')), value['size']


def manifest_config(manifest_digest, config_digest=None):
    value = blob_json(manifest_digest)
    require(value.get('schemaVersion') == 2 and isinstance(value.get('layers'), list) and 0 < len(value['layers']) <= 512,
            'manifest_shape')
    config_ref, size = descriptor(value.get('config'))
    require(config_digest is None or config_ref == config_digest, 'config_binding_mismatch')
    config = blob_json(config_ref, size)
    require(config.get('architecture') == 'arm64' and config.get('os') == 'linux', 'image_not_linux_arm64')
    return config_ref, config


def complete_row(row):
    for ref in refs(row):
        require(reference(ref) == row['manifest_digest'], 'candidate_reference_changed')
        raw = capture(CTR + ['images', 'check', '--snapshotter', 'overlayfs', '--quiet', 'name==' + ref],
                      'CONTENT_AND_UNPACK', limit=65536)
        require(raw.decode().split() == [ref], 'content_or_unpack_incomplete')
        cri = parse(capture(CRI + ['inspecti', ref], 'CRI_VISIBILITY'))
        status = cri.get('status', {})
        require(status.get('id') in (row['manifest_digest'], row['config_digest']), 'cri_identity_not_bound')
    config_ref, config = manifest_config(row['manifest_digest'], row['config_digest'])
    return dict(role=row['role'], tag=row['tag'], digest_reference=row['digest_reference'],
                manifest_digest=row['manifest_digest'], config_digest=config_ref, platform='linux/arm64',
                archive_sha256=row['archive_sha256'], content_and_unpack='PASS', cri_visibility='PASS'), config


def archive_metadata(path, row):
    """Read only small metadata; archive hash is already sealed. Never extract."""
    wanted = {'index.json', 'blobs/sha256/' + row['manifest_digest'][7:], 'blobs/sha256/' + row['config_digest'][7:]}
    found = {}
    with tarfile.open(str(path), mode='r:') as archive:
        for number, member in enumerate(archive):
            deadline()
            require(number < 4096, 'archive_header_count_limit')
            if member.name in wanted:
                require(member.isfile() and not member.linkname and member.size <= META_LIMIT and member.name not in found,
                        'archive_metadata_shape')
                found[member.name] = archive.extractfile(member).read(META_LIMIT + 1)
    require(set(found) == wanted, 'archive_metadata_missing')
    index = parse(found['index.json'])
    manifests = index.get('manifests')
    require(index.get('schemaVersion') == 2 and isinstance(manifests, list) and len(manifests) == 1,
            'archive_index_shape')
    item = manifests[0]
    require(item.get('digest') == row['manifest_digest'], 'archive_index_manifest_mismatch')
    annotations = item.get('annotations', {})
    require(annotations.get('io.containerd.image.name') == row['tag'], 'archive_unexpected_import_reference')
    expected_ref_name = row['tag'] if row.get('role') == 'ppt-master-agent-host-forward' else row['tag'].rsplit(':', 1)[1]
    require(annotations.get('org.opencontainers.image.ref.name') == expected_ref_name, 'archive_unexpected_import_reference')
    for dig in (row['manifest_digest'], row['config_digest']):
        require('sha256:' + sha(found['blobs/sha256/' + dig[7:]]) == dig, 'archive_metadata_hash')
    manifest = parse(found['blobs/sha256/' + row['manifest_digest'][7:]])
    require(manifest.get('config', {}).get('digest') == row['config_digest'], 'archive_config_binding')
    config = parse(found['blobs/sha256/' + row['config_digest'][7:]])
    require(config.get('architecture') == 'arm64' and config.get('os') == 'linux', 'archive_platform')
    return config


def safe_path(value):
    return isinstance(value, str) and 0 < len(value) <= 512 and value.startswith('/') and re.fullmatch(r'/[A-Za-z0-9_./+ -]*', value) is not None and '..' not in value.split('/') and not any(c.isspace() for c in value)


def host_projection(config, config_digest):
    env = config.get('config', {}).get('Env', [])
    require(isinstance(env, list) and len(env) <= 512, 'host_env_shape')
    keep = {'PATH', 'HOME', 'XDG_CACHE_HOME', 'PLAYWRIGHT_BROWSERS_PATH', 'PPT_MASTER_AGENT_COMMAND'}
    selected = {}
    for item in env:
        require(isinstance(item, str) and len(item) <= 65536 and '=' in item, 'host_env_shape')
        key, value = item.split('=', 1)
        if key in keep:
            require(key not in selected, 'host_duplicate_runtime_key')
            selected[key] = value
    paths_out = {}
    for key in ('PATH', 'HOME', 'XDG_CACHE_HOME', 'PLAYWRIGHT_BROWSERS_PATH'):
        if key not in selected:
            continue
        value = selected[key]
        if key == 'PATH':
            require(0 < len(value) <= 4096 and all(safe_path(x) for x in value.split(':')), 'host_runtime_path_unsupported')
        else:
            require((key == 'PLAYWRIGHT_BROWSERS_PATH' and value == '0') or safe_path(value), 'host_runtime_path_unsupported')
        paths_out[key] = value
    require('PATH' in paths_out, 'host_runtime_path_absent')
    selector = None
    if 'PPT_MASTER_AGENT_COMMAND' in selected:
        try:
            tokens = shlex.split(selected['PPT_MASTER_AGENT_COMMAND'], posix=True)
        except ValueError:
            tokens = []
        selector = tokens[0] if len(tokens) == 1 and tokens[0] in ('codex', '/usr/local/bin/codex', '/usr/bin/codex') else 'UNSUPPORTED_CONFIGURED_LAUNCHER'
    return dict(state='SAFE_IMAGE_PATHS_BOUND', image_config_digest=config_digest,
                runtime_paths=paths_out, codex_selector=selector)


def postgres_cache(profile):
    ref = profile['postgres_reference']
    target = reference(ref)
    require(target is not None, 'postgres_reference_not_cached')
    # The digest in a reference can be an index or a manifest, never assumed config.
    require(target == ref.rsplit('@', 1)[1], 'postgres_reference_target_mismatch')
    value = blob_json(target)
    selected = target
    if isinstance(value.get('manifests'), list):
        matches = [r for r in value['manifests'] if r.get('platform', {}).get('os') == 'linux'
                   and r.get('platform', {}).get('architecture') == 'arm64'
                   and r.get('platform', {}).get('variant', 'v8') == 'v8']
        require(len(matches) == 1, 'postgres_arm64_index_selection_ambiguous')
        selected, size = descriptor(matches[0])
        blob_json(selected, size)
    config_ref, config = manifest_config(selected)
    require(reference(ref) == target, 'postgres_reference_changed')
    return dict(state='CACHED_LINUX_ARM64_IDENTITY_BOUND', reference=ref, target_digest=target,
                manifest_digest=selected, config_digest=config_ref, platform='linux/arm64', node='datax-02',
                executed=False, imported=False, runtime_version='NOT_EXECUTED', runtime_contract=postgres_contract(config))


def postgres_contract(config):
    runtime = config.get('config', {})
    entry, cmd, user = runtime.get('Entrypoint'), runtime.get('Cmd'), runtime.get('User', '')
    launcher = 'DOCKER_ENTRYPOINT_POSTGRES' if entry in (['docker-entrypoint.sh'], ['/usr/local/bin/docker-entrypoint.sh']) and cmd == ['postgres'] else 'UNSUPPORTED_LAUNCHER'
    user_projection = user if isinstance(user, str) and (user in ('', 'postgres', '0', '0:0') or re.fullmatch(r'[0-9]{1,10}(?::[0-9]{1,10})?', user)) else 'UNSUPPORTED_USER'
    major = None
    env = runtime.get('Env', [])
    require(isinstance(env, list) and len(env) <= 512, 'postgres_env_shape')
    for item in env:
        if isinstance(item, str) and item.startswith('PG_MAJOR='):
            require(major is None, 'postgres_duplicate_major')
            value = item.split('=', 1)[1]
            major = value if re.fullmatch('[0-9]{1,2}', value) else 'UNSUPPORTED'
    return dict(launcher=launcher, user=user_projection, pg_major=major,
                command_sha256=sha(encoded([entry, cmd])), arbitrary_command_exported=False)


def node_guard(profile):
    raw = capture(['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '--cache-dir=/dev/null', 'get', 'node', 'datax-02', '-o', 'json'],
                  'TARGET_NODE_IDENTITY', limit=2 * 1024 ** 2)
    node = parse(raw)
    expect = profile['target']
    require(node.get('metadata', {}).get('name') == expect['name'] and node['metadata'].get('uid') == expect['uid'], 'target_node_uid_changed')
    info = node.get('status', {}).get('nodeInfo', {})
    require(info.get('architecture') == 'arm64' and info.get('operatingSystem') == 'linux', 'target_architecture_changed')
    addresses = node.get('status', {}).get('addresses', [])
    require([x.get('address') for x in addresses if x.get('type') == 'InternalIP'] == [expect['ip']], 'target_ip_changed')
    cond = {x.get('type'): x.get('status') for x in node.get('status', {}).get('conditions', [])}
    require(cond.get('Ready') == 'True' and all(cond.get(k) == 'False' for k in ('MemoryPressure', 'DiskPressure', 'PIDPressure')),
            'target_not_ready_or_under_pressure')
    require(not node.get('spec', {}).get('unschedulable') and not node.get('spec', {}).get('taints'), 'target_scheduling_changed')
    return dict(name=expect['name'], uid=expect['uid'], ip=expect['ip'], platform='linux/arm64', ready=True,
                scheduler_capacity='UNDETERMINED_NOT_COLLECTED', capacity_pass=False)


def trusted_key(raw, expected):
    keys = set()
    for line in raw.decode('ascii').splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == 'ecdsa-sha2-nistp256':
            blob = base64.b64decode(parts[2], validate=True)
            finger = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
            if finger == expected['ssh_fingerprint']:
                keys.add(parts[1] + ' ' + parts[2])
    require(len(keys) == 1, 'ssh_fingerprint_mismatch')
    return (expected['name'] + ' ' + keys.pop() + '\n').encode('ascii')


def ssh_options(known):
    return ['-F', '/dev/null', '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=datax-02',
            '-o', 'UserKnownHostsFile=' + str(known), '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'HostKeyAlgorithms=ecdsa-sha2-nistp256', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-o', 'ForwardAgent=no', '-o', 'UpdateHostKeys=no']


def remote_result(raw):
    matches = re.findall(rb'PORTAL_NATIVE_RESULT=([A-Za-z0-9+/=]+)', raw)
    require(len(matches) == 1, 'remote_receipt_missing_or_multiple')
    value = parse(base64.b64decode(matches[0], validate=True))
    require(value.get('schema') == 'portal-ppt-native-preload.v1' and value.get('target') == 'datax-02', 'remote_receipt_schema')
    return value


def bootstrap_command(code, profile_raw, token):
    """Command carries small verified text only; archives always use scp."""
    token_ok(token)
    require(sha(profile_raw) == PROFILE_SHA and len(code) < 128 * 1024, 'bootstrap_input')
    control = str(paths(token)['control'])
    embedded = {'preload_native.py': base64.b64encode(code).decode(), 'PROFILE.json': base64.b64encode(profile_raw).decode()}
    expected = {key: sha(base64.b64decode(val)) for key, val in embedded.items()}
    source = "import os,stat,base64,hashlib,socket\nassert os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-02'\n"
    source += "p='/var/lib'\ns=os.lstat(p)\nassert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022\n"
    source += 'd=' + repr(control) + '\nos.mkdir(d,0o700)\n'
    source += 'files=' + repr(embedded) + '\nhashes=' + repr(expected) + '\n'
    source += "for name,val in files.items():\n raw=base64.b64decode(val,validate=True)\n assert hashlib.sha256(raw).hexdigest()==hashes[name]\n fd=os.open(d+'/'+name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)\n with os.fdopen(fd,'wb') as f: f.write(raw);f.flush();os.fsync(f.fileno())\n"
    source += "os.execvp('python3',['python3','-I','-B','-u',d+'/preload_native.py','--prepare','--token'," + repr(token) + '])\n'
    return 'sudo -p ' + shlex.quote('[portal sudo password] ') + ' python3 -I -B -c ' + shlex.quote(source)


def prepare_target(profile, token):
    global STATE, REPORT
    selected = paths(token)
    safe_dir(Path(__file__).resolve().parent)
    STATE = fresh(selected['state'])
    REPORT = base_report(token)
    write_new(STATE / 'STARTED.json', encoded(REPORT))
    REPORT['disk_before'] = target_budget(profile)
    collision_check(profile)
    fresh(STATE / 'sealed')
    # /opt is the historical untrusted data staging filesystem. Never root
    # write/chown beneath its user-writable ancestor; run mkdir as cnooc.
    run(['runuser', '-u', 'cnooc', '--', 'mkdir', '-m', '700', str(selected['upload'])], 'CREATE_USER_UPLOAD')
    s = selected['upload'].lstat()
    import pwd
    owner = pwd.getpwnam('cnooc')
    require(owner.pw_uid > 0 and stat.S_ISDIR(s.st_mode) and s.st_uid == owner.pw_uid and stat.S_IMODE(s.st_mode) == 0o700,
            'upload_directory_invalid')
    REPORT.update(status='PREPARED_NO_IMPORT', upload=str(selected['upload']), upload_device=s.st_dev,
                  upload_inode=s.st_ino, upload_uid=owner.pw_uid)
    write_new(STATE / 'PREPARED.json', encoded(REPORT))
    return REPORT


def import_target(profile, token):
    global STATE, REPORT
    selected = paths(token)
    STATE = selected['state']
    safe_dir(STATE)
    prepared = parse(read_file(STATE / 'PREPARED.json', root_only=True))
    require(prepared.get('operation') == token and prepared.get('status') == 'PREPARED_NO_IMPORT', 'prepared_state_invalid')
    REPORT = base_report(token)
    write_new(STATE / 'IMPORT-ATTEMPT.json', encoded(REPORT))  # exclusive, no retry
    s = selected['upload'].lstat()
    require(stat.S_ISDIR(s.st_mode) and (s.st_dev, s.st_ino, s.st_uid) ==
            (prepared['upload_device'], prepared['upload_inode'], prepared['upload_uid']), 'upload_directory_replaced')
    REPORT['disk_before_seal'] = target_budget(profile, uploaded=True)
    collision_check(profile)
    for row in profile['images']:
        destination = STATE / 'sealed' / row['basename']
        seal(selected['upload'] / row['basename'], destination, row, monitor_paths(['/var/lib', '/opt']))
        archive_metadata(destination, row)
        write_new(STATE / (row['role'] + '.SEALED.json'), encoded(dict(role=row['role'], archive_sha256=row['archive_sha256'])))
    collision_check(profile)
    image_dirs = image_fs_paths(parse(capture(CRI + ['imagefsinfo', '--output', 'json'], 'IMAGEFS')))
    for i, row in enumerate(profile['images']):
        REPORT['disk_before_import_' + str(i)] = target_budget(profile, profile['images'][i:], True, True)
        collision_check(profile)
        write_new(STATE / (row['role'] + '.IMPORT-REQUESTED.json'), encoded(dict(role=row['role'], automatic_retry=False)))
        # --digests plus fixed --base-name creates only this repository's
        # digest alias, needed by imagePullPolicy Never native fixture Pods.
        argv = ['nice', '-n', '10', 'ionice', '-c', '2', '-n', '7'] + CTR + [
            'images', 'import', '--local', '--platform', 'linux/arm64', '--snapshotter', 'overlayfs',
            '--digests', '--base-name', row['repository'], '--label', 'io.cri-containerd.image=managed',
            str(STATE / 'sealed' / row['basename'])]
        run(argv, 'IMPORT_' + str(i), timeout=2400, limit=2 * 1024 ** 2,
            monitor=monitor_paths(['/var/lib', '/opt'] + image_dirs))
        receipt, config = complete_row(row)
        REPORT['image_rows'].append(receipt)
        write_new(STATE / (row['role'] + '.PASS.json'), encoded(receipt))
        if row['role'] == 'ppt-master-agent-host-forward':
            try:
                REPORT['host_runtime_inputs'] = host_projection(config, row['config_digest'])
            except Failure as exc:
                REPORT['host_runtime_inputs'] = dict(state='NOT_COLLECTED', error_code=exc.code)
    try:
        REPORT['postgres_cache'] = postgres_cache(profile)
    except Exception as exc:
        REPORT['postgres_cache'] = dict(state='NOT_COLLECTED', error_code=exc.code if isinstance(exc, Failure) else 'METADATA_UNAVAILABLE',
                                        scope='LOCAL_NODE_CACHE_ONLY_NOT_CLUSTER_ABSENCE')
    require(len(REPORT['image_rows']) == 6, 'six_images_not_confirmed')
    # Recheck all fixed references after the serial imports.
    for row in profile['images']:
        complete_row(row)
    REPORT['status'] = 'COMPLETE_PRELOAD'
    write_new(STATE / 'REPORT.json', encoded(REPORT))
    return REPORT


def base_report(token):
    return dict(schema='portal-ppt-native-preload.v1', operation=token, target='datax-02', source_host='datax-01',
                profile_sha256=PROFILE_SHA, status='INCOMPLETE', image_rows=[], postgres_cache=dict(state='NOT_COLLECTED'),
                host_runtime_inputs=dict(state='NOT_COLLECTED'), evidence=str(STATE) if STATE else None,
                capacity_pass=False, ready_for_rollout=False, native_runtime_passed=False,
                workload_mutation=False, database_access=False, model_access=False, automatic_retry=False,
                pin_label_added=False, existing_images_deleted=False)


def distribute(profile):
    global STATE, REPORT
    token = uuid.uuid4().hex
    selected = paths(token)
    STATE = fresh(selected['source'])
    REPORT = base_report(token)
    write_new(STATE / 'STARTED.json', encoded(REPORT))
    REPORT['node_before'] = node_guard(profile)
    fs = disk('/var/lib')
    require(fs['available_bytes'] >= profile['archive_total_bytes'] + reserve(fs) + 2 * GIB and fs['available_inodes'] >= 100000,
            'source_seal_space_insufficient')
    sealed = fresh(STATE / 'sealed')
    for row in profile['images']:
        seal(row['source_path'], sealed / row['basename'], row, monitor_paths(['/var/lib']))
        archive_metadata(sealed / row['basename'], row)
    code, profile_raw = CODE_SNAPSHOT, PROFILE_SNAPSHOT
    require(isinstance(code, bytes) and isinstance(profile_raw, bytes), 'initial_code_snapshot_missing')
    require(sha(profile_raw) == PROFILE_SHA, 'profile_changed')
    write_new(STATE / 'preload_native.py', code)
    write_new(STATE / 'PROFILE.json', profile_raw)
    known = STATE / 'known_hosts'
    scanned = capture(['ssh-keyscan', '-T', '8', '-t', 'ecdsa', profile['target']['ip']], 'PINNED_SSH_KEY', limit=65536)
    write_new(known, trusted_key(scanned, profile['target']))
    opts, remote = ssh_options(known), 'cnooc@' + profile['target']['ip']
    command = bootstrap_command(code, profile_raw, token)
    rc, raw = run(['ssh'] + opts + ['-tt', remote, command], 'REMOTE_PREPARE', 300,
                  interactive=True, allow_failure=True)
    receipt = remote_result(raw)
    write_new(STATE / 'TARGET-PREPARE.json', encoded(receipt))
    require(rc == 0 and receipt.get('status') == 'PREPARED_NO_IMPORT' and receipt.get('operation') == token, 'target_prepare_failed')
    # Binary files are scp positional sources, never sent through a TTY stream.
    run(['scp'] + opts + [str(sealed / row['basename']) for row in profile['images']] +
        [remote + ':' + str(selected['upload']) + '/'], 'TRANSFER_SIX_ARCHIVES', 5400, interactive=True, limit=2 * 1024 ** 2)
    node_guard(profile)
    command = 'sudo -p ' + shlex.quote('[portal sudo password] ') + ' python3 -I -B -u ' + str(selected['control'] / 'preload_native.py') + ' --import-images --token ' + token
    rc, raw = run(['ssh'] + opts + ['-tt', remote, command], 'REMOTE_IMPORT', 15600,
                  interactive=True, allow_failure=True, limit=2 * 1024 ** 2)
    receipt = remote_result(raw)
    write_new(STATE / 'TARGET-REPORT.json', encoded(receipt))
    REPORT['target_report'] = receipt
    require(rc == 0 and receipt.get('status') == 'COMPLETE_PRELOAD' and len(receipt.get('image_rows', [])) == 6
            and receipt.get('operation') == token, 'target_preload_incomplete')
    REPORT['node_after'] = node_guard(profile)
    REPORT.update(status='COMPLETE_PRELOAD', image_rows=receipt['image_rows'], postgres_cache=receipt['postgres_cache'],
                  host_runtime_inputs=receipt['host_runtime_inputs'])
    write_new(STATE / 'REPORT.json', encoded(REPORT))
    return REPORT


def main():
    global DEADLINE, CODE_SNAPSHOT, PROFILE_SNAPSHOT
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for mode in ('distribute', 'prepare', 'import-images'):
        group.add_argument('--' + mode, action='store_true')
    parser.add_argument('--token')
    args = parser.parse_args()
    require(sys.platform.startswith('linux') and os.geteuid() == 0, 'linux_root_required')
    hostname = socket.gethostname().split('.')[0]
    require(hostname == ('datax-01' if args.distribute else 'datax-02'), 'host_out_of_scope')
    require((args.distribute and args.token is None) or (not args.distribute and token_ok(args.token)), 'invalid_mode_token')
    os.umask(0o077)
    def interrupted(signum, frame):
        raise Failure('interrupted_no_automatic_retry')
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    DEADLINE = time.monotonic() + 21600
    # The operator-verified entry is the trust boundary. Capture both files
    # before any long transfer/hash phase; never switch to later user bytes.
    CODE_SNAPSHOT = read_file(Path(__file__).absolute(), 128 * 1024)
    PROFILE_SNAPSHOT = read_file(Path(__file__).absolute().parent / 'PROFILE.json', 65536)
    require(sha(PROFILE_SNAPSHOT) == PROFILE_SHA, 'profile_hash_mismatch')
    profile = parse(PROFILE_SNAPSHOT)
    import fcntl
    safe_dir('/var/lib')
    lock = os.open('/var/lib/portal-ppt-native-preload-r1.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(lock)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and not s.st_mode & 0o022, 'unsafe_lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = distribute(profile) if args.distribute else prepare_target(profile, args.token) if args.prepare else import_target(profile, args.token)
        return result, 0, not args.distribute
    finally:
        os.close(lock)


if __name__ == '__main__':
    remote_mode = '--prepare' in sys.argv or '--import-images' in sys.argv
    try:
        result, rc, remote_mode = main()
    except (Exception, KeyboardInterrupt) as exc:
        result = REPORT or base_report(None)
        result.update(status='INCOMPLETE', error_code=exc.code if isinstance(exc, Failure) else 'OPERATION_FAILED',
                      failed_phase=PHASE, image_import_may_be_partial=True, automatic_retry=False)
        if STATE is not None:
            try:
                write_new(STATE / 'FAIL.json', encoded(result))
            except Exception:
                pass
        rc = 1
    raw = encoded(result)
    if remote_mode:
        print(MARKER + base64.b64encode(raw).decode('ascii'), flush=True)
    else:
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        print(json.dumps(dict(status=result['status'], report_sha256=sha(raw), report_bytes=len(raw), exitcode=rc)), file=sys.stderr, flush=True)
    raise SystemExit(rc)
