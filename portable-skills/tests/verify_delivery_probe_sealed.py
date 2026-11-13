"""Actual Python 3.7 parent/child compilation and zero-query host guard."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

target=Path(sys.argv[1]);raw=target.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='e0245793115640fb54eb12c838ddd599863a36faa66f71b062f31f9449e69235'
ns={'__name__':'sealed_delivery_fixture'};exec(compile(raw,str(target),'exec'),ns)
child=ns['assets']()
assert hashlib.sha256(child.encode()).hexdigest()=='569d28531a42ec286fd223fae4cf5c338eca01e70111375e30eaf40c8cfb5bfb'
assert ns['THREAD']=='545097ca-bd47-4416-888e-b6579229c865'
ns['ASSETS']['child']['sha256']='0'*64
try:
    ns['assets']()
    raise AssertionError('tampered asset accepted')
except ns['Refused']:
    pass
result=subprocess.run([sys.executable,'-I','-B',str(target),'--inspect-readonly'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8)
observed=json.loads(result.stdout)
assert result.returncode==1 and observed['status']=='INCOMPLETE'
assert observed['error_code']=='DATAX01_ROOT_REQUIRED' and observed['queries']==0
print(json.dumps({'python':sys.version.split()[0],'parent_and_combined_child_compile':True,'resource_fingerprints_verified':True,
    'fixed_thread_verified':True,'tamper_rejected':True,'wrong_host_zero_query_guard':True}))
