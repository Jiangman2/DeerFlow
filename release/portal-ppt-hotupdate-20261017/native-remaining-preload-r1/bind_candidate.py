"""Build local delivery candidate from exact reviewed supervisor and accepted success evidence."""
import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE = Path(__file__).resolve().parent
supervisor = (HERE.parent / 'native-gateway-recovery-r1/recover_gateway.py').read_bytes()
report = (HERE.parent / 'native-gateway-recovery-evidence-r1/REPORT.json').read_bytes()
assert hashlib.sha256(supervisor).hexdigest() == '13b18107c4be2018d74e6b5d4fea00ab36b46ff091a4a601958da593dfbb5cec'
assert len(report) == 6504 and hashlib.sha256(report).hexdigest() == 'c0732346dcb41b9a979984b1036bf67c05ce314c030551246f5c7ec3f20ab662'
data = json.loads(report)
assert data['status'] == 'GATEWAY_COMPLETE_REVIEW_REQUIRED'
source = (HERE / 'continue_remaining_template.py').read_text(encoding='utf-8')
for old, new in {'__SUPERVISOR_Z64__': base64.b64encode(zlib.compress(supervisor, 9)).decode(),
                 '__SUCCESS_REPORT_SHA__': hashlib.sha256(report).hexdigest(),
                 '__SUCCESS_EVIDENCE__': data['evidence']}.items():
    assert source.count(old) == 1
    source = source.replace(old, new)
raw = source.encode()
compile(source, 'resume_remaining.py', 'exec')
(HERE / 'resume_remaining.py').write_bytes(raw)
print(json.dumps(dict(path=str(HERE / 'resume_remaining.py'), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())))
