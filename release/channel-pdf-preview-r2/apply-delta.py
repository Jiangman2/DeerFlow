"""Prepare all exact edits against the audited image sources before writing any."""
import ast
import hashlib
import json
import sys
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(root, manifest):
    root = root.resolve()
    prepared = []
    for entry in manifest:
        target = (root / entry['path']).resolve()
        if root not in target.parents or not target.is_file():
            raise ValueError('missing or unsafe source: ' + entry['path'])
        raw = target.read_bytes()
        before_hash = sha(raw)
        if before_hash not in (entry['source_sha256'], entry['target_sha256']):
            raise ValueError('audited source hash mismatch: ' + entry['path'])
        text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
        if before_hash != entry['target_sha256']:
            for index, edit in enumerate(entry['edits']):
                old, new = edit['before'], edit['after']
                if not old or text.count(old) != 1:
                    raise ValueError('baseline mismatch: {} edit {}'.format(entry['path'], index))
                text = text.replace(old, new, 1)
        if sha(text.encode('utf-8')) != entry['target_sha256']:
            raise ValueError('audited target hash mismatch: ' + entry['path'])
        ast.parse(text, filename=str(target))
        temporary = target.with_name(target.name + '.channel-pdf-r2.tmp')
        if temporary.exists():
            raise ValueError('temporary file already exists: ' + str(temporary))
        prepared.append((target, text, before_hash, temporary))
    return prepared


def apply(root, manifest, check_only=False):
    root = root.resolve()
    prepared = prepare(root, manifest)
    receipts = []
    for target, text, before_hash, temporary in prepared:
        if not check_only:
            with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(text)
            temporary.chmod(target.stat().st_mode)
            temporary.replace(target)
        receipts.append({'path': str(target.relative_to(root)), 'before': before_hash,
                         'after': sha(text.encode('utf-8'))})
    return receipts


if __name__ == '__main__':
    check_only = sys.argv[-1] == '--check-only'
    args = sys.argv[1:-1] if check_only else sys.argv[1:]
    root, source, receipt = map(Path, args)
    result = apply(root, json.loads(source.read_text(encoding='utf-8')), check_only)
    if not check_only:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('gateway_audited_delta=PASS files={} mode={}'.format(len(result), 'check-only' if check_only else 'apply'))
