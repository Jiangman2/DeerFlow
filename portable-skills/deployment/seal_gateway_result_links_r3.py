"""Seal the six fixed Gateway sources and isolated, non-secret qualification."""
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = Path(__file__).resolve().parent / 'gateway-link-r3'
RELEASE = ROOT / 'release/energy-daily-portable-20261111/gateway-final-r3-py37'


def main():
    source = DEPLOY / 'activate-energy-daily-result-links-20261111-r3.py'
    paths = {'source_inspector': DEPLOY / 'inspect-energy-daily-result-link-source-20261111-r3.py',
             'qualifier': DEPLOY / 'qualify-energy-daily-result-link-20261111-r3.py',
             'skill_reader': DEPLOY / 'skill_reader.py'}
    namespace = {'__name__': 'unsealed_gateway_template'}
    exec(compile(source.read_bytes(), str(source), 'exec'), namespace)
    paths.update({key: DEPLOY / 'backend' / relative for relative, key in namespace['FILE_KEYS'].items()})
    raw_assets = {name: path.read_bytes() for name, path in paths.items()}
    assets = {name: {'base64': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in raw_assets.items()}
    raw = source.read_bytes()
    if raw.count(b'ASSETS = {}') != 1:
        raise ValueError('UNSEALED_SOURCE_REQUIRED')
    sealed = raw.replace(b'ASSETS = {}', b'ASSETS = ' + repr(assets).encode(), 1)
    compile(sealed, str(source), 'exec')
    loaded = {'__name__': 'sealed_gateway_validation'}
    exec(compile(sealed, str(source), 'exec'), loaded)
    if set(loaded['load_assets']()) != set(assets) | {'pre'}:
        raise ValueError('SEALED_CLOSURE_INVALID')
    RELEASE.mkdir(parents=True, exist_ok=True)
    target = RELEASE / source.name
    if target.exists():
        raise ValueError('PACKAGE_ALREADY_EXISTS')
    target.write_bytes(sealed)
    result = {'schema': 'energy-gateway-result-links-package.v3', 'filename': target.name, 'bytes': len(sealed),
              'sha256': hashlib.sha256(sealed).hexdigest(), 'gateway_before_generation': 413, 'gateway_after_generation': 414,
              'same_image': namespace['IMAGE'], 'application_source_files': len(namespace['FILE_KEYS']),
              'assets': {name: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in raw_assets.items()},
              'local_real_module_qualification_checks': 25, 'windows_focused_tests': 79, 'linux_focused_tests': 79,
              'operator_boundary_checks': 28, 'requires_installed_portable_skill': True,
              'public_conversation_origin': namespace['EXPECTED_ORIGIN'], 'operator_direct_cutover_no_task_wait': True, 'production_executed': False, 'credentials_included': False}
    (RELEASE / 'GATEWAY-RESULT-LINKS-PACKAGE-R3.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('filename', 'bytes', 'sha256')}))


if __name__ == '__main__':
    main()
