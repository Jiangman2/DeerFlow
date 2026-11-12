"""Verify the finite lease/attachment delta before exposing its immutable parent.

This validates source provenance only, never deployment or business acceptance.
"""
import ast
import hashlib
import json

ARCHIVE = "release/lease-work-source-20261110/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "9492ea5892eaa261265f2f31ec420a959f5aeebf86ac4540faad73a8169d76d1"
BASE_COMMIT = "ced7db0cf083aeff02fd142a7d8fb7dcc266213d"
SOURCE_TREE = "06933fb2211346321f47efa5d1ac07f706e9944e"
CONFIG = "deploy/enterprise/work-production-env-20261110.json"
GUIDE = "docs/attachment-conversion-work-20261110.md"
ARCHIVE_INPUTS = (ARCHIVE, "backend/tests", CONFIG, GUIDE)
MARKERS = (
    "backend/app/channels/attachment_storage.py",
    "backend/packages/harness/deerflow/utils/bounded_work.py",
    "backend/packages/harness/deerflow/utils/isolated_conversion.py",
    "backend/packages/harness/deerflow/utils/conversion_worker.py",
)


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def historical_view(read):
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        for path in MARKERS:
            try:
                read(path)
            except (KeyError, FileNotFoundError):
                continue
            raise AssertionError("LEASE_WORK_MANIFEST_REQUIRED")
        return read, None
    assert digest(raw) == MANIFEST_SHA256, "LEASE_WORK_MANIFEST_CHANGED"
    manifest = json.loads(raw)
    assert manifest["schema"] == "lease-work-source-provenance.v1", "LEASE_WORK_SCHEMA"
    assert manifest["base_commit"] == BASE_COMMIT and manifest["tested_source_tree"] == SOURCE_TREE, "LEASE_WORK_AUTHORITY"
    assert manifest["production_acceptance_claimed"] is False, "LEASE_WORK_ACCEPTANCE"
    changes = manifest["changes"]
    assert len(changes) == 15 and set(MARKERS) <= changes.keys(), "LEASE_WORK_SCOPE"
    parents = {}
    for path, row in changes.items():
        current = read(path)
        assert digest(current) == row["current_sha256"], (path, "LEASE_WORK_CURRENT_CHANGED")
        parent = None if row["base_sha256"] is None else read(ARCHIVE + "baseline/" + path)
        assert (digest(parent) if parent is not None else None) == row["base_sha256"], (path, "LEASE_WORK_PARENT_CHANGED")
        if path.endswith(".py"):
            ast.parse(current, filename=path)
        parents[path] = parent

    def projected(path):
        if path == MANIFEST or path in parents and parents[path] is None:
            raise KeyError(path)
        return parents[path] if path in parents else read(path)

    return projected, {
        "base_commit": BASE_COMMIT,
        "tested_source_tree": SOURCE_TREE,
        "changed_paths_verified": len(changes),
        "added_gateway_runtime": sorted(path for path, parent in parents.items() if parent is None and path.startswith(("backend/app/", "backend/packages/harness/")) and path.endswith(".py")),
        "production_acceptance_claimed": False,
    }
