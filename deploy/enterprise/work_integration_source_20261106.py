"""Qualify this Work source delta before reading its immutable Git parent."""

import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import subprocess

_im_s08_spec = importlib.util.spec_from_file_location(
    "im_s08_source_20261111", Path(__file__).with_name("im_s08_source_20261111.py")
)
im_s08 = importlib.util.module_from_spec(_im_s08_spec)
_im_s08_spec.loader.exec_module(im_s08)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "release/work-integration-followup-20261106/SOURCE.json"
MANIFEST_SHA256 = "535503070c0c53a0a928fe6ab362bbc6bfc8de685a681fed7fd7832c6629dd76"
BASE_COMMIT = "3f7a59c06db925359f2d48c89057ffc73b88803b"
_MARKERS = (
    "backend/app/integrations/work/file_search.py",
    "frontend/src/components/workspace/scheduled-task-work-notification.tsx",
)
_LOCKED_TEST = "frontend/tests/unit/core/scheduled-tasks/hooks.test.ts"


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _gateway_source(path):
    return path.startswith(("backend/app/", "backend/packages/harness/")) and path.endswith(".py")


def _allowed_path(path):
    pure = PurePosixPath(path)
    return (
        not pure.is_absolute()
        and ".." not in pure.parts
        and "\\" not in path
        and pure.as_posix() == path
        and (_gateway_source(path) or path.startswith("frontend/src/") or path == _LOCKED_TEST)
    )


def _parent_sources(paths):
    queries = {path: f"{BASE_COMMIT}:{path}".encode() for path in paths}
    raw = subprocess.check_output(["git", "cat-file", "--batch"], input=b"\n".join(queries.values()) + b"\n", cwd=ROOT)
    stream = io.BytesIO(raw)
    parents = {}
    for path, query in queries.items():
        header = stream.readline().rstrip(b"\n")
        if header == query + b" missing":
            parents[path] = None
            continue
        parts = header.split()
        assert len(parts) == 3 and parts[1] == b"blob", (path, "WORK_FOLLOWUP_PARENT_BLOB")
        parents[path] = stream.read(int(parts[2]))
        assert stream.read(1) == b"\n", "WORK_FOLLOWUP_PARENT_BLOB"
    assert not stream.read(), "WORK_FOLLOWUP_PARENT_BLOB"
    return parents


def historical_view(read):
    """Validate all declared current and parent bytes, then expose only their parents."""
    read, im_s08_manifest = im_s08.historical_view(read)
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        for path in _MARKERS:
            try:
                read(path)
            except (KeyError, FileNotFoundError):
                continue
            raise AssertionError("WORK_FOLLOWUP_MANIFEST_REQUIRED")
        return read, None
    assert digest(raw) == MANIFEST_SHA256, "WORK_FOLLOWUP_SEAL_CHANGED"
    manifest = json.loads(raw)
    assert set(manifest) == {"schema", "status", "base_commit", "production_acceptance_claimed", "changes"}, "WORK_FOLLOWUP_SCHEMA"
    assert manifest["schema"] == "work-integration-followup.v1" and manifest["status"] == "NOT_DEPLOYED", "WORK_FOLLOWUP_SCHEMA"
    assert manifest["base_commit"] == BASE_COMMIT and manifest["production_acceptance_claimed"] is False, "WORK_FOLLOWUP_AUTHORITY"
    changes = manifest["changes"]
    assert isinstance(changes, dict) and set(_MARKERS) <= set(changes), "WORK_FOLLOWUP_SCOPE"
    assert all(_allowed_path(path) for path in changes), "WORK_FOLLOWUP_SCOPE"
    parents = _parent_sources(changes)
    for path, row in changes.items():
        assert set(row) == {"base_sha256", "current_sha256"}, (path, "WORK_FOLLOWUP_SCHEMA")
        current, previous = read(path), parents[path]
        assert digest(current) == row["current_sha256"], (path, "WORK_FOLLOWUP_CURRENT_CHANGED")
        assert (digest(previous) if previous is not None else None) == row["base_sha256"], (path, "WORK_FOLLOWUP_PARENT_CHANGED")
        if path.endswith(".py"):
            ast.parse(current, filename=path)

    def projected(path):
        if path not in parents:
            return read(path)
        parent = parents[path]
        if parent is None:
            raise KeyError(path)
        return parent

    added = [path for path, parent in parents.items() if parent is None]
    return projected, {
        "status": manifest["status"],
        "base_commit": BASE_COMMIT,
        "changed_paths_verified": len(changes),
        "added_gateway_runtime": sorted(path for path in added if _gateway_source(path)),
        "added_frontend_runtime": sorted(path for path in added if path.startswith("frontend/src/")),
        "production_acceptance_claimed": False,
        "im_s08_parent": im_s08_manifest,
    }
