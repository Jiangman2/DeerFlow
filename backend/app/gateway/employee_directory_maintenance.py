"""Operator-only backup/rollback of valid shared hot state, never SQL or channels.

Rollback is a new transaction, not a raw copy which would reuse old CAS versions.
No HTTP entry, automatic recovery, schema migration or container-local fallback.
"""

import argparse
import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.gateway.employee_featured_store import MAX_BYTES, EmployeeIdentity, FeaturedConflict, FeaturedStore, FeaturedUnavailable


def _private_backup(path, data):
    target = FeaturedStore(path)
    target._check_parent()
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode()
    target._decode(raw)
    # Exclusive creation also rejects symlinks/hard links and preserves earlier
    # evidence. Keep a partial private file on IO error; never delete user data.
    fd = os.open(target.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        target._check_file(stream.fileno())
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        directory = os.open(target.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "revision": data["revision"], "entries": len(data["entries"])}


def backup(store, path):
    with store._transaction():
        return _private_backup(path, store._load())


def _read_backup(path, expected_hash):
    archive = FeaturedStore(path)
    try:
        archive._check_parent()
        fd = os.open(archive.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            archive._check_file(stream.fileno())
            raw = stream.read(MAX_BYTES + 1)
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise FeaturedUnavailable("backup_hash_mismatch")
        return archive._decode(raw)
    except (OSError, ValidationError, ValueError) as exc:
        raise FeaturedUnavailable("invalid_backup") from exc


def restore(store, archive, *, sha256, expected_revision, actor, backup_current):
    if type(expected_revision) is not int or expected_revision < 1 or not isinstance(actor, str) or not 1 <= len(actor) <= 256:
        raise FeaturedUnavailable("invalid_restore_request")
    saved = _read_backup(archive, sha256)
    with store._transaction():
        current = store._load()
        if current["revision"] != expected_revision:
            raise FeaturedConflict("featured_revision_changed")
        data = deepcopy(current)
        data["revision"] = max(current["revision"], saved["revision"]) + 1
        now = datetime.now(UTC).isoformat()
        changes = []
        # Entries absent in the saved snapshot used the default hot=True.
        # Keep tombstones with increasing versions so stale edits stay stale.
        for key in sorted(set(current["entries"]) | set(saved["entries"])):
            present = current["entries"].get(key)
            old = saved["entries"].get(key)
            entry = dict(present or old)
            entry.update(
                is_hot=old["is_hot"] if old else True,
                version=max((present or {}).get("version", 0), (old or {}).get("version", 0)) + 1,
                updated_by=actor,
                updated_at=now,
            )
            data["entries"][key] = entry
            identity = EmployeeIdentity(entry["source"], entry["name"], entry["owner_id"], entry["publication_id"])
            changes.append(dict(revision=data["revision"], identity=identity.token, actor=actor, is_hot=entry["is_hot"], at=now))
        data["changes"] = (current["changes"] + changes)[-256:]
        store._decode(json.dumps(data).encode())
        before = _private_backup(backup_current, current)
        store._save(data)
        return {"revision": data["revision"], "entries": len(data["entries"]), "preimage_sha256": before["sha256"], "restored_snapshot_sha256": sha256}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("status")
    export = commands.add_parser("backup")
    export.add_argument("--output", type=Path, required=True)
    rollback = commands.add_parser("restore")
    rollback.add_argument("--from-file", type=Path, required=True)
    rollback.add_argument("--sha256", required=True)
    rollback.add_argument("--expected-revision", type=int, required=True)
    rollback.add_argument("--actor", required=True)
    rollback.add_argument("--backup-current", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        store = FeaturedStore()
        if args.action == "backup":
            receipt = backup(store, args.output)
        elif args.action == "restore":
            receipt = restore(store, args.from_file, sha256=args.sha256, expected_revision=args.expected_revision, actor=args.actor, backup_current=args.backup_current)
        else:
            data = store.snapshot().document
            receipt = {"revision": data["revision"], "entries": len(data["entries"]), "seed_count": data["seed_count"]}
        print(json.dumps({"employee_hot_maintenance": "PASS", "action": args.action, **receipt, "database_schema_changed": False, "names_printed": False}))
        return 0
    except (FeaturedUnavailable, FeaturedConflict, OSError, ValueError) as exc:
        print(json.dumps({"employee_hot_maintenance": "FAIL", "action": args.action, "error_type": type(exc).__name__, "automatic_retry": False, "raw_error_printed": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
