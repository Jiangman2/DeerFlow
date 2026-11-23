"""Check accepted 420 source/evidence from disk or Git objects, without deployment."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import personal_notification_source_20261117 as source

ROOT = Path(__file__).resolve().parents[2]


def verify(ref=None):
    if ref is None:
        read = lambda path: (ROOT / path).read_bytes()
    else:
        raw = subprocess.check_output(['git', 'show', ref + ':' + source.MANIFEST], cwd=ROOT)
        seal = json.loads(raw)
        names = sorted(set(seal['files']) | {r['snapshot'] for r in seal['parent_files'].values()} | {source.MANIFEST})
        if subprocess.check_output(['git', 'ls-tree', '--name-only', ref, source.procurement.MANIFEST], cwd=ROOT).strip():
            procurement_files = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ref, source.procurement.ARCHIVE], cwd=ROOT).decode().splitlines()
            names = sorted(set(names) | set(source.procurement.PATHS) | set(procurement_files))
            successor = source.procurement.successor
            if subprocess.check_output(['git', 'ls-tree', '--name-only', ref, successor.MANIFEST], cwd=ROOT).strip():
                current_files = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ref, successor.ARCHIVE], cwd=ROOT).decode().splitlines()
                names = sorted(set(names) | set(successor.PATHS) | set(current_files))
        batch = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=ROOT,
                                        input=''.join(ref + ':' + p + '\n' for p in names).encode())
        stream = io.BytesIO(batch)
        data = {}
        for name in names:
            header = stream.readline().split()
            source.require(len(header) == 3 and header[1] == b'blob', 'PN420_GIT_BLOB_MISSING')
            data[name] = stream.read(int(header[2]))
            source.require(stream.read(1) == b'\n', 'PN420_GIT_BATCH_INVALID')
        read = data.__getitem__
    seal = source.qualify(read, full=True)
    source.require(seal is not None, 'PN420_MANIFEST_REQUIRED')
    return {'status': 'PERSONAL_NOTIFICATION_420_SOURCE_AND_OPERATOR_ACCEPTANCE_VERIFIED',
            'gateway_generation': 420, 'verified_pods': 70, 'source_files_per_pod': 17,
            'personal_work_business_accepted': True, 'scheduler_concurrency': 32, 'scheduler_budget': 10000,
            'sealed_files': len(seal['files']), 'production_actions': 0, 'work_messages_sent': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref')
    print(json.dumps(verify(parser.parse_args().ref), sort_keys=True))
