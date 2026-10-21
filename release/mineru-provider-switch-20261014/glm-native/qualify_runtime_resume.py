"""Local Linux-only tool regression receipt, not a native image acceptance."""
import hashlib
import json
from pathlib import Path
import unittest


def main():
    root = Path(__file__).resolve().parent
    out = Path('/out')
    suite = unittest.defaultTestLoader.loadTestsFromNames(('test_runtime_resume', 'test_runtime_finish',
        'test_dwarf_build_fix', 'test_sqlite_build_fix', 'test_native_build', 'test_unpack_rust'))
    with (out/'tests.txt').open('x') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    assert result.wasSuccessful() and result.testsRun == 66 and not result.skipped
    raw = (root/'resume_runtime_finish_once.py').read_bytes().replace(b'\r\n', b'\n')
    proof = {'local_runtime_resume_tests': 'PASS', 'tests': result.testsRun, 'skipped': len(result.skipped),
             'linux_root_private_fixture': True, 'delivered_original_patch_verified': True,
             'script_sha256': hashlib.sha256(raw).hexdigest(), 'native_candidate': 'NOT_TESTED',
             'production_writes': 0, 'provider_requests': 0, 'ready_for_rollout': False}
    with (out/'LOCAL-VERIFICATION.json').open('x') as f:
        json.dump(proof, f, indent=2); f.write('\n')
    print(json.dumps(proof), flush=True)


if __name__ == '__main__':
    main()
