"""Round-trip the delivered archive with real POSIX checksum and Bash parsers."""
import hashlib
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

downloads = Path.home() / 'Downloads'
name = 'deerflow-channel-pdf-preview-20260923-r2'
archive = downloads / (name + '.tar.gz')
checksum = archive.with_name(archive.name + '.sha256')
assert b'\r' not in checksum.read_bytes()
assert checksum.read_text().split()[0] == hashlib.sha256(archive.read_bytes()).hexdigest()
bash = Path('C:/Program Files/Git/bin/bash.exe')
subprocess.run([str(bash), '-c', 'sha256sum -c ' + checksum.name], cwd=downloads, check=True)
with tempfile.TemporaryDirectory(prefix='channel-pdf-r2-roundtrip-') as directory:
    base = Path(directory)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            assert member.isdir() or member.isfile()
            assert member.name == name or member.name.startswith(name + '/')
            target = (base / member.name).resolve()
            assert base.resolve() in target.parents
            if member.isdir():
                target.mkdir(exist_ok=True)
            else:
                target.write_bytes(tar.extractfile(member).read())
    root = base / name
    subprocess.run([str(bash), '-c', 'sha256sum -c PACKAGE.SHA256'], cwd=root, check=True)
    for script in root.glob('*.sh'):
        subprocess.run([str(bash), '-n', script.name], cwd=root, check=True)
    subprocess.run([sys.executable, str(root / 'verify-package.py'), str(root)], check=True)
print('delivered_r2_archive_roundtrip=PASS external_and_internal_checksums=PASS bash_syntax=PASS')
