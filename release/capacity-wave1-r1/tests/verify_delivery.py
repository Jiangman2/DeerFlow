"""Validate the delivered archive bytes, not only the development sources."""
import ast
import hashlib
from pathlib import Path, PurePosixPath
import sys
import tarfile

archive = Path(sys.argv[1])
prefix = 'deerflow-capacity-wave1-prepare-20260924-r1/'
sidecar = Path(str(archive) + '.sha256').read_bytes()
assert not sidecar.startswith(b'\xef\xbb\xbf') and b'\r' not in sidecar
expected, name = sidecar.decode().strip().split('  ')
assert name == archive.name
assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected
with tarfile.open(str(archive), 'r:gz') as tar:
    files = {}
    for member in tar.getmembers():
        p = PurePosixPath(member.name)
        assert not p.is_absolute() and '..' not in p.parts
        assert member.name.startswith(prefix) or member.name == prefix.rstrip('/')
        assert member.isfile() or member.isdir()
        if member.isfile():
            files[member.name[len(prefix):]] = tar.extractfile(member).read()
    assert set(files) == {'prepare-wave1.py', 'README.md', 'PACKAGE.SHA256'}
    for content in files.values():
        assert not content.startswith(b'\xef\xbb\xbf') and b'\r' not in content
    for line in files['PACKAGE.SHA256'].decode().splitlines():
        digest, name = line.split('  ')
        assert hashlib.sha256(files[name]).hexdigest() == digest
    assert files['prepare-wave1.py'] == (Path(__file__).parents[1] / 'prepare-wave1.py').read_bytes().replace(b'\r\n', b'\n')
    ast.parse(files['prepare-wave1.py'].decode(), feature_version=(3, 6))
print('wave1_delivered_archive=PASS outer_and_inner_sha256=yes lf_no_bom=yes python36_grammar=yes')
