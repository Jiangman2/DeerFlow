"""Fresh offline build-only DWARF4 fallback; Python 3.7+. No rollout/retry."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import sys
import tarfile

HERE = Path(__file__).resolve().parent
# The launcher hash-checks this private, root-owned package before execution.
sys.path.insert(0, str(HERE))
import repair_sqlite_build as base

OLD = Path('/opt/weknora-glm-sqlite-fix-BDPsrLAi/rebuild/package')
STATE = Path('/var/lib/weknora-glm-dwarf-fix-20261016-r1')
require = base.require

COMPILER_PREFLIGHT = '''# Keep the exact toolchain and optimizations; change only DWARF debug format.
export GOEXPERIMENT=nodwarf5
test "$(go env GOEXPERIMENT)" = nodwarf5
go env -json GOVERSION GOOS GOARCH GOEXPERIMENT > /out/evidence/compiler-config.json
printf '%s\\n' 'compiler_preflight=RUNNING debug_format=DWARF4'
printf '%s\\n' 'package main' 'func main() { println("dwarf4 probe") }' > /out/dwarf-probe.go
go build -p 2 -trimpath -o /out/dwarf-probe /out/dwarf-probe.go
readelf --debug-dump=info /out/dwarf-probe > /out/evidence/dwarf-probe-info.txt
python3 -I -B - <<'PY_DWARF'
import json,re
from pathlib import Path
p=Path('/out/evidence')
versions=[int(x) for x in re.findall(r'^\\s*Version:\\s+(\\d+)\\s*$', (p/'dwarf-probe-info.txt').read_text(), re.M)]
assert versions and set(versions)=={4}, 'DWARF4 fallback not active'
(p/'dwarf-format.json').write_text(json.dumps({'versions':sorted(set(versions)), 'compilation_units':len(versions)})+'\\n')
PY_DWARF
# Reproduce the exact failing package early, before the Rust/AnyDoc compilation.
go build -p 2 -trimpath github.com/elastic/go-elasticsearch/v8/typedapi/ml/gettrainedmodels
printf '%s\\n' 'compiler_dependency_preflight=PASS GOEXPERIMENT=nodwarf5' > /out/evidence/compiler-preflight.txt
printf '%s\\n' 'compiler_dependency_preflight=PASS starting_anydoc_build=true'

'''

VERIFIER_CHECK = '''    compiler = json.loads((root/'compiler-config.json').read_bytes())
    require(compiler.get('GOEXPERIMENT') == 'nodwarf5' and compiler.get('GOOS') == 'linux'
            and compiler.get('GOARCH') == 'arm64' and compiler.get('GOVERSION') == 'go1.26.8',
            'compiler_config_mismatch')
    dwarf = json.loads((root/'dwarf-format.json').read_bytes())
    require(dwarf.get('versions') == [4] and dwarf.get('compilation_units', 0) > 0,
            'dwarf4_probe_missing')
    require((root/'compiler-preflight.txt').read_text().strip() ==
            'compiler_dependency_preflight=PASS GOEXPERIMENT=nodwarf5', 'compiler_preflight_missing')
    require('GOEXPERIMENT=nodwarf5' in (root/'server-buildinfo.txt').read_text(),
            'server_compiler_fallback_missing')
'''


def patched_script(raw):
    anchor = b'# These are hash-checked local archives, never shell downloads.\n'
    require(raw.count(anchor) == 1 and b'GOEXPERIMENT' not in raw, 'old_build_script_shape')
    return raw.replace(anchor, COMPILER_PREFLIGHT.encode() + anchor)


def patched_verifier(raw):
    anchor = b'def evidence(root):\n'
    require(raw.count(anchor) == 1 and b'compiler_config_mismatch' not in raw, 'old_verifier_shape')
    return raw.replace(anchor, anchor + VERIFIER_CHECK.encode())


def context_delta(original, target, patch, plan):
    require(base.digest(original) == plan['old_context_sha256'], 'original_context_changed')
    require(not target.exists(), 'fresh_context_required')
    names, total = set(), 0
    with target.open('xb') as out, tarfile.open(fileobj=out, mode='w|', format=tarfile.GNU_FORMAT) as dest:
        with tarfile.open(str(original), 'r|') as src:
            for m in src:
                name = m.name
                require(m.isfile() and not m.issparse() and not m.linkname
                        and not PurePosixPath(name).is_absolute()
                        and all(x not in ('', '.', '..') for x in name.split('/'))
                        and not any(x in name for x in ('\\', ':', '\r', '\n'))
                        and name not in names and len(names) < 30000, 'unsafe_context_member')
                names.add(name)
                total += m.size
                require(0 <= m.size < base.GIB and total < 4*base.GIB, 'context_size')
                with src.extractfile(m) as f:
                    if name in ('build_native.sh', 'CONTEXT.json'):
                        require(m.size < 4*1024**2, 'context_metadata_size')
                        raw = f.read()
                        if name == 'build_native.sh':
                            require(hashlib.sha256(raw).hexdigest() == plan['old_build_native_sha256'],
                                    'original_build_script_changed')
                            raw = patched_script(raw)
                            require(raw == (patch/'build_native.sh').read_bytes(), 'patched_script_binding')
                        else:
                            meta = json.loads(raw)
                            meta.update(compiler_debug_format='DWARF4', go_experiment='nodwarf5',
                                        compiler_preflight='EXACT_PREVIOUSLY_FAILING_DEPENDENCY',
                                        linux_build_executed=False, ready_for_rollout=False)
                            raw = (json.dumps(meta, sort_keys=True, indent=2)+'\n').encode()
                        base.add(dest, name, raw=raw)
                    else:
                        clean = tarfile.TarInfo(name)
                        clean.mode, clean.uid, clean.gid, clean.mtime, clean.size = 0o600, 0, 0, 0, m.size
                        dest.addfile(clean, f)
        require({'build_native.sh', 'CONTEXT.json', 'offline/debian/SQLITE-BUILD-LOCK.json'} <= names,
                'sqlite_fixed_context_required')
    target.chmod(0o600)
    return base.digest(target)


def prepare(old, patch, output, plan):
    rows = base.validate_package(old, plan['old_package_manifest_sha256'])
    require(rows['context.tar'] == plan['old_context_sha256'], 'context_manifest_binding')
    require(rows['verify_native_candidate.py'] == plan['old_verifier_sha256'], 'verifier_manifest_binding')
    require(patched_verifier((old/'verify_native_candidate.py').read_bytes()) ==
            (patch/'verify_native_candidate.py').read_bytes(), 'verifier_preservation')
    require(not output.exists(), 'fresh_output_required')
    output.mkdir(mode=0o700)
    package = output/'package'
    package.mkdir(mode=0o700)
    changed = {'context.tar', 'BUILD-PLAN.json', 'verify_native_candidate.py', 'LOCAL-VERIFICATION.json'}
    for name, expected in rows.items():
        if name in changed:
            continue
        with (old/name).open('rb') as inp, (package/name).open('xb') as out:
            shutil.copyfileobj(inp, out, 1024**2)
        (package/name).chmod(0o600)
        require(base.digest(package/name) == expected, 'copied_package_hash')
    context_sha = context_delta(old/'context.tar', package/'context.tar', patch, plan)
    original_plan = json.loads((old/'BUILD-PLAN.json').read_bytes())
    # Keep the preceding SQLite repair and exact live/base/toolchain identities.
    original_plan.update(context_sha256=context_sha, compiler_repair={
        'id':'dwarf4-fallback-20261016-r1', 'GOEXPERIMENT':'nodwarf5',
        'original_package_manifest_sha256':plan['old_package_manifest_sha256'],
        'source_and_dependencies_unchanged':True, 'all_existing_tests_retained':True})
    base.record(package/'BUILD-PLAN.json', original_plan)
    for name in ('verify_native_candidate.py', 'LOCAL-VERIFICATION.json'):
        base.write_new(package/name, (patch/name).read_bytes())
    base.write_new(package/'PACKAGE.SHA256', ''.join(base.digest(f)+'  '+f.name+'\n'
                   for f in sorted(package.iterdir())).encode('ascii'))
    base.validate_package(package)
    return {'repair_preparation':'PASS', 'build_root':str(output), 'package':str(package),
            'context_sha256':context_sha, 'package_manifest_sha256':base.digest(package/'PACKAGE.SHA256'),
            'compiler_fallback':'DWARF4', 'source_and_dependencies_unchanged':True,
            'original_evidence_modified':False, 'production_workloads_mutated':False,
            'host_system_packages_modified':False, 'database_changed':False, 'provider_requests':0,
            'images_imported':False, 'build_started':False, 'ready_for_rollout':False}


def confirm_failure(failed, log):
    base.private(failed)
    require(failed.stat().st_size < 65536, 'failure_receipt_size')
    previous = json.loads(failed.read_bytes())
    require(previous.get('stage') == 'compile-and-runtime-check' and previous.get('images_imported') is False
            and previous.get('production_writes') == 0, 'previous_failure_requires_review')
    base.private(log)
    require(log.stat().st_size < 16*1024**2, 'previous_log_size')
    raw = log.read_bytes()
    require(all(x in raw for x in (b'panic: runtime error: invalid memory address',
            b'PutLocationListDwarf5', b'github.com/elastic/go-elasticsearch/v8/typedapi/ml/gettrainedmodels')),
            'compiler_dwarf5_failure_not_confirmed')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--status', action='store_true')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
            'requires_root_datax01')
    os.umask(0o077)
    if a.status:
        base.STATE = STATE
        base.status()
        return
    require(a.output is not None and a.output.is_absolute() and a.output.name == 'rebuild', 'output_shape')
    base.private(a.output.parent, True)
    require(a.output.parent.parent == Path('/opt') and a.output.parent.name.startswith('weknora-glm-dwarf-fix-')
            and a.output.parent.stat().st_dev == Path('/opt').stat().st_dev != Path('/').stat().st_dev,
            'private_opt_build_parent_required')
    require(not os.path.lexists(str(STATE)), 'previous_attempt_exists_USE_STATUS')
    base.private(STATE.parent, True)
    base.validate_package(HERE)
    plan = json.loads((HERE/'FIX-PLAN.json').read_bytes())
    failed = OLD.parent/'build1/BUILD-FAILED.json'
    confirm_failure(failed, OLD.parent/'build1/05-compile-and-runtime-check.log')
    require(not (OLD.parent/'build1/BUILD-RESULT.json').exists(), 'previous_build_already_completed')
    v = os.statvfs(str(a.output.parent))
    require(v.f_bavail*v.f_frsize > max(20*base.GIB, v.f_blocks*v.f_frsize//5)+65*base.GIB, 'build_disk_reserve')
    STATE.mkdir(mode=0o700)
    base.record(STATE/'STARTED.json', {'build_root':str(a.output), 'old_failure_sha256':base.digest(failed),
                                    'production_writes':0, 'automatic_retry':False})
    try:
        result = prepare(OLD, HERE, a.output, plan)
        base.record(STATE/'PREPARED.json', result)
        print(json.dumps(result, sort_keys=True), flush=True)
    except Exception as exc:
        base.record(STATE/'FAILED.json', {'error_type':type(exc).__name__,
            'check':str(exc) if isinstance(exc, base.CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            'production_writes':0, 'automatic_retry':False})
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'repair_preparation':'UNCONFIRMED', 'error_type':type(exc).__name__,
            'check':str(exc) if isinstance(exc, base.CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            'production_writes':0, 'automatic_retry':False, 'instruction':'USE_STATUS_DO_NOT_REPEAT'}), flush=True)
        sys.exit(1)
