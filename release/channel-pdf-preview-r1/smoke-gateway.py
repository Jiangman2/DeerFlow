"""Real native PDFium gate, run inside the candidate ARM64 Gateway image."""
import base64
import json
import tempfile
from pathlib import Path

from fastapi import HTTPException
from app.gateway.file_security import pdf_file_requires_attachment
from app.gateway.routers.artifacts import _validate_pdf_preview_artifact, _render_pdf_preview_page
from app.gateway.routers.channel_connections import _connect_instruction, _PROVIDER_META

fixtures = json.loads(Path(__file__).with_name('compressed-fixtures.json').read_text())
with tempfile.TemporaryDirectory(prefix='channel-pdf-smoke-') as directory:
    root = Path(directory)
    for name, data in fixtures.items():
        payload = base64.b64decode(data)
        target = root / (name + '.pdf')
        target.write_bytes(payload)
        if name == 'normal':
            checked = _validate_pdf_preview_artifact(target, 'mnt/user-data/outputs/normal.pdf')
            assert _render_pdf_preview_page(checked, 0).startswith(b'\x89PNG\r\n\x1a\n')
            assert pdf_file_requires_attachment(target)
            try:
                _validate_pdf_preview_artifact(target, 'mnt/user-data/uploads/normal.pdf')
            except HTTPException as error:
                assert error.status_code == 400
            else:
                raise AssertionError('uploaded PDF bypassed boundary')
        else:
            try:
                _validate_pdf_preview_artifact(target, 'mnt/user-data/outputs/' + name + '.pdf')
            except HTTPException as error:
                assert error.status_code == 400
            else:
                raise AssertionError('unsafe fixture accepted: ' + name)
        assert target.read_bytes() == payload
assert _PROVIDER_META['feishu']['display_name'] == '海能 Work'
message = _connect_instruction('feishu', 'TEST-CODE')
assert '中国海油通用智能体' in message and '海能 Work' in message
assert '/connect TEST-CODE' in message and 'DeerFlow' not in message
print('channel_pdf_gateway_native_smoke=PASS positive=1 negative=5 original_unchanged=yes')
