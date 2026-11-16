"""Exercise the complete qualifier/audit in the offline field-identical image.

Uses synthetic config and a local source inventory; production overlay identity
is enforced separately by the operator's fixed expected-source closure.
"""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('audit_builder',HERE/'build.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
HOST,CHILD,EXPECTED=builder.build()
host={'__name__':'audit_host'};exec(compile(HOST,'<host>','exec'),host)
child={'__name__':'audit_child'};exec(compile(CHILD,'<child>','exec'),child)
MODE=sys.argv[1] if len(sys.argv)>1 else 'qualify'
assert MODE in ('qualify','verify')
BASE=b'max_recursion_limit: 10000\nrun_ownership:\n  heartbeat_enabled: true\nscheduler:\n  enabled: true\n  multi_instance: false\n  max_concurrent_runs: 3\n  recursion_limit: 10000\n'
host.update(OLD_CONFIG_BYTES=len(BASE),OLD_CONFIG_SHA=host['sha'](BASE))
CANDIDATE=host['replace_scheduler'](BASE)
temporary=tempfile.TemporaryDirectory(prefix='scheduler-audit-full-')
root=Path(temporary.name)
config=root/'config.yaml'
config.write_bytes(BASE if MODE=='qualify' else CANDIDATE)
sources={}
for index,path in enumerate(child['SOURCE_PATHS']):
    target=Path(path)
    if not target.is_file():
        target=root/('synthetic-source-'+str(index)+'.py')
        target.write_bytes(b'# synthetic source fixture, not a production overlay\n')
    sources[str(target)]={'bytes':len(target.read_bytes()),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
child['SOURCE_PATHS']=tuple(sorted(sources))
child['CONFIG']=str(config)
raw=config.read_bytes()
payload={'mode':MODE,'sources':sources,'config':{'bytes':len(raw),'sha256':host['sha'](raw),
         'max_concurrent_runs':3 if MODE=='qualify' else 32,'multi_instance':MODE!='qualify'}}
if MODE=='qualify':
    payload['candidate']={'base64':base64.b64encode(CANDIDATE).decode(),'bytes':len(CANDIDATE),
                          'sha256':host['sha'](CANDIDATE),'max_concurrent_runs':32,'multi_instance':True}
sys.stdin=types.SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))
# main installs the production audit. No cleanup filesystem action is permitted
# after that point; let the local container's private tmpfs own fixture cleanup.
temporary._finalizer.detach()
code=child['main']()
assert code==0,'Full audited qualifier failed'
