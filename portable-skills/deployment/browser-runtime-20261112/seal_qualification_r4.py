"""Seal an explicit public candidate plus synthetic fixtures in the operator."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
TESTS = HERE.parents[1] / 'tests'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    with zipfile.ZipFile(args.package) as archive:
        if any('.private' in Path(item.filename).parts for item in archive.infolist()):
            raise ValueError('PUBLIC_PACKAGE_REQUIRED')
    files = {}
    for name in ('test_energy_market_daily.py', 'test_energy_browser_runtime.py'):
        files[name] = base64.b64encode((TESTS / name).read_bytes()).decode('ascii')
    for path in sorted((TESTS / 'fixtures').rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            files[path.relative_to(TESTS).as_posix()] = base64.b64encode(path.read_bytes()).decode('ascii')
    raw_assets = {'init': (HERE / 'browser_init_r3.py').read_bytes(), 'child': (HERE / 'qualify_browser_child_r4.py').read_bytes(),
                  'package': args.package.read_bytes(), 'test_files': json.dumps(files, sort_keys=True, separators=(',', ':')).encode('ascii')}
    assets = {name: {'base64': base64.b64encode(raw).decode('ascii'), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in raw_assets.items()}
    source = HERE / 'qualify-energy-browser-nonroot-20261112-r4.py'
    raw = source.read_bytes()
    if raw.count(b'ASSETS = {}') != 1:
        raise ValueError('SOURCE_NOT_UNSEALED')
    sealed = raw.replace(b'ASSETS = {}', b'ASSETS = ' + repr(assets).encode('ascii'), 1)
    compile(sealed, str(source), 'exec')
    with args.output.open('xb') as stream:
        stream.write(sealed)
    metadata = {'schema': 'energy-browser-nonroot-qualification-package.v1', 'filename': args.output.name,
                'bytes': len(sealed), 'sha256': hashlib.sha256(sealed).hexdigest(),
                'assets': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in raw_assets.items()},
                'credentials_included': False, 'production_executed': False}
    with args.output.with_suffix('.package.json').open('x', encoding='utf-8') as stream:
        json.dump(metadata, stream, indent=2)
        stream.write('\n')
    print(json.dumps(metadata, sort_keys=True))


if __name__ == '__main__':
    main()
