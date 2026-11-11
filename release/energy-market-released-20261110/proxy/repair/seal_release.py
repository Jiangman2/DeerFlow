"""Seal the reviewed collector and dedicated fresh probe into the operator tool."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import re

root = Path(__file__).resolve().parent
path = root / 'repair-energy-market-proxy-20261110-r1.py'
text = path.read_text(encoding='utf-8')
payloads = {'CODE': root / 'energy_market_collector.py', 'FRESH_PROBE': root / 'fresh_target_probe.py'}
result = {}
for prefix, source in payloads.items():
    raw = source.read_bytes()
    ast.parse(raw)
    digest = hashlib.sha256(raw).hexdigest()
    for suffix, value in (('B64', base64.b64encode(raw).decode('ascii')), ('SHA256', digest)):
        text, count = re.subn(r'^' + prefix + '_' + suffix + r' = [^\r\n]+$',
                             prefix + '_' + suffix + ' = ' + repr(value), text, flags=re.M)
        assert count == 1
    result[source.name] = {'bytes': len(raw), 'sha256': digest}
ast.parse(text, feature_version=(3, 7))
path.write_bytes(text.encode('utf-8'))
raw = path.read_bytes()
result[path.name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
print(json.dumps(result, sort_keys=True))
