"""Offline IM S08 source verification; never a deployment or acceptance check."""

import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile

import work_integration_source_20261106 as work_followup

ROOT = Path(__file__).resolve().parents[2]


def verify(ref=None):
    if ref:
        extra = []
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, work_followup.MANIFEST], cwd=ROOT).strip():
            extra.append(work_followup.MANIFEST)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, "release/im-s08-source-20261111/SOURCE.json"], cwd=ROOT).strip():
            extra.append("release/im-s08-source-20261111/")
        raw = subprocess.check_output(["git", "archive", ref, "backend/app", "backend/packages/harness", "backend/scripts", "backend/tests", "frontend", *extra], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            files = {member.name: archive.extractfile(member).read() for member in archive if member.isfile()}
        read = files.__getitem__
    else:
        read = lambda name: (ROOT / name).read_bytes()
    read, qualified_work = work_followup.historical_view(read)
    result = (qualified_work or {}).get("im_s08_parent")
    assert result, "IM_S08_MANIFEST_REQUIRED"
    return dict(result, git_ref=ref, production_commands_executed=False, work_integration_followup=qualified_work)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref")
    print(json.dumps(verify(parser.parse_args().ref)))