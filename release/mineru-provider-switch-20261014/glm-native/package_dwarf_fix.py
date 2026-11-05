"""Local publisher; reuses the exact uploaded SQLite-fixed offline inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

import repair_dwarf_build as r

HERE = Path(__file__).resolve().parent
OLD_MANIFEST = '98090a0149a9392f1e93d91adcaf91efaf6d5ab3d4d4ff058590c7cd769fe82e'
OLD_CONTEXT = 'a926799b004119a3cad49e54daf6f13e0a8097c481a8a70432e455089ba0d35b'
SQLITE_PATCH_MANIFEST = 'ff2912d42b4bc8e91898dd209740441b6165ae18d9462b29a61c6ee4aecadf30'


def pack(old, output, verification, compiler_probe):
    proof = json.loads(verification.read_bytes())
    probe = json.loads(compiler_probe.read_bytes())
    r.require(proof.get('local_tool_tests') == 'PASS' and proof.get('real_context_rehearsal') == 'PASS',
              'local_verification_required')
    r.require(probe.get('local_compiler_probe') == 'PASS' and
              probe.get('exact_previously_failing_dependency') == 'PASS' and
              probe.get('compiler', {}).get('GOEXPERIMENT') == 'nodwarf5', 'local_compiler_probe_required')
    # Derive from the previously delivered patch bytes, not a tar recreated by
    # another Python version. Server-generated package/context hashes remain
    # independently pinned in FIX-PLAN and must match on datax-01 before use.
    r.require(r.base.digest(old/'PACKAGE.SHA256') == SQLITE_PATCH_MANIFEST, 'sqlite_patch_manifest_binding')
    rows = r.base.manifest((old/'PACKAGE.SHA256').read_bytes())
    r.require(set(rows) | {'PACKAGE.SHA256'} == {f.name for f in old.iterdir()}, 'old_members')
    for name, digest in rows.items():
        r.require((old/name).is_file() and not (old/name).is_symlink() and
                  r.base.digest(old/name) == digest, 'old_file_hash:'+name)
    r.require(not output.exists(), 'fresh_local_output_required')
    output.mkdir(parents=True)
    payload = output/'payload'
    payload.mkdir()
    original_script = (old/'build_native.sh').read_bytes()
    for name in ('repair_dwarf_build.py', 'repair_sqlite_build.py', 'DWARF-FIX-RUNBOOK.txt'):
        r.base.write_new(payload/name, (HERE/name).read_bytes().replace(b'\r\n', b'\n'))
    r.base.write_new(payload/'build_native.sh', r.patched_script(original_script))
    r.base.write_new(payload/'verify_native_candidate.py', r.patched_verifier((old/'verify_native_candidate.py').read_bytes()))
    r.base.write_new(payload/'LOCAL-VERIFICATION.json', verification.read_bytes())
    r.base.write_new(payload/'LOCAL-COMPILER-PROBE.json', compiler_probe.read_bytes())
    r.base.record(payload/'FIX-PLAN.json', {'old_package_manifest_sha256':OLD_MANIFEST,
        'old_context_sha256':OLD_CONTEXT, 'old_build_native_sha256':hashlib.sha256(original_script).hexdigest(),
        'old_verifier_sha256':rows['verify_native_candidate.py'], 'scope':'BUILD_ONLY_DWARF4_FALLBACK',
        'derived_from_sqlite_patch_manifest_sha256':SQLITE_PATCH_MANIFEST,
        'source_changes':False, 'toolchain_change':False, 'database_change':False})
    r.base.write_new(payload/'PACKAGE.SHA256', ''.join(r.base.digest(f)+'  '+f.name+'\n'
                      for f in sorted(payload.iterdir())).encode('ascii'))
    archive = output/'weknora-glm-dwarf-build-fix-20261016-r1.tar.gz'
    with tarfile.open(str(archive), 'w:gz') as dest:
        for f in sorted(payload.iterdir()):
            r.base.add(dest, f.name, source=f)
    # Same isolated launcher and resource/baseline gates, new private operation.
    starter = (HERE/'START-sqlite-fix-template.sh').read_text(encoding='utf-8')
    starter = starter.replace('sqlite', 'dwarf')
    starter = starter.replace('@PATCH_SHA256@', r.base.digest(archive))
    r.base.write_new(output/'START-weknora-glm-dwarf-fix.sh', starter.encode())
    result = {'dwarf_fix_package':'READY_FOR_SERVER_PREFLIGHT', 'archive':str(archive),
        'archive_sha256':r.base.digest(archive), 'bytes':archive.stat().st_size,
        'starter':str(output/'START-weknora-glm-dwarf-fix.sh'),
        'starter_sha256':r.base.digest(output/'START-weknora-glm-dwarf-fix.sh'),
        'candidate_image_built':False, 'production_writes':0, 'ready_for_rollout':False}
    r.base.record(output/'PACKAGE-RESULT.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for arg in ('old', 'output', 'verification', 'compiler-probe'):
        p.add_argument('--'+arg, type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(pack(a.old, a.output, a.verification, a.compiler_probe), sort_keys=True))
