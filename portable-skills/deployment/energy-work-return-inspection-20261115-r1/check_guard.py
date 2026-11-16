"""Exercise the sealed inspector's Python 3.7 asset and wrong-host guard."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[3]
path=root/'release/energy-work-return-inspection-20261115-r1/inspect-energy-work-return-20261115-r1.py'
scope={'__name__':'guard_check'};exec(compile(path.read_bytes(),'<sealed-inspector>','exec'),scope)
item=scope['ASSET'];raw=base64.b64decode(item['base64'],validate=True)
assert len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256']
compile(raw,'<sealed-readonly-child>','exec')
sys.argv=['inspector','--inspect-readonly']
with contextlib.redirect_stdout(io.StringIO()) as capture:code=scope['main']()
row=json.loads(capture.getvalue().splitlines()[-1])
assert code==1 and row['error_code']=='DATAX01_ROOT_REQUIRED' and row['queries']==0
assert row['work_messages_sent']==row['provider_requests']==0
print(json.dumps({'status':'SEALED_CHILD_AND_WRONG_HOST_GUARD_PASSED','queries':0,'work_messages_sent':0,'provider_requests':0}))
