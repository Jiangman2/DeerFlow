"""Create a public standard Skill and a separate private preconfigured ZIP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'portable-skills/energy-market-daily'
OUTPUT = ROOT / 'release/energy-daily-portable-20261111'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inventory():
    result = {}
    for path in sorted(SKILL.rglob('*')):
        if path.is_file() and not set(path.relative_to(SKILL).parts) & {'.private', '__pycache__', '.venv'} and path.suffix != '.pyc' and path.name != 'MANIFEST.json':
            raw = path.read_bytes()
            result[path.relative_to(SKILL).as_posix()] = {'bytes': len(raw), 'sha256': sha(raw)}
    return result


def build_zip(path, files, private=None):
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in sorted(files):
            archive.write(SKILL / relative, 'energy-market-daily/' + relative)
        if private is not None:
            info = zipfile.ZipInfo('energy-market-daily/.private/credentials.json')
            info.external_attr = 0o100600 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, private)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--credentials', required=True)
    parser.add_argument('--private-output', required=True)
    args = parser.parse_args()
    private_raw = Path(args.credentials).read_bytes()
    private = json.loads(private_raw.decode('utf-8-sig'))
    password = private.get('JKX_API_PASSWORD')
    if not isinstance(password, str) or not password:
        raise ValueError('CREDENTIAL_FORMAT')
    members = inventory()
    manifest = {'schema': 'energy-daily-portable-files.v1', 'version': '2.0.0-portable', 'files': members}
    (SKILL / 'MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    members['MANIFEST.json'] = {}
    for relative in members:
        if password.encode() in (SKILL / relative).read_bytes():
            raise ValueError('PRIVATE_VALUE_IN_PUBLIC_SOURCE')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    public = OUTPUT / 'energy-market-daily-2.0.0-public.zip'
    private_path = Path(args.private_output).resolve()
    if private_path.is_relative_to(ROOT) or private_path == public.resolve():
        raise ValueError('PRIVATE_PACKAGE_MUST_STAY_OUTSIDE_REPOSITORY')
    build_zip(public, members)
    build_zip(private_path, members, private_raw)
    with zipfile.ZipFile(public) as archive:
        if any(password.encode() in archive.read(name) for name in archive.namelist()):
            raise ValueError('PRIVATE_VALUE_IN_PUBLIC_ARCHIVE')
    with zipfile.ZipFile(private_path) as archive:
        secret_members = [name for name in archive.namelist() if password.encode() in archive.read(name)]
        if secret_members != ['energy-market-daily/.private/credentials.json']:
            raise ValueError('PRIVATE_ARCHIVE_LAYOUT')
        # Validate installed-file discovery from a fresh working directory.
        with tempfile.TemporaryDirectory(prefix='energy-private-check-') as temporary:
            root = Path(temporary).resolve()
            archive.extractall(root)
            entry = root / 'energy-market-daily/scripts/daily.py'
            env = {key: value for key, value in os.environ.items() if not key.startswith('JKX_') and key != 'ENERGY_DAILY_CREDENTIALS'}
            result = subprocess.run([sys.executable, '-I', '-B', str(entry), 'doctor'], cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            if result.returncode or not json.loads(result.stdout)['credentials_configured']:
                raise ValueError('PRIVATE_PACKAGE_DISCOVERY')
    report = {'schema': 'energy-daily-portable-package.v1', 'public_files': len(members), 'public_credential_scan': 'PASS',
              'private_config_discovery': 'PASS', 'private_credentials_location': 'energy-market-daily/.private/credentials.json',
              'public': {'filename': public.name, 'bytes': public.stat().st_size, 'sha256': sha(public.read_bytes())},
              'private_package_verified_separately': True,
              'production_deployed': False, 'credentials_exported': False}
    private_meta = {'filename': private_path.name, 'bytes': private_path.stat().st_size, 'sha256': sha(private_path.read_bytes())}
    fd = os.open(str(private_path) + '.verification.private.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(private_meta, stream)
    (OUTPUT / 'PACKAGE.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
