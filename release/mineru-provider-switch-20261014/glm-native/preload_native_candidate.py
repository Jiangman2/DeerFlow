"""One exact verified candidate, one local node, no rollout. Python 3.7+."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import traceback

STATE = Path('/var/lib/weknora-glm-preload-datax01-20261017-r1')
SOURCE = Path('/opt/weknora-glm-verified-ysyi3g70')
REPORT_SHA = 'cb92bb8ebff2b7caabddbc090578d6e427613a6eea1ddd42daa228d1a8892e78'
HELPER = Path('/var/lib/weknora-runtime-reverify-tool-xV0a2uTB/check.py')
HELPER_SHA = '2ec930d1f256c926aafc033af39af7834bd38b3a396239384c90ef7d37cfd4f9'
IMAGE = 'deerflow.local/weknora-app:20261016-native-glm-runtimefix-fc44d25744194ece'
MANIFEST = 'sha256:3bc43f4e4643fd069f59fdcd6a824ed6edb6603c3f2b957ff5b5451b853f4709'
CONFIG = 'sha256:22b73c727cfb445ecf9fabe97f693efd5ceb7cbcb449b593c219e6407aef64c5'
ARCHIVE_SHA = '471ba1fecc997ce0785da151dac47da313d256506ece353275d04cdcff168f51'
ARCHIVE_BYTES = 737000448
COMPRESSED = 736962461
EXPANDED = 2238767616
GIB = 1024**3
SOCKET = '/run/containerd/containerd.sock'
CTR = ['ctr', '--address', SOCKET, '--namespace', 'k8s.io']
CRI = ['crictl', '--runtime-endpoint', 'unix://'+SOCKET,
       '--image-endpoint', 'unix://'+SOCKET, '--timeout', '25s']
PIN = 'io.cri-containerd.pinned=pinned'
PHASE = 'PREFLIGHT'
LAST = {}


class PreloadFailure(RuntimeError):
    pass


def require(value, check):
    if not value: raise PreloadFailure(check)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def phase(value):
    global PHASE
    PHASE = value
    emit({'weknora_candidate_preload': 'RUNNING', 'phase': value, 'node': 'datax-01'})


def load_helper():
    for parent in HELPER.parents:
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'helper_parent_unsafe')
    fd = os.open(str(HELPER), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022 and 0 < s.st_size < 1024**2, 'helper_file_unsafe')
        raw = f.read(1024**2)
    require(hashlib.sha256(raw).hexdigest() == HELPER_SHA, 'verified_helper_changed')
    # Execute exactly the checked bytes, not a second unverified filesystem read.
    spec = importlib.util.spec_from_loader('weknora_verified_recovery', loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(HELPER)
    exec(compile(raw, str(HELPER), 'exec'), module.__dict__)
    return module


def command(argv, timeout=40, limit=4*1024**2, monitor=None):
    LAST.clear(); LAST.update(program=Path(argv[0]).name)
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, start_new_session=True)
    data = {'stdout': bytearray(), 'stderr': bytearray()}
    start = time.monotonic(); beat = start; watch = start
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(p.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(p.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map() or p.poll() is None:
                now = time.monotonic()
                require(now-start < timeout, 'command_timeout_STATUS_ONLY')
                if monitor and now-watch >= 2:
                    monitor(); watch = now
                if now-beat >= 20:
                    emit({'weknora_candidate_preload': 'RUNNING', 'phase': PHASE,
                          'elapsed_seconds': int(now-start), 'raw_output_printed': False})
                    beat = now
                for key, _ in selector.select(.2):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj); continue
                    data[key.data].extend(block)
                    require(len(data[key.data]) <= (limit if key.data == 'stdout' else 65536),
                            'command_output_bound')
            code = p.wait(timeout=2)
        LAST.update(exit_code=code, stdout_bytes=len(data['stdout']), stderr_bytes=len(data['stderr']))
        require(code == 0, 'command_failed_STATUS_ONLY')
        return bytes(data['stdout'])
    finally:
        if p.poll() is None:
            # Stop only this tool's client group, never containerd or a workload.
            os.killpg(p.pid, signal.SIGTERM)
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL); p.wait(timeout=5)
        p.stdout.close(); p.stderr.close()


def cri(*args):
    require(args[0] in ('inspecti', 'imagefsinfo'), 'cri_read_only_required')
    return json.loads(command(CRI+list(args)))


def image_fs_paths(value):
    if 'status' in value:
        require(not any(k in value for k in ('imageFilesystems', 'containerFilesystems')),
                'ambiguous_imagefs_response')
        value = value['status']
    images, containers = value.get('imageFilesystems', []), value.get('containerFilesystems', [])
    require(isinstance(images, list) and 0 < len(images) <= 4 and
            isinstance(containers, list) and len(containers) <= 4, 'imagefs_unreported')
    result = set()
    for row in images+containers:
        name = row.get('fsId', {}).get('mountpoint')
        require(isinstance(name, str) and name.startswith('/') and '..' not in Path(name).parts
                and not any(ord(c) < 32 for c in name), 'unsafe_imagefs_path')
        result.add(Path(name))
    return sorted(result)


def required_bytes(capacity):
    reserve = max(20*GIB, (capacity+4)//5)
    # Full cold import: compressed content + ingest scratch, unpacked snapshots
    # + a full additional unpack workspace; then 2 GiB unrelated-use headroom.
    return reserve, reserve + 2*COMPRESSED + 2*EXPANDED + 2*GIB


def disk_budget(q, paths, initial):
    rows, devices = [], set()
    for path in paths:
        q.directory(path)
        devices.add(path.stat().st_dev)
        v = os.statvfs(str(path))
        capacity, free = v.f_blocks*v.f_frsize, v.f_bavail*v.f_frsize
        reserve, required = required_bytes(capacity)
        threshold = required if initial else reserve+2*GIB
        require(not v.f_flag & os.ST_RDONLY and v.f_favail >= 100000, 'imagefs_unusable')
        require(free >= threshold, 'cold_import_space_insufficient' if initial else 'live_imagefs_reserve_reached')
        rows.append({'path': str(path), 'free_GiB': round(free/GIB, 2),
                     'reserve_GiB': round(reserve/GIB, 2), 'required_GiB': round(threshold/GIB, 2)})
    require(len(devices) == 1 and devices == {Path('/var/lib/containerd').stat().st_dev},
            'split_or_unexpected_imagefs_requires_review')
    return {'imagefs': rows, 'assume_base_layers_cached': False, 'archive_copied_to_rootfs': False}


def source(q):
    raw = q.read(SOURCE/'BUILD-RESULT.json')
    require(hashlib.sha256(raw).hexdigest() == REPORT_SHA, 'candidate_receipt_changed')
    value = json.loads(raw, object_pairs_hook=q.unique)
    c = value.get('component', {})
    expected = dict(image=IMAGE, manifest_digest=MANIFEST, config_digest=CONFIG,
                    archive_sha256=ARCHIVE_SHA, layers_verified=24, compressed_layer_bytes=COMPRESSED,
                    uncompressed_layer_bytes=EXPANDED)
    require(all(c.get(k) == v for k, v in expected.items()), 'candidate_identity_changed')
    require(value.get('weknora_glm_candidate_build') == 'PASS' and value.get('compile_reused') is True
            and value.get('images_imported') is False and value.get('ready_for_rollout') is False
            and value.get('production_workloads_mutated') is False, 'candidate_not_qualified')
    archive = SOURCE/'weknora.candidate.oci.tar'
    require(value['archive'] == str(archive), 'candidate_archive_path_changed')
    with q.opened(archive, ARCHIVE_BYTES) as f:
        require(os.fstat(f.fileno()).st_size == ARCHIVE_BYTES, 'candidate_archive_size_changed')
    require(q.digest(archive) == ARCHIVE_SHA, 'candidate_archive_hash_changed')
    return archive, value


def reference_present():
    refs = command(CTR+['images', 'ls', '--quiet', 'name=='+IMAGE], limit=65536).decode().split()
    require(refs in ([], [IMAGE]), 'unexpected_candidate_reference')
    return bool(refs)


def image_check(require_pin=False):
    listing = command(CTR+['images', 'ls', 'name=='+IMAGE], limit=65536).decode()
    rows = [s.split() for s in listing.splitlines() if s.split() and s.split()[0] == IMAGE]
    require(len(rows) == 1 and len(rows[0]) >= 3 and rows[0][2] == MANIFEST, 'candidate_reference_conflict')
    raw = command(CTR+['content', 'get', MANIFEST], limit=1024**2)
    require('sha256:'+hashlib.sha256(raw).hexdigest() == MANIFEST, 'imported_manifest_hash')
    m = json.loads(raw)
    require(m['config']['digest'] == CONFIG and len(m['layers']) == 24
            and sum(x['size'] for x in m['layers']) == COMPRESSED, 'imported_manifest_shape')
    complete = command(CTR+['images', 'check', '--snapshotter', 'overlayfs', '--quiet', 'name=='+IMAGE],
                       timeout=60, limit=65536).decode().split()
    require(complete == [IMAGE], 'candidate_content_or_unpack_incomplete')
    s = cri('inspecti', IMAGE).get('status', {})
    require(s.get('id') == CONFIG and IMAGE in s.get('repoTags', []), 'cri_candidate_identity_changed')
    require(not require_pin or s.get('pinned') is True, 'candidate_pin_not_confirmed')
    return {'image': IMAGE, 'manifest_digest': MANIFEST, 'config_id': CONFIG,
            'content_and_unpack': 'PASS', 'pinned': s.get('pinned') is True}


def import_args(archive):
    return ['nice', '-n', '10', 'ionice', '-c', '2', '-n', '7'] + CTR + [
        'images', 'import', '--local', '--platform', 'linux/arm64', '--snapshotter', 'overlayfs',
        '--label', PIN, '--label', 'io.cri-containerd.image=managed', str(archive)]


def pin(q):
    current = image_check()
    if not current['pinned']:
        q.record(STATE/'PIN-REQUESTED.json', {'image': IMAGE, 'manifest_digest': MANIFEST})
        command(CTR+['images', 'label', IMAGE, PIN], limit=65536)
        for _ in range(15):
            s = cri('inspecti', IMAGE).get('status', {})
            require(s.get('id') == CONFIG and IMAGE in s.get('repoTags', []), 'pin_identity_changed')
            if s.get('pinned') is True: break
            time.sleep(1)
    return image_check(require_pin=True)


def preflight(q):
    phase('VERIFY_CANDIDATE_AND_FROZEN_HELPERS')
    archive, result = source(q)
    _, guard, plan = q.package()
    require(plan['production_baseline'] == result['production_baseline'], 'baseline_receipts_differ')
    phase('CURRENT_WEKNORA_BASELINE_AND_IMPORT_RESOURCES')
    guard.live(plan, binary=True)
    require(guard.available_memory() >= 4*GIB, 'host_memory_reserve')
    for name in ('ctr', 'crictl', 'nice', 'ionice', 'kubectl'):
        require(shutil.which(name) is not None, 'missing_tool:'+name)
    require(stat.S_ISSOCK(Path(SOCKET).lstat().st_mode), 'containerd_socket_missing')
    node = json.loads(guard.audit.kube(['get', 'node', 'datax-01', '-o', 'json']))
    info = node['status']['nodeInfo']
    require(info['architecture'] == 'arm64' and info['operatingSystem'] == 'linux'
            and info['containerRuntimeVersion'].startswith('containerd://'), 'node_runtime_changed')
    paths = image_fs_paths(cri('imagefsinfo', '--output', 'json'))
    present = reference_present()
    if present: image_check()
    budget = disk_budget(q, paths, initial=not present)
    q.processes_clear()
    return {'archive': archive, 'guard': guard, 'plan': plan, 'paths': paths,
            'present': present, 'budget': budget, 'node_uid': node['metadata']['uid']}


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def import_once(q):
    require(not os.path.lexists(str(STATE)), 'previous_operation_exists_USE_STATUS')
    q.directory(STATE.parent)
    with q.lock():
        p = preflight(q)
        # No state/engine mutation before all read-only checks above pass.
        STATE.mkdir(mode=0o700)
        q.record(STATE/'STARTED.json', {'utc': stamp(), 'node': 'datax-01', 'node_uid': p['node_uid'],
            'candidate_image': IMAGE, 'candidate_manifest': MANIFEST, 'source_report_sha256': REPORT_SHA,
            'already_present_and_complete': p['present'], 'budget': p['budget'], 'automatic_retry': False})
        requested = False
        try:
            if not p['present']:
                phase('IMPORT_LOCAL_CANDIDATE_ONLY')
                disk_budget(q, p['paths'], initial=True)
                p['guard'].live(p['plan'], binary=True)
                q.processes_clear()
                require(not reference_present(), 'candidate_reference_appeared_before_import')
                # Durable boundary before any containerd write; never auto retry.
                q.record(STATE/'IMPORT-REQUESTED.json', {'utc': stamp(), 'image': IMAGE,
                    'archive_sha256': ARCHIVE_SHA, 'automatic_retry': False})
                requested = True
                command(import_args(p['archive']), timeout=1200, limit=1024**2,
                        monitor=lambda: disk_budget(q, p['paths'], initial=False))
                q.record(STATE/'IMPORT-ACK.json', {'utc': stamp(), 'client_exit': 0})
            phase('VERIFY_CONTENT_UNPACK_CRI_AND_PIN')
            accepted = pin(q)
            phase('POST_IMPORT_BASELINE_RECHECK')
            p['guard'].live(p['plan'], binary=True)
            disk_budget(q, p['paths'], initial=False)
            summary = {'weknora_candidate_preload': 'PASS', 'node': 'datax-01', 'candidate': accepted,
                       'import_requested': requested, 'reused_complete_image': p['present']}
        except BaseException as exc:
            summary = {'weknora_candidate_preload': 'UNCONFIRMED', 'phase': PHASE,
                'error_type': type(exc).__name__, 'failed_check': safe_detail(exc),
                'last_operation': dict(LAST), 'import_requested': requested,
                'image_import_may_be_partial': requested,
                'code_frames': [{'file': Path(x.filename).name, 'function': x.name, 'line': x.lineno}
                                for x in traceback.extract_tb(exc.__traceback__)[-6:]]}
            summary.update(common()); q.record(STATE/'RESULT.json', summary); emit(summary); raise
        summary.update(common()); q.record(STATE/'RESULT.json', summary); emit(summary)


def safe_detail(exc):
    message = str(exc)
    return message if type(exc).__name__ in ('PreloadFailure', 'ReverifyFailure', 'CheckFailure') \
        and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', message) else 'SAFE_DIAGNOSTIC_REQUIRED'


def common():
    return {'production_workloads_mutated': False, 'production_configuration_changed': False,
            'provider_requests': 0, 'pods_created': 0, 'model_changed': False,
            'consumer_slots_changed': False, 'automatic_retry': False,
            'ready_for_rollout': False, 'rollout': 'NOT_PERFORMED',
            'native_intranet_acceptance': 'REQUIRED', 'state': str(STATE), 'utc': stamp(),
            'instruction': 'USE_STATUS_ON_UNCERTAINTY_DO_NOT_REPEAT_IMPORT'}


def status(q):
    records = {}
    if STATE.exists():
        q.directory(STATE)
        for name in ('STARTED.json', 'IMPORT-REQUESTED.json', 'IMPORT-ACK.json', 'PIN-REQUESTED.json', 'RESULT.json'):
            if (STATE/name).exists(): records[name] = q.document(STATE/name)
    try:
        candidate = image_check() if reference_present() else {'present': False}
    except Exception as exc:
        candidate = {'status': 'NOT_VERIFIED', 'failed_check': safe_detail(exc)}
    emit({'weknora_preload_status': 'READ_ONLY', 'candidate': candidate, 'records': records,
          'files_modified': False, 'images_imported_by_status': False, 'production_writes': 0,
          'provider_requests': 0, 'state': str(STATE)})


def interrupt(signum, frame):
    raise PreloadFailure('interrupted_USE_STATUS_NO_REPEAT')


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--import-once', action='store_true')
    group.add_argument('--status', action='store_true')
    group.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01'
            and platform.machine() == 'aarch64', 'root_arm64_datax01_required')
    os.umask(0o077)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(sig, interrupt)
    q = load_helper()
    if args.status: status(q)
    elif args.preflight:
        with q.lock(): p = preflight(q)
        emit({'preload_preflight': 'PASS', 'budget': p['budget'], 'production_writes': 0,
              'images_imported': False, 'provider_requests': 0, 'ready_for_rollout': False})
    else: import_once(q)


if __name__ == '__main__':
    try: main()
    except BaseException as exc:
        if isinstance(exc, SystemExit): raise
        emit(dict(common(), weknora_candidate_preload='UNCONFIRMED', phase=PHASE,
                  error_type=type(exc).__name__, failed_check=safe_detail(exc),
                  raw_error_printed=False))
        sys.exit(1)
