"""Verify the narrow Work merge before exposing its immutable production parent.

Source qualification only: no application imports, network or deployment actions.
"""

import ast
import hashlib
import json

LOCK = "deploy/enterprise/production-source-lock-20261029.json"
CANDIDATE = "deploy/enterprise/work-source-candidate-20261026.json"
INCIDENT_DELTA = "deploy/enterprise/incident-source-candidate-20261031.json"
WORK_ROUTING_DELTA = "deploy/enterprise/work-routing-source-candidate-20261102.json"
WORK_TASK_AGENT_DELTA = (
    "deploy/enterprise/work-task-agent-source-candidate-20261102.json"
)
WORK_TASK_AGENT_FIX_DELTA = (
    "deploy/enterprise/work-task-agent-fix-source-candidate-20261103.json"
)
WORK_TASK_AGENT_MODEL_FIX_DELTA = (
    "deploy/enterprise/work-task-agent-model-fix-source-candidate-20261103.json"
)
MANIFEST_SHA256 = "ef66e4b98b22e23d54dd02ee5eec9596f50a28d4becf4de861b52397d25f83ef"
WORK_RELEASE_ALIGNMENT = (
    "deploy/enterprise/work-release-alignment-source-candidate-20261104.json"
)
WORK_RELEASE_ALIGNMENT_ARCHIVE = "release/work-release-alignment-20261104/"
WORK_RELEASE_BASE_COMMIT = "ef4ce7a177a92750ceb780eb8725814328bade3d"
WORK_RELEASE_ALIGNMENT_PATHS = frozenset(
    {
        "backend/app/gateway/services.py",
        "backend/packages/harness/deerflow/agents/lead_agent/agent.py",
    }
)
WORK_PARENT_MANIFESTS = (
    LOCK,
    CANDIDATE,
    INCIDENT_DELTA,
    WORK_ROUTING_DELTA,
    WORK_TASK_AGENT_DELTA,
    WORK_TASK_AGENT_FIX_DELTA,
    WORK_TASK_AGENT_MODEL_FIX_DELTA,
)


def qualify(read):
    """Check the narrow rebased candidate before exposing production parents."""
    if isinstance(read, HistoricalReader):
        manifest = qualify(read.read)
        assert manifest == read.qualification, "WORK_RELEASE_ALIGNMENT_READER_CHANGED"
        return manifest
    try:
        raw = read(WORK_RELEASE_ALIGNMENT)
    except (FileNotFoundError, KeyError):
        for path in WORK_RELEASE_ALIGNMENT_PATHS:
            try:
                read(WORK_RELEASE_ALIGNMENT_ARCHIVE + "production-base/" + path)
            except (FileNotFoundError, KeyError):
                continue
            raise AssertionError("WORK_RELEASE_ALIGNMENT_MISSING_MANIFEST")
        return None
    assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == MANIFEST_SHA256, (
        "WORK_RELEASE_ALIGNMENT_SEAL_CHANGED"
    )
    manifest = json.loads(raw)
    assert isinstance(manifest, dict) and set(manifest) == {
        "schema",
        "status",
        "base_commit",
        "scope",
        "parent_manifests",
        "changes",
    }, "WORK_RELEASE_ALIGNMENT_SCHEMA"
    assert manifest["schema"] == "work-release-alignment-source-candidate.v1", (
        "WORK_RELEASE_ALIGNMENT_SCHEMA"
    )
    assert manifest["status"] == "NOT_DEPLOYED", (
        "WORK_RELEASE_ALIGNMENT_NOT_DEPLOYED_REQUIRED"
    )
    assert manifest["base_commit"] == WORK_RELEASE_BASE_COMMIT, (
        "WORK_RELEASE_ALIGNMENT_BASE_COMMIT"
    )
    assert isinstance(manifest["scope"], str), "WORK_RELEASE_ALIGNMENT_SCHEMA"
    parents = manifest["parent_manifests"]
    assert isinstance(parents, dict) and set(parents) == set(WORK_PARENT_MANIFESTS), (
        "WORK_RELEASE_ALIGNMENT_PARENT_SCOPE"
    )
    for path, digest in parents.items():
        assert (
            hashlib.sha256(read(path).replace(b"\r\n", b"\n")).hexdigest() == digest
        ), "WORK_RELEASE_ALIGNMENT_PARENT_CHANGED"
    changes = manifest["changes"]
    assert isinstance(changes, dict) and set(changes) == WORK_RELEASE_ALIGNMENT_PATHS, (
        "WORK_RELEASE_ALIGNMENT_PATH_SCOPE"
    )
    for path, row in changes.items():
        assert isinstance(row, dict) and set(row) == {
            "base_sha256",
            "work_candidate_sha256",
            "candidate_sha256",
            "baseline_path",
        }, "WORK_RELEASE_ALIGNMENT_CHANGE_SCHEMA"
        for field in ("base_sha256", "work_candidate_sha256", "candidate_sha256"):
            digest = row[field]
            assert (
                isinstance(digest, str)
                and len(digest) == 64
                and all(c in "0123456789abcdef" for c in digest)
            ), "WORK_RELEASE_ALIGNMENT_HASH_FORMAT"
        assert (
            row["baseline_path"]
            == WORK_RELEASE_ALIGNMENT_ARCHIVE + "production-base/" + path
        ), "WORK_RELEASE_ALIGNMENT_BASELINE_PATH"
        baseline = read(row["baseline_path"]).replace(b"\r\n", b"\n")
        current = read(path).replace(b"\r\n", b"\n")
        assert hashlib.sha256(baseline).hexdigest() == row["base_sha256"], (
            path,
            "WORK_RELEASE_ALIGNMENT_BASELINE_CHANGED",
        )
        assert hashlib.sha256(current).hexdigest() == row["candidate_sha256"], (
            path,
            "WORK_RELEASE_ALIGNMENT_CURRENT_CHANGED",
        )
        ast.parse(baseline, filename=row["baseline_path"])
        ast.parse(current, filename=path)
    return manifest


class HistoricalReader:
    """Only a fully checked candidate can project the two declared parent files."""

    def __init__(self, read, qualification):
        verified = qualify(read)
        assert verified is not None and verified == qualification, (
            "WORK_RELEASE_ALIGNMENT_READER_CHANGED"
        )
        self.read = read
        self.qualification = verified

    def __call__(self, path):
        row = self.qualification["changes"].get(path)
        return self.read(row["baseline_path"] if row else path)


def historical_read(read):
    if isinstance(read, HistoricalReader):
        qualify(read)
        return read
    qualification = qualify(read)
    return HistoricalReader(read, qualification) if qualification else read
