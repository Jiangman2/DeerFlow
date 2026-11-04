"""Verify the scoped, already-released November 1 source and receipt archive offline."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

from released_source_20261102 import ARCHIVE as NOV02_ARCHIVE, DELTA as NOV02_DELTA, PARENT_DELTA, historical_read
from released_source_20261102 import CI_ARCHIVE, CI_MANIFEST, followup

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = "release/nov01-released-fixes-source-20261102/"


def verify(ref=None):
    has_nov02 = bool(subprocess.check_output(["git", "ls-tree", "--name-only", ref, NOV02_DELTA], cwd=ROOT).strip()) if ref else (ROOT / NOV02_DELTA).is_file()
    archived = {}
    if ref:
        roots = ["backend/app", "backend/packages/harness", "backend/tests", "frontend/src", "frontend/tests", "skills/public/data-analysis", ARCHIVE]
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.ARTIFACT_PARENT], cwd=ROOT).strip():
            roots.append(followup.ARTIFACT_PARENT)
        if has_nov02:
            roots.extend((NOV02_ARCHIVE, NOV02_DELTA, PARENT_DELTA))
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, CI_MANIFEST], cwd=ROOT).strip():
            roots.append(CI_ARCHIVE)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.MANIFEST], cwd=ROOT).strip():
            roots.append(followup.ARCHIVE)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.navigation.MANIFEST], cwd=ROOT).strip():
            roots.append(followup.navigation.ARCHIVE)
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.alignment.WORK_RELEASE_ALIGNMENT], cwd=ROOT).strip():
            roots.extend((followup.alignment.WORK_RELEASE_ALIGNMENT, *followup.alignment.WORK_PARENT_MANIFESTS))
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, followup.alignment.WORK_RELEASE_ALIGNMENT_ARCHIVE], cwd=ROOT).strip():
            roots.append(followup.alignment.WORK_RELEASE_ALIGNMENT_ARCHIVE)
        raw = subprocess.check_output(["git", "archive", ref, *roots], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            archived = {member.name: archive.extractfile(member).read() for member in archive if member.isfile()}

    def read(path):
        if ref:
            return archived[path]
        return (ROOT / path).read_bytes()

    read = followup.artifact_directory_historical_read(read)
    if has_nov02:
        read = historical_read(read)

    digest = lambda raw: hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    manifest = json.loads(read(ARCHIVE + "SOURCE.json"))
    assert manifest["schema"] == "nov01-released-source.v1"
    assert len(manifest["sources"]) == 20
    assert manifest["work_datalake_deferred"] and not manifest["schema_migration_requested"]
    for path, record in manifest["sources"].items():
        assert ".." not in Path(path).parts and not Path(path).is_absolute()
        assert digest(read(path)) == record["source_sha256"], (path, "RELEASED_SOURCE_CHANGED")
        if record.get("normalization"):
            assert record["normalization"] == "python_layout_ast_equal"
            original = read(record["published_source"])
            assert digest(original) == record["deployed_sha256"], "PUBLISHED_SOURCE_CHANGED"
            assert ast.dump(ast.parse(original), include_attributes=False) == ast.dump(ast.parse(read(path)), include_attributes=False), "PUBLISHED_LOGIC_CHANGED"
        else:
            assert record["source_sha256"] == record["deployed_sha256"], "UNDECLARED_NORMALIZATION"
    for path, expected in manifest["evidence"].items():
        assert Path(path).name == path
        assert digest(read(ARCHIVE + path)) == expected, (path, "RECEIPT_CHANGED")
    for path, record in manifest.get("ci_formatting", {}).items():
        original, current = read(record["baseline_path"]), read(path)
        assert digest(original) == record["base_sha256"] and digest(current) == record["source_sha256"], "FORMAT_SOURCE_CHANGED"
        assert ast.dump(ast.parse(original), include_attributes=False) == ast.dump(ast.parse(current), include_attributes=False), "FORMAT_LOGIC_CHANGED"
    forbidden = [
        "backend/app/gateway/auth/data_lake_work.py",
        "backend/packages/harness/deerflow/persistence/data_lake_auth/work_grant.py",
        "backend/packages/harness/deerflow/persistence/migrations/versions/0023_data_lake_work_grants.py",
    ]
    for path in forbidden:
        if ref:
            assert not subprocess.check_output(["git", "ls-tree", "--name-only", ref, path], cwd=ROOT).strip(), path
        else:
            assert not (ROOT / path).exists(), path
    for path in ("backend/app/gateway/services.py", "frontend/src/core/data-lake/auth.ts", "frontend/src/core/settings/store.ts"):
        assert b"work-grant" not in read(path) and b"inject_work_data_lake_context" not in read(path), path
    return {"status": "SCOPED_RELEASED_SOURCE_VERIFIED", "sources": 20,
            "evidence": len(manifest["evidence"]), "work_datalake_deferred": True,
            "production_commands_executed": False, "browser_business_acceptance_pending": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref")
    print(json.dumps(verify(parser.parse_args().ref)))
