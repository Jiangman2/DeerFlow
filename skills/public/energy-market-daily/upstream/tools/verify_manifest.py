"""Verify all immutable delivery files; generated outputs are excluded."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'MANIFEST.sha256.json').read_text(encoding='utf-8'))
bad=[]
for name,expected in manifest.items():
    p=ROOT/name
    if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:bad.append(name)
if bad:raise SystemExit('Mismatches: '+', '.join(bad))
print(f'PASS: {len(manifest)} files verified')
