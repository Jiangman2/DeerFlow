"""Stage/publish only the delivery Skill; reuse the existing browser dependency.

Filesystem operations only. The caller owns cached-image identity, Kubernetes
UID-bound consumer rendering and the operator receipt. Never read credentials.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import zipfile

ROOT = Path('/mnt/skills/public/energy-market-daily')
PARENT = Path('/mnt/skills')
RUNTIME_PARENT = PARENT / 'public/.energy-browser-runtime-20261112-r1'
RUNTIME = RUNTIME_PARENT / 'linux-arm64'
OLD_TREE = 'f9865f7b1fd1745c471bb808f081bf3e86f2170c0c9c22df20e776c0a56ffcf9'
OLD_COUNT, CODE_COUNT, NEW_COUNT = 107, 107, 108
CANDIDATE_VERSION = '2.1.0-portable'
RUNTIME_SHA = 'cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598'
PACKAGE_SHA = '980eed816ef30b5d6bbbc91744943d64ba0fb7ad1abf10c2abb1ec10b60b2ac8'
IMAGE_ID = 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
MAX_FILE, MAX_RUNTIME = 384 * 1024 * 1024, 512 * 1024 * 1024


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def safe(path):
    path = Path(path)
    need(path.is_absolute(), 'ABSOLUTE_PATH_REQUIRED')
    for part in (path,) + tuple(path.parents):
        try:
            state = part.lstat()
        except FileNotFoundError:
            continue
        need(not stat.S_ISLNK(state.st_mode) and not getattr(state, 'st_file_attributes', 0) & 0x400, 'PATH_LINK_REFUSED')
    return path


def stamp(state):
    result = state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns
    return result if os.name == 'nt' else result + (state.st_ctime_ns,)


def identity(path):
    state = safe(path).stat()
    need(stat.S_ISDIR(state.st_mode), 'DIRECTORY_REQUIRED')
    return [state.st_dev, state.st_ino]


def mkdir_new(path, mode):
    path = safe(path)
    path.mkdir(mode=mode)
    path.chmod(mode)
    return identity(path)


def write_new(path, raw, mode=0o600):
    path = safe(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        os.chmod(path, mode)
    finally:
        os.close(fd)


def read(path, limit=6 * 1024 * 1024):
    path = safe(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, 'FILE_TYPE_OR_BOUND')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        need(len(raw) == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.stat()), 'FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def sync_directory(path):
    if os.name != 'nt':
        fd = os.open(str(safe(path)), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def save(path, value):
    path = safe(path)
    temporary = path.with_name('.journal-' + os.urandom(8).hex())
    write_new(temporary, canonical(value) + b'\n')
    os.replace(temporary, path)
    sync_directory(path.parent)


def inventory(root):
    identity(root)
    rows, total, directories = [], 0, 0
    for directory, folders, names in os.walk(str(root), followlinks=False):
        directories += 1
        need(directories <= 256, 'SKILL_DIRECTORY_BOUND')
        for name in folders:
            identity(Path(directory) / name)
        for name in names:
            path = Path(directory) / name
            raw = read(path)
            total += len(raw)
            rows.append({'path': path.relative_to(root).as_posix(), 'bytes': len(raw), 'sha256': sha(raw)})
            need(len(rows) <= 512 and total <= 32 * 1024 * 1024, 'SKILL_TREE_BOUND')
    return sorted(rows, key=lambda row: row['path'])


def tree_hash(rows):
    return sha(canonical(rows))


def verify_tree(root, expected, count):
    rows = inventory(root)
    need(len(rows) == count and tree_hash(rows) == expected, 'SKILL_TREE_CHANGED')
    return rows


def release_path(operation):
    need(isinstance(operation, str) and re.fullmatch('[0-9a-f]{32}', operation), 'OPERATION_ID_REQUIRED')
    return safe(PARENT / ('.energy-delivery-skill-install-20261112-r1-' + operation))


def relative(name):
    need(isinstance(name, str) and 0 < len(name) <= 256 and '\\' not in name and ':' not in name, 'INVENTORY_PATH_INVALID')
    path = PurePosixPath(name)
    need(not path.is_absolute() and '..' not in path.parts and str(path) == name and name != '.', 'INVENTORY_PATH_INVALID')
    return path


def runtime_rows(manifest, digest):
    need(isinstance(manifest, dict) and set(manifest) == {'schema', 'architecture', 'source_image_id', 'executable', 'files', 'licenses'}
         and manifest.get('schema') == 'energy-browser-runtime.v1' and manifest.get('architecture') == 'linux/arm64'
         and manifest.get('source_image_id') == IMAGE_ID and manifest.get('executable') == 'chrome-linux/headless_shell', 'RUNTIME_MANIFEST_IDENTITY')
    raw = canonical(manifest) + b'\n'
    need(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest) and sha(raw) == digest, 'RUNTIME_MANIFEST_CHANGED')
    files = manifest['files']
    need(isinstance(files, list) and 0 < len(files) <= 256, 'RUNTIME_FILE_BOUND')
    found, total = {}, 0
    for row in files:
        need(isinstance(row, dict) and set(row) == {'path', 'bytes', 'sha256', 'executable'}, 'RUNTIME_ROW_INVALID')
        name = row['path']
        path = relative(name)
        need(len(path.parts) >= 2 and path.parts[0] == 'chrome-linux' and name not in found, 'RUNTIME_PATH_INVALID')
        need(type(row['bytes']) is int and 0 <= row['bytes'] <= MAX_FILE and type(row['executable']) is bool
             and isinstance(row['sha256'], str) and re.fullmatch('[0-9a-f]{64}', row['sha256']), 'RUNTIME_ROW_INVALID')
        found[name], total = row, total + row['bytes']
    need(total <= MAX_RUNTIME and manifest['executable'] in found and found[manifest['executable']]['executable'], 'RUNTIME_TOTAL_OR_ENTRY_INVALID')
    licenses = manifest['licenses']
    need(isinstance(licenses, list) and len(licenses) == len(set(licenses)) and all(isinstance(name, str) and name in found for name in licenses), 'RUNTIME_LICENSE_INVENTORY')
    return found, raw


def file_hash(path, expected=None):
    path = safe(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= MAX_FILE
             and not before.st_mode & (stat.S_ISUID | stat.S_ISGID), 'RUNTIME_SOURCE_TYPE_OR_BOUND')
        executable = bool(before.st_mode & 0o111)
        if expected:
            need(before.st_size == expected['bytes'] and executable == expected['executable'], 'RUNTIME_SOURCE_CHANGED')
        digest, count = hashlib.sha256(), 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            count += len(chunk)
            need(count <= before.st_size, 'RUNTIME_SOURCE_CHANGED')
            digest.update(chunk)
        need(count == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.stat()), 'RUNTIME_SOURCE_CHANGED')
        if expected:
            need(digest.hexdigest() == expected['sha256'], 'RUNTIME_SOURCE_CHANGED')
        return {'bytes': count, 'sha256': digest.hexdigest(), 'executable': executable}
    finally:
        os.close(fd)


def runtime_inventory(root, include_manifest=False):
    identity(root)
    result, total, directories = {}, 0, 0
    for directory, folders, names in os.walk(str(root), followlinks=False):
        directories += 1
        need(directories <= 256, 'RUNTIME_DIRECTORY_BOUND')
        for name in folders:
            identity(Path(directory) / name)
        for name in names:
            path = Path(directory) / name
            relative_name = path.relative_to(root).as_posix()
            if include_manifest and relative_name == 'RUNTIME.json':
                continue
            row = file_hash(path)
            total += row['bytes']
            need(len(result) < 256 and total <= MAX_RUNTIME, 'RUNTIME_FILE_BOUND')
            result[relative_name] = dict(row, path=relative_name)
    return result


def verify_runtime(manifest, digest):
    rows, raw = runtime_rows(manifest, digest)
    need(read(RUNTIME / 'RUNTIME.json', 262144) == raw and runtime_inventory(RUNTIME, include_manifest=True) == rows, 'INSTALLED_RUNTIME_CHANGED')
    return {'path': str(RUNTIME), 'manifest_sha256': digest, 'files': len(rows), 'bytes': sum(row['bytes'] for row in rows.values()), 'source_image_id': IMAGE_ID}




def extract_candidate(encoded, digest, candidate):
    need(digest == PACKAGE_SHA and isinstance(encoded, str) and len(encoded) <= 4 * 1024 * 1024, 'PACKAGE_IDENTITY')
    raw = base64.b64decode(encoded, validate=True)
    need(len(raw) <= 2 * 1024 * 1024 and sha(raw) == PACKAGE_SHA, 'PACKAGE_CHANGED')
    mkdir_new(candidate, 0o755)
    names, total = set(), 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        need(len(archive.infolist()) == CODE_COUNT, 'PACKAGE_COUNT')
        for item in archive.infolist():
            name = item.filename
            part = relative(name)
            mode = item.external_attr >> 16
            need(len(part.parts) >= 2 and part.parts[0] == 'energy-market-daily' and not item.is_dir()
                 and not stat.S_ISLNK(mode) and name not in names and item.file_size <= 1048576
                 and '.private' not in part.parts and 'assets/browser-runtime.json' != '/'.join(part.parts[1:]), 'PACKAGE_LAYOUT')
            total += item.file_size
            need(total <= 32 * 1024 * 1024, 'PACKAGE_TOTAL_BOUND')
            target = candidate / Path(*part.parts[1:])
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            for directory in (target.parent,) + tuple(target.parent.parents):
                if directory == candidate.parent:
                    break
                directory.chmod(0o755)
            write_new(target, archive.read(item), 0o644)
            names.add(name)
    manifest = json.loads(read(candidate / 'MANIFEST.json', 262144))
    need(isinstance(manifest, dict) and manifest.get('schema') == 'energy-daily-portable-files.v1'
         and manifest.get('version') == CANDIDATE_VERSION and isinstance(manifest.get('files'), dict), 'CODE_MANIFEST_IDENTITY')
    need(names == {'energy-market-daily/MANIFEST.json'} | {'energy-market-daily/' + name for name in manifest['files']}, 'CODE_MANIFEST_SET')
    for name, item in manifest['files'].items():
        path = candidate / Path(*relative(name).parts)
        need(isinstance(item, dict) and set(item) == {'bytes', 'sha256'}, 'CODE_MANIFEST_ROW')
        contents = read(path, 1048576)
        need(len(contents) == item['bytes'] and sha(contents) == item['sha256'], 'CODE_MANIFEST_CHANGED')
    return manifest


def stage(payload):
    need(set(payload) == {'action', 'operation', 'package', 'package_sha256', 'runtime_manifest_sha256'}, 'PAYLOAD_FIELDS')
    need(CANDIDATE_VERSION == '2.1.0-portable' and CODE_COUNT > 106 and NEW_COUNT == CODE_COUNT + 1
         and re.fullmatch('[0-9a-f]{64}', PACKAGE_SHA), 'CANDIDATE_NOT_SEALED')
    operation, runtime_digest = payload['operation'], payload['runtime_manifest_sha256']
    need(runtime_digest == RUNTIME_SHA, 'EXISTING_PUBLIC_RUNTIME_REQUIRED')
    manifest = json.loads(read(RUNTIME / 'RUNTIME.json', 262144))
    release = release_path(operation)
    before_inode = identity(ROOT)
    original = verify_tree(ROOT, OLD_TREE, OLD_COUNT)
    runtime_rows(manifest, runtime_digest)
    need(not release.exists(), 'RELEASE_EXISTS_REVIEW_REQUIRED')
    mkdir_new(release, 0o700)
    backup = release / 'backup'
    shutil.copytree(ROOT, backup)
    need(inventory(backup) == original and identity(ROOT) == before_inode, 'BACKUP_OR_LIVE_CHANGED')
    candidate = release / 'candidate'
    code_manifest = extract_candidate(payload['package'], payload['package_sha256'], candidate)
    installed = verify_runtime(manifest, runtime_digest)
    binding = {'schema': 'energy-browser-runtime-binding.v1', 'directory': '../.energy-browser-runtime-20261112-r1/linux-arm64', 'manifest_sha256': runtime_digest}
    binding_raw = canonical(binding) + b'\n'
    write_new(candidate / 'assets/browser-runtime.json', binding_raw, 0o644)
    code_manifest['files']['assets/browser-runtime.json'] = {'bytes': len(binding_raw), 'sha256': sha(binding_raw)}
    code_manifest['files'] = dict(sorted(code_manifest['files'].items()))
    # The code ZIP is immutable; only this production pointer is added to its
    # inventory. Generic portable archives never contain that sibling binding.
    save(candidate / 'MANIFEST.json', code_manifest)
    (candidate / 'MANIFEST.json').chmod(0o644)
    rows = inventory(candidate)
    need(len(rows) == NEW_COUNT, 'CANDIDATE_COUNT')
    verify_tree(ROOT, OLD_TREE, OLD_COUNT)
    need(identity(ROOT) == before_inode, 'LIVE_INODE_CHANGED')
    receipt = {'status': 'ENERGY_DELIVERY_SKILL_STAGED', 'operation': operation, 'live_before_identity': before_inode,
               'before_tree_sha256': OLD_TREE, 'after_tree_sha256': tree_hash(rows), 'before_files': OLD_COUNT, 'after_files': NEW_COUNT,
               'package_sha256': PACKAGE_SHA, 'backup_path': str(backup), 'candidate_path': str(candidate), 'release_path': str(release),
               'runtime': installed, 'existing_runtime_reused': True, 'runtime_modified': False, 'runtime_manifest': manifest, 'binding_sha256': sha(binding_raw), 'skill_modified': False,
               'credentials_read': False, 'provider_requests': 0, 'automatic_rollback': False}
    write_new(release / 'STAGED.json', canonical(receipt) + b'\n')
    return receipt


def staged(operation):
    release = release_path(operation)
    receipt = json.loads(read(release / 'STAGED.json', 1048576))
    need(receipt.get('status') == 'ENERGY_DELIVERY_SKILL_STAGED' and receipt.get('operation') == operation
         and receipt.get('package_sha256') == PACKAGE_SHA and receipt.get('before_tree_sha256') == OLD_TREE
         and receipt.get('before_files') == OLD_COUNT and receipt.get('after_files') == NEW_COUNT
         and re.fullmatch('[0-9a-f]{64}', receipt.get('after_tree_sha256', '')), 'STAGE_RECEIPT_INVALID')
    return release, receipt


def inspect_stage(payload):
    need(set(payload) == {'action', 'operation'}, 'PAYLOAD_FIELDS')
    release, receipt = staged(payload['operation'])
    need(not (release / 'SWITCH.json').exists(), 'SWITCH_ALREADY_REQUESTED')
    need(identity(ROOT) == receipt['live_before_identity'], 'LIVE_INODE_CHANGED')
    verify_tree(ROOT, OLD_TREE, OLD_COUNT)
    verify_tree(release / 'backup', OLD_TREE, OLD_COUNT)
    verify_tree(release / 'candidate', receipt['after_tree_sha256'], NEW_COUNT)
    runtime = verify_runtime(receipt['runtime_manifest'], receipt['runtime']['manifest_sha256'])
    return {'status': 'ENERGY_DELIVERY_SKILL_STAGED', 'before_tree_sha256': OLD_TREE,
            'after_tree_sha256': receipt['after_tree_sha256'], 'after_files': NEW_COUNT,
            'runtime': runtime, 'skill_modified': False, 'provider_requests': 0}


def publish(payload):
    need(set(payload) == {'action', 'operation', 'acceptance'}, 'PAYLOAD_FIELDS')
    operation, acceptance = payload['operation'], payload['acceptance']
    release, receipt = staged(operation)
    need(isinstance(acceptance, dict) and acceptance.get('status') == 'NONROOT_ARM64_DELIVERY_SKILL_QUALIFIED'
         and type(acceptance.get('uid')) is int and acceptance['uid'] == 1000
         and type(acceptance.get('gid')) is int and acceptance['gid'] == 1000
         and acceptance.get('package_sha256') == PACKAGE_SHA and acceptance.get('runtime_manifest_sha256') == receipt['runtime']['manifest_sha256']
         and acceptance.get('after_tree_sha256') == receipt['after_tree_sha256']
         and acceptance.get('final_text', {}).get('complete_artifacts_accepted') is True
          and acceptance.get('final_text', {}).get('missing_artifact_rejected') is True
          and acceptance.get('final_text', {}).get('messages_sent') == 0 and acceptance.get('final_text', {}).get('network_requests') == 0
          and isinstance(acceptance.get('png_sha256'), str) and re.fullmatch('[0-9a-f]{64}', acceptance['png_sha256']), 'NONROOT_RENDER_ACCEPTANCE_REQUIRED')
    need(identity(ROOT) == receipt['live_before_identity'], 'LIVE_INODE_CHANGED')
    verify_tree(ROOT, OLD_TREE, OLD_COUNT)
    verify_tree(release / 'backup', OLD_TREE, OLD_COUNT)
    verify_tree(release / 'candidate', receipt['after_tree_sha256'], NEW_COUNT)
    verify_runtime(receipt['runtime_manifest'], receipt['runtime']['manifest_sha256'])
    retired = release / 'retired-original'
    need(not retired.exists() and not retired.is_symlink() and not (release / 'SWITCH.json').exists(), 'SWITCH_ALREADY_REQUESTED')
    write_new(release / 'ACCEPTED-NONROOT.json', canonical(acceptance) + b'\n')
    save(release / 'SWITCH.json', {'phase': 'BEFORE_FIRST_RENAME', 'operation': operation, 'automatic_rollback': False})
    os.rename(ROOT, retired)
    sync_directory(ROOT.parent)
    save(release / 'SWITCH.json', {'phase': 'OLD_RETIRED_NEW_NOT_INSTALLED', 'operation': operation, 'automatic_rollback': False})
    os.rename(release / 'candidate', ROOT)
    sync_directory(ROOT.parent)
    need(identity(retired) == receipt['live_before_identity'], 'RETIRED_INODE_CHANGED')
    verify_tree(ROOT, receipt['after_tree_sha256'], NEW_COUNT)
    verify_tree(retired, OLD_TREE, OLD_COUNT)
    result = {'status': 'ENERGY_DELIVERY_SKILL_PUBLISHED', 'operation': operation, 'before_tree_sha256': OLD_TREE,
              'after_tree_sha256': receipt['after_tree_sha256'], 'files': NEW_COUNT, 'after_files': NEW_COUNT, 'skill_modified': True,
              'backup_path': str(release / 'backup'), 'retired_path': str(retired), 'runtime': receipt['runtime'],
              'nonroot_render_sha256': acceptance['png_sha256'], 'automatic_rollback': False, 'historical_reports_modified': False,
              'switch_strategy': 'TWO_RENAMES_WITH_SHORT_NAME_GAP'}
    save(release / 'SWITCH.json', result)
    return result


def verify(payload):
    need(set(payload) == {'action', 'operation'}, 'PAYLOAD_FIELDS')
    release, receipt = staged(payload['operation'])
    switch = json.loads(read(release / 'SWITCH.json'))
    need(switch.get('status') == 'ENERGY_DELIVERY_SKILL_PUBLISHED' and switch.get('operation') == payload['operation']
         and switch.get('after_tree_sha256') == receipt['after_tree_sha256'], 'PUBLISHED_RECEIPT_REQUIRED')
    verify_tree(ROOT, receipt['after_tree_sha256'], NEW_COUNT)
    runtime = verify_runtime(receipt['runtime_manifest'], receipt['runtime']['manifest_sha256'])
    return {'status': 'ENERGY_DELIVERY_SKILL_VERIFIED', 'tree_sha256': receipt['after_tree_sha256'],
            'after_tree_sha256': receipt['after_tree_sha256'], 'files': NEW_COUNT,
            'runtime': runtime, 'skill_modified': False, 'provider_requests': 0}


def main():
    out = {'schema': 'energy-delivery-skill-filesystem.v1', 'status': 'INCOMPLETE', 'provider_requests': 0,
           'credentials_read': False, 'database_access': False, 'automatic_rollback': False}
    try:
        sys.dont_write_bytecode = True
        need(os.name == 'posix' and os.geteuid() == 0, 'ROOT_PUBLISHER_REQUIRED')
        raw = sys.stdin.buffer.read(8388609)
        need(len(raw) <= 8388608, 'INPUT_BOUND')
        payload = json.loads(raw)
        need(isinstance(payload, dict) and payload.get('action') in ('stage', 'inspect-stage', 'publish', 'verify'), 'FIXED_ACTION_REQUIRED')
        def audit(event, args):
            if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system', 'sqlite3.connect'):
                raise Refused('EXTERNAL_OPERATION_FORBIDDEN')
        sys.addaudithook(audit)
        out.update({'stage': stage, 'inspect-stage': inspect_stage, 'publish': publish, 'verify': verify}[payload['action']](payload))
    except Exception as exc:
        out.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) and re.fullmatch('[A-Z0-9_]{1,100}', str(exc)) else 'PUBLICATION_FAILED_REDACTED')
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
