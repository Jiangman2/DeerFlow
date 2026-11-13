"""Verify the post-44 source archive from worktree or committed Git objects."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import subprocess

import post44_source_20261113 as source

ROOT = Path(__file__).resolve().parents[2]


def snapshot(ref=None):
    if ref is None:
        paths = [p.relative_to(ROOT).as_posix() for p in (ROOT / source.ENERGY).rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
        return lambda path: (ROOT / path).read_bytes(), paths
    names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ref], cwd=ROOT).decode().splitlines()
    # Reading is lazy and cached: every selected byte comes from the chosen ref.
    cache = {}
    def read(path):
        if path not in names:
            raise KeyError(path)
        if path not in cache:
            cache[path] = subprocess.check_output(['git', 'show', ref + ':' + path], cwd=ROOT)
        return cache[path]
    seal = json.loads(read(source.MANIFEST))
    historical = json.loads(read('release/released-sync-20261110/SOURCE.json'))
    required = sorted(set(seal['support_files']) | set(seal['energy_files']) | set(seal['gateway_sources'])
                      | {row['snapshot'] for row in seal['parent_files'].values() if row is not None}
                      | (set(historical['files']) & set(names)))
    raw = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=ROOT,
                                  input=''.join(ref + ':' + path + '\n' for path in required).encode())
    stream = io.BytesIO(raw)
    for path in required:
        header = stream.readline().split()
        source.require(len(header) == 3 and header[1] == b'blob', 'POST44_GIT_BLOB_MISSING: ' + path)
        cache[path] = stream.read(int(header[2]))
        source.require(stream.read(1) == b'\n', 'POST44_GIT_BATCH_INVALID')
    return read, [path for path in names if path.startswith(source.ENERGY)]


def verify(ref=None):
    read, paths = snapshot(ref)
    seal = source.qualify(read, paths)
    source.require(seal is not None, 'POST44_MANIFEST_REQUIRED')
    return {'status': 'POST44_SOURCE_AND_EVIDENCE_VERIFIED', 'source_files': len(paths),
            'gateway_files': len(seal['gateway_sources']), 'support_files': len(seal['support_files']),
            'energy_version': '2.1.0-portable', 'scheduler_budget_candidate': 10000,
            'scheduler_budget_deployed': False, 'production_commands_executed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref')
    print(json.dumps(verify(parser.parse_args().ref), sort_keys=True))
