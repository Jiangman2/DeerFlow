"""Offline verification of the November 2 Gateway403 / Frontend239 source archive."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile

from released_source_20261102 import ARCHIVE, DELTA, FORBIDDEN, PARENT_DELTA, load

ROOT = Path(__file__).resolve().parents[2]


def verify(ref=None):
    cache = {}
    if ref:
        raw = subprocess.check_output(["git", "archive", ref, "backend/app", "backend/packages/harness", "backend/tests", "frontend/src", "frontend/tests", ARCHIVE, DELTA, PARENT_DELTA], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            cache = {member.name: archive.extractfile(member).read() for member in archive if member.isfile()}

    def read(path):
        return cache[path] if ref else (ROOT / path).read_bytes()

    _, manifest = load(read)
    for path in FORBIDDEN:
        if ref:
            assert not subprocess.check_output(["git", "ls-tree", "--name-only", ref, path], cwd=ROOT).strip(), (path, "NOV02_DEFERRED_SOURCE_PRESENT")
        else:
            assert not (ROOT / path).exists(), (path, "NOV02_DEFERRED_SOURCE_PRESENT")
    for path in ("backend/app/gateway/services.py", "frontend/src/core/data-lake/auth.ts", "frontend/src/core/settings/store.ts"):
        assert b"work-grant" not in read(path) and b"inject_work_data_lake_context" not in read(path), (path, "NOV02_DEFERRED_HOOK_PRESENT")
    return {"status": "NOV02_RELEASED_SOURCE_VERIFIED", "delta": DELTA, "sources": len(manifest["sources"]), "tests": len(manifest["tests"]), "evidence": len(manifest["evidence"]), "gateway": 403, "frontend": 239, "work_datalake_deferred": True, "production_commands_executed": False, "scope": manifest["scope"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref")
    print(json.dumps(verify(parser.parse_args().ref)))
