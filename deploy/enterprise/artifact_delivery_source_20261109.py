"""Validate the artifact delivery delta before projecting its PR42 parent."""
import ast
import hashlib
import json

ARCHIVE = "release/artifact-delivery-recovery-20261109/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "0420e05dc6de2a8da22ef6a3c0962aa762dbc81c2543e4b934d71c329ff4ab30"
PATHS = ('backend/packages/harness/deerflow/agents/middlewares/artifact_delivery_middleware.py', 'backend/tests/test_directory_listing_delivery.py')


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def historical_read(read):
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        return read
    assert digest(raw) == MANIFEST_SHA256, "ARTIFACT_DELIVERY_MANIFEST_CHANGED"
    manifest = json.loads(raw)
    assert manifest["base_tree"] == "6c31ffdcabdf8f2852304e6b3528835880f9be0e"
    assert manifest["status"] == "NOT_DEPLOYED"
    assert set(manifest["changes"]) == set(PATHS)
    parents = {}
    for path, row in manifest["changes"].items():
        current = read(path)
        parent = read(ARCHIVE + "baseline/" + path)
        assert digest(current) == row["current_sha256"], (path, "ARTIFACT_DELIVERY_CURRENT_CHANGED")
        assert digest(parent) == row["base_sha256"], (path, "ARTIFACT_DELIVERY_PARENT_CHANGED")
        ast.parse(current, filename=path)
        parents[path] = parent

    def projected(path):
        return parents[path] if path in parents else read(path)

    return projected
