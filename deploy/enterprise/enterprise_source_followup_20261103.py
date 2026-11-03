"""Validate a committed successor before exposing immutable historical source bytes.

This is source provenance only, not acceptance of a new production deployment.
"""

import ast
import hashlib
import json
from pathlib import PurePosixPath

ARCHIVE = "release/enterprise-source-followup-20261103/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "d383005667ae3f623045ae54fe333ba98677ec3ea03096914b23daf59342d1bc"
BASE_COMMIT = "5a29e5aa66b4190024bfd55be14cbd1cc8b16f77"
SOURCE_COMMIT = "1fbc07fcf6a7a7258d15e84e040042da4b5c8748"


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def qualify(read):
    if isinstance(read, HistoricalReader):
        return read.qualification
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        return {}
    assert digest(raw) == MANIFEST_SHA256, "ENTERPRISE_FOLLOWUP_SEAL_CHANGED"
    manifest = json.loads(raw)
    assert manifest["schema"] == "enterprise-source-followup.v1", "ENTERPRISE_FOLLOWUP_SCHEMA"
    assert manifest["status"] == "COMMITTED_SOURCE_NOT_DEPLOYMENT_ACCEPTANCE", "ENTERPRISE_FOLLOWUP_STATUS"
    assert manifest["base_commit"] == BASE_COMMIT, "ENTERPRISE_FOLLOWUP_PARENT"
    assert manifest["source_commit"] == SOURCE_COMMIT, "ENTERPRISE_FOLLOWUP_SOURCE"
    assert manifest["production_acceptance_claimed"] is False, "ENTERPRISE_FOLLOWUP_OVERCLAIM"
    assert manifest["work_datalake_deferred"] is True and manifest["database_head"] == "0022", "ENTERPRISE_FOLLOWUP_FEATURE_SCOPE"
    for path, row in manifest["changes"].items():
        pure = PurePosixPath(path)
        assert not pure.is_absolute() and ".." not in pure.parts and "\\" not in path and pure.as_posix() == path, "ENTERPRISE_FOLLOWUP_PATH"
        runtime = path.startswith(("backend/app/", "backend/packages/harness/", "frontend/src/"))
        tests = path.startswith(("backend/tests/", "frontend/tests/"))
        assert (runtime and row["kind"] == "runtime-source") or (tests and row["kind"] == "regression-test"), "ENTERPRISE_FOLLOWUP_SCOPE"
        current = read(path)
        assert digest(current) == row["current_sha256"], (path, "ENTERPRISE_FOLLOWUP_CURRENT_CHANGED")
        if path.endswith(".py"):
            ast.parse(current, filename=path)
        if row["base_sha256"] is None:
            assert row["baseline_path"] is None, "ENTERPRISE_FOLLOWUP_UNEXPECTED_BASELINE"
        else:
            assert row["baseline_path"] == ARCHIVE + "baseline/" + path, "ENTERPRISE_FOLLOWUP_BASELINE_PATH"
            assert digest(read(row["baseline_path"])) == row["base_sha256"], (path, "ENTERPRISE_FOLLOWUP_BASELINE_CHANGED")
    return manifest


class HistoricalReader:
    """Only the fixed, verified changes are projected back for older audits."""

    def __init__(self, read, qualification):
        self.read = read
        self.qualification = qualification

    def __call__(self, path):
        row = self.qualification["changes"].get(path)
        if row is None:
            return self.read(path)
        if row["baseline_path"] is None:
            raise KeyError(path)
        return self.read(row["baseline_path"])


def historical_read(read):
    if isinstance(read, HistoricalReader):
        return read
    qualification = qualify(read)
    return HistoricalReader(read, qualification) if qualification else read


def summary(qualification):
    if not qualification:
        return None
    return {
        "status": qualification["status"],
        "source_commit": qualification["source_commit"],
        "changed_paths_verified": len(qualification["changes"]),
        "runtime_sources_verified": sum(row["kind"] == "runtime-source" for row in qualification["changes"].values()),
        "production_acceptance_claimed": False,
    }
