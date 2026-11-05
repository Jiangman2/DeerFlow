"""Local publisher of a small offline repair; original 773 MB upload is reused."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request

import repair_sqlite_build as r

HERE = Path(__file__).resolve().parent
ORIGINAL_SHA = '8dcd2f3db5efcd0489f57a645b5a051c719139017568ccc0abd734d9d82c5559'
MEMBERS = ('repair_sqlite_build.py', 'build_native.sh', 'verify_native_candidate.py',
           'SQLITE-BUILD-LOCK.json', 'SQLITE-FIX-RUNBOOK.txt')


def acquire(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = json.loads((HERE/'SQLITE-BUILD-LOCK.json').read_bytes())
    for item in lock['packages']:
        path = root/item['filename']
        if not path.exists():
            with urllib.request.urlopen(item['url'], timeout=60) as response:
                r.require(response.status == 200 and response.geturl().startswith('https://deb.debian.org/'),
                          'debian_download_origin')
                raw = response.read(item['bytes']+1)
            r.require(len(raw) == item['bytes'] and hashlib.sha256(raw).hexdigest() == item['sha256'],
                      'debian_download_hash')
            r.write_new(path, raw)
        r.require(path.stat().st_size == item['bytes'] and r.digest(path) == item['sha256'], 'debian_cache_hash')
    return {'packages':lock['packages'], 'builder_only':True, 'production_contact':False}


def pack(original, output, verification):
    r.require(r.digest(original) == ORIGINAL_SHA, 'original_archive_hash')
    r.require(not output.exists(), 'fresh_local_output_required')
    proof = json.loads(verification.read_bytes())
    r.require(proof.get('local_tool_tests') == 'PASS' and proof.get('linux_arm64_build') == 'NOT_EXECUTED',
              'local_qualification_required')
    output.mkdir(mode=0o700, parents=True)
    old = output/'original-package'
    old.mkdir(mode=0o700)
    with tarfile.open(str(original), 'r|gz') as t:
        for m in t:
            r.require(m.isfile() and not m.issparse() and not m.linkname and
                      '/' not in m.name and '\\' not in m.name and m.name not in ('.','..') and
                      0 < m.size < 4*r.GIB, 'original_archive_shape')
            with t.extractfile(m) as inp, (old/m.name).open('xb') as out:
                shutil.copyfileobj(inp, out, 1024**2)
    rows = r.manifest((old/'PACKAGE.SHA256').read_bytes())
    r.require(set(rows) | {'PACKAGE.SHA256'} == {f.name for f in old.iterdir()}, 'original_member_set')
    for name, expected in rows.items():
        r.require(r.digest(old/name) == expected, 'original_member_hash')
    with tarfile.open(str(old/'context.tar'), 'r:') as t:
        raw = t.extractfile('build_native.sh').read()
    payload = output/'payload'
    payload.mkdir(mode=0o700)
    acquire(payload)
    for name in MEMBERS:
        r.write_new(payload/name, (HERE/name).read_bytes().replace(b'\r\n',b'\n'))
    r.write_new(payload/'LOCAL-VERIFICATION.json', verification.read_bytes())
    r.record(payload/'FIX-PLAN.json', {'old_package_manifest_sha256':r.digest(old/'PACKAGE.SHA256'),
        'old_context_sha256':rows['context.tar'], 'old_build_native_sha256':hashlib.sha256(raw).hexdigest(),
        'original_archive_sha256':ORIGINAL_SHA, 'scope':'OFFLINE_BUILD_DEPENDENCIES_ONLY'})
    r.write_new(payload/'PACKAGE.SHA256', ''.join(r.digest(f)+'  '+f.name+'\n' for f in sorted(payload.iterdir())).encode())
    archive = output/'weknora-glm-sqlite-build-fix-20261016-r1.tar.gz'
    with tarfile.open(str(archive), 'w:gz') as t:
        for f in sorted(payload.iterdir()):
            r.add(t, f.name, source=f)
    template = (HERE/'START-sqlite-fix-template.sh').read_text(encoding='utf-8')
    starter = output/'START-weknora-glm-sqlite-fix.sh'
    r.write_new(starter, template.replace('@PATCH_SHA256@',r.digest(archive)).encode())
    result = {'sqlite_fix_package':'READY_FOR_SERVER_PREFLIGHT', 'archive':str(archive),
        'archive_sha256':r.digest(archive), 'bytes':archive.stat().st_size,
        'starter':str(starter), 'starter_sha256':r.digest(starter),
        'candidate_image_built':False, 'production_writes':0, 'ready_for_rollout':False}
    r.record(output/'PACKAGE-RESULT.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--original', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--verification', required=True, type=Path)
    a = p.parse_args()
    print(json.dumps(pack(a.original, a.output, a.verification), sort_keys=True), flush=True)
