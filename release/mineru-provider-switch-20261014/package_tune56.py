"""Produce a standalone, dependency-hash-checked operator delivery."""
import ast
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parent
DEST = Path('C:/Users/15285/.codex/visualizations/2026/09/24/01a0d2d7-0015-7a73-93c3-791b713a47f6/weknora-tune56-20261016-r1-fix1')


def main():
    parts = ['#!/usr/bin/env python3\nimport base64 as _b, hashlib as _h, types as _t, sys as _s\n']
    hashes = {}
    for name in ('observe_pipeline', 'tuning_review', 'audit_weknora_process',
                 'audit_weknora_scheduling', 'weknora_tune_placement'):
        raw = (ROOT/(name+'.py')).read_bytes()
        h = hashlib.sha256(raw).hexdigest(); hashes[name+'.py'] = h
        if name == 'observe_pipeline':
            assert h == 'dd30806b5225824650d9fa9326a76878dad8af497e33d823732f892c267d5551'
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
    src = (ROOT/'tune_weknora56.py').read_bytes()
    hashes['tune_weknora56.py'] = hashlib.sha256(src).hexdigest()
    parts.append(src.decode('utf-8'))
    bundled = ''.join(parts).encode('utf-8')
    ast.parse(bundled.decode('utf-8'), feature_version=(3, 7))
    assert not DEST.exists(), 'delivery_must_be_new'
    DEST.mkdir(parents=True)
    name = 'weknora-tune56-20261016-r1-fix1.py'
    (DEST/name).write_bytes(bundled)
    (DEST/'RUNBOOK.txt').write_bytes((ROOT/'TUNE56-RUNBOOK.txt').read_bytes())
    manifest = {'script': name, 'sha256': hashlib.sha256(bundled).hexdigest(),
                'embedded_source_hashes': hashes, 'production_apply_performed_by_codex': False,
                'linux_signal_behavior': 'NOT_LOCALLY_TESTED_LIVE_SIX_POOL_GATE_REQUIRED',
                'unit_mock_tests_passed': 138, 'python37_grammar': 'PASS',
                'placement_authority': 'TWO_EXACT_BOUND_LOCAL_PV_NODE_AFFINITIES',
                'original_failure_preserved': True, 'same_image_config_only': True,
                'runtime_and_load_acceptance': 'PENDING_SERVER_EXECUTION'}
    (DEST/'VERIFICATION.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'directory': str(DEST), **manifest}))


if __name__ == '__main__':
    main()
