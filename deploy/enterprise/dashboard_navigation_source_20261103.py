"""Verify the scoped frontend candidate before projecting immutable baseline bytes."""

import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath

_spec = importlib.util.spec_from_file_location("model_default_test_followup_20261106", Path(__file__).with_name("model_default_test_followup_20261106.py"))
model_default_test = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(model_default_test)

ARCHIVE = "release/dashboard-navigation-20261103-r2/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "5a8f98a6ebd63175e83f6db294e2c7f826efaa6662b69c66d7043147b7086b92"
BASE_COMMIT = "443cbd8ab6e2e74298075976fa68af079c1f7100"


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def qualify(read):
    if isinstance(read, HistoricalReader):
        return read.qualification
    read = model_default_test.historical_read(read)
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        return {}
    assert digest(raw) == MANIFEST_SHA256, "DASHBOARD_NAVIGATION_SEAL_CHANGED"
    manifest = json.loads(raw)
    assert manifest["schema"] == "dashboard-navigation-source.v1", "DASHBOARD_NAVIGATION_SCHEMA"
    assert manifest["base_commit"] == BASE_COMMIT, "DASHBOARD_NAVIGATION_PARENT"
    assert manifest["status"] == "CANDIDATE_SOURCE_NOT_DEPLOYMENT_ACCEPTANCE", "DASHBOARD_NAVIGATION_STATUS"
    assert manifest["production_acceptance_claimed"] is False, "DASHBOARD_NAVIGATION_OVERCLAIM"
    assert manifest["gateway_modified"] is False and manifest["database_modified"] is False, "DASHBOARD_NAVIGATION_SCOPE"
    changes = manifest["changes"]
    assert sum(path.startswith("frontend/src/") for path in changes) == 10, "DASHBOARD_NAVIGATION_RUNTIME_SCOPE"
    for path, row in changes.items():
        pure = PurePosixPath(path)
        assert not pure.is_absolute() and ".." not in pure.parts and "\\" not in path and pure.as_posix() == path, "DASHBOARD_NAVIGATION_PATH"
        assert path.startswith(("frontend/src/", "frontend/tests/")), "DASHBOARD_NAVIGATION_SCOPE"
        assert digest(read(path)) == row["current_sha256"], (path, "DASHBOARD_NAVIGATION_CURRENT_CHANGED")
        if row["base_sha256"] is None:
            assert row["baseline_path"] is None, "DASHBOARD_NAVIGATION_UNEXPECTED_BASELINE"
        else:
            assert row["baseline_path"] == ARCHIVE + "baseline/" + path, "DASHBOARD_NAVIGATION_BASELINE_PATH"
            assert digest(read(row["baseline_path"])) == row["base_sha256"], (path, "DASHBOARD_NAVIGATION_BASELINE_CHANGED")
    return manifest


class HistoricalReader:
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
        "changed_paths_verified": len(qualification["changes"]),
        "frontend_runtime_paths": [p for p in qualification["changes"] if p.startswith("frontend/src/")],
        "production_acceptance_claimed": False,
    }
