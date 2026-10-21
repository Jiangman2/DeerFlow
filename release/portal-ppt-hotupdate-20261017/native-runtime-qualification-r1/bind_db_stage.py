import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE = Path(__file__).resolve().parent
SOURCE = Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017/native-db-design-r2')
preparation = json.loads((SOURCE / 'PREPARATION.json').read_bytes())
bundle = {}
for name in ('POD-TEMPLATE.json', 'probe_supervisor.py', 'bootstrap_probe.py', 'RECEIPT-CONTRACT.json'):
    raw = (SOURCE / name).read_bytes(); expected = preparation['files'][name]
    assert len(raw) == expected['bytes'] and hashlib.sha256(raw).hexdigest() == expected['sha256']
    bundle[name] = dict(expected, base64=base64.b64encode(raw).decode())
pg = json.loads((HERE.parent / 'native-preload-complete-evidence-r1/POSTGRES-CACHE.json').read_bytes())
report = (HERE.parent / 'native-preload-complete-evidence-r1/REPORT.json').read_bytes()
assert len(report) == 24125 and hashlib.sha256(report).hexdigest() == '99135299a8615bcadc90af60ab6e3332665e9714826b847d9a1f5e48c426688a'
assert json.loads(report)['postgres_cache'] == pg
contract = json.loads((SOURCE / 'RECEIPT-CONTRACT.json').read_bytes())
identities = {step['label']: step['image'] for step in contract['steps']}
identities['final-receipt'] = contract['steps'][0]['image']
pg_identity = {key: pg[key] for key in ('reference', 'target_digest', 'manifest_digest', 'config_digest', 'platform')}
identities['postgres-fixture'] = pg_identity; identities['pg-profile'] = pg_identity
wire = json.dumps(bundle, sort_keys=True, separators=(',', ':')).encode()
template = (HERE / 'db_stage_template.py').read_text(encoding='utf-8')
replacements = {'__ASSET_Z64__': base64.b64encode(zlib.compress(wire, 9)).decode(), '__ASSET_SHA__': hashlib.sha256(wire).hexdigest(), '__PG_CACHE__': repr(pg), '__CONTAINER_IDENTITIES__': repr(identities)}
for old, new in replacements.items():
    assert template.count(old) == 1
    template = template.replace(old, new)
raw = template.encode(); compile(raw, 'db_stage.py', 'exec')
(HERE / 'db_stage.py').write_bytes(raw)
print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())))
