"""Build a new self-contained delivery; never overwrite a prior release."""
import ast
import base64
import hashlib
import importlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parent
DEST = Path('C:/Users/15285/.codex/visualizations/2026/09/24/01a0d2d7-0015-7a73-93c3-791b713a47f6/weknora-tune88-20261016-r1')
NAME = 'weknora-tune88-20261016-r1.py'
LAUNCHER = 'START-weknora88-datax01.sh'
PINS = {
    'observe_pipeline': 'dd30806b5225824650d9fa9326a76878dad8af497e33d823732f892c267d5551',
    'tuning_review': 'e3970fee2abd13dfeac14e4aea51207eda191fea3a60d4fd0d3577c81be15f5a',
    'audit_weknora_process': '3f4a18e191c37396c63ffe8609529170e505242b9a851763536f9c12a565d059',
    'audit_weknora_scheduling': 'ffa7f6a2dcc8ea3c6091d9608f48e38b6a5f1a275aa72e750a613cd9051dd9da',
    'weknora_tune_placement': 'aa31560ce648e0ec7e2fd68bfe1799b3fd3c757afa1369b40a4cbae6118449d9',
}
MODULES = tuple(PINS) + ('tune_weknora88', 'observe_tune88')
TESTS = ('test_tune_weknora88', 'test_tune88_contract', 'test_observe_tune88',
         'test_tune_weknora56', 'test_weknora_tune_placement', 'test_audit_weknora_scheduling',
         'test_audit_weknora_process', 'test_tuning_review', 'test_observe_pipeline', 'test_package_tune88')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def bundle():
    parts = ['#!/usr/bin/env python3\nimport base64 as _b, hashlib as _h, types as _t, sys as _s\n']
    hashes = {}
    for name in MODULES:
        raw = (ROOT/(name+'.py')).read_bytes()
        h = digest(raw); hashes[name+'.py'] = h
        if name in PINS and h != PINS[name]:
            raise RuntimeError('immutable_dependency_changed:'+name)
        ast.parse(raw.decode('utf-8'), feature_version=(3, 7))
        parts.append('''_raw = _b.b85decode(%r)
if _h.sha256(_raw).hexdigest() != %r:
    raise SystemExit('embedded_dependency_digest_mismatch')
_mod = _t.ModuleType(%r)
_mod.__file__ = %r
_s.modules[%r] = _mod
exec(compile(_raw, _mod.__file__, 'exec'), _mod.__dict__)
del _raw
''' % (base64.b85encode(raw), h, name, '<embedded_'+name+'>', name))
    # The observer imports the same engine instance; neither starts on import.
    parts.append('''if __name__ == '__main__':
    try:
        _s.exit(_s.modules['tune_weknora88'].main())
    except KeyboardInterrupt:
        _s.modules['observe_pipeline'].emit({'configuration_switch': 'UNCONFIRMED',
            'failure': {'check': 'OPERATOR_INTERRUPTED', 'raw_error_printed': False},
            'instruction': 'USE_STATUS_DO_NOT_REPEAT', 'automatic_retry': False})
        _s.exit(130)
    except Exception as _exc:
        _s.modules['observe_pipeline'].emit({'configuration_switch': 'UNCONFIRMED',
            'failure': _s.modules['observe_pipeline'].failure(_exc),
            'instruction': 'USE_STATUS_DO_NOT_REPEAT', 'automatic_retry': False})
        _s.exit(1)
''')
    raw = ''.join(parts).encode('utf-8')
    ast.parse(raw.decode('utf-8'), feature_version=(3, 7))
    return raw, hashes


def launcher(script_sha):
    if not re.fullmatch('[0-9a-f]{64}', script_sha):
        raise ValueError('invalid_script_digest')
    return ('''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
test "$(id -u)" -eq 0
test "$(hostname -s)" = datax-01
cd /home/cnooc
SCRIPT=SCRIPT_NAME
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-tune88-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/tune.py"
test "$(sha256sum "$CONTROL/tune.py" | awk '{print $1}')" = SCRIPT_SHA
printf 'tuning_tool=%s\n' "$CONTROL/tune.py"
printf 'status_command=sudo python3 -I -B %s --status\n' "$CONTROL/tune.py"
set +e
python3 -I -B -u "$CONTROL/tune.py" --apply 2>&1 | tee "$CONTROL/tune.log"
RESULT=("${PIPESTATUS[@]}")
set -e
printf 'tune_exit_code=%s tee_exit_code=%s\n' "${RESULT[0]}" "${RESULT[1]}"
printf 'tune_log=%s\n' "$CONTROL/tune.log"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
printf 'next=READ_ONLY_OBSERVATION_21_SAMPLES_NO_AUTOMATIC_ROLLBACK\n'
set +e
python3 -I -B -u "$CONTROL/tune.py" --observe 2>&1 | tee "$CONTROL/observe.log"
OBS_RESULT=("${PIPESTATUS[@]}")
set -e
printf 'observe_exit_code=%s tee_exit_code=%s\n' "${OBS_RESULT[0]}" "${OBS_RESULT[1]}"
printf 'observe_log=%s\n' "$CONTROL/observe.log"
printf 'after_any_failure=USE_PRINTED_STATUS_COMMAND_DO_NOT_REPEAT_START_OR_APPLY\n'
test "${OBS_RESULT[0]}" -eq 0
test "${OBS_RESULT[1]}" -eq 0
'''.replace('SCRIPT_NAME', NAME).replace('SCRIPT_SHA', script_sha)).encode('utf-8')


