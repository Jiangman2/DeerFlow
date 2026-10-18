"""Verify the current Runner COPY closure without importing application code."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath

BASE_IMAGE = "deerflow.local/deer-flow-gateway@sha256:eb23de08345bab65c78264f99ce33085c4ea3574ff8d6281b62418f644fc06cd"
SOURCE_PREFIX = "docker/ppt-master-runner/production-source/"
EXPECTED_FILES = frozenset(
    {
        "backend/app/gateway/deps.py",
        "backend/app/gateway/routers/features.py",
        "backend/app/gateway/routers/ppt_confirmations.py",
        "backend/packages/harness/deerflow/agents/ppt_master_runner/agent.py",
        "backend/packages/harness/deerflow/persistence/migrations/versions/0018_scheduled_task_reliability.py",
        "backend/packages/harness/deerflow/persistence/migrations/versions/0019_digital_employee_agents.py",
        "backend/packages/harness/deerflow/persistence/migrations/versions/0020_ppt_confirmation.py",
        "backend/packages/harness/deerflow/persistence/models/__init__.py",
        "backend/packages/harness/deerflow/persistence/ppt_confirmation/__init__.py",
        "backend/packages/harness/deerflow/persistence/ppt_confirmation/model.py",
        "backend/packages/harness/deerflow/persistence/ppt_confirmation/sql.py",
        "backend/packages/harness/deerflow/ppt_confirmation/__init__.py",
        "backend/packages/harness/deerflow/ppt_confirmation/admission.py",
        "backend/packages/harness/deerflow/ppt_confirmation/continuation.py",
        "backend/packages/harness/deerflow/ppt_confirmation/controller.py",
        "backend/packages/harness/deerflow/ppt_confirmation/host_owner.py",
        "backend/packages/harness/deerflow/ppt_confirmation/lifecycle.py",
        "backend/packages/harness/deerflow/ppt_confirmation/monitor.py",
        "backend/packages/harness/deerflow/ppt_confirmation/proposal.py",
    }
)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def verify(manifest: dict, root: Path, *, image: bool = False) -> dict:
    require(manifest.get("schema") == "portal-ppt-current-runner-build-inputs.v1", "manifest_schema")
    require(manifest.get("base_image") == BASE_IMAGE, "base_image")
    require(manifest.get("platform") == "linux/arm64", "platform")
    rows = manifest.get("files")
    require(type(rows) is list and len(rows) == len(EXPECTED_FILES), "file_count")
    seen = set()
    total = 0
    for row in rows:
        require(type(row) is dict and set(row) == {"source", "target", "bytes", "sha256"}, "file_fields")
        target = row["target"]
        require(type(target) is str and target in EXPECTED_FILES and target not in seen, "target_closure")
        require(row["source"] == SOURCE_PREFIX + target, "source_mapping")
        require(not PurePosixPath(target).is_absolute() and ".." not in PurePosixPath(target).parts, "target_path")
        require(type(row["bytes"]) is int and 0 < row["bytes"] <= 512 * 1024, "file_bytes")
        require(type(row["sha256"]) is str and len(row["sha256"]) == 64, "file_digest")
        path = root / (target if image else row["source"])
        require(path.is_file() and not path.is_symlink(), "file_missing_or_symlink:" + target)
        data = path.read_bytes()
        require(len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"], "file_pin:" + target)
        total += len(data)
        require(total <= 4 * 1024 * 1024, "total_source_bytes")
        # Parsing/compilation has no application import, DB, network, or .pyc write.
        compile(ast.parse(data, filename=target), target, "exec")
        seen.add(target)
    require(seen == EXPECTED_FILES, "file_closure")
    return {
        "status": "CURRENT_RUNNER_SOURCE_INPUTS_VERIFIED",
        "files": len(rows),
        "bytes": total,
        "application_imported": False,
        "runtime_started": False,
        "new_release_image_identity": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("confirmation-build-manifest.json"))
    roots = parser.add_mutually_exclusive_group()
    roots.add_argument("--source-root", type=Path, default=None)
    roots.add_argument("--image-root", type=Path)
    args = parser.parse_args()
    root = args.image_root if args.image_root is not None else args.source_root or Path(__file__).resolve().parents[2]
    manifest = json.loads(args.manifest.read_bytes())
    print(json.dumps(verify(manifest, root, image=args.image_root is not None), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
