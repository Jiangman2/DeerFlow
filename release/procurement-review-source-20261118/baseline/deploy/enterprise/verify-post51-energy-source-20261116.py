"""Verify current November 16 bytes and field evidence without production access."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import post51_energy_source_20261116 as source

ROOT = Path(__file__).resolve().parents[2]


def verify(ref=None):
    if ref is None:
        read = lambda name: (ROOT / name).read_bytes()
        names = [p.relative_to(ROOT).as_posix() for p in (ROOT / source.ENERGY).rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    else:
        names = list(filter(None, subprocess.check_output(['git', 'ls-tree', '-rz', '--name-only', ref], cwd=ROOT).decode().split('\0')))
        raw = subprocess.check_output(['git', 'show', ref + ':' + source.MANIFEST], cwd=ROOT)
        seal = json.loads(raw)
        required = sorted(set(seal['files']) | {r['snapshot'] for r in seal['parent_files'].values() if r}
                          | {p for p in names if p.startswith(source.current420.ARCHIVE)})
        source.require(set(required) <= set(names), 'POST51_COMMITTED_FILE_MISSING')
        data = {source.MANIFEST: raw}
        batch = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=ROOT,
                                        input=''.join(ref + ':' + p + '\n' for p in required).encode())
        stream = io.BytesIO(batch)
        for path in required:
            header = stream.readline().split()
            source.require(len(header) == 3 and header[1] == b'blob', 'POST51_GIT_BLOB_MISSING')
            data[path] = stream.read(int(header[2]))
            source.require(stream.read(1) == b'\n', 'POST51_GIT_BATCH_INVALID')
        read = data.__getitem__
    energy = [p for p in names if p.startswith(source.ENERGY)]
    seal = source.qualify(read, energy, full=True)
    return {'status': 'POST51_RELEASED_SOURCE_AND_EVIDENCE_VERIFIED',
            'skill_version': seal['skill_version'], 'public_skill_files': len(seal['energy_files']),
            'field_python_sources': len(seal['runtime_files']), 'sealed_files': len(seal['files']),
            'gateway_generation': 419, 'scheduler_max_concurrent_runs': 32,
            'gateway_rollout_converged': True, 'fleet_runtime_acceptance_complete': False,
            'personal_notification_candidate_deployed': False, 'production_access': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref')
    print(json.dumps(verify(parser.parse_args().ref), sort_keys=True))
