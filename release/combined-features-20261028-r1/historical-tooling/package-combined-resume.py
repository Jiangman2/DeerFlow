import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def extract(path, sha, include=None, exclude=()):
    raw = path.read_bytes(); assert hashlib.sha256(raw).hexdigest() == sha
    source = raw.decode(); pieces = []; found = set()
    for n in ast.parse(source).body:
        name = n.name if isinstance(n, (ast.FunctionDef, ast.ClassDef)) else n.targets[0].id if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) else None
        if name and name not in exclude and (include is None or name in include):
            pieces.append(ast.get_source_segment(source, n)); found.add(name)
    if include: assert found == set(include)
    return '\n\n'.join(pieces)


def build(activation, diagnosis):
    release = extract(activation, '19c3024033ddd42c6cb2317c9aa2ad0b7ad44d687cd26cf8ea76c1addf5b5471',
                      exclude={'CONTROL', 'execute', 'main', 'run_child', 'validate_baselines', 'OLD_SOURCES'})
    diagnostic = extract(diagnosis, '519b1ab1cac50ff8a90ace771c484ef7c675faad71ec7fdb920e04683f4a958c',
                         include={'PRIOR', 'PLANNED', 'error_categories', 'DiagnosticReader', 'child_summary'})
    diagnostic = diagnostic.replace('self.commands < 300', 'self.commands < 6000').replace('self.started < 600', 'self.started < 10800').replace('600 - (time.monotonic()', '10800 - (time.monotonic()')
    source = (HERE/'resume-combined-features.py').read_text(encoding='utf-8')
    source = source.replace('RELEASE_HELPERS = None', release).replace('DIAGNOSTIC_HELPERS = None', diagnostic)
    ast.parse(source, feature_version=(3, 7))
    return source.encode()


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--activation', type=Path, required=True); p.add_argument('--diagnosis', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); raw = build(a.activation, a.diagnosis)
    with a.output.open('xb') as f: f.write(raw)
    print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), production_modified=False)))
