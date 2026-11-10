"""Validate the bounded pagination delta before projecting its PR42 parent."""
import ast
import hashlib
import json

ARCHIVE = "release/weknora-pagination-20261109/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "a05d658c2da09b5fde7ee880d007fbcd08603829fb5324d737f6cae8a9f49874"
PATHS = (
    "backend/packages/harness/deerflow/community/weknora/tools.py",
    "backend/packages/harness/deerflow/agents/middlewares/tool_output_budget_middleware.py",
)


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def historical_read(read):
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        return read
    assert digest(raw) == MANIFEST_SHA256, "WEKNORA_PAGINATION_MANIFEST_CHANGED"
    manifest = json.loads(raw)
    assert manifest["base_tree"] == "3ab826f4f6c9332b8477f257239e777deb181877"
    assert manifest["status"] == "NOT_DEPLOYED"
    assert set(manifest["changes"]) == set(PATHS)
    parents = {}
    for path, row in manifest["changes"].items():
        current = read(path)
        parent = read(ARCHIVE + "baseline/" + path)
        assert digest(current) == row["current_sha256"], (path, "WEKNORA_PAGINATION_CURRENT_CHANGED")
        assert digest(parent) == row["base_sha256"], (path, "WEKNORA_PAGINATION_PARENT_CHANGED")
        ast.parse(current, filename=path)
        parents[path] = parent

    def projected(path):
        return parents[path] if path in parents else read(path)

    return projected
