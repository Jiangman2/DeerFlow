"""Seal the public candidate and non-secret regression fixtures in one tool."""
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = Path(__file__).resolve().parent
RELEASE = ROOT / 'release/energy-daily-portable-20261111'
SOURCE = DEPLOY / 'qualify-energy-market-daily-portable-20261111-r1.py'


def main():
    tests = ROOT / 'portable-skills/tests'
    files = {name: base64.b64encode((tests / name).read_bytes()).decode() for name in ('test_energy_market_daily.py', 'fixtures/approved.json', 'fixtures/original_renderer.py', 'fixtures/dxy-source.txt', 'fixtures/cny-source.txt') if (tests / name).is_file()}
    # Include every actual non-secret fixture referenced by the fixed test suite.
    for path in sorted((tests / 'fixtures').rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            files[path.relative_to(tests).as_posix()] = base64.b64encode(path.read_bytes()).decode()
    raw_assets = {'child': (DEPLOY / 'qualify_portable_daily_child.py').read_bytes(),
                  'package': (RELEASE / 'energy-market-daily-2.0.0-public.zip').read_bytes(),
                  'test_files': json.dumps(files, separators=(',', ':')).encode()}
    assets = {key: {'base64': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()} for key, raw in raw_assets.items()}
    source = SOURCE.read_bytes()
    if source.count(b'ASSETS = {}') != 1:
        raise ValueError('SOURCE_NOT_UNSEALED')
    sealed = source.replace(b'ASSETS = {}', b'ASSETS = ' + repr(assets).encode(), 1)
    compile(sealed, str(SOURCE), 'exec')
    destination = RELEASE / SOURCE.name
    if destination.exists():
        raise ValueError('SEALED_TOOL_EXISTS')
    destination.write_bytes(sealed)
    metadata = {'schema': 'energy-portable-qualification-package.v1', 'filename': destination.name, 'bytes': len(sealed),
                'sha256': hashlib.sha256(sealed).hexdigest(), 'assets': {key: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for key, raw in raw_assets.items()},
                'production_execution_requested': False}
    (RELEASE / 'QUALIFICATION-PACKAGE.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(metadata))


if __name__ == '__main__':
    main()
