"""Seal the combined welcome-notice and mobile-spacing change over Frontend 228."""
import argparse
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASE = Path('C:/Users/15285/.codex/tmp/frontend-mobile-notice-removal-20261024-r1a')
BASE_RECORD_SHA = '57252f53b5964bb5d1329e7d0c1f01ef48fed04acad023e01ac49c49e92370af'
BASE_IMAGE = 'sha256:0af4bb16a0924a02f6cfcb927c137642329b5831d01a5eded8c4f61ee8ad43f7'
PAGE = 'frontend/src/app/workspace/chats/[thread_id]/page.tsx'
LAYOUT = 'frontend/src/app/workspace/chats/[thread_id]/layout.tsx'
IMPORT = 'import { NonConfidentialNotice } from "@/components/workspace/non-confidential-notice";\n'
CHANGES = {
    PAGE: [
        ('className={cn("size-full", !isWelcomeMode && "pt-10")}',
         'className={cn("size-full", !isWelcomeMode && "md:pt-10")}'),
        ('} from "@/components/workspace/mode-selector";\n',
         '} from "@/components/workspace/mode-selector";\n' + IMPORT),
        ('            </header>', '              {isWelcomeMode && (\n'
         '                <NonConfidentialNotice className="cnooc-welcome-notice absolute top-full right-0 left-0 hidden md:block" />\n'
         '              )}\n            </header>'),
    ],
    LAYOUT: [(IMPORT, ''), ('        <NonConfidentialNotice className="hidden md:block" />\n', '')],
}


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
        if row['path'] in CHANGES:
            content = content.replace(b'\r\n', b'\n')
            for old, new in CHANGES[row['path']]:
                assert content.count(old.encode()) == 1, 'EXPECTED_SOURCE_SHAPE_REQUIRED'
                content = content.replace(old.encode(), new.encode(), 1)
            assert (REPO / row['path']).read_bytes().replace(b'\r\n', b'\n') == content, 'CHECKOUT_DIFFERS_FROM_CANDIDATE'
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        after.append({'path': row['path'], 'bytes': len(content), 'sha256': sha(content)})
    prior = {row['path']: row for row in before}
    changed = [row['path'] for row in after if row != prior[row['path']]]
    assert set(changed) == set(CHANGES), 'TWO_CHAT_FILES_ONLY'
    record = {'schema': 'portal-chat-notice-layout-source.v1', 'base_image': BASE_IMAGE,
              'base_source_record_sha256': BASE_RECORD_SHA, 'changed_files': changed,
              'source': str(source), 'source_inventory': after, 'production_modified': False,
              'notice_scope': 'DESKTOP_WELCOME_ONLY_BELOW_HEADER',
              'notice_hidden_on_submit_before_server_response': True,
              'mobile_warning_visible': False, 'mobile_breakpoint': 'width < 768px',
              'mobile_scroll_padding_before_px': 40, 'mobile_scroll_padding_after_px': 0}
    (output / 'SOURCE-CHANGE.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'SEALED_CHAT_NOTICE_LAYOUT_PREPARED', 'source': str(source), 'changed_files': changed}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output)
