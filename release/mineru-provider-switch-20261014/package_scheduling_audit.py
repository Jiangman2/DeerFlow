"""Build an immutable standalone read-only audit, not a new apply package."""
import ast
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parent
DEST = Path('C:/Users/15285/.codex/visualizations/2026/09/24/01a0d2d7-0015-7a73-93c3-791b713a47f6/weknora-scheduling-audit-20261016-r1')


def main():
    dep = (ROOT/'observe_pipeline.py').read_bytes()
    h = hashlib.sha256(dep).hexdigest()
    assert h == 'dd30806b5225824650d9fa9326a76878dad8af497e33d823732f892c267d5551'
    source = (ROOT/'audit_weknora_scheduling.py').read_bytes()
    header = '''#!/usr/bin/env python3
import base64 as _b, hashlib as _h, types as _t, sys as _s
_raw = _b.b85decode(%r)
if _h.sha256(_raw).hexdigest() != %r:
    raise SystemExit('embedded_dependency_digest_mismatch')
_mod = _t.ModuleType('observe_pipeline')
_mod.__file__ = '<embedded_observe_pipeline>'
_s.modules['observe_pipeline'] = _mod
exec(compile(_raw, _mod.__file__, 'exec'), _mod.__dict__)
del _raw
''' % (base64.b85encode(dep), h)
    result = header.encode() + source
    for raw in (dep, source, result):
        ast.parse(raw.decode(), feature_version=(3, 7))
    assert not DEST.exists(), 'delivery_must_be_new'
    DEST.mkdir(parents=True)
    script = DEST/'weknora-scheduling-audit-20261016-r1.py'
    script.write_bytes(result)
    print(json.dumps({'script': str(script), 'sha256': hashlib.sha256(result).hexdigest(),
                      'python37_grammar': 'PASS', 'production_mutation': False}))


if __name__ == '__main__':
    main()
