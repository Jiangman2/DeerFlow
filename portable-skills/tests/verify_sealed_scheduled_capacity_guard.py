"""Run against the sealed operator with the actual Python 3.7 interpreter."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

target=Path(sys.argv[1])
raw=target.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='5bbc42c697077d0adf71a2f716e8c45392d135d45430203dd437dbf92e560b1d'
ns={'__name__':'fixed_sealed_guard_fixture'}
exec(compile(raw,str(target),'exec'),ns)
child=ns['assets']()
assert hashlib.sha256(child.encode()).hexdigest()=='688dfe86d9eb5167450403135712efe3575f9a42b84fefcd6f0b92f42e4b5566'
assert ns['FIXED_TASK_IDENTIFIER']=='887ea9f6-1347-4f0f-ab6d-1295ae3ead49'
ns['ASSETS']['child']['sha256']='0'*64
try:
    ns['assets']()
    raise AssertionError('tampered asset accepted')
except ns['Refused']:
    pass
result=subprocess.run([sys.executable,'-I','-B',str(target),'--inspect-scheduled-run-and-capacity'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8)
observed=json.loads(result.stdout)
assert result.returncode==1 and observed['status']=='INCOMPLETE'
assert observed['error_code']=='DATAX01_ROOT_REQUIRED' and observed['queries']==0
print(json.dumps({'python':sys.version.split()[0],'sealed_parent_and_child_compile':True,'resource_sha256_verified':True,
    'fixed_identifier_verified':True,'tamper_rejected':True,'wrong_host_rejected_before_queries':True,'queries':observed['queries']}))
