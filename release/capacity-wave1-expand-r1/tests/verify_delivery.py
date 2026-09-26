import ast
import hashlib
from pathlib import Path, PurePosixPath
import sys
import tarfile

archive = Path(sys.argv[1])
prefix = 'deerflow-capacity-wave1-expand-20260924-r1/'
sidecar = Path(str(archive) + '.sha256').read_bytes()
assert not sidecar.startswith(b'\xef\xbb\xbf') and b'\r' not in sidecar
checksum, name = sidecar.decode().strip().split('  ')
assert name == archive.name and hashlib.sha256(archive.read_bytes()).hexdigest() == checksum
with tarfile.open(str(archive), 'r:gz') as tar:
    files = {}
    for member in tar.getmembers():
        p = PurePosixPath(member.name)
        assert not p.is_absolute() and '..' not in p.parts
        assert member.name.startswith(prefix) or member.name == prefix.rstrip('/')
        assert member.isfile() or member.isdir()
        if member.isfile():
            key = member.name[len(prefix):]
            assert key not in files
            files[key] = tar.extractfile(member).read()
    assert set(files) == {'expand-wave1.py','prepare-wave1.py','README.md','PACKAGE.SHA256'}
    for content in files.values():
        assert b'\r' not in content and not content.startswith(b'\xef\xbb\xbf')
    for line in files['PACKAGE.SHA256'].decode().splitlines():
        digest, name = line.split('  ')
        assert hashlib.sha256(files[name]).hexdigest() == digest
    for name in ('expand-wave1.py','README.md'):
        assert files[name] == (Path(__file__).parents[1]/name).read_bytes().replace(b'\r\n',b'\n')
    for name in ('expand-wave1.py','prepare-wave1.py'):
        ast.parse(files[name].decode(), feature_version=(3,6))
    assert hashlib.sha256(files['prepare-wave1.py']).hexdigest() == '7df921c0675ada9544e33e7d7627e3881e143a331fd0df40d9bde36a80395383'
print('wave1_expand_delivered_archive=PASS outer_and_inner_sha256=yes lf_no_bom=yes python36_grammar=yes')
