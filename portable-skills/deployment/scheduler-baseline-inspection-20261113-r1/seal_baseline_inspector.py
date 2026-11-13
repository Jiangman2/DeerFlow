"""Create one new reviewed, read-only baseline inspector; no production calls."""
import argparse
import base64
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed', action='store_true', required=True)
    parser.parse_args()
    folder = Path(__file__).resolve().parent
    root = folder.parents[2]
    release = root / 'release/scheduler-baseline-inspection-20261113-r1'
    if release.exists():
        raise RuntimeError('RELEASE_EXISTS')
    host = (folder / 'inspect-scheduler-baseline-20261113-r1.py').read_bytes()
    child = (folder / 'inspect_baseline_child.py').read_bytes()
    marker = b'CHILD_ASSET = {}'
    assert host.count(marker) == 1 and 0 < len(child) <= 32768
    payload = {'base64': base64.b64encode(child).decode(), 'sha256': hashlib.sha256(child).hexdigest()}
    sealed = host.replace(marker, ('CHILD_ASSET = ' + repr(payload)).encode())
    compile(sealed, '<sealed-host>', 'exec'); compile(child, '<sealed-child>', 'exec')
    release.mkdir()
    target = release / 'inspect-scheduler-baseline-20261113-r1.py'
    with target.open('xb') as stream:
        stream.write(sealed)
    manifest = {'schema': 'scheduler-baseline-inspector-source.v1', 'operator': {'filename': target.name,
        'bytes': len(sealed), 'sha256': hashlib.sha256(sealed).hexdigest()},
        'child': {'bytes': len(child), 'sha256': hashlib.sha256(child).hexdigest()}, 'production_accessed': False}
    with (release / 'SOURCE.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, sort_keys=True, indent=2)
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
