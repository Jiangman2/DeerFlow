"""Apply audited exact-context source edits; never replace an entire module."""
import ast
import hashlib
import json
import sys
from pathlib import Path


def apply(root, manifest):
    root = root.resolve()
    prepared = []
    for entry in manifest:
        target = (root / entry['path']).resolve()
        if root not in target.parents or not target.is_file():
            raise ValueError('missing or unsafe source: ' + entry['path'])
        text = target.read_text(encoding='utf-8')
        before_hash = hashlib.sha256(text.encode()).hexdigest()
        for index, edit in enumerate(entry['edits']):
            old, new = edit['before'], edit['after']
            if text.count(old) == 1:
                text = text.replace(old, new, 1)
            elif text.count(old) == 0 and text.count(new) == 1:
                continue
            else:
                raise ValueError('baseline mismatch: {} edit {}'.format(entry['path'], index))
        ast.parse(text, filename=str(target))
        prepared.append((target, text, before_hash))
    receipts = []
    for target, text, before_hash in prepared:
        temporary = target.with_name(target.name + '.channel-pdf-r1.tmp')
        if temporary.exists():
            raise ValueError('temporary file already exists: ' + str(temporary))
        temporary.write_text(text, encoding='utf-8', newline='\n')
        temporary.chmod(target.stat().st_mode)
        temporary.replace(target)
        receipts.append({'path': str(target.relative_to(root)), 'before': before_hash,
                         'after': hashlib.sha256(text.encode()).hexdigest()})
    return receipts


if __name__ == '__main__':
    root, source, receipt = map(Path, sys.argv[1:])
    result = apply(root, json.loads(source.read_text(encoding='utf-8')))
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('gateway_exact_delta=PASS files={}'.format(len(result)))
