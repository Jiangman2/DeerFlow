"""Qualify the scoped Work search candidates candidate before projecting its fixed 0716 parent.

Offline source registration only; this never claims a deployed image or business
acceptance and never imports application/runtime code.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = "release/work-search-candidates-20261111/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "bfe2db0f82c0eaecdc7aeb1d3dc929225168e61dcfd90a14a7bf347712aec106"
BASE_COMMIT = "28b6269ad488bc7d34778187cf3bf8dc837ce13b"
PARENT_MANIFESTS = {
    "release/work-cloud-routing-20261111/SOURCE.json": "2c9b0bc5cc70340e7f5d52a65069c7305b4e7da81d3b2f98f9a19273a8f63f5c",
    "release/work-integration-followup-20261106/SOURCE.json": "535503070c0c53a0a928fe6ab362bbc6bfc8de685a681fed7fd7832c6629dd76",
    "release/artifact-delivery-recovery-20261109/SOURCE.json": "0420e05dc6de2a8da22ef6a3c0962aa762dbc81c2543e4b934d71c329ff4ab30",
    "release/released-sync-20261110/SOURCE.json": "3f570a30ac92fb02511af9e306b2f8d5188b4b06cf4ad80fce87e7dbf99671b7",
}
DOCUMENTATION_PATHS = (
    "backend/AGENTS.md",
    "backend/README.md",
    "backend/docs/WORK_SEARCH_CANDIDATES_20261111.md",
)
RUNTIME_PATHS = ("backend/app/integrations/work/file_search.py",)
TEST_PATHS = (
    "backend/tests/test_work_file_search.py",
    "backend/tests/test_work_file_search_service.py",
    "backend/tests/test_work_search_cards.py",
)
REQUIRED_PATHS = RUNTIME_PATHS
MARKERS = (ARCHIVE + "README.md",)


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _gateway_source(path):
    return path.startswith(
        ("backend/app/", "backend/packages/harness/")
    ) and path.endswith(".py")


def allowed_path(path):
    pure = PurePosixPath(path)
    return (
        not pure.is_absolute()
        and ".." not in pure.parts
        and "\\" not in path
        and ":" not in path
        and pure.as_posix() == path
        and (path in RUNTIME_PATHS or path in DOCUMENTATION_PATHS or path in TEST_PATHS)
    )


def _parent_sources(paths):
    queries = {path: f"{BASE_COMMIT}:{path}".encode() for path in paths}
    raw = subprocess.check_output(
        ["git", "cat-file", "--batch"],
        input=b"\n".join(queries.values()) + b"\n",
        cwd=ROOT,
    )
    stream, parents = io.BytesIO(raw), {}
    for path, query in queries.items():
        header = stream.readline().rstrip(b"\n")
        if header == query + b" missing":
            parents[path] = None
            continue
        parts = header.split()
        assert len(parts) == 3 and parts[1] == b"blob", (
            "WORK_SEARCH_CANDIDATES_PARENT_BLOB"
        )
        parents[path] = stream.read(int(parts[2]))
        assert stream.read(1) == b"\n", "WORK_SEARCH_CANDIDATES_PARENT_BLOB"
    assert not stream.read(), "WORK_SEARCH_CANDIDATES_PARENT_BLOB"
    return parents


class HistoricalReader:
    """A projection, never an authority marker: each entry revalidates its source."""

    def __init__(self, original_read, parents):
        self.original_read = original_read
        self.parents = dict(parents)

    def __call__(self, path):
        if path.startswith(ARCHIVE):
            raise KeyError(path)
        if path not in self.parents:
            return self.original_read(path)
        if self.parents[path] is None:
            raise KeyError(path)
        return self.parents[path]


def historical_view(read):
    # Independent importlib loaders can create distinct copies of this class.
    # Unwrap only the source callable, never trust a cached projection or marker.
    seen = set()
    while type(read).__name__ == "HistoricalReader" and callable(
        getattr(read, "original_read", None)
    ):
        assert id(read) not in seen, "WORK_SEARCH_CANDIDATES_READER_CYCLE"
        seen.add(id(read))
        read = read.original_read
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        for path in MARKERS:
            try:
                read(path)
            except (KeyError, FileNotFoundError):
                continue
            raise AssertionError("WORK_SEARCH_CANDIDATES_MANIFEST_REQUIRED")
        return read, None
    assert digest(raw) == MANIFEST_SHA256, "WORK_SEARCH_CANDIDATES_SEAL_CHANGED"
    manifest = json.loads(raw)
    assert set(manifest) == {
        "schema",
        "status",
        "base_commit",
        "production_acceptance_claimed",
        "schema_migration_requested",
        "parent_manifests",
        "changes",
    }, "WORK_SEARCH_CANDIDATES_SCHEMA"
    assert (
        manifest["schema"] == "work-search-candidates.v1"
        and manifest["status"] == "NOT_DEPLOYED"
    ), "WORK_SEARCH_CANDIDATES_SCHEMA"
    assert (
        manifest["base_commit"] == BASE_COMMIT
        and manifest["production_acceptance_claimed"] is False
        and manifest["schema_migration_requested"] is False
    ), "WORK_SEARCH_CANDIDATES_AUTHORITY"
    assert manifest["parent_manifests"] == PARENT_MANIFESTS, (
        "WORK_SEARCH_CANDIDATES_LINEAGE"
    )
    for path, expected in PARENT_MANIFESTS.items():
        try:
            parent_manifest = read(path)
        except (KeyError, FileNotFoundError):
            raise AssertionError(
                "WORK_SEARCH_CANDIDATES_PARENT_MANIFEST_REQUIRED"
            ) from None
        assert digest(parent_manifest) == expected, (
            "WORK_SEARCH_CANDIDATES_PARENT_MANIFEST_CHANGED"
        )
    changes = manifest["changes"]
    assert isinstance(changes, dict) and set(REQUIRED_PATHS) <= set(changes), (
        "WORK_SEARCH_CANDIDATES_SCOPE"
    )
    assert all(allowed_path(path) for path in changes), "WORK_SEARCH_CANDIDATES_SCOPE"
    parents = _parent_sources(changes)
    for path, row in changes.items():
        assert isinstance(row, dict) and set(row) == {
            "base_sha256",
            "current_sha256",
        }, "WORK_SEARCH_CANDIDATES_SCHEMA"
        previous, current = parents[path], read(path)
        assert digest(current) == row["current_sha256"], (
            path,
            "WORK_SEARCH_CANDIDATES_CURRENT_CHANGED",
        )
        assert (digest(previous) if previous is not None else None) == row[
            "base_sha256"
        ], (path, "WORK_SEARCH_CANDIDATES_PARENT_CHANGED")
        assert previous is None or digest(previous) != digest(current), (
            path,
            "WORK_SEARCH_CANDIDATES_UNCHANGED_PATH",
        )
        if path.endswith(".py"):
            ast.parse(current, filename=path)
    added = [path for path, previous in parents.items() if previous is None]
    return HistoricalReader(read, parents), {
        "status": "NOT_DEPLOYED",
        "base_commit": BASE_COMMIT,
        "changed_paths_verified": len(changes),
        "documentation_paths_verified": sum(
            path in DOCUMENTATION_PATHS for path in changes
        ),
        "added_gateway_runtime": sorted(
            path for path in added if _gateway_source(path)
        ),
        "added_frontend_runtime": sorted(
            path for path in added if path.startswith("frontend/src/")
        ),
        "production_acceptance_claimed": False,
        "schema_migration_requested": False,
    }
