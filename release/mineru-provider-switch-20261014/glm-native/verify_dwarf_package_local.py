"""Validate the exact published packet in Linux without executing its starter."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

os.umask(0o077)
root = Path('/root/published-packet')
root.mkdir(mode=0o700)
archive = Path(sys.argv[1])
assert hashlib.sha256(archive.read_bytes()).hexdigest() == sys.argv[2]
seen = set()
with tarfile.open(str(archive), 'r|gz') as source:
    for m in source:
        assert m.isfile() and not m.linkname and not m.issparse() and m.name not in seen
        assert re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]+', m.name)
        assert 0 < m.size < 4*1024**2 and len(seen) < 20
        seen.add(m.name)
        with (root/m.name).open('xb') as dest:
            dest.write(source.extractfile(m).read())
        (root/m.name).chmod(0o600)
sys.path.insert(0, str(root))
import repair_dwarf_build as repair
repair.base.validate_package(root)
subprocess.run(['bash', '-n', str(root/'build_native.sh')], check=True)
subprocess.run(['bash', '-n', sys.argv[3]], check=True)
subprocess.run([sys.executable, '-I', '-B', str(root/'repair_dwarf_build.py'), '--help'], check=True,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
plan = json.loads((root/'FIX-PLAN.json').read_bytes())
assert plan['old_package_manifest_sha256'] == '98090a0149a9392f1e93d91adcaf91efaf6d5ab3d4d4ff058590c7cd769fe82e'
assert plan['old_context_sha256'] == 'a926799b004119a3cad49e54daf6f13e0a8097c481a8a70432e455089ba0d35b'
assert repair.OLD == Path('/opt/weknora-glm-sqlite-fix-BDPsrLAi/rebuild/package')
print(json.dumps({'published_packet':'PASS', 'members':len(seen), 'private_package_hashes':'PASS',
                  'bash_syntax':'PASS', 'isolated_python_entrypoint':'PASS', 'starter_executed':False,
                  'production_writes':0, 'provider_requests':0}))
