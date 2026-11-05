"""Seal the configuration-only activation tool; never execute it locally."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def packaged():
    child = (ROOT / 'enable_child.py').read_text(encoding='utf-8')
    profile = (ROOT / 'model.yaml').read_text(encoding='utf-8')
    child = child.replace('PROFILE = None  # Packaged from model.yaml.', 'PROFILE = ' + repr(profile))
    reader = (ROOT / 'host_r2.py').read_text(encoding='utf-8')
    # Derive only the tested transport/identity functions; preserve original verifier.
    assert reader.count('self.commands < 40') == 1 and reader.count('remaining = 440 -') == 1
    reader = reader.replace('self.commands < 40', 'self.commands < 1600').replace('remaining = 440 -', 'remaining = 7200 -')
    tree = ast.parse(reader)
    cutoff = next(node.lineno for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    reader = '\n'.join(reader.splitlines()[:cutoff - 1]) + '\n'
    host = (ROOT / 'enable_host.py').read_text(encoding='utf-8')
    host = host.replace('CHILD = None', 'CHILD = ' + repr(child))
    host = host.replace('LIBRARY = None  # Packaged bounded reader; no provider-check main is executed.', 'LIBRARY = ' + repr(reader))
    for source in (host, reader, child): ast.parse(source, feature_version=(3, 7))
    return host.encode('utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('output', type=Path)
    args = parser.parse_args(); raw = packaged()
    with args.output.open('xb') as handle: handle.write(raw)
    print(json.dumps({'output': str(args.output), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}))
