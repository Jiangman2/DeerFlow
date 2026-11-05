"""Seal the mobile-only removal over the recorded production frontend source."""
import argparse
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASE = Path('C:/Users/15285/.codex/tmp/frontend-nonconfidential-notice-20261023-r2')
BASE_RECORD_SHA = '409bcf7f34f08fc92e44ce5e890cc72f3d4aac4105abf9434c94a0d3463b4672'
BASE_IMAGE = 'sha256:b37036e9b226f8605e3da98d9f81cb072fc30da29cb5b972702c5ed6ec535638'
PAGE = 'frontend/src/app/workspace/chats/[thread_id]/page.tsx'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare(output):
    raw = (BASE / 'SOURCE-CHANGE.json').read_bytes()
    assert sha(raw) == BASE_RECORD_SHA, 'RELEASE_SOURCE_RECORD_CHANGED'
    before = json.loads(raw)['source_inventory']
    assert not output.exists(), 'FRESH_OUTPUT_REQUIRED'
    source = output / 'source'
    source.mkdir(parents=True)
    after = []
    for row in before:
        relative = Path(row['path'])
        assert not relative.is_absolute() and '..' not in relative.parts
        origin = BASE / 'source' / relative
        content = origin.read_bytes()
        assert not origin.is_symlink() and len(content) == row['bytes'] and sha(content) == row['sha256']
        if row['path'] == PAGE:
            newline = b'\r\n' if b'\r\n' in content else b'\n'
            for line in (
                b'import { NonConfidentialNotice } from "@/components/workspace/non-confidential-notice";\n',
                b'            <NonConfidentialNotice className="md:hidden" />\n',
            ):
                line = line.rstrip(b'\n') + newline
                assert content.count(line) == 1, 'EXPECTED_MOBILE_RENDER_REQUIRED'
                content = content.replace(line, b'', 1)
            assert (REPO / PAGE).read_bytes().replace(b'\r\n', b'\n') == content.replace(b'\r\n', b'\n'), 'CHECKOUT_DIFFERS_FROM_CANDIDATE'
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        after.append({'path': row['path'], 'bytes': len(content), 'sha256': sha(content)})
    prior = {row['path']: row for row in before}
    changed = [row['path'] for row in after if row != prior[row['path']]]
    assert changed == [PAGE], 'SINGLE_PAGE_CHANGE_REQUIRED'
    layout = (source / 'frontend/src/app/workspace/chats/[thread_id]/layout.tsx').read_text(encoding='utf-8')
    assert '<NonConfidentialNotice className="hidden md:block" />' in layout
    record = {'schema': 'portal-mobile-notice-removal-source.v1', 'base_image': BASE_IMAGE,
              'base_source_record_sha256': BASE_RECORD_SHA, 'changed_files': changed,
              'source': str(source), 'source_inventory': after, 'production_modified': False,
              'desktop_notice_preserved': True, 'mobile_breakpoint': 'width < 768px'}
    (output / 'SOURCE-CHANGE.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'SEALED_MOBILE_NOTICE_REMOVAL_PREPARED', 'changed_files': changed,
                      'source': str(source), 'production_modified': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output)
