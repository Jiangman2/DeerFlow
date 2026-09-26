"""Bounded, read-only release inventory of existing employee data.

This is a preservation check, not a backup or recovery engine. It never parses
definitions into AgentConfig, repairs a journal, migrates, deletes, or writes.
Operators must quiesce management writes for a consistent cross-store capture.
An unadapted old image is explicitly unsafe once versioned records exist.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from datetime import date, datetime
from pathlib import Path


class InventoryUnavailable(Exception):
    """Incomplete, changing or unsafe evidence must block release validation."""


def _signature(path: Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(path, "is_junction", lambda: False)():
        raise InventoryUnavailable("Employee inventory cannot follow a link")
    return info.st_mode, info.st_size, info.st_mtime_ns, info.st_ino


def capture_files(paths, *, max_files=100_000, max_bytes=2 * 1024**3, timeout=60):
    """Fingerprint only employee roots, including unknown ancillary files.

    Records paths and hashes, never config, SOUL, memory or credential contents.
    Complete bounded traversal is required; links and pending edits fail closed.
    """
    start = time.monotonic()
    base = paths.base_dir
    signatures = {}
    files = {}
    identities = []
    blockers = []
    total = 0

    def observe(path):
        try:
            signature = _signature(path)
        except OSError:
            raise InventoryUnavailable("Employee inventory source became unavailable") from None
        signatures[path] = signature
        if time.monotonic() - start > timeout:
            raise InventoryUnavailable("Employee inventory time limit exceeded")
        return signature

    def exists(path):
        # Detect missing roots appearing during the capture as well.
        try:
            path.lstat()
        except FileNotFoundError:
            signatures[path] = None
            return False
        return True

    def scan(root):
        nonlocal total
        mode, size, _, _ = observe(root)
        if not stat.S_ISDIR(mode):
            raise InventoryUnavailable("Employee inventory root is not a directory")
        pending = [root]
        while pending:
            directory = pending.pop()
            observe(directory)
            for path in sorted(directory.iterdir()):
                mode, size, _, _ = observe(path)
                if stat.S_ISDIR(mode):
                    pending.append(path)
                    continue
                if not stat.S_ISREG(mode):
                    raise InventoryUnavailable("Employee inventory contains a non-regular file")
                if path.name == ".draft-edit.pending.json":
                    raise InventoryUnavailable("Employee inventory found a pending draft; recover before capture")
                relative = path.relative_to(base).as_posix()
                if len(files) >= max_files or total + size > max_bytes:
                    raise InventoryUnavailable("Employee inventory size limit exceeded")
                digest = hashlib.sha256()
                actual = 0
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        actual += len(chunk)
                        if total + actual > max_bytes or actual > size or time.monotonic() - start > timeout:
                            raise InventoryUnavailable("Employee inventory read limit exceeded")
                        digest.update(chunk)
                if actual != size or _signature(path) != signatures[path]:
                    raise InventoryUnavailable("Employee inventory changed during capture")
                total += actual
                # A script with unchanged bytes but a lost executable bit is
                # not a preserved dependency. Record only mode bits, not owners
                # or ACLs; those still need a separate storage-level rehearsal.
                files[relative] = {"sha256": digest.hexdigest(), "bytes": actual, "mode": stat.S_IMODE(mode)}
                if path.name == "current.json" and path.parent.parent == paths.team_agents_dir:
                    blockers.append(relative)
                if path.name == "FORMAT-v1" and path.parent.name == ".avatar-assets":
                    blockers.append(relative)
        for employee in sorted(root.iterdir()):
            if employee.is_dir() and not employee.name.startswith(".") and ((employee / "config.yaml").is_file() or (root == paths.team_agents_dir and (employee / "current.json").is_file())):
                identities.append(employee.relative_to(base).as_posix())

    observe(base)
    roots = [paths.agents_dir, paths.team_agents_dir]
    users = base / "users"
    if exists(users):
        observe(users)
        for user in sorted(users.iterdir()):
            mode, _, _, _ = observe(user)
            if stat.S_ISDIR(mode):
                roots.append(user / "agents")
    for root in roots:
        if exists(root):
            scan(root)
    for path, signature in signatures.items():
        try:
            current = _signature(path)
        except FileNotFoundError:
            current = None
        if current != signature:
            raise InventoryUnavailable("Employee inventory changed during capture")
    return {
        "schema": 1,
        "kind": "files",
        "complete": True,
        "employee_count": len(identities),
        "identities": sorted(identities),
        "files": files,
        "total_bytes": total,
        "file_mode_semantics": "windows-basic" if os.name == "nt" else "posix",
        "legacy_image_compatible": not blockers,
        "legacy_image_blockers": sorted(blockers),
    }


def _json_scalar(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError("Unsupported raw employee inventory value")


def capture_sql(engine, *, max_rows=100_000):
    """Read raw rows in a bounded transaction, retaining unknown JSON fields.

    PostgreSQL is explicitly read-only with a statement and lock timeout. No
    DDL, migration, ORM normalization, or user/provider calls are performed.
    SQLite is used by local regression; PostgreSQL needs a release rehearsal.
    """
    from sqlalchemy import select

    from deerflow.persistence.agents.model import AgentRow

    rows = {}
    with engine.connect() as connection:
        postgres = connection.dialect.name == "postgresql"
        if postgres:
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            if postgres:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
                connection.exec_driver_sql("SET LOCAL lock_timeout = '1s'")
            statement = select(AgentRow.__table__).order_by(AgentRow.id).limit(max_rows + 1)
            result = connection.execution_options(stream_results=True).execute(statement)
            try:
                for row in result.mappings():
                    if len(rows) >= max_rows:
                        raise InventoryUnavailable("Employee SQL inventory row limit exceeded")
                    raw = dict(row)
                    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_scalar).encode()
                    if len(payload) > 16 * 1024**2:
                        raise InventoryUnavailable("Employee SQL inventory record limit exceeded")
                    key = str(raw["id"])
                    if not key or key in rows:
                        raise InventoryUnavailable("Employee SQL inventory identity is invalid")
                    rows[key] = {"user_id": raw["user_id"], "name": raw["name"], "sha256": hashlib.sha256(payload).hexdigest()}
            finally:
                result.close()
    return {"schema": 1, "kind": "sql", "complete": True, "employee_count": len(rows), "rows": rows}


def compare_preservation(before, after):
    """Additions are retained; unexplained loss or changes are blocking.

    This never rewrites the after-state to match the before-state. A valid
    business edit requires a separately reviewed receipt, not blanket approval.
    """
    if any(value.get("schema") != 1 or value.get("complete") is not True for value in (before, after)) or before.get("kind") != after.get("kind") or before.get("kind") not in ("files", "sql"):
        raise InventoryUnavailable("Cannot compare incomplete or incompatible employee inventories")
    field = "files" if before["kind"] == "files" else "rows"
    old, new = before[field], after[field]
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise InventoryUnavailable("Invalid employee inventory records")
    missing = sorted(set(old) - set(new))
    changed = sorted(key for key in old.keys() & new.keys() if old[key] != new[key])
    return {"preserved": not missing and not changed, "missing": missing, "changed": changed, "added": sorted(set(new) - set(old))}
