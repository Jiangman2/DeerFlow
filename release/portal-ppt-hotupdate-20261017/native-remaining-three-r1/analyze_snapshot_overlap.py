import hashlib
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
PROFILE = json.loads((HERE.parent / 'native-preload-r1/PROFILE.json').read_bytes())
ARCHIVES = Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017/backend-bound-build-r1/remainder-r3')
WANTED = {'sha256:7d930ba7368a4920a00cb8f22d4e6e3f031b82677781b91468a22b7fe4d95f3e',
          'sha256:1c704d7fab9f4303c6452acbdda90a78689ec5b4cc69224391cb875e08d2111d'}
PREFIXES = ('addc0a7c', '4739bf58', '36216858', 'a610060b', 'cd69448a', '1c704d7f', 'f79a76bb', 'f9b33dba',
            '9e0d8102', 'c3feb08f', '3e107d76', '5d651b0a', '33037870', '680a54f4', '20dd4e41', '7d930ba7',
            'ea63a8da', 'dac8fe59', '635349c3', '37b99526', '4f2e5fa1', 'bea50e15')
result = {}; chain_rows = {}
for row in PROFILE['images']:
    if not row['role'].startswith('ppt-master-runner-'): continue
    with tarfile.open(ARCHIVES / row['basename'], 'r:') as archive:
        raw = archive.extractfile('blobs/sha256/' + row['config_digest'][7:]).read()
    assert 'sha256:' + hashlib.sha256(raw).hexdigest() == row['config_digest']
    config = json.loads(raw); chain = ''; matches = {}; layers = []
    for index, diff in enumerate(config['rootfs']['diff_ids']):
        parent = chain
        chain = diff if not chain else 'sha256:' + hashlib.sha256((chain + ' ' + diff).encode()).hexdigest()
        layers.append(dict(chain_id=chain, parent=parent))
        if chain in WANTED or chain[7:].startswith(PREFIXES): matches[chain] = index + 1
    result[row['role']] = dict(config_digest=row['config_digest'], layers=len(config['rootfs']['diff_ids']),
                               matched_parent_chain_1based_layer=matches)
    chain_rows[row['role']] = dict(config_digest=row['config_digest'], layers=layers)
raw = (json.dumps(result, indent=2, sort_keys=True) + '\n').encode()
(HERE / 'RUNNER-PARENT-OVERLAP.json').write_bytes(raw)
(HERE / 'RUNNER-CHAINS.json').write_bytes((json.dumps(chain_rows, sort_keys=True, indent=2) + '\n').encode())
print(raw.decode())
