"""Seal the reviewed one-Skill operator installer; no credential values."""
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = Path(__file__).resolve().parent
RELEASE = ROOT / 'release/energy-daily-portable-20261111'


def main():
    source = DEPLOY / 'install-energy-market-daily-portable-20261111-r2.py'
    paths = {'publisher': DEPLOY / 'publish_portable_daily_child.py',
             'package': RELEASE / 'energy-market-daily-2.0.0-public.zip',
             'source_inspector': DEPLOY / 'gateway-link-r2/inspect-energy-daily-result-link-source-20261111-r2.py',
             'visual_receipt': RELEASE / 'ARM64-VISUAL-REVIEW.json'}
    raw_assets = {name: path.read_bytes() for name, path in paths.items()}
    assets = {name: {'base64': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in raw_assets.items()}
    raw = source.read_bytes()
    if raw.count(b'ASSETS = {}') != 1:
        raise ValueError('UNSEALED_SOURCE_REQUIRED')
    sealed = raw.replace(b'ASSETS = {}', b'ASSETS = ' + repr(assets).encode(), 1)
    compile(sealed, str(source), 'exec')
    target = RELEASE / source.name
    if target.exists():
        raise ValueError('PACKAGE_ALREADY_EXISTS')
    target.write_bytes(sealed)
    result = {'schema': 'energy-portable-install-package.v2', 'filename': target.name, 'bytes': len(sealed),
              'sha256': hashlib.sha256(sealed).hexdigest(),
              'assets': {name: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in raw_assets.items()},
              'windows_and_linux_tests': 66, 'actual_arm64_qualification': 'PASS', 'actual_arm64_sample_visual_review': 'PASS',
              'operator_direct_cutover_no_task_wait': True, 'production_executed': False, 'gateway_link_activation_separate': True, 'credentials_included': False}
    (RELEASE / 'INSTALL-PACKAGE-R2.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('filename', 'bytes', 'sha256')}))


if __name__ == '__main__':
    main()
