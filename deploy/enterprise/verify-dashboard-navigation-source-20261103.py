"""Offline source verification; never a deployment or business-acceptance check."""

import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile

import dashboard_navigation_source_20261103 as navigation

ROOT = Path(__file__).resolve().parents[2]


def verify(ref=None):
    if ref:
        raw = subprocess.check_output(["git", "archive", ref, "frontend/src", "frontend/tests", navigation.ARCHIVE], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
        read = files.__getitem__
    else:
        read = lambda name: (ROOT / name).read_bytes()
    result = navigation.summary(navigation.qualify(read))
    assert result, "DASHBOARD_NAVIGATION_MANIFEST_REQUIRED"
    return dict(result, git_ref=ref, production_commands_executed=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref")
    print(json.dumps(verify(parser.parse_args().ref)))
