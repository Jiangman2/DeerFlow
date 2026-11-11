"""Offline verification of the November 2 Gateway403 / Frontend239 source archive."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile

from released_source_20261102 import ARCHIVE, CI_ARCHIVE, CI_MANIFEST, DELTA, FORBIDDEN, PARENT_DELTA, followup, load

from released_source_20261102 import composer
from work_integration_source_20261106 import MANIFEST as WORK_MANIFEST, historical_view as work_historical_view
import im_s08_source_20261111 as im_s08

ROOT = Path(__file__).resolve().parents[2]


def verify(ref=None):
    cache = {}
    if ref:
        extra = [CI_ARCHIVE] if subprocess.check_output(["git", "ls-tree", "--name-only", ref, CI_MANIFEST], cwd=ROOT).strip() else []
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.MANIFEST], cwd=ROOT).strip():
            extra.append(followup.ARCHIVE)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.IMAGE_PNG_ARCHIVE.rstrip("/")], cwd=ROOT).strip():
            extra.append(followup.IMAGE_PNG_ARCHIVE)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.sidechat.MANIFEST], cwd=ROOT).strip():
            extra.extend((followup.sidechat.ARCHIVE, *followup.sidechat.EVIDENCE_PATHS))
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.new_chat.MANIFEST], cwd=ROOT).strip():
            extra.extend((followup.new_chat.ARCHIVE, followup.new_chat.GUIDE))
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.navigation.MANIFEST], cwd=ROOT).strip():
            extra.append(followup.navigation.ARCHIVE)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.navigation.model_default_test.MANIFEST], cwd=ROOT).strip():
            extra.append(followup.navigation.model_default_test.ARCHIVE)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.alignment.WORK_RELEASE_ALIGNMENT], cwd=ROOT).strip():
            extra.extend((followup.alignment.WORK_RELEASE_ALIGNMENT, *followup.alignment.WORK_PARENT_MANIFESTS))
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.alignment.WORK_RELEASE_ALIGNMENT_ARCHIVE], cwd=ROOT).strip():
            extra.append(followup.alignment.WORK_RELEASE_ALIGNMENT_ARCHIVE)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, composer.MANIFEST], cwd=ROOT).strip():
            extra.append(composer.ARCHIVE)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, WORK_MANIFEST], cwd=ROOT).strip():
            extra.append(WORK_MANIFEST)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, im_s08.MANIFEST], cwd=ROOT).strip():
            extra.extend((im_s08.ARCHIVE, "backend/scripts"))
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, "release/artifact-delivery-recovery-20261109/SOURCE.json"], cwd=ROOT).strip():
            extra.append("release/artifact-delivery-recovery-20261109/")
        raw = subprocess.check_output(["git", "archive", ref, "backend/app", "backend/packages/harness", "backend/tests", "frontend", ARCHIVE, DELTA, PARENT_DELTA, *extra], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            cache = {member.name: archive.extractfile(member).read() for member in archive if member.isfile()}

    def read(path):
        return cache[path] if ref else (ROOT / path).read_bytes()

    historical, work_followup = work_historical_view(read)
    _, manifest = load(historical)
    for path in FORBIDDEN:
        if ref:
            assert not subprocess.check_output(["git", "ls-tree", "--name-only", ref, path], cwd=ROOT).strip(), (path, "NOV02_DEFERRED_SOURCE_PRESENT")
        else:
            assert not (ROOT / path).exists(), (path, "NOV02_DEFERRED_SOURCE_PRESENT")
    for path in ("backend/app/gateway/services.py", "frontend/src/core/data-lake/auth.ts", "frontend/src/core/settings/store.ts"):
        assert b"work-grant" not in read(path) and b"inject_work_data_lake_context" not in read(path), (path, "NOV02_DEFERRED_HOOK_PRESENT")
    successor = followup.summary(followup.qualify(historical))
    return {"status": "ENTERPRISE_SOURCE_FOLLOWUP_ON_NOV02_VERIFIED" if successor else "NOV02_RELEASED_SOURCE_VERIFIED", "enterprise_source_followup": successor, "work_integration_followup": work_followup, "delta": DELTA, "sources": len(manifest["sources"]), "tests": len(manifest["tests"]), "evidence": len(manifest["evidence"]), "gateway": 403, "frontend": 239, "work_datalake_deferred": True, "production_commands_executed": False, "scope": manifest["scope"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref")
    print(json.dumps(verify(parser.parse_args().ref)))
