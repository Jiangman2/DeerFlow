"""Readonly ordinary-sandbox check of the already published 110-file Skill."""
import ast
import hashlib
import io
import json
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RELEASE = ROOT / 'release/energy-webhook-visibility-20261116-r1'
INSTALL = ROOT / 'release/energy-webhook-skill-20261116-r3'
NAME = 'verify-energy-webhook-skill-20261116-r1.py'
TREE = 'e9f273fb122b42a692517e39323ef0bbe6c80318cacdf694bb39ebc094ce99a0'
OPERATION = '1ed48e84ae104d2abd3645c01221cf34'
RECEIPT_SHA = 'a6d9ead7b1a0fe4d3573c655eb0cac582290a59c0f978a1f691a633e79055815'
RUNTIME_SHA = 'cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598'


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def inventory():
    raw = (INSTALL / 'energy-market-daily-2.1.7-public-code.zip').read_bytes()
    assert sha(raw) == '6ecc8bf66ac874f9d99b7402657d585ed9efac64f26f6a87cc9603839b609d9f'
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = {name.split('/', 1)[1]: archive.read(name) for name in archive.namelist()}
    binding = canonical({'schema': 'energy-browser-runtime-binding.v1',
        'directory': '../.energy-browser-runtime-20261112-r1/linux-arm64', 'manifest_sha256': RUNTIME_SHA}) + b'\n'
    files['assets/browser-runtime.json'] = binding
    manifest = json.loads(files['MANIFEST.json'])
    manifest['files']['assets/browser-runtime.json'] = {'bytes': len(binding), 'sha256': sha(binding)}
    files['MANIFEST.json'] = canonical(manifest) + b'\n'
    rows = [{'path': name, 'bytes': len(value), 'sha256': sha(value)} for name, value in sorted(files.items())]
    assert len(rows) == 110 and sha(canonical(rows)) == TREE
    return rows


def build():
    prior = ROOT / 'release/energy-daily-html-visibility-20261114-r1'
    meta = json.loads((prior / 'SOURCE.json').read_bytes())
    raw = (prior / meta['filename']).read_bytes()
    assert sha(raw) == meta['sha256']
    source = raw.decode()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == 'VISIBILITY_CHILD' for t in n.targets))
    child = ast.literal_eval(node.value)
    rows_node = next(n for n in ast.parse(child).body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == 'EXPECTED_ROWS' for t in n.targets))
    lines = child.splitlines(keepends=True)
    lines[rows_node.lineno - 1:rows_node.end_lineno] = ['EXPECTED_ROWS=' + repr(inventory()) + '\n']
    child = ''.join(lines)
    assert child.count('EXPECTED_SKILL_COUNT = 108') == 1
    child = child.replace('EXPECTED_SKILL_COUNT = 108', 'EXPECTED_SKILL_COUNT = 110')
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = ['VISIBILITY_CHILD = ' + repr(child) + '\n']
    source = ''.join(lines)
    replacements = {
        '/var/lib/portal-energy-html-warning-skill-install-20261114-r1/REPORT.json': '/var/lib/portal-energy-webhook-skill-install-20261116-r3/REPORT.json',
        '5dd40639193ff9a9776c235b084977d47219ea2fde4ff154822feca625066663': RECEIPT_SHA,
        '2a09015d31cee41cd2a329fff96062848255d3d49fb7cec8c52d47c939345259': TREE,
        '91ae0c5e6aff42dbb0b73aef146732d0': OPERATION,
        'fixed_read(RECEIPT, RECEIPT_SHA, 9328)': 'fixed_read(RECEIPT, RECEIPT_SHA, 9782)',
        "receipt['publication']['after_files'] == 108": "receipt['publication']['after_files'] == 110",
        "get('run') == 27": "get('run') == 57",
        'energy-html-warning-visibility.v1': 'energy-webhook-skill-visibility.v1',
        'sandbox-0636d6b8': 'sandbox-0147427f', 'ecs-znws-0004': 'ecs-znws-0003',
        'need(output.tell() <= 16777216 and errors.tell() <= 65536':
            'need(os.fstat(output.fileno()).st_size <= 16777216 and os.fstat(errors.fileno()).st_size <= 65536',
        '        self.queries += 1': "        need(self.queries < 10, 'TEN_QUERY_BOUND')\n        self.queries += 1",
    }
    for before, after in replacements.items():
        assert before in source, before
        source = source.replace(before, after)
    for value in (source, child):
        ast.parse(value, feature_version=(3, 7))
        compile(value, '<readonly-webhook-visibility>', 'exec')
    return source.encode(), child


def main():
    raw, child = build()
    meta = {'schema': 'energy-webhook-visibility-package.v1', 'filename': NAME, 'bytes': len(raw), 'sha256': sha(raw),
        'accepted_operation': OPERATION, 'accepted_receipt_sha256': RECEIPT_SHA, 'expected_tree_sha256': TREE,
        'expected_files': 110, 'max_sampled_nodes': 3, 'read_only': True, 'work_messages_sent': 0,
        'production_executed': False, 'visibility_child_sha256': sha(child.encode())}
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, value in [(NAME, raw), ('SOURCE.json', (json.dumps(meta, indent=2, sort_keys=True) + '\n').encode())]:
        path = RELEASE / name
        if path.exists():assert path.read_bytes() == value
        else:path.write_bytes(value)
    print(json.dumps(meta, sort_keys=True))


if __name__ == '__main__':main()
