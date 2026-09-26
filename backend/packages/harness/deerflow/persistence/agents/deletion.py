"""Retain owner-reviewed deletion bytes outside the active employee namespace.

Callers hold the definition's write lock. No recursive removal or automatic
restore happens here. A database/IO failure can leave a prepared recovery record;
operators must reconcile that record with the live row before restoring anything.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from deerflow.persistence.agents.drafts import AgentDefinition, _replace_bytes
from deerflow.persistence.agents.publications import _sync_dir


def retain_deleted_definition(root: Path, definition: AgentDefinition, *, owner: str, backend: str, row: dict | None = None) -> Path:
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("Unsafe employee directory")
    if root.exists() and not root.is_dir():
        raise ValueError("Unsafe employee directory")
    if root.parent.is_symlink() or getattr(root.parent, "is_junction", lambda: False)():
        raise ValueError("Unsafe employee parent directory")
    record = {
        "schema": 1,
        "backend": backend,
        "owner": owner,
        "prepared_at": datetime.now(UTC).isoformat(),
        "config": definition.config,
        "soul": definition.soul,
        "revision": definition.revision,
        "row": row,
    }
    payload = json.dumps(record, ensure_ascii=False).encode("utf-8")
    if len(payload) > 12 * 1024 * 1024:
        raise ValueError("Employee deletion recovery record exceeds size limit")
    archive = root.parent / (".deleted-" + uuid.uuid4().hex)
    archive.mkdir(mode=0o700, parents=True, exist_ok=False)
    _replace_bytes(archive / "definition.json", payload)
    _sync_dir(archive)
    _sync_dir(root.parent)
    if root.exists():
        # Same-filesystem rename preserves memory, unknown files, and exact
        # bytes. The unique destination is not a live employee or an overwrite.
        os.rename(root, archive / "files")
        _sync_dir(archive)
        _sync_dir(root.parent)
    return archive
