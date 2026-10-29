"""Package separate staging and rollout entry points; preserve the original tool."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def selected_source(source, excluded):
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    return ''.join(''.join(lines[n.lineno - 1:n.end_lineno]) for n in tree.body
                   if not (isinstance(n, ast.FunctionDef) and n.name in excluded) and
                   not isinstance(n, ast.If))


def packaged(mode):
    assert mode in ('prepare', 'rollout')
    inputs = {name: (ROOT / name).read_text(encoding='utf-8') for name in
              ('enable_host.py', 'host_r2.py', 'enable_child.py', 'model.yaml', 'split_host.py', 'package_split.py')}
    bundle = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    original = inputs['enable_host.py']
    excluded = {'execute', 'main'}
    if mode == 'prepare': excluded |= {'plan', 'patch_once', 'verify_endpoints'}
    core = selected_source(original, excluded)
    if mode == 'rollout':
        # Reuse the exact already-qualified 70-Pod acceptance body, not a weaker fork.
        execute = next(n for n in ast.parse(original).body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        boundary = next(n for n in execute.body if isinstance(n, ast.Assign) and
                        any(isinstance(t, ast.Name) and t.id == 'generation' for t in n.targets))
        tail = ''.join(original.splitlines(keepends=True)[boundary.lineno - 1:execute.end_lineno])
        core += '\n\ndef observe_rollout(reader, lib, result, gateway, old_name, old_cm, services, name, raw, created, expected, after):\n' + tail
    reader = selected_source(inputs['host_r2.py'], {'main'})
    assert reader.count('self.commands < 40') == 1 and reader.count('remaining = 440 -') == 1
    reader = reader.replace('self.commands < 40', 'self.commands < 1600').replace('remaining = 440 -', 'remaining = 7200 -')
    child = inputs['enable_child.py'].replace('PROFILE = None  # Packaged from model.yaml.', 'PROFILE = ' + repr(inputs['model.yaml']))
    source = selected_source(inputs['split_host.py'], {'rollout', 'load_preparation'} if mode == 'prepare' else {'prepare'})
    # Replace only top-level assignment lines before inserting any nested source.
    values = {'MODE': mode, 'CORE': core, 'LIBRARY': reader, 'CHILD': child, 'BUNDLE_ID': bundle}
    lines = source.splitlines(keepends=True)
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in values: lines[node.lineno - 1] = name + ' = ' + repr(values[name]) + '\n'
    source = ''.join(lines) + "\nif __name__ == '__main__': raise SystemExit(main())\n"
    for code in (core, reader, child, source): ast.parse(code, feature_version=(3, 7))
    return source.encode()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'rollout')); parser.add_argument('output', type=Path)
    args = parser.parse_args(); raw = packaged(args.mode)
    with args.output.open('xb') as handle: handle.write(raw)
    print(json.dumps({'phase': args.mode, 'output': str(args.output), 'bytes': len(raw),
                      'sha256': hashlib.sha256(raw).hexdigest()}))
