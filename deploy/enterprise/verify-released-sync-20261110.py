"""Verify the committed source/receipt archive, without production access."""
import argparse
import io
import importlib.util
import json
from pathlib import Path
import subprocess

import released_sync_source_20261110 as source

ROOT = Path(__file__).resolve().parents[2]


def snapshot(ref=None):
    if ref is None:
        paths = [p.relative_to(ROOT).as_posix() for p in (ROOT/source.ENERGY).rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts]
        return lambda p:(ROOT/p).read_bytes(), paths
    if subprocess.check_output(['git', 'ls-tree', '--name-only', ref, source.successor.MANIFEST], cwd=ROOT).strip():
        spec = importlib.util.spec_from_file_location('post44_snapshot', ROOT / 'deploy/enterprise/verify-post44-source-20261113.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.snapshot(ref)
    def blob(path):
        return subprocess.check_output(['git','show',ref+':'+path],cwd=ROOT)
    raw = blob(source.MANIFEST)
    seal = json.loads(raw)
    paths = sorted(set(seal['files']) | {source.MANIFEST})
    data = subprocess.check_output(['git','cat-file','--batch'],cwd=ROOT,
                                  input=''.join(ref+':'+p+'\n' for p in paths).encode())
    stream, files = io.BytesIO(data), {}
    for path in paths:
        header = stream.readline().split()
        source.require(len(header)==3 and header[1]==b'blob','RELEASE_GIT_BLOB_MISSING: '+path)
        files[path]=stream.read(int(header[2]))
        source.require(stream.read(1)==b'\n','RELEASE_BATCH_INVALID')
    actual = subprocess.check_output(['git','ls-tree','-r','--name-only',ref,'--',source.ENERGY],cwd=ROOT).decode().splitlines()
    return files.__getitem__,actual


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ref')
    args=p.parse_args()
    print(json.dumps(source.verify(*snapshot(args.ref)),sort_keys=True))
