"""Verify the exact delivered packet in a disposable Linux container, no starter execution."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

os.umask(0o077)
source=Path(sys.argv[1]); receipt=json.loads((source/'PACKAGE-RESULT.json').read_bytes())
archive=source/'weknora-glm-runtime-finish-20261016-r1.tar.gz'
starter=source/'START-weknora-glm-runtime-finish.sh'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==receipt['archive_sha256']
assert hashlib.sha256(starter.read_bytes()).hexdigest()==receipt['starter_sha256']
root=Path('/root/verified-runtime-packet'); root.mkdir(mode=0o700)
seen=set()
with tarfile.open(archive,'r:gz') as t:
    for m in t:
        assert m.isfile() and not m.linkname and not m.issparse() and m.name not in seen
        assert re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]+',m.name) and 0<m.size<4*1024**2 and len(seen)<20
        seen.add(m.name)
        with (root/m.name).open('xb') as f: f.write(t.extractfile(m).read())
sys.path.insert(0,str(root))
import prepare_runtime_finish as r
r.safe.validate_package(root)
assert r.OLD==Path('/opt/weknora-glm-dwarf-fix-qYPHEQjp/rebuild')
assert r.ERROR_HASH=='dbe87c354a439f7f644f179f0905c624c0677fee22eec75526a660d41fd07071'
assert r.STATE==Path('/var/lib/weknora-glm-runtime-finish-20261016-r1')
subprocess.run(['bash','-n',str(starter)],check=True)
subprocess.run([sys.executable,'-I','-B',str(root/'prepare_runtime_finish.py'),'--help'],
               stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
text=starter.read_text()
assert './build_weknora.py' not in text and text.count('./finish_runtime_build.py')==2
assert receipt['archive_sha256'] in text and 'prepare_runtime_finish.py --status' in text
print(json.dumps({'exact_delivered_packet':'PASS','archive_sha256':receipt['archive_sha256'],
                  'starter_sha256':receipt['starter_sha256'],'starter_executed':False,
                  'production_writes':0,'provider_requests':0}))
