import base64
import hashlib
import json
from pathlib import Path
import re
import zlib

HERE = Path(__file__).resolve().parent
OLD = Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017/native-host-frontend-r1')
manifest = json.loads((OLD / 'ARTIFACT-MANIFEST.json').read_bytes())
for row in manifest['files']:
    raw = (OLD / row['path']).read_bytes()
    assert len(raw) == row['bytes'] and hashlib.sha256(raw).hexdigest() == row['sha256'], row['path']
names = ('BINDING.template.json','configmaps.template.json','host-pod.template.json','frontend-pod.template.json')
assets = {name:json.loads((OLD/name).read_bytes()) for name in names}
assets['SOURCE-EXPECTATIONS.json'] = json.loads((OLD/'frozen/SOURCE-EXPECTATIONS.json').read_bytes())
assets['FRONTEND-CHECK-NAMES'] = re.findall(r"check\('([^']+)'\)", (OLD/'frozen/http_qualification.cjs').read_text(encoding='utf-8'))
assert len(assets['FRONTEND-CHECK-NAMES']) == 14
raw = (json.dumps(assets,sort_keys=True,indent=2)+'\n').encode()
(HERE/'ASSETS.json').write_bytes(raw)
source = (HERE/'stage_template.py').read_text(encoding='utf-8')
source = source.replace('__ASSET_SHA__',hashlib.sha256(raw).hexdigest()).replace('__ASSET_Z64__',base64.b64encode(zlib.compress(raw,9)).decode())
compile(source,'host_frontend_stage.py','exec')
(HERE/'host_frontend_stage.py').write_bytes(source.encode())
print(json.dumps(dict(bytes=len(source.encode()),sha256=hashlib.sha256(source.encode()).hexdigest(),frozen_manifest_verified=True)))
