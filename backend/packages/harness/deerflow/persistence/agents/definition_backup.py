"""Private, verified backups of employee definition files only.

This is a release rehearsal primitive, NOT an automatic production rollback.
Management writes must be quiesced by the caller. SQL rows and thread bindings
need separate coordinated backup. Restore accepts only a new empty location:
an old backup can never overwrite employees created or edited after rollout.
Portable file modes are retained; owners, directory modes, ACLs and extended
attributes are not. This is not an operational shared-volume restore engine.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path, PurePosixPath

from deerflow.persistence.agents.inventory import InventoryUnavailable, capture_files

_MAX_MANIFEST = 32 * 1024**2
_MAX_FILES = 100_000
_MAX_BYTES = 2 * 1024**3
_SCOPE = "employee_definition_files_only"


def _safe_parents(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise ValueError("Backup paths cannot traverse links")


def _fresh(path):
    path = Path(path).absolute()
    _safe_parents(path)
    if path.exists():
        raise ValueError("Backup or restore destination already exists")
    if not path.parent.is_dir():
        raise ValueError("Destination parent must already exist")
    return path


def _relative(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\0" in name:
        raise ValueError("Invalid employee backup scope")
    parts = name.split("/")
    if PurePosixPath(name).is_absolute() or any(part in ("", ".", "..") or part.rstrip(" .") != part for part in parts):
        raise ValueError("Invalid employee backup scope")
    if not ((parts[0] in ("agents", "team_agents") and len(parts) >= 3) or (parts[0] == "users" and len(parts) >= 5 and parts[2] == "agents")):
        raise ValueError("File is outside employee backup scope")
    if parts[-1] == ".draft-edit.pending.json":
        raise ValueError("Unrecovered employee edit in backup")
    return parts


def _read_json(path, limit=_MAX_MANIFEST):
    _safe_parents(path)
    if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > limit:
        raise ValueError("Invalid employee backup metadata")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Employee backup metadata exceeds limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate employee backup key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique), raw


def _create_file(path):
    return os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb")


def _sync(directory):
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _copy_verified(source, target, record, deadline):
    """No links, no overwrite, bounded streaming; copied bytes must match."""
    _safe_parents(source)
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size != record["bytes"]:
        raise ValueError("Employee backup object size/type changed")
    digest, size = hashlib.sha256(), 0
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        # Also validate the opened file, not just the earlier path lookup.
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (info.st_dev, info.st_ino, info.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
            raise ValueError("Employee backup object changed before read")
        output = _create_file(target) if target is not None else None
        try:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > record["bytes"] or time.monotonic() > deadline:
                    raise ValueError("Employee backup copy limit exceeded")
                digest.update(chunk)
                if output is not None:
                    output.write(chunk)
            if size != record["bytes"] or digest.hexdigest() != record["sha256"]:
                raise ValueError("Employee backup object checksum mismatch")
            if output is not None:
                output.flush()
                os.fsync(output.fileno())
        finally:
            if output is not None:
                output.close()


def _write_json(path, document):
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > _MAX_MANIFEST:
        raise ValueError("Employee backup manifest exceeds limit")
    with _create_file(path) as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def _file_mode(record):
    mode = record.get("mode")
    if type(mode) is not int or not 0 <= mode <= 0o777:
        # Never silently strip special bits and claim an exact restore; nor
        # recreate setuid/setgid/sticky permissions from backup metadata.
        raise ValueError("Invalid or missing portable employee file mode")
    return mode


def create(paths, destination, *, timeout=120):
    """Capture verified raw files; leave failures visibly unsealed, not PASS."""
    destination = _fresh(destination)
    base = paths.base_dir.absolute()
    if destination.is_relative_to(base):
        raise ValueError("Backup destination must be outside source data")
    deadline = time.monotonic() + timeout
    initial = capture_files(paths, timeout=timeout)
    for record in initial["files"].values():
        _file_mode(record)
    destination.mkdir(mode=0o700)
    objects = destination / "objects"
    objects.mkdir(mode=0o700)
    for relative, record in initial["files"].items():
        _relative(relative)
        target = objects / record["sha256"]
        if not target.exists():
            _copy_verified(base / relative, target, record, deadline)
    after = capture_files(paths, timeout=max(0, deadline - time.monotonic()))
    if initial != after:
        raise InventoryUnavailable("Employee data changed during backup; no complete snapshot")
    _sync(objects)
    report = {"schema": 1, "scope": _SCOPE, "complete": True, "file_count": len(initial["files"]), "legacy_image_compatible": initial["legacy_image_compatible"], "database_included": False, "thread_bindings_included": False}
    digest = _write_json(destination / "manifest.json", {"schema": 1, "scope": _SCOPE, "inventory": initial})
    _sync(destination)
    _write_json(destination / "COMPLETE.json", {**report, "manifest_sha256": digest})
    _sync(destination)
    _sync(destination.parent)
    return report


def verify(directory, *, timeout=120):
    directory = Path(directory).absolute()
    deadline = time.monotonic() + timeout
    seal, _ = _read_json(directory / "COMPLETE.json", 8192)
    manifest, raw = _read_json(directory / "manifest.json")
    if seal.get("schema") != 1 or seal.get("complete") is not True or seal.get("scope") != _SCOPE or manifest.get("schema") != 1 or manifest.get("scope") != _SCOPE or hashlib.sha256(raw).hexdigest() != seal.get("manifest_sha256"):
        raise ValueError("Employee backup seal/manifest checksum mismatch")
    inventory = manifest["inventory"]
    records = inventory.get("files")
    if inventory.get("schema") != 1 or inventory.get("kind") != "files" or inventory.get("complete") is not True or not isinstance(records, dict) or len(records) > _MAX_FILES:
        raise ValueError("Invalid employee backup inventory")
    if inventory.get("file_mode_semantics") not in ("windows-basic", "posix"):
        raise ValueError("Missing employee file mode semantics")
    total, seen = 0, set()
    for name, record in records.items():
        _relative(name)
        if name.casefold() in seen:
            raise ValueError("Ambiguous employee backup path")
        seen.add(name.casefold())
        if not isinstance(record, dict) or type(record.get("bytes")) is not int or record["bytes"] < 0 or not re.fullmatch(r"[a-f0-9]{64}", record.get("sha256", "")):
            raise ValueError("Invalid employee backup object")
        _file_mode(record)
        total += record["bytes"]
        if total > _MAX_BYTES:
            raise ValueError("Employee backup exceeds size limit")
        _copy_verified(directory / "objects" / record["sha256"], None, record, deadline)
    if len(records) != seal.get("file_count") or total != inventory.get("total_bytes"):
        raise ValueError("Employee backup inventory totals mismatch")
    return inventory


def restore_to_new_directory(directory, destination, *, timeout=120):
    """Rehearse recovery only into a NEW tree; never merge into live data."""
    destination = _fresh(destination)
    directory = Path(directory).absolute()
    if destination.is_relative_to(directory):
        raise ValueError("Restore destination must be outside backup")
    deadline = time.monotonic() + timeout
    inventory = verify(directory, timeout=timeout)
    semantics = "windows-basic" if os.name == "nt" else "posix"
    if inventory["file_mode_semantics"] != semantics:
        raise ValueError("Employee file mode platform differs; storage migration requires review")
    destination.mkdir(mode=0o700)
    for name, record in inventory["files"].items():
        target = destination.joinpath(*_relative(name))
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _safe_parents(target)
        _copy_verified(directory / "objects" / record["sha256"], target, record, deadline)
        target.chmod(_file_mode(record))
    from deerflow.config.paths import Paths

    actual = capture_files(Paths(base_dir=destination), timeout=max(0, deadline - time.monotonic()))
    if actual != inventory:
        raise ValueError("Restored employee inventory does not match backup")
    for name in inventory["files"]:
        _sync(destination.joinpath(*_relative(name)).parent)
    _sync(destination)
    _sync(destination.parent)
    return {"restored": True, "scope": _SCOPE, "file_count": len(inventory["files"]), "live_data_overwritten": False}
