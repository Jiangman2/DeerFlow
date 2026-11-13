"""Seal only the portable code change; original2.0.0 archives stay immutable."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'portable-skills/energy-market-daily'
RELEASE = ROOT / 'release/energy-daily-browser-fix-20261112-r1'
ORIGINAL = ROOT / 'release/energy-daily-portable-20261111/energy-market-daily-2.0.0-public.zip'
ORIGINAL_SHA = '7964309dda73a26f7679ae48b98e2dcfb1aa1cefdc0bb9d2a8c09f2e06562610'
ALLOWED = {'SKILL.md','references/runtime.md','scripts/daily.py','scripts/browser_runtime.py','MANIFEST.json'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', choices=('r2',), default=None)
    args = parser.parse_args()
    suffix = '-' + args.revision if args.revision else ''
    before = ORIGINAL.read_bytes()
    if sha(before) != ORIGINAL_SHA:
        raise ValueError('ACCEPTED_ORIGINAL_ARCHIVE_CHANGED')
    with zipfile.ZipFile(io.BytesIO(before)) as archive:
        old = {name.removeprefix('energy-market-daily/'):archive.read(name) for name in archive.namelist()}
    files = {}
    for path in sorted(SKILL.rglob('*')):
        if path.is_file() and not set(path.relative_to(SKILL).parts) & {'.private','__pycache__','.venv'} and path.suffix != '.pyc' and path.name != 'MANIFEST.json':
            files[path.relative_to(SKILL).as_posix()] = path.read_bytes()
    if any(name not in files for name in old if name != 'MANIFEST.json'):
        raise ValueError('ORIGINAL_SOURCE_REMOVED')
    changed = sorted(name for name,raw in files.items() if old.get(name) != raw)
    if not set(changed).issubset(ALLOWED):
        raise ValueError('BUSINESS_SOURCE_CHANGED')
    manifest = {'schema':'energy-daily-portable-files.v1','version':'2.0.1-portable',
                'files':{name:{'bytes':len(raw),'sha256':sha(raw)} for name,raw in files.items()}}
    raw_manifest = (json.dumps(manifest,ensure_ascii=False,indent=2) + '\n').encode()
    (SKILL / 'MANIFEST.json').write_bytes(raw_manifest)
    files['MANIFEST.json'] = raw_manifest
    RELEASE.mkdir(parents=True,exist_ok=True)
    target = RELEASE / ('energy-market-daily-2.0.1-public-code' + suffix + '.zip')
    with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,raw in sorted(files.items()):
            info = zipfile.ZipInfo('energy-market-daily/' + name)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info,raw)
    rows = [{'path':name,'bytes':len(raw),'sha256':sha(raw)} for name,raw in sorted(files.items())]
    report = {'schema':'energy-browser-code-package.v1','version':'2.0.1-portable','files':len(rows),'tree_sha256':sha(canonical(rows)),
              'accepted_original_archive_sha256':ORIGINAL_SHA,'accepted_live_before_tree_sha256':'f880bc0bd379612a20c0f62fe0c3c14389aad4a9aa26960f5d67a3145f8a7044',
              'changed_source_files':changed + ['MANIFEST.json'],'business_source_bytes_unchanged':True,'credentials_included':False,
              'runtime_dependency_binaries_included':False,'production_deployed':False,
              'archive':{'filename':target.name,'bytes':target.stat().st_size,'sha256':sha(target.read_bytes())}}
    with (RELEASE / ('CODE-PACKAGE' + suffix + '.json')).open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report,indent=2) + '\n')
    print(json.dumps(report,sort_keys=True))


if __name__ == '__main__':
    main()
