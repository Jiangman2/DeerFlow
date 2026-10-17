"""Record one final local controller fixture run; no remote execution."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
image='sha256:1353c9a1723ad9a8f05be830d951311623e153488500b1e22b3bcb059e676630'
argv=['docker','--context','desktop-linux','run','--rm','--pull','never','--network','none','--read-only',
      '--cpus','1','--memory','256m','--pids-limit','128','--tmpfs','/tmp:rw,mode=700','--mount',
      'type=bind,src='+str(HERE).replace('\\','/')+',dst=/work,readonly',image,'python','-I','-B','/work/test_priority.py']
started=time.monotonic()
result=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
for name,raw in [('LOCAL-FINAL.stdout.txt',result.stdout),('LOCAL-FINAL.stderr.txt',result.stderr)]:
    (HERE/name).write_bytes(raw)
record=dict(schema='portal-native-controller-local-test-execution.v1',status='PASS' if result.returncode==0 else 'FAIL',
            returncode=result.returncode,elapsed_seconds=round(time.monotonic()-started,3),argv=argv,
            script=dict(bytes=(HERE/'run_native.py').stat().st_size,sha256=hashlib.sha256((HERE/'run_native.py').read_bytes()).hexdigest()),
            test=dict(bytes=(HERE/'test_priority.py').stat().st_size,sha256=hashlib.sha256((HERE/'test_priority.py').read_bytes()).hexdigest()),
            stdout=dict(bytes=len(result.stdout),sha256=hashlib.sha256(result.stdout).hexdigest()),
            stderr=dict(bytes=len(result.stderr),sha256=hashlib.sha256(result.stderr).hexdigest()),
            remote_execution=False,production_access=False,model_access=False)
(HERE/'LOCAL-FINAL-EXECUTION.json').write_text(json.dumps(record,sort_keys=True,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(record,sort_keys=True))
print(result.stderr.decode(),end='')
sys.exit(result.returncode)
