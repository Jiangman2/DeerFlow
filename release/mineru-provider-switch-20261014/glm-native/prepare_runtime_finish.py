"""Copy completed native outputs READ-ONLY; prepare a small fresh runtime build."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import repair_sqlite_build as safe

OLD = Path('/opt/weknora-glm-dwarf-fix-qYPHEQjp/rebuild')
PREPARED = Path('/var/lib/weknora-glm-dwarf-fix-20261016-r1/PREPARED.json')
STATE = Path('/var/lib/weknora-glm-runtime-finish-20261016-r1')
ERROR_HASH = 'dbe87c354a439f7f644f179f0905c624c0677fee22eec75526a660d41fd07071'
DICT_NAMES = ('jieba.dict.utf8', 'hmm_model.utf8', 'user.dict.utf8', 'idf.utf8', 'stop_words.utf8')
ZIP_NAME = 'go-proxy/github.com/yanyiwu/gojieba/@v/v1.4.7.zip'
ZIP_PREFIX = 'github.com/yanyiwu/gojieba@v1.4.7/deps/cppjieba/dict/'
PROOF = ('source.sha256', 'binaries.sha256', 'sqlite-build-preflight.txt', 'compiler-config.json',
         'dwarf-format.json', 'compiler-preflight.txt', 'server-buildinfo.txt', 'vlm-tests.jsonl',
         'anydoc-tests.jsonl', 'ssrf-regression.jsonl', 'anydoc-self-test.json', 'go-mod-verify.txt',
         'server-elf.txt', 'dependency-locks.before.sha256')
require = safe.require


def checked_image_file(path, fs, maximum):
    require(fs in path.parents, 'snapshot_path_escape')
    for p in (path.parent,) + tuple(path.parent.parents):
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0, 'snapshot_parent_link_or_owner')
        if p == fs:
            break
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    f = os.fdopen(fd, 'rb')
    s = os.fstat(f.fileno())
    if not (stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and 0 < s.st_size <= maximum):
        f.close()
        raise safe.CheckFailure('snapshot_file_type_size_or_owner')
    return f


def small(path, fs):
    with checked_image_file(path, fs, 16*1024**2) as f:
        return f.read()


def context_inputs(archive):
    wanted = {'source/SOURCE.SHA256', 'offline/INPUTS.SHA256', 'offline/'+ZIP_NAME}
    result = {}
    with tarfile.open(str(archive), 'r:') as t:
        for m in t:
            if m.name not in wanted:
                continue
            require(m.name not in result and m.isfile() and not m.issparse() and not m.linkname
                    and 0 < m.size < 32*1024**2, 'locked_input_member')
            result[m.name] = t.extractfile(m).read()
    require(set(result) == wanted, 'locked_inputs_missing')
    lines = result['offline/INPUTS.SHA256'].decode('utf-8').splitlines()
    expected = [line.split('  ', 1)[0] for line in lines if line.endswith('  '+ZIP_NAME)]
    require(expected == [hashlib.sha256(result['offline/'+ZIP_NAME]).hexdigest()], 'jieba_zip_hash')
    return result


def dictionaries(raw):
    result = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        for name in DICT_NAMES:
            full = ZIP_PREFIX+name
            require(names.count(full) == 1, 'dictionary_missing_or_duplicate')
            info = z.getinfo(full)
            require(0 < info.file_size < 16*1024**2 and not info.is_dir()
                    and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG), 'unsafe_dictionary_member')
            result[name] = z.read(info)
    require(sum(map(len, result.values())) < 32*1024**2, 'dictionary_budget')
    return result


def find_compiled(snapshots, source_manifest):
    rows = []
    entries = list(os.scandir(str(snapshots)))
    require(len(entries) <= 1024, 'snapshot_count_bound')
    for item in entries:
        if not item.name.isdigit() or not item.is_dir(follow_symlinks=False):
            continue
        fs = Path(item.path)/'fs'
        file = fs/'out/evidence/binaries.sha256'
        if not file.exists():
            continue
        try:
            require(small(fs/'out/evidence/source.sha256', fs) == source_manifest, 'source_manifest_mismatch')
            hashes = {}
            for line in small(file, fs).decode('ascii').splitlines():
                m = re.fullmatch(r'([a-f0-9]{64})  /out/(WeKnora|weknora-anydoc-probe|weknora-glm-probe)', line)
                require(m and m[2] not in hashes, 'compiled_binary_receipt')
                hashes[m[2]] = m[1]
            require(len(hashes) == 3 and all((fs/'out/evidence'/n).is_file() for n in PROOF),
                    'compile_receipts_incomplete')
            rows.append((fs, hashes))
        except FileNotFoundError:
            continue
    require(len(rows) == 1, 'exact_completed_compile_snapshot_required')
    return rows[0]


def copy_binary(fs, name, expected, target):
    h = hashlib.sha256()
    with checked_image_file(fs/'out'/name, fs, 1024**3) as src, target.open('xb') as dst:
        for block in iter(lambda: src.read(1024**2), b''):
            h.update(block); dst.write(block)
    target.chmod(0o600)
    require(h.hexdigest() == expected and safe.digest(target) == expected, 'reused_binary_digest')


def prepare(output):
    safe.private(PREPARED)
    prepared = json.loads(PREPARED.read_bytes())
    require(prepared.get('build_root') == str(OLD), 'previous_build_binding')
    print(json.dumps({'runtime_recovery': 'VERIFY_ORIGINAL_PACKAGE', 'compile_started': False}), flush=True)
    rows = safe.validate_package(OLD/'package', prepared['package_manifest_sha256'])
    require(rows['context.tar'] == prepared['context_sha256'], 'old_context_receipt')
    failed = OLD/'build1/BUILD-FAILED.json'
    safe.private(failed)
    result = json.loads(failed.read_bytes())
    require(result.get('stage') == 'compile-and-runtime-check' and result.get('images_imported') is False
            and result.get('production_writes') == 0 and not (OLD/'build1/BUILD-RESULT.json').exists(),
            'previous_failure_state')
    log = OLD/'build1/05-compile-and-runtime-check.log'
    safe.private(log)
    require(log.stat().st_size < 16*1024**2, 'old_log_size')
    text = log.read_bytes()
    require(b'offline_native_qualification=PASS provider_requests=0' in text and
            b'[runtime-check 4/5]' in text and b'exit code: 2' in text, 'runtime_failure_not_confirmed')
    snapshots = OLD/'build1/buildkit/runc-overlayfs/snapshots/snapshots'
    safe.private(snapshots, True)
    # The retained exact error is audit evidence, not code to execute.
    err = snapshots/'41/fs/tmp/weknora-glm-qualification/glm-help.txt'
    require(hashlib.sha256(small(err, snapshots/'41/fs')).hexdigest() == ERROR_HASH,
            'jieba_failure_receipt_changed')
    require(b'Dictionary file does not exist:' in small(err, snapshots/'41/fs'), 'dictionary_failure_not_confirmed')
    inputs = context_inputs(OLD/'package/context.tar')
    fs, hashes = find_compiled(snapshots, inputs['source/SOURCE.SHA256'])
    data = dictionaries(inputs['offline/'+ZIP_NAME])
    require(not output.exists(), 'fresh_runtime_output_required')
    output.mkdir(mode=0o700)
    package, context = output/'package', output/'runtime-context'
    package.mkdir(mode=0o700); context.mkdir(mode=0o700)
    for name in ('payload', 'dict', 'evidence'):
        (context/name).mkdir(mode=0o700)
    print(json.dumps({'runtime_recovery': 'COPY_HASH_BOUND_COMPILED_OUTPUTS', 'snapshot': fs.parent.name,
                      'binaries': 3, 'dictionary_files': 5, 'compile_started': False}), flush=True)
    for name, expected in hashes.items():
        copy_binary(fs, name, expected, context/'payload'/name)
    for name in PROOF:
        safe.write_new(context/'evidence'/name, small(fs/'out/evidence'/name, fs))
    for name, raw in data.items():
        safe.write_new(context/'dict'/name, raw)
    safe.write_new(context/'runtime_check.py', (HERE/'runtime_check.py').read_bytes())
    safe.write_new(context/'Dockerfile.weknora', (HERE/'Dockerfile.runtime-finish').read_bytes())
    for name, expected in rows.items():
        if name in ('context.tar', 'builder.oci.tar', 'BUILD-PLAN.json', 'LOCAL-VERIFICATION.json'):
            continue
        safe.write_new(package/name, (OLD/'package'/name).read_bytes())
        require(safe.digest(package/name) == expected, 'copied_helper_changed')
    for name in ('finish_runtime_build.py', 'verify_runtime_candidate.py'):
        safe.write_new(package/name, (HERE/name).read_bytes())
    with (package/'context.tar').open('xb') as out, tarfile.open(fileobj=out, mode='w|') as t:
        for path in sorted(context.rglob('*')):
            if path.is_file():
                safe.add(t, path.relative_to(context).as_posix(), source=path)
    plan = json.loads((OLD/'package/BUILD-PLAN.json').read_bytes())
    mapping = {'WeKnora': 'app/WeKnora', 'weknora-anydoc-probe': 'usr/local/bin/weknora-anydoc-probe',
               'weknora-glm-probe': 'usr/local/bin/weknora-glm-probe'}
    plan.update(context_sha256=safe.digest(package/'context.tar'), runtime_finish={
        'compile_reused': True, 'old_package_manifest_sha256': prepared['package_manifest_sha256'],
        'old_failure_sha256': safe.digest(failed), 'old_compile_log_sha256': safe.digest(log),
        'source_manifest_sha256': hashlib.sha256(inputs['source/SOURCE.SHA256']).hexdigest(),
        'snapshot': fs.parent.name, 'binaries': {mapping[n]: sha for n, sha in hashes.items()},
        'dictionary_module': 'github.com/yanyiwu/gojieba@v1.4.7',
        'dictionary_zip_sha256': hashlib.sha256(inputs['offline/'+ZIP_NAME]).hexdigest(),
        'dictionaries': {n: hashlib.sha256(raw).hexdigest() for n, raw in data.items()}})
    safe.record(package/'BUILD-PLAN.json', plan)
    safe.write_new(package/'LOCAL-VERIFICATION.json', (HERE/'LOCAL-VERIFICATION.json').read_bytes())
    safe.write_new(package/'PACKAGE.SHA256', ''.join(safe.digest(p)+'  '+p.name+'\n'
        for p in sorted(package.iterdir())).encode('ascii'))
    safe.validate_package(package)
    return {'runtime_finish_preparation': 'PASS', 'build_root': str(output), 'compile_reused': True,
        'package_manifest_sha256': safe.digest(package/'PACKAGE.SHA256'),
        'context_sha256': plan['context_sha256'], 'original_evidence_modified': False,
        'production_writes': 0, 'images_imported': False, 'provider_requests': 0, 'ready_for_rollout': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
            'root_datax01_required')
    os.umask(0o077)
    if args.status:
        safe.STATE = STATE
        safe.status()
        return
    require(args.output is not None and args.output.name == 'rebuild' and args.output.is_absolute(), 'output_shape')
    safe.private(args.output.parent, True)
    require(args.output.parent.parent == Path('/opt') and args.output.parent.name.startswith('weknora-glm-runtime-fix-'),
            'new_runtime_output_location')
    require(not STATE.exists(), 'previous_attempt_exists_USE_STATUS')
    safe.private(STATE.parent, True)
    safe.validate_package(HERE)
    proc = subprocess.run(['ps', '-eo', 'comm=,args='], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, timeout=15).stdout.decode('utf-8', 'replace')
    require(not re.search(r'(?m)^\s*(buildctl|nerdctl)\s|^\s*ctr\s.*\bimages\s+(import|export)\b', proc),
            'other_image_operation_active')
    require(not any('buildkitd' in line and str(OLD/'build1/buildkit') in line for line in proc.splitlines()),
            'original_cache_daemon_still_running')
    v = os.statvfs(str(args.output.parent))
    require(args.output.parent.stat().st_dev == Path('/opt').stat().st_dev != Path('/').stat().st_dev and
            v.f_bavail*v.f_frsize > max(20*safe.GIB, v.f_blocks*v.f_frsize//5)+65*safe.GIB, 'build_disk_reserve')
    STATE.mkdir(mode=0o700)
    safe.record(STATE/'STARTED.json', {'build_root': str(args.output), 'automatic_retry': False})
    try:
        value = prepare(args.output)
        safe.record(STATE/'PREPARED.json', value)
        print(json.dumps(value), flush=True)
    except Exception as exc:
        safe.record(STATE/'FAILED.json', {'error_type': type(exc).__name__, 'check': str(exc)
            if isinstance(exc, safe.CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED'})
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'runtime_finish_preparation': 'UNCONFIRMED', 'error_type': type(exc).__name__,
            'check': str(exc) if isinstance(exc, safe.CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            'automatic_retry': False, 'production_writes': 0, 'provider_requests': 0,
            'instruction': 'USE_STATUS_DO_NOT_REPEAT', 'ready_for_rollout': False}), flush=True)
        sys.exit(1)
