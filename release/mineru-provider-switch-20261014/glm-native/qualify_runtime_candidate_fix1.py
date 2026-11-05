"""Reverify one existing OCI; preserve /app ownership from the pinned base.

No build, image execution/import, model request or production mutation. Py3.7+.
The old package and failed outputs remain immutable; success has a fresh receipt.
"""
import argparse
import gzip
import hashlib
import importlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

ROOT = Path('/opt/weknora-glm-runtime-fix-8meva2vp/rebuild')
STATE = Path('/var/lib/weknora-glm-runtime-reverify-20261016-r1')
PACKAGE_SHA = '75c3442c222321f095e01cc5b3fcb75c187f9d486216f4d3f85ceb219852914f'
VERIFIER_SHA = '4ae459fff4f58f9094985fb0924497771f798fe4b565c5a0fcd0d042a6a26b29'
PHASE = 'READ_ONLY_PREFLIGHT'
START = time.monotonic()
GIB = 1024**3
APP = {'uid': 1000, 'gid': 1000, 'mode': 0o755, 'size': 0}
BINARIES = {'app/WeKnora', 'usr/local/bin/weknora-anydoc-probe', 'usr/local/bin/weknora-glm-probe'}
DICT = 'usr/local/share/weknora/jieba/'
DICT_NAMES = {'jieba.dict.utf8', 'hmm_model.utf8', 'user.dict.utf8', 'idf.utf8', 'stop_words.utf8'}


class ReverifyFailure(RuntimeError):
    pass


def require(ok, check):
    if not ok: raise ReverifyFailure(check)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def phase(name):
    global PHASE
    PHASE = name
    emit({'runtime_reverify': 'RUNNING', 'phase': name, 'build_started': False})


def directory(path):
    for p in (path,) + tuple(path.parents):
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_private_directory')


def opened(path, maximum, minimum=1):
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    f = os.fdopen(fd, 'rb'); s = os.fstat(f.fileno())
    if not (stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
            and not s.st_mode & 0o022 and minimum <= s.st_size <= maximum):
        f.close(); raise ReverifyFailure('unsafe_private_file:' + path.name)
    return f


def identity(s):
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def read(path, maximum=4*1024**2):
    with opened(path, maximum) as f:
        before = identity(os.fstat(f.fileno())); raw = f.read(maximum+1)
        require(identity(os.fstat(f.fileno())) == before and len(raw) == before[2], 'read_changed')
    return raw


def unique(pairs):
    value = {}
    for k, v in pairs:
        require(k not in value, 'duplicate_json_key'); value[k] = v
    return value


def document(path):
    return json.loads(read(path), object_pairs_hook=unique)


def digest(path):
    h, count, last = hashlib.sha256(), 0, time.monotonic()
    with opened(path, 4*GIB) as f:
        before = identity(os.fstat(f.fileno()))
        for b in iter(lambda: f.read(1024**2), b''):
            h.update(b); count += len(b)
            require(time.monotonic()-START < 1800, 'reverify_deadline')
            if time.monotonic()-last >= 10:
                emit({'runtime_reverify': 'HASH_RUNNING', 'file': path.name, 'bytes_read': count})
                last = time.monotonic()
        require(identity(os.fstat(f.fileno())) == before and count == before[2], 'hash_input_changed')
    return h.hexdigest()


