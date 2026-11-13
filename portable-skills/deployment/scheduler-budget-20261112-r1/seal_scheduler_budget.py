"""Seal this new standalone operator after review; never modify prior releases."""
import argparse
import base64
import hashlib
import json
from pathlib import Path


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed', action='store_true', required=True)
    arguments = parser.parse_args()
    assert arguments.reviewed
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    release = root / 'release' / 'scheduler-budget-20261112-r1'
    if release.exists():
        raise RuntimeError('RELEASE_DIRECTORY_EXISTS_REVIEW_REQUIRED')
    host = (here / 'activate-scheduler-budget-20261112-r1.py').read_bytes()
    child = (here / 'qualify_budget_child.py').read_bytes()
    assert 0 < len(child) <= 65536
    marker = b'CHILD_ASSET = {}  # Sealer fills only after independent source review.'
    assert host.count(marker) == 1
    asset = {'base64': base64.b64encode(child).decode('ascii'), 'sha256': digest(child)}
    sealed = host.replace(marker, ('CHILD_ASSET = ' + repr(asset)).encode('ascii'))
    compile(sealed, '<sealed-scheduler-budget-host>', 'exec')
    compile(child, '<sealed-scheduler-budget-child>', 'exec')
    release.mkdir()
    target = release / 'activate-scheduler-budget-20261112-r1.py'
    with target.open('xb') as stream:
        stream.write(sealed)
    manifest = {'schema': 'scheduler-budget-source-release.v1',
        'operator': {'filename': target.name, 'bytes': len(sealed), 'sha256': digest(sealed)},
        'source': {'bytes': len(host), 'sha256': digest(host)},
        'qualification_child': {'bytes': len(child), 'sha256': digest(child)},
        'before_gateway_generation': 414, 'after_gateway_generation': 415,
        'scheduler_recursion_limit_before': 300, 'scheduler_recursion_limit_after': 1000,
        'global_max_recursion_limit': 10000, 'production_mutation_performed_by_sealer': False}
    with (release / 'SOURCE.json').open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
