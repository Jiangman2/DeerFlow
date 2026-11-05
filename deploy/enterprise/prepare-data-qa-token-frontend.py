"""Create a source-only release snapshot from the qualified Data QA build plus one commit."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

BASE = Path('C:/Users/15285/.codex/tmp/oa-data-qa-progress-frontend-build-r3')
REPO = Path(__file__).resolve().parents[2]
COMMIT = '1c74cfe3a37a9d85cf38bdde4e002c5f722adf26'
BASE_INVENTORY_SHA = 'e7a1156f04044016607dedcb9916d90231085c1db9158c4cf4fd27a7e390870c'
RUNTIME_FILES = {
    'frontend/src/components/workspace/messages/message-list.tsx',
    'frontend/src/components/workspace/messages/message-token-usage.tsx',
    'frontend/src/core/i18n/locales/en-US.ts',
    'frontend/src/core/i18n/locales/zh-CN.ts',
    'frontend/src/core/i18n/locales/types.ts',
}
TEST = 'frontend/tests/unit/components/workspace/message-token-usage.dom.test.tsx'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def command(args, cwd, data=None):
    result = subprocess.run(args, cwd=cwd, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=120, check=True)
    return result.stdout


def prepare(output):
    assert not output.exists(), 'FRESH_OUTPUT_REQUIRED'
    rows = json.loads((BASE / 'SOURCE.json').read_text(encoding='utf-8'))
    assert sha(json.dumps(rows, sort_keys=True).encode()) == BASE_INVENTORY_SHA
    patch = command(['git', 'diff', COMMIT + '^', COMMIT, '--', 'frontend'], REPO)
    names = command(['git', 'diff', '--name-only', COMMIT + '^', COMMIT, '--', 'frontend'], REPO).decode().splitlines()
    assert set(names) == RUNTIME_FILES | {TEST}, 'PATCH_SCOPE'
    output.mkdir(parents=True)
    context = output / 'source'
    context.mkdir()
    for row in rows:
        relative = Path(row['path'])
        assert not relative.is_absolute() and '..' not in relative.parts
        source = BASE / 'context' / relative
        raw = source.read_bytes()
        assert not source.is_symlink() and len(raw) == row['bytes'] and sha(raw) == row['sha256']
        target = context / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
    command(['git', 'apply', '--check', '-'], context, patch)
    command(['git', 'apply', '-'], context, patch)
    (output / 'TOKEN-DISPLAY.patch').write_bytes(patch)
    before = {row['path']: row for row in rows}
    after = []
    changed = []
    for file in sorted(context.rglob('*')):
        if not file.is_file():
            continue
        assert not file.is_symlink()
        name = file.relative_to(context).as_posix()
        raw = file.read_bytes()
        row = {'path': name, 'bytes': len(raw), 'sha256': sha(raw)}
        after.append(row)
        if row != before.get(name):
            changed.append(name)
    assert set(changed) == RUNTIME_FILES | {TEST}, 'UNEXPECTED_SOURCE_CHANGE'
    assert set(before) <= {row['path'] for row in after}, 'SOURCE_REMOVAL'
    record = {'schema': 'portal-data-qa-token-frontend-source.v1',
              'base_image': 'sha256:15d1327740e688faf0617d1d8fb9f662618a963624ba7280a6d0efb54ed70475',
              'base_inventory_sha256': BASE_INVENTORY_SHA, 'token_display_commit': COMMIT,
              'patch_sha256': sha(patch), 'changed_files': changed,
              'runtime_files_changed': len(RUNTIME_FILES), 'source': str(context),
              'source_inventory': after, 'production_modified': False}
    (output / 'SOURCE-CHANGE.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in record.items() if key != 'source_inventory'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output)
