"""Prepare the notice-only change over the exact released Token/Data QA source."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[2]
BASE = Path('C:/Users/15285/.codex/tmp/oa-data-qa-token-frontend-20261022-r1')
BASE_RECORD_SHA = '0ccbaed8c687af03736c12b5633206ab37f56fd145493277f383b221fe0167d7'
BASE_IMAGE = 'sha256:b6c17e31ebcadb3c514fd2c3f87752e0eb84ee3e28ccc921366bac6a0bf91cd5'
COMPONENT = 'frontend/src/components/workspace/non-confidential-notice.tsx'
TRACKED = {
    'frontend/src/app/workspace/chats/[thread_id]/layout.tsx',
    'frontend/src/app/workspace/chats/[thread_id]/page.tsx',
    'frontend/src/styles/cnooc-concept.css',
    'frontend/src/core/i18n/locales/types.ts',
    'frontend/src/core/i18n/locales/zh-CN.ts',
    'frontend/src/core/i18n/locales/en-US.ts',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(args, cwd, data=None):
    return subprocess.run(['git'] + args, cwd=cwd, input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, timeout=120).stdout


def prepare(output):
    record_raw = (BASE / 'SOURCE-CHANGE.json').read_bytes()
    assert sha(record_raw) == BASE_RECORD_SHA, 'RELEASE_SOURCE_RECORD_CHANGED'
    before = json.loads(record_raw)['source_inventory']
    assert not output.exists(), 'FRESH_OUTPUT_REQUIRED'
    source = output / 'source'
    source.mkdir(parents=True)
    for row in before:
        relative = Path(row['path'])
        assert not relative.is_absolute() and '..' not in relative.parts
        origin = BASE / 'source' / relative
        raw = origin.read_bytes()
        assert not origin.is_symlink() and len(raw) == row['bytes'] and sha(raw) == row['sha256']
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    patch = git(['diff', '--', *sorted(TRACKED)], REPO)
    assert patch, 'NOTICE_PATCH_REQUIRED'
    git(['apply', '--check', '-'], source, patch)
    git(['apply', '-'], source, patch)
    component = (REPO / COMPONENT).read_bytes()
    assert not (source / COMPONENT).exists()
    (source / COMPONENT).write_bytes(component)
    (output / 'NOTICE.patch').write_bytes(patch)
    prior = {row['path']: row for row in before}
    after = []
    for file in sorted(source.rglob('*')):
        if not file.is_file():
            continue
        assert not file.is_symlink()
        raw = file.read_bytes()
        after.append({'path': file.relative_to(source).as_posix(), 'bytes': len(raw), 'sha256': sha(raw)})
    changed = [row['path'] for row in after if row != prior.get(row['path'])]
    assert set(changed) == TRACKED | {COMPONENT}, 'CHANGE_SCOPE'
    assert set(prior) <= {row['path'] for row in after}, 'SOURCE_REMOVAL'
    record = {'schema': 'portal-nonconfidential-notice-frontend-source.v1',
              'base_image': BASE_IMAGE, 'base_source_record_sha256': BASE_RECORD_SHA,
              'patch_sha256': sha(patch), 'changed_files': changed,
              'source': str(source), 'source_inventory': after, 'production_modified': False}
    (output / 'SOURCE-CHANGE.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'SEALED_NOTICE_SOURCE_PREPARED', 'changed_files': changed,
                      'source': str(source), 'production_modified': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output)
