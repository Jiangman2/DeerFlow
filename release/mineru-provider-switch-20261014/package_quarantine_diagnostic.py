"""Create a new immutable delivery from the tested diagnostic source."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

import diagnose_mineru_quarantine as diag

ROOT = Path(__file__).parent
DEST = Path('C:/Users/15285/.codex/visualizations/2026/09/24/'
            '01a0d2d7-0015-7a73-93c3-791b713a47f6/mineru-quarantine-audit-20261016-r1')
NAME = 'mineru-quarantine-audit-20261016-r1.py'


def main():
    if DEST.exists():
        raise RuntimeError('delivery_already_exists')
    result = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromName('test_diagnose_mineru_quarantine'))
    if not result.wasSuccessful():
        raise RuntimeError('tests_failed_no_delivery')
    raw = (ROOT/'diagnose_mineru_quarantine.py').read_bytes()
    ast.parse(raw.decode(), feature_version=(3,7))
    smoke = subprocess.run([sys.executable, '-I', '-B', str(ROOT/'diagnose_mineru_quarantine.py')],
                           timeout=15, capture_output=True)
    receipt = json.loads(smoke.stdout)
    if smoke.returncode != 1 or smoke.stderr or receipt.get('failed_check') != 'run_on_datax01_as_root':
        raise RuntimeError('wrong_host_smoke_failed')
    artifact = DEST.parent/'weknora-tune88-20261016-r1'/'weknora-tune88-20261016-r1.py'
    # Verify importing the immutable bundle under a non-main name performs no actions.
    import_probe = ('from pathlib import Path; import hashlib,sys; p=Path(sys.argv[1]); '
                    'b=p.read_bytes(); assert hashlib.sha256(b).hexdigest()==sys.argv[2]; '
                    "exec(compile(b,str(p),'exec'),{'__name__':'diagnostic_import','__file__':str(p)}); "
                    "print('import_only=PASS')")
    imported = subprocess.run([sys.executable, '-I', '-B', '-c', import_probe, str(artifact), diag.TOOL_SHA],
                              timeout=15, capture_output=True)
    if imported.returncode != 0 or imported.stderr or imported.stdout.strip() != b'import_only=PASS':
        raise RuntimeError('immutable_bundle_import_failed')
    verification = {'unit_mock_tests':result.testsRun, 'python37_syntax':'PASS',
                    'wrong_host_refusal':'PASS', 'immutable_helper_import_without_main':'PASS',
                    'production_executed':False, 'scope':'READ_ONLY_CLUSTER_NEW_LOCAL_EVIDENCE',
                    'script_sha256':hashlib.sha256(raw).hexdigest(), 'helper_sha256':diag.TOOL_SHA}
    files = {NAME:raw, 'RUNBOOK.txt':(ROOT/'QUARANTINE-DIAGNOSTIC-RUNBOOK.txt').read_bytes(),
             'VERIFICATION.json':(json.dumps(verification,indent=2)+'\n').encode()}
    DEST.mkdir()
    for name, data in files.items():
        with (DEST/name).open('xb') as f:
            f.write(data)
        if (DEST/name).read_bytes() != data:
            raise RuntimeError('artifact_readback_failed')
    print(json.dumps({'delivery':str(DEST), 'script':NAME, **verification}))


if __name__ == '__main__':
    main()
