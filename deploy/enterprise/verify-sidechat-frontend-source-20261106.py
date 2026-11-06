"""Verify the Frontend 242 source registration offline; never run deployment tools."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile

import sidechat_frontend_source_20261106 as source

ROOT = Path(__file__).resolve().parents[2]


def verify(ref=None):
    if ref:
        raw = subprocess.check_output([
            'git', 'archive', ref, source.ARCHIVE, *sorted(source.PATHS),
            *source.EVIDENCE_PATHS,
        ], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            files = {member.name: archive.extractfile(member).read()
                     for member in archive if member.isfile()}
        read = files.__getitem__
    else:
        read = lambda path: (ROOT / path).read_bytes()
    qualification = source.qualify(read)
    assert qualification, 'SIDECHAT_SOURCE_MANIFEST_REQUIRED'
    return dict(source.summary(qualification), status='SIDECHAT_FRONTEND_SOURCE_VERIFIED',
                historical_inventories_modified=False, production_commands_executed=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref')
    print(json.dumps(verify(parser.parse_args().ref), sort_keys=True))
