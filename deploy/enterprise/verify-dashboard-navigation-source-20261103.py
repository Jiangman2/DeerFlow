"""Offline source verification; never a deployment or business-acceptance check."""

import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile

import dashboard_navigation_source_20261103 as navigation
import work_integration_source_20261106 as work_followup

ROOT = Path(__file__).resolve().parents[2]


def verify(ref=None):
    if ref:
        extra = []
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, navigation.model_default_test.MANIFEST], cwd=ROOT).strip():
            extra.append(navigation.model_default_test.ARCHIVE)
        extra.extend(work_followup.archive_paths(ref, root=ROOT))
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, "release/artifact-delivery-recovery-20261109/SOURCE.json"], cwd=ROOT).strip():
            extra.append("release/artifact-delivery-recovery-20261109/")
        raw = subprocess.check_output(["git", "archive", ref, "frontend/src", "frontend/tests", navigation.ARCHIVE, *extra], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
        read = files.__getitem__
    else:
        read = lambda name: (ROOT / name).read_bytes()
    read, qualified_work = work_followup.historical_view(read)
    result = navigation.summary(navigation.qualify(read))
    assert result, "DASHBOARD_NAVIGATION_MANIFEST_REQUIRED"
    return dict(result, git_ref=ref, production_commands_executed=False, work_integration_followup=qualified_work)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref")
    print(json.dumps(verify(parser.parse_args().ref)))