def record(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def previous():
    require(not os.path.lexists(str(ROOT/'build1/BUILD-RESULT.json')) and
            not os.path.lexists(str(ROOT/'build1/weknora.candidate.oci.tar')), 'old_success_exists_USE_STATUS')
    raw = read(ROOT/'build1/BUILD-FAILED.json')
    value = json.loads(raw, object_pairs_hook=unique)
    require(value.get('stage') == 'verify-runtime-candidate' and value.get('images_imported') is False
            and value.get('production_writes') == 0, 'previous_failure_boundary')
    log = read(ROOT/'build1/06-verify-runtime-candidate.log', 8*1024**2)
    require(b'unsafe_runtime_delta' in log and b'"total_layers": 24' in log, 'previous_delta_failure_missing')
    return {'failed_result_sha256': hashlib.sha256(raw).hexdigest(),
            'failed_log_sha256': hashlib.sha256(log).hexdigest()}


def package():
    phase('VERIFY_PINNED_PACKAGE')
    root = ROOT/'package'; raw = read(root/'PACKAGE.SHA256', 65536)
    require(hashlib.sha256(raw).hexdigest() == PACKAGE_SHA, 'package_manifest_changed')
    names = {}
    for line in raw.decode('ascii').splitlines():
        m = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9][A-Za-z0-9_.+-]+)', line)
        require(m is not None and m[2] not in names and m[2] != 'PACKAGE.SHA256', 'manifest_format')
        names[m[2]] = m[1]
    require(names.get('verify_runtime_candidate.py') == VERIFIER_SHA and
            {p.name for p in root.iterdir()} == set(names) | {'PACKAGE.SHA256'}, 'package_members_changed')
    for name, expected in sorted(names.items()):
        require(digest(root/name) == expected, 'package_digest:' + name)
    # No code is imported until the complete private package has been verified.
    sys.path.insert(0, str(root))
    old = importlib.import_module('verify_runtime_candidate')
    guard = importlib.import_module('build_guard')
    require(Path(old.__file__).resolve() == root/'verify_runtime_candidate.py' and
            Path(guard.__file__).resolve() == root/'build_guard.py', 'helper_import_location')
    return old, guard, document(root/'BUILD-PLAN.json')


def metadata(m):
    return {'uid': m.uid, 'gid': m.gid, 'mode': m.mode, 'size': m.size}


def attributes(m):
    # Times are not permission changes. All other PAX attributes must match the base.
    return {k: v for k, v in m.pax_headers.items() if k not in ('mtime', 'atime', 'ctime', 'path')}


class Bounded(io.RawIOBase):
    def __init__(self, source):
        self.source, self.count = source, 0
    def readable(self): return True
    def read(self, size=-1):
        require(size >= 0, 'unbounded_layer_read')
        data = self.source.read(size); self.count += len(data)
        require(self.count <= 4*GIB and time.monotonic()-START < 1800, 'layer_read_budget')
        return data


def layer_members(blob, layer):
    kind = layer['mediaType']
    require(kind in ('application/vnd.oci.image.layer.v1.tar+gzip', 'application/vnd.oci.image.layer.v1.tar',
                    'application/vnd.docker.image.rootfs.diff.tar.gzip'), 'unsupported_layer_encoding')
    with opened(blob, 4*GIB) as raw:
        stream = gzip.GzipFile(fileobj=raw) if kind.endswith('gzip') else raw
        try:
            with tarfile.open(fileobj=Bounded(stream), mode='r|') as tar:
                for i, m in enumerate(tar):
                    require(i < 100000, 'layer_header_budget')
                    name = m.name[2:] if m.name.startswith('./') else m.name
                    name = name.rstrip('/')
                    require(not name.startswith('/') and '..' not in name.split('/') and '\\' not in name,
                            'layer_path_invalid')
                    yield name, m, tar
        finally:
            if stream is not raw: stream.close()


def base_app(layout, layers):
    # Resolve the last effective /app header from the hash-verified base prefix.
    for i in range(len(layers)-1, -1, -1):
        row = None
        layer = layers[i]
        for name, m, _ in layer_members(layout/'blobs/sha256'/layer['digest'][7:], layer):
            require(name not in ('.wh.app', '.wh..wh..opq'), 'base_app_whiteout_requires_review')
            if name == 'app':
                require(row is None and m.isdir() and not m.issparse() and not m.linkname,
                        'base_app_not_single_directory')
                row = {'metadata': metadata(m), 'attributes': attributes(m)}
        if row is not None:
            require(row['metadata'] == APP, 'pinned_base_app_metadata_changed')
            emit({'base_app_metadata': APP, 'base_layer': i+1, 'base_parent_binding': 'PASS'})
            return row
    raise ReverifyFailure('base_app_directory_missing')


