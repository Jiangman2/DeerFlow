"""Validate a committed successor before exposing immutable historical source bytes.

This is source provenance only, not acceptance of a new production deployment.
"""

import ast
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath

_spec = importlib.util.spec_from_file_location("dashboard_navigation_source_20261103", Path(__file__).with_name("dashboard_navigation_source_20261103.py"))
navigation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(navigation)

_alignment_spec = importlib.util.spec_from_file_location("work_release_alignment_20261104", Path(__file__).with_name("work_release_alignment_20261104.py"))
alignment = importlib.util.module_from_spec(_alignment_spec)
_alignment_spec.loader.exec_module(alignment)

ARCHIVE = "release/enterprise-source-followup-20261103/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "d383005667ae3f623045ae54fe333ba98677ec3ea03096914b23daf59342d1bc"
BASE_COMMIT = "5a29e5aa66b4190024bfd55be14cbd1cc8b16f77"
SOURCE_COMMIT = "1fbc07fcf6a7a7258d15e84e040042da4b5c8748"
CI_FORMATTING = ARCHIVE + "CI_FORMATTING.json"
CI_FORMATTING_SHA256 = "427d6bcc12ba537c06b97dcbc4755a27b4244b47e671004116750bdef9e2c922"
UNUSED_TEST_IMPORTS = {
    "backend/tests/test_weknora_relay_budget.py": "json",
    "backend/tests/test_weknora_router_counts.py": "asyncio",
}


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def formatting_fingerprint(raw, path):
    """Ignore layout and contiguous import ordering, never executable statements."""
    tree = ast.parse(raw)
    unused = UNUSED_TEST_IMPORTS.get(path)
    if unused:
        tree.body = [node for node in tree.body if not (
            isinstance(node, ast.Import) and len(node.names) == 1
            and node.names[0].name == unused and node.names[0].asname is None
        )]
    result, imports = [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                atom = ast.Import(names=[alias]) if isinstance(node, ast.Import) else ast.ImportFrom(module=node.module, names=[alias], level=node.level)
                imports.append(ast.dump(atom, include_attributes=False))
        else:
            if imports:
                result.append(sorted(imports))
                imports = []
            result.append(ast.dump(node, include_attributes=False))
    if imports:
        result.append(sorted(imports))
    return result


def formatting_read(read, manifest):
    try:
        raw = read(CI_FORMATTING)
    except (FileNotFoundError, KeyError):
        return read, 0
    assert digest(raw) == CI_FORMATTING_SHA256, "ENTERPRISE_FORMATTING_SEAL_CHANGED"
    formatting = json.loads(raw)
    assert formatting["schema"] == "enterprise-source-formatting.v1", "ENTERPRISE_FORMATTING_SCHEMA"
    assert formatting["parent_manifest_sha256"] == MANIFEST_SHA256, "ENTERPRISE_FORMATTING_PARENT"
    originals = {}
    for path, row in formatting["changes"].items():
        assert path in manifest["changes"] and path.endswith(".py"), "ENTERPRISE_FORMATTING_SCOPE"
        assert row["baseline_path"] == ARCHIVE + "ci-baseline/" + path, "ENTERPRISE_FORMATTING_PATH"
        original, current = read(row["baseline_path"]), read(path)
        assert digest(original) == row["baseline_sha256"] == manifest["changes"][path]["current_sha256"], (path, "ENTERPRISE_FORMATTING_BASE_CHANGED")
        assert digest(current) == row["formatted_sha256"], (path, "ENTERPRISE_FOLLOWUP_CURRENT_CHANGED")
        assert formatting_fingerprint(original, path) == formatting_fingerprint(current, path), (path, "ENTERPRISE_FORMATTING_LOGIC_CHANGED")
        originals[path] = original
    return lambda path: originals[path] if path in originals else read(path), len(originals)


def qualify(read):
    if isinstance(read, HistoricalReader):
        verified = qualify(read.original_read)
        assert verified == read.qualification, "ENTERPRISE_FOLLOWUP_READER_CHANGED"
        return verified
    return _qualify_projected(alignment.historical_read(read))


def _qualify_projected(read):
    read = navigation.historical_read(read)
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
    read, formatted = formatting_read(read, manifest)
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
    return dict(manifest, formatting_corrections_verified=formatted)


class HistoricalReader:
    """Only the fixed, verified changes are projected back for older audits."""

    def __init__(self, read, qualification):
        verified = qualify(read)
        assert verified and verified == qualification, "ENTERPRISE_FOLLOWUP_READER_CHANGED"
        self.original_read = read
        self.read = navigation.historical_read(alignment.historical_read(read))
        self.qualification = verified

    def __call__(self, path):
        row = self.qualification["changes"].get(path)
        if row is None:
            return self.read(path)
        if row["baseline_path"] is None:
            raise KeyError(path)
        return self.read(row["baseline_path"])


def historical_read(read):
    if isinstance(read, HistoricalReader):
        qualify(read)
        return read
    original = read
    read = alignment.historical_read(read)
    read = navigation.historical_read(read)
    qualification = _qualify_projected(read)
    return HistoricalReader(original, qualification) if qualification else read


def summary(qualification):
    if not qualification:
        return None
    return {
        "status": qualification["status"],
        "source_commit": qualification["source_commit"],
        "changed_paths_verified": len(qualification["changes"]),
        "runtime_sources_verified": sum(row["kind"] == "runtime-source" for row in qualification["changes"].values()),
        "production_acceptance_claimed": False,
        "formatting_corrections_verified": qualification.get("formatting_corrections_verified", 0),
    }
