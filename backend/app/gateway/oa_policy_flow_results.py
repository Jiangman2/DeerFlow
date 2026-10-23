"""Versioned OA result sets. Snapshots live outside the Agent's sandbox mounts."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from deerflow.config.paths import get_paths


def public_results(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [{k: row[k] for k in ("id", "filename", "bytes", "sha256", "part_name")} for row in job.get("result_files") or []]


def _snapshot_root(job: dict[str, Any]) -> Path:
    from app.gateway.oa_policy_flow_service import OAPolicyFlowError

    thread = get_paths().thread_dir(job["thread_id"], user_id=job["owner_user_id"])
    root = thread / "oa-delivery"
    if root.is_symlink():
        raise OAPolicyFlowError("invalid_result_path", "Result snapshot directory is invalid")
    return root


def collect_result_set(job: dict[str, Any], artifacts: list[str], config: Any) -> list[dict[str, Any]]:
    from app.gateway.oa_policy_flow_service import OAPolicyFlowError, parse_strict_json_text, resolve_oa_result_path

    if not 1 <= len(artifacts) <= config.max_result_files:
        raise OAPolicyFlowError("invalid_artifact_count", "Presented JSON result count exceeds the allowed range")
    if len(set(artifacts)) != len(artifacts):
        raise OAPolicyFlowError("duplicate_result", "Duplicate result artifact")
    payloads = []
    names: set[str] = set()
    paths: set[Path] = set()
    total = 0
    for virtual in sorted(artifacts):
        path = resolve_oa_result_path({**job, "output_virtual_path": virtual}, max_result_bytes=config.max_result_bytes)
        name = path.name
        normalized = unicodedata.normalize("NFC", name).casefold()
        if normalized in names or path in paths:
            raise OAPolicyFlowError("duplicate_result", "Duplicate result filename or resolved artifact")
        if any(ord(c) < 32 or ord(c) == 127 or c in '/\\"' for c in name) or len(name.encode("utf-8")) > 255:
            raise OAPolicyFlowError("invalid_result_filename", "Result filename is not portable")
        names.add(normalized)
        paths.add(path)
        with path.open("rb") as handle:
            payload = handle.read(config.max_result_bytes + 1)
        total += len(payload)
        if not payload or len(payload) > config.max_result_bytes or total > config.max_total_result_bytes:
            raise OAPolicyFlowError("invalid_result_size", "Result set exceeds the allowed size")
        try:
            parse_strict_json_text(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise OAPolicyFlowError("invalid_json", "Every result must be valid UTF-8 JSON") from exc
        payloads.append((name, payload))

    # Validate the entire batch before publishing any durable manifest. A retry
    # after a crash reuses content-addressed bytes; it never overwrites a snapshot.
    root = _snapshot_root(job)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    rows = []
    for index, (name, payload) in enumerate(payloads, 1):
        digest = hashlib.sha256(payload).hexdigest()
        snapshot = root / (digest + ".json")
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, snapshot)
            except FileExistsError:
                if snapshot.is_symlink() or not snapshot.is_file() or snapshot.stat().st_size != len(payload) or snapshot.read_bytes() != payload:
                    raise OAPolicyFlowError("result_snapshot_conflict", "Accepted result snapshot differs") from None
        finally:
            Path(temporary).unlink(missing_ok=True)
        rows.append({"id": f"result_{index:03d}", "part_name": f"result_{index:03d}", "filename": name, "bytes": len(payload), "sha256": digest})
    if os.name != "nt":
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return rows


def read_result_snapshot(job: dict[str, Any], result: dict[str, Any], *, max_result_bytes: int) -> bytes:
    from app.gateway.oa_policy_flow_service import OAPolicyFlowError

    digest = result.get("sha256", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise OAPolicyFlowError("result_changed", "Invalid accepted result digest")
    path = _snapshot_root(job) / (digest + ".json")
    if path.is_symlink() or not path.is_file():
        raise OAPolicyFlowError("result_missing", "Accepted result snapshot is unavailable")
    with path.open("rb") as handle:
        payload = handle.read(max_result_bytes + 1)
    if not payload or len(payload) > max_result_bytes or len(payload) != result.get("bytes") or hashlib.sha256(payload).hexdigest() != digest:
        raise OAPolicyFlowError("result_changed", "Accepted result snapshot failed integrity verification")
    return payload
