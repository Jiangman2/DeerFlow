"""Latest-state equivalence checks for a recovery rehearsal, never a restore.

The caller must supply freshly observed employee-file and raw SQL inventories
plus the server-side thread bindings. An upgrade-time backup is NOT the current
state. This module never writes data or authorizes an old-image downgrade.
It does not prove cross-store atomicity, writer quiescence, ACL/xattr/ownership
preservation, database-wide consistency or target-image read compatibility.
"""

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time


def signature(path):
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("Unsafe linked binding inventory path refused")
    if not (stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode)):
        raise ValueError("Special binding inventory path refused")
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns)


def read_record(path, remaining):
    before = signature(path)
    if (
        before is None
        or not stat.S_ISREG(before[2])
        or before[3] > min(8 * 1024 * 1024, remaining)
    ):
        raise ValueError("Binding inventory byte limit or type check failed")
    fd = os.open(
        str(path),
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
    )
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
        ) != before:
            raise ValueError("Binding changed before read")
        payload = stream.read(min(8 * 1024 * 1024, remaining) + 1)
    if len(payload) != before[3] or signature(path) != before:
        raise ValueError("Binding changed during read")
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "mode": stat.S_IMODE(before[2]),
    }


def capture_bindings(
    base, *, max_entries=100000, max_bytes=64 * 1024 * 1024, timeout=30
):
    """Hash only employee-publication.json, not user-data, prompts or uploads.

    No app initialization, receipts created, database queries or permission
    changes. A second metadata pass detects ordinary concurrent changes; this
    observation cannot replace a coordinated write fence and snapshot.
    """
    base = Path(base).absolute()
    for parent in (base,) + tuple(base.parents):
        signature(parent)
    if not base.is_dir():
        raise ValueError("State root must already exist")
    deadline = time.monotonic() + timeout
    observed, records = {}, {}
    count, size = 0, 0

    def observe(path):
        nonlocal count
        count += 1
        if count > max_entries or time.monotonic() > deadline:
            raise ValueError("Binding inventory entry or time limit exceeded")
        value = signature(path)
        if path in observed and observed[path] != value:
            raise ValueError("Binding directory changed during scan")
        observed[path] = value
        return value

    def directories(root):
        value = observe(root)
        if value is None:
            return
        if not stat.S_ISDIR(value[2]):
            raise ValueError("Expected binding parent directory")
        with os.scandir(str(root)) as entries:
            for entry in entries:
                path = root / entry.name
                info = observe(path)
                if info is None:
                    raise ValueError("Binding parent changed during scan")
                if stat.S_ISDIR(info[2]):
                    yield path

    def threads(root):
        nonlocal size
        for thread in directories(root):
            path = thread / "employee-publication.json"
            if observe(path) is not None:
                record = read_record(path, max_bytes - size)
                size += record["bytes"]
                records[path.relative_to(base).as_posix()] = record

    observe(base)
    threads(base / "threads")
    for user in directories(base / "users"):
        threads(user / "threads")
    for path, before in observed.items():
        if time.monotonic() > deadline:
            raise ValueError("Binding inventory time limit exceeded")
        if signature(path) != before:
            raise ValueError("Binding inventory changed during scan")
    return {
        "schema": 1,
        "kind": "bindings",
        "complete": True,
        "files": records,
        "total_bytes": size,
        "observation_only": True,
        "file_mode_semantics": "posix" if os.name == "posix" else "windows",
    }


def _relative(name, bindings=False):
    if (
        not isinstance(name, str)
        or not name
        or any(x in name for x in ("\\", "\x00", ":"))
    ):
        raise ValueError("Invalid preservation path")
    parts = name.split("/")
    if PurePosixPath(name).is_absolute() or any(
        p in ("", ".", "..") or p.endswith((".", " ")) for p in parts
    ):
        raise ValueError("Unsafe preservation path")
    if bindings:
        good = (len(parts) == 3 and parts[0] == "threads") or (
            len(parts) == 5 and parts[0] == "users" and parts[2] == "threads"
        )
        if not good or parts[-1] != "employee-publication.json":
            raise ValueError("Invalid binding evidence scope")
    elif not (
        (len(parts) >= 3 and parts[0] in ("agents", "team_agents"))
        or (len(parts) >= 5 and parts[0] == "users" and parts[2] == "agents")
    ):
        raise ValueError("Invalid employee file evidence scope")


def _validate(state):
    if (
        not isinstance(state, dict)
        or state.get("schema") != 1
        or state.get("agent_backend") not in ("file", "db")
    ):
        raise ValueError("Invalid preservation evidence")
    for scope, kind, field in (
        ("employee_files", "files", "files"),
        ("employee_sql", "sql", "rows"),
        ("thread_bindings", "bindings", "files"),
    ):
        inventory = state.get(scope)
        if (
            scope == "employee_sql"
            and scope in state
            and inventory is None
            and state["agent_backend"] == "file"
        ):
            continue
        if (
            not isinstance(inventory, dict)
            or inventory.get("schema") != 1
            or inventory.get("kind") != kind
            or inventory.get("complete") is not True
        ):
            raise ValueError("Incomplete preservation scope: " + scope)
        entries = inventory.get(field)
        if not isinstance(entries, dict) or len(entries) > 100000:
            raise ValueError("Invalid preservation entries")
        normalized = set()
        for key, value in entries.items():
            if (
                not isinstance(key, str)
                or not key
                or not isinstance(value, dict)
                or not isinstance(value.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
            ):
                raise ValueError("Invalid preservation digest")
            if kind == "sql":
                if any(
                    not isinstance(value.get(k), str) or not value[k]
                    for k in ("user_id", "name")
                ):
                    raise ValueError("SQL preservation lacks identity")
            else:
                _relative(key, bindings=(kind == "bindings"))
                if key.casefold() in normalized:
                    raise ValueError("Ambiguous preservation path")
                normalized.add(key.casefold())
                if (
                    type(value.get("bytes")) is not int
                    or value["bytes"] < 0
                    or type(value.get("mode")) is not int
                    or not 0 <= value["mode"] <= 0o777
                ):
                    raise ValueError("Invalid preserved file metadata")


def compare_latest(current, restored):
    """Reject losses, resurrection, edits, grants rewind and binding rewind.

    Even additions on the restored side are not silently allowed: they might
    resurrect deleted employees or obsolete authorization records. Comparison
    is raw digest/identity based, retaining unknown fields in source inventories.
    """
    _validate(current)
    _validate(restored)
    if current["agent_backend"] != restored["agent_backend"]:
        raise ValueError("Storage backend changed during recovery rehearsal")
    differences = {}
    equivalent = True
    for scope in ("employee_files", "employee_sql", "thread_bindings"):
        first, second = current[scope], restored[scope]
        if first is None or second is None:
            if first != second:
                raise ValueError("SQL preservation scope changed")
            continue
        if first.get("file_mode_semantics") != second.get("file_mode_semantics"):
            raise ValueError("File permission semantics differ")
        field = "rows" if scope == "employee_sql" else "files"
        old, new = first[field], second[field]
        diff = {
            "missing": sorted(old.keys() - new.keys()),
            "unexpected": sorted(new.keys() - old.keys()),
            "changed": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
        }
        differences[scope] = diff
        equivalent = equivalent and not any(diff.values())
    return {
        "schema": 1,
        "data_equivalent": equivalent,
        "differences": differences,
        "comparison_basis": "latest_state_not_upgrade_snapshot",
        "production_restore_authorized": False,
        "consistent_snapshot_proven": False,
        "legacy_image_downgrade": "NOT_AUTHORIZED",
        "ready_for_rollout": False,
    }
