"""Raw private employee definitions and compare-and-swap editing.

The file backend retains config.yaml/SOUL.md for legacy readers. A durable undo
journal makes interrupted multi-file edits recoverable without deleting the
employee, avatar, memory, or unknown fields. Only the narrow draft readers and
writers using this boundary are transactional; legacy direct writers must be
migrated before enabling the complete editing feature in a release.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml

from deerflow.persistence.agents.publications import PublicationConflict, _publication_lock, _read_file, _sync_dir

_JOURNAL = ".draft-edit.pending.json"
_MAX_JOURNAL = 24 * 1024 * 1024


class DraftConflict(Exception):
    """The editor must reload, never retry a stale mutation automatically."""


@dataclass(frozen=True)
class AgentDefinition:
    config: dict
    soul: str
    revision: str

    @classmethod
    def from_parts(cls, name: str, config: dict, soul: str) -> AgentDefinition:
        # The identifier belongs to the store key, not to a client patch.
        document = {**config, "name": name.lower()}
        payload = json.dumps([document, soul], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return cls(document, soul, "draft-" + hashlib.sha256(payload.encode()).hexdigest())


def patched_definition(before: AgentDefinition, updates: dict, soul: str | None, expected_revision: str | None) -> AgentDefinition:
    if expected_revision is None or expected_revision != before.revision:
        raise DraftConflict("Employee draft changed; reload before saving")
    if "name" in updates and updates["name"] != before.config["name"]:
        raise ValueError("Employee identity cannot be renamed by a draft edit")
    return AgentDefinition.from_parts(before.config["name"], {**before.config, **updates}, before.soul if soul is None else soul)


def _replace_bytes(target: Path, content: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".draft-", delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _safe_root(root: Path) -> None:
    if not root.is_dir() or root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise FileNotFoundError("Personal employee directory is unavailable")
    for name in ("config.yaml", "SOUL.md", _JOURNAL, ".draft-edit.lock"):
        if (root / name).is_symlink():
            raise ValueError("Unsafe employee draft path")


@contextmanager
def file_draft_transaction(root: Path):
    _safe_root(root)
    try:
        # A deletion must not unlink the inode another writer is waiting on.
        # Keep the identity lock outside the removable employee directory.
        locks = root.parent / ".draft-locks"
        if locks.is_symlink() or getattr(locks, "is_junction", lambda: False)():
            raise ValueError("Unsafe employee draft lock directory")
        key = hashlib.sha256(root.name.encode("utf-8")).hexdigest()
        with _publication_lock(locks / (key + ".lock")):
            _safe_root(root)
            recover_file_draft(root)
            yield
    except PublicationConflict:
        raise DraftConflict("Employee draft is busy; reload before saving") from None


def read_file_definition(root: Path, name: str) -> AgentDefinition:
    """Caller holds file_draft_transaction, including recovery before read."""
    document = yaml.safe_load(_read_file(root / "config.yaml")) or {}
    if not isinstance(document, dict):
        raise ValueError("Employee config must be a mapping")
    return AgentDefinition.from_parts(name, document, _read_file(root / "SOUL.md", optional=True).decode("utf-8"))


def recover_file_draft(root: Path) -> None:
    """Undo an uncommitted edit using only fixed, server-owned member names."""
    journal = root / _JOURNAL
    if not journal.exists():
        return
    if not journal.is_file() or journal.stat().st_size > _MAX_JOURNAL:
        raise ValueError("Invalid employee draft recovery journal")
    with journal.open("rb") as stream:
        raw = stream.read(_MAX_JOURNAL + 1)
    if len(raw) > _MAX_JOURNAL:
        raise ValueError("Invalid employee draft recovery journal")
    try:
        document = json.loads(raw)
        if document["schema"] != 1 or set(document["before"]) != {"config.yaml", "SOUL.md"}:
            raise ValueError("Invalid employee draft recovery journal")
        restored = {key: None if value is None else base64.b64decode(value, validate=True) for key, value in document["before"].items()}
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid employee draft recovery journal") from None
    # Decode and validate the entire journal before replacing either member.
    for name, content in restored.items():
        target = root / name
        if content is None:
            target.unlink(missing_ok=True)
        else:
            _replace_bytes(target, content)
    _sync_dir(root)
    journal.unlink()
    _sync_dir(root)


def commit_file_definition(root: Path, definition: AgentDefinition) -> None:
    """Commit a pair, retaining exact previous bytes until BOTH are durable."""
    config = yaml.safe_dump(definition.config, allow_unicode=True, sort_keys=False).encode("utf-8")
    soul = definition.soul.encode("utf-8")
    if len(config) + len(soul) > 8 * 1024 * 1024:
        raise ValueError("Employee definition exceeds size limit")
    before = {}
    for name in ("config.yaml", "SOUL.md"):
        target = root / name
        before[name] = base64.b64encode(_read_file(target)).decode("ascii") if target.exists() else None
    journal = json.dumps({"schema": 1, "before": before}).encode()
    if len(journal) > _MAX_JOURNAL:
        raise ValueError("Employee draft recovery journal exceeds size limit")
    _replace_bytes(root / _JOURNAL, journal)
    _sync_dir(root)
    _replace_bytes(root / "config.yaml", config)
    _replace_bytes(root / "SOUL.md", soul)
    _sync_dir(root)
    (root / _JOURNAL).unlink()
    _sync_dir(root)
