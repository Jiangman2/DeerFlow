import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE=Path(__file__).resolve().parent
source=(HERE/'link_guard_template.py').read_text(encoding='utf-8')
for name,prefix in [('guard_kernel.py','GUARD'),('preexec.sh','WRAPPER')]:
    raw=(HERE/name).read_bytes()
    source=source.replace('__'+prefix+'_Z64__',base64.b64encode(zlib.compress(raw,9)).decode())
    source=source.replace('__'+prefix+('_CODE_SHA__' if prefix=='GUARD' else '_SHA__'),hashlib.sha256(raw).hexdigest())
compile(source,'link_guard.py','exec')
raw=source.encode();(HERE/'link_guard.py').write_bytes(raw)
print(json.dumps(dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())))