def validate_shell_and_remote(shell, directory):
    bash = Path('C:/Program Files/Git/bin/bash.exe')
    if not bash.is_file():
        raise RuntimeError('git_bash_required_for_shell_syntax_check')
    checked = []
    for name, code in [('launcher', shell.decode())] + [
        ('redis', importlib.import_module('observe_pipeline').REDIS_SHELL),
        ('db', importlib.import_module('tuning_review').DB_SHELL),
        ('process', importlib.import_module('tuning_review').PROCESS_SHELL),
    ]:
        path = directory/(name+'.sh'); path.write_bytes(code.encode())
        subprocess.run([str(bash), '-n', str(path)], check=True, capture_output=True)
        checked.append(name)
    for name, key in [('observe_pipeline', 'HEALTH_CODE'), ('audit_weknora_process', 'REMOTE'),
                      ('tune_weknora88', 'SIGNAL_CODE')]:
        ast.parse(getattr(importlib.import_module(name), key), feature_version=(3, 7))
    return checked


def main():
    if DEST.exists():
        raise RuntimeError('delivery_must_be_new')
    old_hash = digest((ROOT/'tune_weknora56.py').read_bytes())
    if old_hash != '782b8c72d3cce7638525e1ea40f9f49fb3068fd46273cb099cab930879d55e6c':
        raise RuntimeError('previous_engine_changed')
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromNames(TESTS))
    if not result.wasSuccessful():
        raise RuntimeError('qualification_failed_no_delivery')
    raw, hashes = bundle(); script_sha = digest(raw); sh = launcher(script_sha)
    with tempfile.TemporaryDirectory(prefix='weknora88-package-') as tmp:
        checked = validate_shell_and_remote(sh, Path(tmp))
        script = Path(tmp)/NAME; script.write_bytes(raw)
        # On Windows this must refuse before creating state or starting kubectl.
        if sys.platform != 'win32':
            raise RuntimeError('wrong_host_smoke_is_windows_specific')
        smoke = subprocess.run([sys.executable, '-I', '-B', str(script), '--status'],
                               capture_output=True, timeout=15)
        receipt = json.loads(smoke.stdout)
        if (smoke.returncode != 1 or smoke.stderr or receipt.get('failure', {}).get('check')
                != 'run_on_datax01_as_root'):
            raise RuntimeError('standalone_wrong_host_gate_failed')
    manifest = {
        'script': NAME, 'sha256': script_sha, 'launcher': LAUNCHER, 'launcher_sha256': digest(sh),
        'embedded_source_hashes': hashes, 'previous56_engine_sha256_unchanged': old_hash,
        'unit_mock_tests_passed': result.testsRun, 'unit_mock_platform': sys.platform,
        'python37_grammar': 'PASS', 'embedded_python37_grammar': 'PASS',
        'shell_syntax_checked': checked, 'standalone_wrong_host_refusal': 'PASS',
        'linux_stop_drain_behavior': 'LIVE_SIX_POOL_GATE_REQUIRED_NOT_LOCALLY_EXECUTED',
        'target_pools': {'core':16, 'postprocess':4, 'enrichment':48, 'maintenance':4, 'shared':4, 'wiki':12},
        'target_slots':88, 'per_background_model_default':128, 'mineru_slots':16,
        'production_apply_performed_by_codex':False, 'database_migration':False, 'image_change':False,
        'runtime_and_throughput_acceptance':'PENDING_SERVER_EXECUTION',
    }
    DEST.mkdir(parents=True)
    files = {NAME:raw, LAUNCHER:sh, 'RUNBOOK.txt':(ROOT/'TUNE88-RUNBOOK.txt').read_bytes(),
             'VERIFICATION.json':(json.dumps(manifest, indent=2)+'\n').encode()}
    for name, content in files.items():
        with (DEST/name).open('xb') as out:
            out.write(content)
    with (DEST/'PACKAGE.SHA256').open('x', encoding='ascii', newline='\n') as out:
        out.write(''.join(digest(data)+'  '+name+'\n' for name,data in files.items()))
    for name, data in files.items():
        if (DEST/name).read_bytes() != data:
            raise RuntimeError('delivery_readback_failed')
    print(json.dumps({'delivery':str(DEST), **manifest}))


if __name__ == '__main__':
    main()
