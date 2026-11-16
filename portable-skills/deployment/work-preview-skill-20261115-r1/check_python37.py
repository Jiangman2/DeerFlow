"""Check the installer's exact sealed resources and wrong-host guard on 3.7."""
import ast
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[3]
path=root/'release/energy-work-preview-skill-20261115-r2/install-energy-work-preview-skill-20261115-r2.py'
scope={'__name__':'preview_python37_guard'};exec(compile(path.read_bytes(),'<sealed>','exec'),scope)
assets=scope['load_assets']();assert len(assets)==4
sys.argv=['installer','--install-work-preview-skill-once']
with contextlib.redirect_stdout(io.StringIO()) as capture:status=scope['main']()
row=json.loads(capture.getvalue().splitlines()[-1])
assert status==1 and row['error_code']=='DATAX01_ROOT_REQUIRED' and row['commands']==0
assert row['skill_updated'] is False and row['gateway_modified'] is False
print(json.dumps({'status':'PYTHON37_SEALED_ASSETS_AND_WRONG_HOST_GUARD_PASSED','assets':4,'commands':0,'skill_modified':False,'gateway_modified':False}))