def delta(blob, layer, expected, seen, inherited):
    parents = {'.'} | {str(p) for n in expected for p in PurePosixPath(n).parents}
    local = set()
    for name, m, tar in layer_members(blob, layer):
        require(name not in local and len(local) < 40 and not m.issparse() and not m.linkname
                and not m.mode & 0o6022, 'unsafe_runtime_delta')
        local.add(name)
        if m.isdir():
            require(name in parents and m.mode == 0o755 and m.size == 0, 'unexpected_runtime_directory')
            if name == 'app':
                require(inherited['metadata'] == APP and metadata(m) == inherited['metadata']
                        and attributes(m) == inherited['attributes'], 'app_directory_differs_from_pinned_base')
            else:
                require(m.uid == m.gid == 0, 'nonroot_new_runtime_directory')
            continue
        require(m.uid == m.gid == 0 and m.isfile() and name in expected and name not in seen,
                'unexpected_runtime_file_or_owner')
        wanted = expected[name]
        require(m.mode == wanted['mode'] and 0 < m.size <= GIB, 'runtime_file_mode_or_size')
        h = hashlib.sha256()
        with tar.extractfile(m) as f:
            for b in iter(lambda: f.read(1024**2), b''): h.update(b)
        require(h.hexdigest() == wanted['sha256'], 'runtime_file_hash')
        seen.add(name)


def copy_sealed(source, output, expected):
    partial, final = output/'weknora.candidate.oci.tar.partial', output/'weknora.candidate.oci.tar'
    require(not os.path.lexists(str(partial)) and not os.path.lexists(str(final)), 'fresh_seal_required')
    h, count = hashlib.sha256(), 0
    with opened(source, 3*GIB) as src, partial.open('xb') as dst:
        before = identity(os.fstat(src.fileno()))
        for b in iter(lambda: src.read(1024**2), b''):
            h.update(b); dst.write(b); count += len(b)
        dst.flush(); os.fsync(dst.fileno()); os.fchmod(dst.fileno(), 0o600)
        require(identity(os.fstat(src.fileno())) == before and count == before[2]
                and h.hexdigest() == expected, 'source_archive_changed_during_seal')
    require(digest(partial) == expected, 'sealed_copy_hash')
    partial.rename(final)  # Only the new copy; never rename or modify old evidence.
    return final


def proof_safety(root):
    directory(root)
    count, total = 0, 0
    for parent, dirs, files in os.walk(str(root), followlinks=False):
        for name in dirs:
            directory(Path(parent)/name)
        for name in files:
            count += 1
            require(count <= 100, 'qualification_file_count')
            # Successful commands legitimately leave empty stderr receipts.
            with opened(Path(parent)/name, 64*1024**2, minimum=0) as f:
                total += os.fstat(f.fileno()).st_size
            require(total <= 128*1024**2, 'qualification_bytes')


