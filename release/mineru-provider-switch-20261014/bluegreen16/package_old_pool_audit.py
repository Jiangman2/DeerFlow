"""Generate one self-contained read-only audit script; never overwrite delivery."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).absolute().parent
NAME = 'mineru-old-pool-audit-20261016-r1-fix1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output).absolute()
    assert not out.exists(), 'never_replace_delivery'
    test = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'test_old_pool_audit', '-q'],
        cwd=str(HERE), capture_output=True, text=True, timeout=30)
    assert test.returncode == 0 and 'OK' in test.stderr, test.stderr
    raw = (HERE/'audit_old_pool.py').read_text(encoding='utf-8')
    probe = (HERE/'old_pool_probe.py').read_text(encoding='utf-8')
    assert raw.count("'__EMBED_OLD_POOL_PROBE__'") == 1
    raw = raw.replace("'__EMBED_OLD_POOL_PROBE__'", repr(probe))
    raw = raw.encode('utf-8')
    ast.parse(raw, feature_version=(3, 7))
    ast.parse(probe, feature_version=(3, 7))
    ns = {'__name__': 'packaged_audit', '__file__': str(out/(NAME+'.py'))}
    exec(compile(raw, '<packaged-audit>', 'exec'), ns)
    assert ns['PROBE'] == probe
    assert ns['SWITCH_SHA'] == '3b7eafbdfb507a1a68e2329912d1dfaab211b5daac4aa312294df41c4fe37847'
    out.mkdir(parents=True, exist_ok=False)
    script = out/(NAME+'.py')
    script.write_bytes(raw)
    (out/'RUNBOOK.txt').write_bytes((HERE/'OLD-POOL-AUDIT-RUNBOOK.txt').read_bytes())
    verification = dict(script_sha256=sha(raw), embedded_probe_sha256=sha(probe.encode()),
        tests=test.stderr.strip(), python37_grammar='PASS', local_runtime='Windows Python3.11',
        linux_runtime_acceptance='PENDING_OPERATOR_EXECUTION', production_writes=0,
        provider_requests=0, credentials_included=False, old_pool_retired=False,
        final16_expansion=False, prior_packages_modified=False)
    (out/'VERIFICATION.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(script=str(script), sha256=sha(raw), bytes=len(raw), verification=verification)))


if __name__ == '__main__':
    main()
