"""Build one standalone stdlib script with pinned embedded observer module."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parent
DEST = Path('C:/Users/15285/.codex/visualizations/2026/09/24/01a0d2d7-0015-7a73-93c3-791b713a47f6/weknora-tuning-review-20261016-r1')
OBS_HASH = 'dd30806b5225824650d9fa9326a76878dad8af497e33d823732f892c267d5551'


def main():
    dependency = (ROOT / 'observe_pipeline.py').read_bytes()
    assert hashlib.sha256(dependency).hexdigest() == OBS_HASH
    main_source = (ROOT / 'tuning_review.py').read_text(encoding='utf-8')
    loader = '''#!/usr/bin/env python3
import base64 as _b, hashlib as _h, types as _t, sys as _s
_raw = _b.b85decode(%r)
if _h.sha256(_raw).hexdigest() != %r:
    raise SystemExit('embedded_dependency_digest_mismatch')
_mod = _t.ModuleType('observe_pipeline')
_mod.__file__ = '<pinned_observe_pipeline>'
exec(compile(_raw, _mod.__file__, 'exec'), _mod.__dict__)
_s.modules['observe_pipeline'] = _mod
del _raw
''' % (base64.b85encode(dependency), OBS_HASH)
    source = loader + main_source
    ast.parse(source, feature_version=(3, 7))
    assert not DEST.exists(), 'do_not_overwrite_delivery'
    DEST.mkdir(parents=True)
    filename = 'weknora-tuning-review-20261016-r1.py'
    raw = source.encode('utf-8')
    (DEST / filename).write_bytes(raw)
    (DEST / 'RUNBOOK.txt').write_bytes((ROOT / 'TUNING-REVIEW-RUNBOOK.txt').read_bytes())
    manifest = {filename: hashlib.sha256(raw).hexdigest(),
                'observer_embedded_sha256': OBS_HASH,
                'source_sha256': hashlib.sha256((ROOT / 'tuning_review.py').read_bytes()).hexdigest(),
                'ready_to_apply': False, 'production_writes': 0}
    (DEST / 'DELIVERY.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'directory': str(DEST), **manifest}))


if __name__ == '__main__':
    main()
