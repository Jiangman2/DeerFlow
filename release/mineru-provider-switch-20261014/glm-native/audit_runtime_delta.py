"""Read only the exact failed candidate's OCI layer metadata. Python 3.7+."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import tarfile
import time

ROOT = Path('/opt/weknora-glm-runtime-fix-8meva2vp/rebuild')
PACKAGE_SHA = '75c3442c222321f095e01cc5b3fcb75c187f9d486216f4d3f85ceb219852914f'
VERIFIER_SHA = '4ae459fff4f58f9094985fb0924497771f798fe4b565c5a0fcd0d042a6a26b29'
START = time.monotonic()
LIMIT = 4*1024**3
SAFE_NAMES = {'app/WeKnora', 'usr/local/bin/weknora-anydoc-probe', 'usr/local/bin/weknora-glm-probe'}
SAFE_NAMES.update('usr/local/share/weknora/jieba/'+n for n in
                  ('jieba.dict.utf8', 'hmm_model.utf8', 'user.dict.utf8', 'idf.utf8', 'stop_words.utf8'))
PARENTS = {'.'} | {str(p) for n in SAFE_NAMES for p in PurePosixPath(n).parents}


def require(ok, check):
    if not ok:
        raise RuntimeError(check)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def opened(path, maximum):
    for p in path.parents:
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_private_ancestor')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    f = os.fdopen(fd, 'rb')
    s = os.fstat(f.fileno())
    if not (stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
            and not s.st_mode & 0o022 and 0 < s.st_size <= maximum):
        f.close()
        raise RuntimeError('unsafe_private_file')
    return f


def read(path, maximum=4*1024**2):
    with opened(path, maximum) as f:
        raw = f.read(maximum+1)
    require(len(raw) <= maximum, 'read_bound')
    return raw


def unique(pairs):
    value = {}
    for k, v in pairs:
        require(k not in value, 'duplicate_json_key')
        value[k] = v
    return value


def doc(path):
    return json.loads(read(path), object_pairs_hook=unique)


def blob(layout, desc, metadata=False):
    digest, size = desc.get('digest', ''), desc.get('size')
    require(re.fullmatch('sha256:[a-f0-9]{64}', digest) and type(size) is int
            and 0 < size < LIMIT and not desc.get('urls') and 'data' not in desc, 'invalid_descriptor')
    path = layout/'blobs/sha256'/digest[7:]
    h, count, pieces = hashlib.sha256(), 0, []
    with opened(path, 4*1024**2 if metadata else LIMIT) as f:
        for b in iter(lambda: f.read(1024**2), b''):
            count += len(b); h.update(b)
            require(count <= size and time.monotonic()-START < 300, 'blob_read_bound')
            if metadata: pieces.append(b)
    require(count == size and h.hexdigest() == digest[7:], 'blob_digest_or_size_changed')
    return json.loads(b''.join(pieces), object_pairs_hook=unique) if metadata else path


def metadata(m, local):
    name = m.name[2:] if m.name.startswith('./') else m.name
    name = name.rstrip('/')
    failures = []
    if name in local: failures.append('duplicate_path_in_layer')
    if len(local) >= 40: failures.append('layer_member_count')
    if m.issparse(): failures.append('sparse_member')
    if m.linkname: failures.append('link_target_present')
    if m.uid != 0 or m.gid != 0: failures.append('nonroot_owner_or_group')
    if m.mode & 0o6022: failures.append('mode_has_setid_or_group_world_write')
    if m.isdir():
        if name not in PARENTS or m.mode != 0o755: failures.append('unexpected_runtime_directory')
    elif not m.isfile() or name not in SAFE_NAMES:
        failures.append('unexpected_runtime_file')
    value = {'name': name if name in SAFE_NAMES | PARENTS else 'UNEXPECTED_PATH_REDACTED',
             'kind': 'directory' if m.isdir() else 'file' if m.isfile() else 'link_or_special',
             'uid': m.uid, 'gid': m.gid, 'mode': oct(m.mode), 'bytes': m.size,
             'link_target_present': bool(m.linkname), 'sparse': bool(m.issparse()), 'failed_conditions': failures}
    if value['name'] == 'UNEXPECTED_PATH_REDACTED':
        value['name_sha256'] = hashlib.sha256(name.encode('utf-8', 'replace')).hexdigest()
    return name, value


class Bounded(io.RawIOBase):
    def __init__(self, source):
        self.source, self.count = source, 0
    def readable(self):
        return True
    def read(self, size=-1):
        require(size >= 0, 'unbounded_layer_read')
        data = self.source.read(size)
        self.count += len(data)
        require(self.count <= LIMIT and time.monotonic()-START < 300, 'layer_read_bound')
        return data


def members(layout, desc):
    path = blob(layout, desc)
    kind = desc.get('mediaType')
    require(kind in ('application/vnd.oci.image.layer.v1.tar+gzip',
                    'application/vnd.oci.image.layer.v1.tar',
                    'application/vnd.docker.image.rootfs.diff.tar.gzip'), 'layer_encoding')
    with opened(path, LIMIT) as f:
        stream = gzip.GzipFile(fileobj=f) if kind.endswith('gzip') else f
        try:
            with tarfile.open(fileobj=Bounded(stream), mode='r|') as tar:
                for i, m in enumerate(tar):
                    require(i < 100000, 'layer_header_count')
                    yield m
        finally:
            if stream is not f: stream.close()


def audit():
    manifest_raw = read(ROOT/'package/PACKAGE.SHA256', 65536)
    require(hashlib.sha256(manifest_raw).hexdigest() == PACKAGE_SHA, 'package_manifest_changed')
    require(hashlib.sha256(read(ROOT/'package/verify_runtime_candidate.py')).hexdigest() == VERIFIER_SHA,
            'failed_verifier_changed')
    result = doc(ROOT/'build1/BUILD-FAILED.json')
    require(result.get('stage') == 'verify-runtime-candidate' and result.get('images_imported') is False
            and result.get('production_writes') == 0, 'failed_build_boundary_changed')
    require(not os.path.lexists(str(ROOT/'build1/BUILD-RESULT.json')), 'successful_build_exists')
    layout = ROOT/'build1/candidate-check'
    rows = doc(layout/'index.json')['manifests']
    require(len(rows) == 1, 'one_candidate_required')
    selected = rows[0]
    require(selected['digest'] == doc(ROOT/'build1/build-metadata.json')['containerimage.digest'],
            'candidate_manifest_changed')
    after = blob(layout, selected, True)
    require(len(after.get('layers', [])) == 24, 'expected_24_layers')
    base = ROOT/'build1/runtime-base'
    base_id = doc(ROOT/'build1/runtime-base-receipt.json')['manifest_digest']
    require(re.fullmatch('sha256:[a-f0-9]{64}', base_id), 'base_digest_format')
    base_raw = read(base/'blobs/sha256'/base_id[7:])
    require('sha256:'+hashlib.sha256(base_raw).hexdigest() == base_id, 'base_manifest_changed')
    before = json.loads(base_raw, object_pairs_hook=unique)
    require(len(before.get('layers', [])) == 20 and after['layers'][:20] == before['layers'],
            'base_layer_prefix_changed')
    suspect_dirs, unexpected = set(), 0
    for number, desc in enumerate(after['layers'][20:], 21):
        emit({'delta_audit': 'READ_LAYER_HEADERS', 'layer': number})
        local = set()
        for m in members(layout, desc):
            require(len(local) < 100, 'delta_header_limit')
            name, value = metadata(m, local)
            local.add(name)
            value['layer'] = number
            emit(value)
            if value['failed_conditions']:
                unexpected += 1
                if name in PARENTS: suspect_dirs.add(name)
    # Locate the effective base header of only suspect allowlisted parent paths.
    # This is diagnosis, not acceptance of an inherited unsafe permission.
    needed = set(suspect_dirs)
    for number in range(19, -1, -1):
        if not needed: break
        emit({'delta_audit': 'COMPARE_BASE_PARENT_HEADERS', 'layer': number+1})
        found = {}
        for m in members(base, before['layers'][number]):
            name = m.name[2:] if m.name.startswith('./') else m.name
            name = name.rstrip('/')
            if name in needed: found[name] = metadata(m, set())[1]
            if '/.wh.' in name or name.startswith('.wh.'):
                require(not any(name == str(PurePosixPath(n).parent/('.wh.'+PurePosixPath(n).name))
                                or name.endswith('.wh..wh..opq') and n.startswith(name.rsplit('/', 1)[0]+'/')
                                for n in needed), 'base_parent_whiteout_requires_review')
        for name, value in found.items():
            emit({'base_parent_metadata': value, 'layer': number+1})
        needed -= set(found)
    emit({'delta_audit': 'COMPLETE', 'members_with_failed_conditions': unexpected,
          'base_parent_not_found': sorted(needed), 'files_modified': False, 'image_code_executed': False,
          'images_imported': False, 'production_writes': 0, 'provider_requests': 0,
          'ready_for_rollout': False})


if __name__ == '__main__':
    try:
        require(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
                'root_datax01_required')
        os.umask(0o077)
        os.nice(10)
        audit()
    except Exception as exc:
        emit({'delta_audit': 'INCOMPLETE', 'error_type': type(exc).__name__,
              'check': str(exc) if type(exc) is RuntimeError else 'SAFE_DIAGNOSTIC_REQUIRED',
              'files_modified': False, 'production_writes': 0, 'provider_requests': 0})
        raise SystemExit(1)