def qualify(output, old, guard, plan):
    require(plan.get('scope') == 'WEKNORA_NATIVE_GLM_BUILD_ONLY' and
            plan['context_sha256'] == 'fc44d25744194ece953249237305856c1c4e7e13fbed739dadc35fb9bb76ca10',
            'exact_runtime_plan_required')
    phase('LIVE_BASELINE_AND_RESOURCE_CHECK')
    guard.live(plan, binary=True)
    space = guard.SpaceGuard(output); space.check()
    require(guard.available_memory() >= 4*GIB, 'memory_reserve')
    archive = ROOT/'build1/weknora.candidate.oci.tar.partial'
    archive_id = identity(archive.lstat())
    phase('RECHECK_COMPILE_AND_RUNTIME_RECEIPTS')
    proof = ROOT/'build1/qualification'
    proof_safety(proof)
    binaries, suites = old.old.evidence(proof)
    runtime = plan['runtime_finish']
    require(set(binaries) == BINARIES and binaries == runtime['binaries'], 'binary_receipt_changed')
    require(set(runtime['dictionaries']) == DICT_NAMES, 'dictionary_set_changed')
    require(digest(proof/'source.sha256') == runtime['source_manifest_sha256'], 'source_receipt_changed')
    expected = {n: {'sha256': h, 'mode': 0o755} for n, h in binaries.items()}
    expected.update({DICT+n: {'sha256': h, 'mode': 0o644} for n, h in runtime['dictionaries'].items()})
    image = 'deerflow.local/weknora-app:20261016-native-glm-runtimefix-' + plan['context_sha256'][:16]
    phase('VERIFY_EXISTING_OCI_AND_CONFIGURATION')
    layout = old.extract(archive, output/'candidate-check')
    result = old.verify(layout, {'source_image': image,
                        'source_image_id': document(ROOT/'build1/build-metadata.json')['containerimage.digest']})
    base = ROOT/'build1/runtime-base'
    base_digest = plan['runtime_base']['manifest_digest']
    require(document(ROOT/'build1/runtime-base-receipt.json')['manifest_digest'] == base_digest,
            'base_receipt_changed')
    base_raw = read(base/'blobs/sha256'/base_digest[7:])
    require('sha256:'+hashlib.sha256(base_raw).hexdigest() == base_digest, 'base_manifest_changed')
    before = json.loads(base_raw, object_pairs_hook=unique)
    config = before['config']
    require(config['digest'] == plan['runtime_base']['config_digest'] and
            'sha256:'+digest(base/'blobs/sha256'/config['digest'][7:]) == config['digest'], 'base_config_changed')
    after = document(layout/'blobs/sha256'/result['manifest_digest'][7:])
    require(len(before['layers']) == 20 and len(after['layers']) == 24
            and after['layers'][:20] == before['layers'], 'base_prefix_or_layers_changed')
    old.preserved_with_dictionary(old.configuration(base, base_digest), old.configuration(layout, result['manifest_digest']))
    phase('REHASH_ALL_24_LAYERS_AND_DIFFIDS')
    checked = old.verify_archive(archive, emit, result['manifest_digest'], result['config_digest'],
                                 24, result['compressed_layer_bytes'])
    phase('VERIFY_EXACT_INHERITED_APP_DIRECTORY_AND_EIGHT_FILES')
    inherited = base_app(layout, before['layers'])
    seen = set()
    for layer in after['layers'][20:]:
        delta(layout/'blobs/sha256'/layer['digest'][7:], layer, expected, seen, inherited)
    require(seen == set(expected), 'runtime_delta_incomplete')
    require(identity(archive.lstat()) == archive_id, 'archive_changed_during_qualification')
    archive_sha = digest(archive)
    phase('FINAL_LIVE_BASELINE_RECHECK')
    guard.live(plan, binary=True); space.check()
    require(guard.available_memory() >= 4*GIB, 'memory_reserve')
    result.update(image=image, archive_sha256=archive_sha, base_layer_prefix='PRESERVED',
        inherited_app_metadata='MATCH_PINNED_BASE_1000_1000_0755', runtime_files=expected,
        runtime_config='ONLY_JIEBA_DICT_DIR_ADDED_OR_EMPTY_VALUE_REPLACED', test_suites=suites,
        reused_binary_hashes='PASS', uncompressed_diffids='PASS',
        uncompressed_layer_bytes=checked['uncompressed_layer_bytes'], provider_requests=0,
        native_intranet_acceptance='NOT_TESTED', ready_for_rollout=False)
    phase('SEAL_VERIFIED_COPY_WITHOUT_MODIFYING_ORIGINAL')
    final = copy_sealed(archive, output, archive_sha)
    return {'weknora_glm_candidate_build': 'PASS', 'component': result, 'archive': str(final),
        'production_baseline': plan['production_baseline'], 'compile_reused': True,
        'existing_oci_reverified': True, 'original_evidence_modified': False,
        'production_workloads_mutated': False, 'images_imported': False, 'provider_requests': 0,
        'model_configuration_changed': False, 'consumer_slots_changed': False,
        'database_migrations': [], 'ready_for_rollout': False,
        'native_intranet_acceptance': 'REQUIRED', 'enrichment_acceptance': 'REQUIRED'}


def processes_clear():
    p = subprocess.run(['ps', '-eo', 'pid=,comm=,args='], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=True, timeout=15)
    require(len(p.stdout) < 8*1024**2, 'process_inventory_bound')
    for line in p.stdout.decode('utf-8', 'replace').splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3 or int(fields[0]) == os.getpid(): continue
        _, comm, args = fields
        require(comm not in ('buildctl', 'nerdctl') and not (comm == 'ctr' and
                re.search(r'\bimages\s+(import|export)\b', args)), 'other_image_operation_active')
        require(not (comm == 'buildkitd' and str(ROOT) in args), 'old_builder_still_active')
        require(not (comm.startswith('python') and str(ROOT) in args and
                     any(x in args for x in ('finish_runtime_build.py', 'verify_runtime_candidate.py'))),
                'old_candidate_operation_active')


