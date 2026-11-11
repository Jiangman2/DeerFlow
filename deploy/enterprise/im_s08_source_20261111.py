"""Qualify the scoped IM S08 source candidate before projecting its 0716 parent."""

import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = "release/im-s08-source-20261111/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = '4b3f5440c680564a7a55b2ea35a875add2a50f925e90a20dfb1ef6ad3446f49e'
BASE_COMMIT = "ced7db0cf083aeff02fd142a7d8fb7dcc266213d"
LOCK = "deploy/enterprise/production-source-lock-20261029.json"
PARENT_MANIFEST = "deploy/enterprise/feishu-export-source-candidate-20261106.json"
SCHEMA = "im-s08-source.v1"
COMPONENTS = ("gateway", "frontend", "test")
MARKERS = (ARCHIVE + "README.md",)


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _allowed_path(path):
    if not path or path.startswith("/") or "\\" in path or ":" in path:
        return False
    return ".." not in Path(path).parts


def _parent_sources(paths):
    """Read the exact parent bytes for the declared paths at BASE_COMMIT."""
    paths = sorted(set(paths))
    if not paths:
        return {}
    query = "".join(f"{BASE_COMMIT}:{path}\n" for path in paths).encode()
    raw = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        input=query,
        capture_output=True,
        check=True,
    ).stdout
    result = {}
    offset = 0
    for path in paths:
        end = raw.index(b"\n", offset)
        header = raw[offset:end].decode("utf-8", "replace")
        offset = end + 1
        if header.endswith(" missing"):
            result[path] = None
            continue
        _, _, size = header.split(" ")
        size = int(size)
        result[path] = raw[offset : offset + size]
        offset += size + 1
    return result


class HistoricalReader:
    """Project the sealed parent bytes for the declared paths only."""

    def __init__(self, read, projected):
        self._read = read
        self._projected = projected

    def __call__(self, path):
        if path.startswith(ARCHIVE):
            raise KeyError(path)
        if path in self._projected:
            if self._projected[path] is None:
                raise KeyError(path)
            return self._projected[path]
        return self._read(path)


def historical_view(read):
    """Return a reader that projects the 0716 parent plus the sealed summary."""
    try:
        raw = read(MANIFEST)
    except (KeyError, OSError):
        raw = None
    if raw is None:
        for marker in MARKERS:
            try:
                read(marker)
            except (KeyError, OSError):
                continue
            raise AssertionError("IM_S08_MANIFEST_REQUIRED")
        return read, None
    raw = raw.replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == MANIFEST_SHA256, "IM_S08_SEAL_CHANGED"
    manifest = json.loads(raw)
    assert manifest["schema"] == SCHEMA, "IM_S08_SCHEMA_CHANGED"
    assert manifest["status"] == "NOT_DEPLOYED", "IM_S08_STATUS_CHANGED"
    assert manifest["base_commit"] == BASE_COMMIT, "IM_S08_BASE_CHANGED"
    assert manifest["production_acceptance_claimed"] is False, "IM_S08_ACCEPTANCE_CHANGED"
    assert set(manifest["changes"]) == set(COMPONENTS), "IM_S08_SCOPE_CHANGED"
    declared = [path for group in manifest["changes"].values() for path in group]
    assert len(declared) == len(set(declared)), "IM_S08_DUPLICATE_PATH"
    for path in declared:
        assert _allowed_path(path), path
    for group in manifest["changes"].values():
        for path, row in group.items():
            assert set(row) == {"base_sha256", "candidate_sha256"}, path
            assert digest(read(path)) == row["candidate_sha256"], (
                path,
                "IM_S08_CURRENT_CHANGED",
            )
    parents = _parent_sources(declared)
    projected = {}
    for group in manifest["changes"].values():
        for path, row in group.items():
            parent = parents[path]
            assert (digest(parent) if parent is not None else None) == row["base_sha256"], (
                path,
                "IM_S08_PARENT_CHANGED",
            )
            if path.endswith(".py"):
                ast.parse(read(path), filename=path)
            projected[path] = parent
    return HistoricalReader(read, projected), summary(manifest)


def load(read):
    """Read and seal-check the raw manifest for the lineage checks."""
    try:
        raw = read(MANIFEST)
    except (KeyError, OSError):
        return None
    raw = raw.replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == MANIFEST_SHA256, "IM_S08_SEAL_CHANGED"
    return json.loads(raw)


def overrides(lock, prior, manifest, lock_digest, parent_digest):
    """Return the current-byte digests that supersede the earlier layers."""
    assert manifest["schema"] == SCHEMA, "IM_S08_SCHEMA_CHANGED"
    assert manifest["status"] == "NOT_DEPLOYED", "IM_S08_STATUS_CHANGED"
    assert manifest["base_commit"] == BASE_COMMIT, "IM_S08_BASE_CHANGED"
    assert manifest["base_lock_sha256"] == lock_digest, "IM_S08_LOCK_CHANGED"
    assert manifest["parent_candidate_sha256"] == parent_digest, "IM_S08_PARENT_SEAL_CHANGED"
    assert manifest["production_acceptance_claimed"] is False, "IM_S08_ACCEPTANCE_CHANGED"
    assert set(manifest["changes"]) == set(COMPONENTS), "IM_S08_SCOPE_CHANGED"
    registered = lock["source_sets"]
    result = {"gateway": {}, "frontend": {}}
    for component in ("gateway", "frontend"):
        for path, row in manifest["changes"][component].items():
            assert set(row) == {"base_sha256", "candidate_sha256"}, path
            base, candidate = row["base_sha256"], row["candidate_sha256"]
            assert candidate != base, (path, "IM_S08_UNCHANGED_SOURCE")
            if base is None:
                limit = registered.get(component, {})
                assert path not in limit, (path, "IM_S08_UNREGISTERED_BASE")
            else:
                assert path in registered.get(component, {}) or path in prior, (
                    path,
                    "IM_S08_UNKNOWN_PARENT",
                )
            result[component][path] = candidate
    return result


def summary(manifest):
    if manifest is None:
        return None
    changes = manifest["changes"]
    return {
        "status": "NOT_DEPLOYED",
        "schema": manifest["schema"],
        "base_commit": manifest["base_commit"],
        "manifest": MANIFEST,
        "parent_candidate": manifest["parent_candidate"],
        "production_acceptance_claimed": False,
        "schema_migration_requested": manifest["schema_migration_requested"],
        "changed_paths_verified": sum(len(group) for group in changes.values()),
        "added_gateway_runtime": sorted(
            path for path, row in changes["gateway"].items() if row["base_sha256"] is None
        ),
        "added_frontend_runtime": sorted(
            path
            for path, row in changes["frontend"].items()
            if row["base_sha256"] is None
            and path.startswith(("frontend/src/", "frontend/public/"))
        ),
        "gateway_runtime_paths": sorted(
            path for path in changes["gateway"] if path.endswith(".py")
        ),
        "frontend_runtime_paths": sorted(
            path
            for path in changes["frontend"]
            if path.startswith(("frontend/src/", "frontend/public/"))
        ),
        "test_paths": sorted(changes["test"]),
        "documentation_paths": sorted(manifest.get("documentation", {})),
        "scope": manifest["scope"],
    }