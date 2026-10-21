"""Bind reviewed helper bytes into the one-file operator controller."""
import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE=Path(__file__).resolve().parent
PREP=HERE.parent
MERGE=Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017')
files={
    'network_probe.py':MERGE/'native-network-proof-r1/network_probe.py',
    'TEMPLATE.json':MERGE/'native-network-proof-r1/TEMPLATE.json',
    'host_frontend_stage.py':PREP/'native-runtime-host-frontend-r1/host_frontend_stage.py',
    'db_stage.py':PREP/'native-runtime-qualification-r1/db_stage.py',
}
p={name:path.read_bytes().decode('utf-8') for name,path in files.items()}
p['host_runtime_inputs']=json.loads((PREP/'native-preload-complete-evidence-r1/HOST-RUNTIME-INPUTS.json').read_bytes())
p['postgres_cache']=json.loads((PREP/'native-preload-complete-evidence-r1/POSTGRES-CACHE.json').read_bytes())
p['preload_report']=dict(bytes=24125,sha256='99135299a8615bcadc90af60ab6e3332665e9714826b847d9a1f5e48c426688a')
raw=json.dumps(p,sort_keys=True,separators=(',',':')).encode()
source=(HERE/'controller_template.py').read_text(encoding='utf-8')
source=source.replace('__PAYLOAD_SHA__',hashlib.sha256(raw).hexdigest()).replace('__PAYLOAD_Z64__',base64.b64encode(zlib.compress(raw,9)).decode())
compile(source,'run_native.py','exec')
output=source.encode()
(HERE/'run_native.py').write_bytes(output)
manifest=dict(schema='portal-ppt-native-controller-build.v1',script=dict(path='run_native.py',bytes=len(output),sha256=hashlib.sha256(output).hexdigest()),
              payload_sha256=hashlib.sha256(raw).hexdigest(),sources={name:dict(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for name,path in files.items()},
              remote_execution=False,ready_for_rollout=False)
(HERE/'BUILD.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(manifest,sort_keys=True))
