"""Prepare a fresh, hash-bound offline build package. Python 3.7+; no rollout.

Never edits original evidence, host packages, images, Kubernetes or databases.
The separately reviewed starter invokes the unchanged isolated build driver.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import sys
import tarfile

HERE = Path(__file__).resolve().parent
OLD = Path('/opt/weknora-glm-build-xSwsO33h/package')
STATE = Path('/var/lib/weknora-glm-sqlite-fix-20261016-r1')
GIB = 1024**3


class CheckFailure(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise CheckFailure(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024**2), b''):
            h.update(b)
    return h.hexdigest()


def private(path, directory=False):
    for parent in path.parents:
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_private_ancestor')
    s = path.lstat()
    require((stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)) and
            s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_private_path')
    if not directory:
        require(s.st_nlink == 1 and 0 < s.st_size < 4*GIB, 'unsafe_file_size_or_links')


def write_new(path, raw):
    with path.open('xb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    path.chmod(0o600)


def record(path, value):
    write_new(path, (json.dumps(value, sort_keys=True, indent=2)+'\n').encode())


def manifest(raw):
    rows = {}
    for line in raw.decode('ascii').splitlines():
        m = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9][A-Za-z0-9_.+-]+)', line)
        require(m is not None, 'manifest_format')
        checksum, name = m.groups()
        require(name not in rows and name != 'PACKAGE.SHA256', 'manifest_duplicate')
        rows[name] = checksum
    require(rows, 'manifest_empty')
    return rows


def validate_package(root, pinned=None):
    private(root, True)
    p = root/'PACKAGE.SHA256'
    private(p)
    require(p.stat().st_size < 65536, 'manifest_size')
    if pinned:
        require(digest(p) == pinned, 'original_manifest_changed')
    rows = manifest(p.read_bytes())
    require({p.name for p in root.iterdir()} == set(rows) | {'PACKAGE.SHA256'}, 'package_members_changed')
    for name, expected in sorted(rows.items()):
        p = root/name
        private(p)
        require(digest(p) == expected, 'package_hash:'+name)
    return rows


def add(t, name, raw=None, source=None):
    m = tarfile.TarInfo(name)
    m.mode, m.uid, m.gid, m.mtime = 0o600, 0, 0, 0
    m.size = len(raw) if raw is not None else source.stat().st_size
    with io.BytesIO(raw) if raw is not None else source.open('rb') as f:
        t.addfile(m, f)


def context_delta(original, target, patch, plan):
    require(digest(original) == plan['old_context_sha256'], 'original_context_changed')
    require(not target.exists(), 'fresh_context_required')
    replacements = {'build_native.sh': (patch/'build_native.sh').read_bytes()}
    debs = json.loads((patch/'SQLITE-BUILD-LOCK.json').read_bytes())['packages']
    additions = {'offline/debian/'+x['filename']: patch/x['filename'] for x in debs}
    additions['offline/debian/SQLITE-BUILD-LOCK.json'] = patch/'SQLITE-BUILD-LOCK.json'
    for x in debs:
        f = patch/x['filename']
        require(f.stat().st_size == x['bytes'] and digest(f) == x['sha256'], 'sqlite_package_hash')
    names, total = set(), 0
    old_inputs = old_meta = None
    with target.open('xb') as output, tarfile.open(fileobj=output, mode='w|') as dest:
        with tarfile.open(str(original), 'r|') as src:
            for m in src:
                name = m.name
                p = PurePosixPath(name)
                require(m.isfile() and not m.issparse() and not m.linkname and not p.is_absolute()
                        and all(x not in ('', '.', '..') for x in name.split('/'))
                        and not any(x in name for x in ('\\', ':', '\r', '\n'))
                        and name not in names and name not in additions and len(names) < 30000,
                        'unsafe_context_member')
                names.add(name)
                total += m.size
                require(0 <= m.size < GIB and total < 4*GIB, 'context_size')
                with src.extractfile(m) as f:
                    if name in replacements or name in ('offline/INPUTS.SHA256', 'CONTEXT.json'):
                        require(m.size < 4*1024**2, 'context_metadata_size')
                        raw = f.read()
                        if name in replacements:
                            require(hashlib.sha256(raw).hexdigest() == plan['old_build_native_sha256'],
                                    'original_build_script_changed')
                            add(dest, name, raw=replacements[name])
                        elif name == 'offline/INPUTS.SHA256':
                            old_inputs = raw
                        else:
                            old_meta = json.loads(raw)
                    else:
                        clean = tarfile.TarInfo(name)
                        clean.mode, clean.uid, clean.gid, clean.mtime, clean.size = 0o600, 0, 0, 0, m.size
                        # Source and all previous offline inputs pass through unchanged.
                        dest.addfile(clean, f)
        require(set(replacements) <= names and old_inputs is not None and old_meta is not None,
                'context_required_members_missing')
        require(old_inputs.endswith(b'\n') and b'debian/' not in old_inputs, 'old_input_manifest_shape')
        new_inputs = old_inputs
        for name, path in sorted(additions.items()):
            add(dest, name, source=path)
            new_inputs += (digest(path)+'  '+name[len('offline/'):]+'\n').encode('ascii')
        add(dest, 'offline/INPUTS.SHA256', raw=new_inputs)
        old_meta.update(offline_manifest_sha256=hashlib.sha256(new_inputs).hexdigest(),
                        input_files=old_meta['input_files']+len(additions),
                        sqlite_build_repair='LOCKED_DEBIAN12_ARM64_BUILDER_ONLY',
                        linux_build_executed=False, ready_for_build=False, ready_for_rollout=False)
        add(dest, 'CONTEXT.json', raw=(json.dumps(old_meta, sort_keys=True, indent=2)+'\n').encode())
    target.chmod(0o600)
    return digest(target)


def prepare(old, patch, output, plan):
    rows = validate_package(old, plan['old_package_manifest_sha256'])
    require(rows['context.tar'] == plan['old_context_sha256'], 'context_manifest_binding')
    require(not output.exists(), 'fresh_output_required')
    output.mkdir(mode=0o700)
    package = output/'package'
    package.mkdir(mode=0o700)
    for name, expected in rows.items():
        if name in ('context.tar', 'BUILD-PLAN.json', 'verify_native_candidate.py', 'LOCAL-VERIFICATION.json'):
            continue
        with (old/name).open('rb') as inp, (package/name).open('xb') as out:
            shutil.copyfileobj(inp, out, 1024**2)
        (package/name).chmod(0o600)
        require(digest(package/name) == expected, 'copied_package_hash')
    context_sha = context_delta(old/'context.tar', package/'context.tar', patch, plan)
    original_plan = json.loads((old/'BUILD-PLAN.json').read_bytes())
    original_plan.update(context_sha256=context_sha, build_repair={
        'id':'sqlite-build-dependencies-20261016-r1',
        'original_package_manifest_sha256':plan['old_package_manifest_sha256'],
        'sqlite_lock_sha256':digest(patch/'SQLITE-BUILD-LOCK.json'),
        'production_database_unchanged':True})
    record(package/'BUILD-PLAN.json', original_plan)
    for name in ('verify_native_candidate.py', 'LOCAL-VERIFICATION.json'):
        write_new(package/name, (patch/name).read_bytes())
    raw = ''.join(digest(f)+'  '+f.name+'\n' for f in sorted(package.iterdir())).encode('ascii')
    write_new(package/'PACKAGE.SHA256', raw)
    validate_package(package)
    return {'repair_preparation':'PASS', 'build_root':str(output), 'package':str(package),
            'context_sha256':context_sha, 'package_manifest_sha256':digest(package/'PACKAGE.SHA256'),
            'original_evidence_modified':False, 'production_workloads_mutated':False,
            'host_system_packages_modified':False, 'database_changed':False, 'provider_requests':0,
            'images_imported':False, 'build_started':False, 'ready_for_rollout':False}


def status():
    if not STATE.exists():
        print(json.dumps({'repair_state':'ABSENT', 'files_modified':False}))
        return
    private(STATE, True)
    result = {'repair_state':str(STATE), 'files_modified':False, 'automatic_retry':False}
    for name in ('STARTED.json','PREPARED.json','FAILED.json'):
        p = STATE/name
        if p.exists():
            private(p)
            require(p.stat().st_size < 65536, 'state_size')
            result[name] = json.loads(p.read_bytes())
    prepared = result.get('PREPARED.json')
    if prepared:
        for name in ('BUILD-RESULT.json','BUILD-FAILED.json'):
            p = Path(prepared['build_root'])/'build1'/name
            if p.exists():
                private(p)
                result[name] = {'sha256':digest(p), 'path':str(p)}
    print(json.dumps(result, sort_keys=True), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--status', action='store_true')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
            'requires_root_datax01')
    os.umask(0o077)
    if a.status:
        status()
        return
    require(a.output is not None and a.output.is_absolute() and a.output.name == 'rebuild', 'output_shape')
    private(a.output.parent, True)
    require(a.output.parent.parent == Path('/opt') and
            a.output.parent.name.startswith('weknora-glm-sqlite-fix-') and
            a.output.parent.stat().st_dev == Path('/opt').stat().st_dev != Path('/').stat().st_dev,
            'private_opt_build_parent_required')
    require(not STATE.exists(), 'previous_attempt_exists_USE_STATUS')
    private(STATE.parent, True)
    validate_package(HERE)
    plan = json.loads((HERE/'FIX-PLAN.json').read_bytes())
    failed = OLD.parent/'build1/BUILD-FAILED.json'
    private(failed)
    previous = json.loads(failed.read_bytes())
    require(previous.get('stage') == 'compile-and-runtime-check' and previous.get('images_imported') is False
            and previous.get('production_writes') == 0, 'previous_failure_requires_review')
    log = OLD.parent/'build1/05-compile-and-runtime-check.log'
    private(log)
    require(log.stat().st_size < 16*1024**2 and b'fatal error: sqlite3.h: No such file or directory' in log.read_bytes(),
            'sqlite_failure_not_confirmed')
    v = os.statvfs(str(a.output.parent))
    require(v.f_bavail*v.f_frsize > max(20*GIB, v.f_blocks*v.f_frsize//5)+65*GIB, 'build_disk_reserve')
    STATE.mkdir(mode=0o700)
    record(STATE/'STARTED.json', {'build_root':str(a.output), 'old_failure_sha256':digest(failed),
                                'production_writes':0, 'automatic_retry':False})
    try:
        result = prepare(OLD, HERE, a.output, plan)
        record(STATE/'PREPARED.json', result)
        print(json.dumps(result, sort_keys=True), flush=True)
    except Exception as exc:
        record(STATE/'FAILED.json', {'error_type':type(exc).__name__,
            'check':str(exc) if isinstance(exc, CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            'production_writes':0, 'automatic_retry':False})
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'repair_preparation':'UNCONFIRMED', 'error_type':type(exc).__name__,
            'check':str(exc) if isinstance(exc, CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            'production_writes':0, 'automatic_retry':False, 'instruction':'USE_STATUS_DO_NOT_REPEAT'}), flush=True)
        sys.exit(1)