def lock():
    import fcntl
    path = Path('/run/lock/deerflow-ppt-relay-build.lock')
    for parent in path.parents:
        s = parent.lstat(); sticky = parent == Path('/run/lock') and s.st_mode & stat.S_ISVTX
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and (not s.st_mode & 0o022 or sticky), 'unsafe_lock_parent')
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    handle = os.fdopen(fd, 'r+')
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and not s.st_mode & 0o022,
                'unsafe_build_lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BaseException:
        handle.close(); raise


def status():
    value = {'runtime_reverify_status': 'READ_ONLY', 'state': str(STATE), 'files_modified': False,
             'production_writes': 0, 'provider_requests': 0}
    if STATE.exists():
        directory(STATE)
        value['records'] = {n: document(STATE/n) for n in ('STARTED.json', 'RESULT.json') if (STATE/n).exists()}
    else: value['state_exists'] = False
    emit(value)


def run():
    require(not os.path.lexists(str(STATE)), 'previous_reverification_exists_USE_STATUS')
    directory(STATE.parent); directory(Path('/opt'))
    evidence = previous(); processes_clear()
    with lock():
        # Bound this read/copy-only process; no BuildKit or container is started.
        resource.setrlimit(resource.RLIMIT_AS, (2*GIB, 2*GIB))
        resource.setrlimit(resource.RLIMIT_CPU, (1200, 1200))
        os.nice(10)
        STATE.mkdir(mode=0o700)
        output = Path(tempfile.mkdtemp(prefix='weknora-glm-verified-', dir='/opt'))
        record(STATE/'STARTED.json', {'output': str(output), 'previous': evidence,
                                    'automatic_retry': False, 'production_writes': 0})
        try:
            old, guard, plan = package()
            result = qualify(output, old, guard, plan)
            require(previous() == evidence, 'original_failure_evidence_changed')
            record(output/'BUILD-RESULT.json', result)
            summary = {'runtime_candidate_reverification': 'PASS', 'build_result': str(output/'BUILD-RESULT.json'),
                       'report_sha256': digest(output/'BUILD-RESULT.json'), 'archive': result['archive'],
                       'image': result['component']['image'], 'build_started': False, 'compile_reused': True}
        except BaseException as exc:
            detail = str(exc)
            summary = {'runtime_candidate_reverification': 'UNCONFIRMED', 'phase': PHASE,
                       'error_type': type(exc).__name__, 'failed_check': detail if
                       (isinstance(exc, ReverifyFailure) or type(exc).__name__ == 'CheckFailure')
                       and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', detail) else 'SAFE_DIAGNOSTIC_REQUIRED',
                       'code_frames': [{'file': Path(x.filename).name, 'function': x.name, 'line': x.lineno}
                                       for x in traceback.extract_tb(exc.__traceback__)[-8:]]}
            summary.update(production_writes=0, provider_requests=0, images_imported=False,
                           ready_for_rollout=False, automatic_retry=False)
            record(STATE/'RESULT.json', summary); emit(summary); raise
        summary.update(production_writes=0, provider_requests=0, images_imported=False,
                       ready_for_rollout=False, automatic_retry=False, original_evidence_modified=False)
        record(STATE/'RESULT.json', summary); emit(summary)


def main():
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--status', action='store_true'); mode.add_argument('--verify-once', action='store_true')
    a = p.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
            'root_datax01_required')
    os.umask(0o077)
    status() if a.status else run()


if __name__ == '__main__':
    try: main()
    except BaseException as exc:
        if isinstance(exc, SystemExit): raise
        emit({'runtime_candidate_reverification': 'STOPPED', 'error_type': type(exc).__name__,
              'failed_check': str(exc) if isinstance(exc, ReverifyFailure) else 'USE_STATUS_FOR_SAFE_DETAIL',
              'instruction': 'USE_STATUS_DO_NOT_REPEAT', 'production_writes': 0, 'provider_requests': 0})
        sys.exit(1)
