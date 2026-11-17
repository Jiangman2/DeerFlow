"""Verify the standards portal candidate from disk or Git without deployment I/O."""
import argparse
import io
import json
from pathlib import Path
import subprocess

import standards_portal_source_20261117 as source

ROOT = Path(__file__).resolve().parents[2]


def verify(ref=None):
    if ref is None:
        read = lambda path: (ROOT / path).read_bytes()
    else:
        names = sorted(source.FILES | {source.MANIFEST})
        raw = subprocess.check_output(["git", "cat-file", "--batch"], cwd=ROOT,
                                      input="".join(ref + ":" + p + "\n" for p in names).encode())
        stream = io.BytesIO(raw)
        files = {}
        for path in names:
            header = stream.readline().split()
            source.require(len(header) == 3 and header[1] == b"blob", "STANDARDS_PORTAL_GIT_BLOB_MISSING")
            files[path] = stream.read(int(header[2]))
            source.require(stream.read(1) == b"\n", "STANDARDS_PORTAL_GIT_BATCH_INVALID")
        read = files.__getitem__
    manifest = source.qualify(read)
    source.require(manifest is not None, "STANDARDS_PORTAL_MANIFEST_REQUIRED")
    return dict(source.summary(manifest), source_verified=True,
                runtime_accepted=False, production_commands_executed=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref")
    print(json.dumps(verify(parser.parse_args().ref), sort_keys=True))
