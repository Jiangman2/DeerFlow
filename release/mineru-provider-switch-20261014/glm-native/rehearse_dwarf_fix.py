"""Linux-root rehearsal using hash-locked historical inputs, never production."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repair_dwarf_build as r


def inventory(path):
    result = {}
    with tarfile.open(str(path), 'r|') as t:
        for member in t:
            h = hashlib.sha256()
            with t.extractfile(member) as f:
                for block in iter(lambda:f.read(1024**2), b''):
                    h.update(block)
            result[member.name] = h.hexdigest()
    return result


def copy_private(source, target):
    target.mkdir(mode=0o700)
    for f in source.iterdir():
        assert f.is_file() and not f.is_symlink()
        shutil.copyfile(f, target/f.name)
        (target/f.name).chmod(0o600)


def main():
    os.umask(0o077)
    root = Path('/root/dwarf-rehearsal')
    root.mkdir(mode=0o700)
    source = Path(sys.argv[1])
    copy_private(source/'original-package', root/'original')
    copy_private(source/'payload', root/'sqlite-patch')
    plan = json.loads((root/'sqlite-patch/FIX-PLAN.json').read_bytes())
    reconstructed = r.base.prepare(root/'original', root/'sqlite-patch', root/'sqlite-replay', plan)
    print(json.dumps({'previous_reconstruction':reconstructed}), flush=True)
    old = root/'sqlite-replay/package'
    patch = root/'patch'
    patch.mkdir(mode=0o700)
    with tarfile.open(str(old/'context.tar'), 'r:') as context:
        script = context.extractfile('build_native.sh').read()
    r.base.write_new(patch/'build_native.sh', r.patched_script(script))
    r.base.write_new(patch/'verify_native_candidate.py', r.patched_verifier((old/'verify_native_candidate.py').read_bytes()))
    r.base.record(patch/'LOCAL-VERIFICATION.json', {'local_tool_tests':'RUNNING'})
    newplan = {'old_context_sha256':r.base.digest(old/'context.tar'),
               'old_package_manifest_sha256':r.base.digest(old/'PACKAGE.SHA256'),
               'old_build_native_sha256':hashlib.sha256(script).hexdigest(),
               'old_verifier_sha256':r.base.digest(old/'verify_native_candidate.py')}
    result = r.prepare(old, patch, root/'fresh', newplan)
    before = inventory(old/'context.tar')
    after = inventory(root/'fresh/package/context.tar')
    assert set(before) == set(after)
    changes = sorted(k for k in before if before[k] != after[k])
    assert changes == ['CONTEXT.json','build_native.sh']
    modules = ('test_dwarf_build_fix','test_sqlite_build_fix','test_native_build','test_unpack_rust')
    suite = unittest.TestLoader().loadTestsFromNames(modules)
    with (root/'tests.txt').open('w') as stream:
        tests = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    assert tests.wasSuccessful() and not tests.skipped
    report = {'real_context_rehearsal':'PASS', 'local_tool_tests':'PASS', 'tests':tests.testsRun,
              'skipped':0, 'linux_root_permissions':'PASS_WITHOUT_MOCK_IN_REAL_REHEARSAL',
              'context_members':len(before), 'changed_context_members':changes,
              'source_and_all_offline_dependencies':'UNCHANGED',
              'reconstructed_previous_context_sha256':reconstructed['context_sha256'],
              'reconstructed_previous_manifest_sha256':reconstructed['package_manifest_sha256'],
              'fresh_context_sha256':result['context_sha256'], 'linux_arm64_full_build':'NOT_EXECUTED',
              'production_writes':0, 'provider_requests':0, 'ready_for_rollout':False}
    r.base.record(root/'VERIFICATION.json', report)
    print(json.dumps(report, sort_keys=True), flush=True)


if __name__ == '__main__': main()
