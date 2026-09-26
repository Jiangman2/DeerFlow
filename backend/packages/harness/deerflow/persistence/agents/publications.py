"""Immutable Square revisions on the existing shared agent volume.

Only ``current.json`` is replaced at commit. Readers never observe a partially
copied config/SOUL/avatar. Writers use a bounded OS file lock and an expected
revision, not a process-local mutex. Multi-node use requires a shared filesystem
with working advisory locks and atomic same-directory rename (release canary).
This stores definitions, NOT Skill/knowledge delegation grants.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from deerflow.config.paths import Paths

_REVISION = re.compile(r"(?:[a-f0-9]{32}|legacy-[a-f0-9]{64})\Z")
_MAX_BYTES = 8 * 1024 * 1024


class PublicationConflict(Exception):
    """Reload before publishing; the observed revision is no longer current."""


class PublicationForbidden(Exception):
    """Only the existing publisher may alter or inspect private history."""


@dataclass(frozen=True)
class PublicationSnapshot:
    publication_id: str
    revision: str
    previous_revision: str | None
    owner: str
    state: str
    created_at: str
    directory: Path
    config: dict
    soul: str
    digest: str
    name: str


def _read_file(path: Path, *, optional: bool = False) -> bytes:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return b""
        raise
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
        raise ValueError("Unsafe publication file")
    with path.open("rb") as stream:
        value = stream.read(_MAX_BYTES + 1)
    if len(value) > _MAX_BYTES:
        raise ValueError("Publication file exceeds limit")
    return value


def _digest(config: bytes, soul: bytes, avatar: bytes) -> str:
    # Length framing prevents ambiguity between adjacent members.
    digest = hashlib.sha256()
    for value in (config, soul, avatar):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _sync_dir(directory: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write_file(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _publication_lock(path: Path):
    # Never unlink the lock inode; another worker may still hold it.
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Unsafe publication lock")
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 5
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise PublicationConflict("Publication is busy; reload and retry") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class TeamPublicationStore:
    def __init__(self, paths: Paths):
        self.paths = paths

    def _directory(self, name: str) -> Path:
        from deerflow.config.agents_config import validate_agent_name

        if not validate_agent_name(name):
            raise ValueError("Expected employee name")
        directory = self.paths.team_agent_dir(name)
        if directory.is_symlink() or (directory / ".revisions").is_symlink():
            raise ValueError("Unsafe publication directory")
        return directory

    def has_record(self, name: str) -> bool:
        root = self._directory(name)
        pointer = root / "current.json"
        return pointer.exists() or pointer.is_symlink() or (root / "config.yaml").is_file()

    def reserve_identity(self, name: str, *, owner: str) -> str:
        """Bind pre-publication resource requests to one stable employee id."""
        if not owner:
            raise PublicationForbidden("Authenticated publisher required")
        root = self._directory(name)
        with _publication_lock(root / ".publication.lock"):
            previous = self.head(name) if self.has_record(name) else None
            if previous is not None:
                self._authorize(previous, owner)
            return self._reserved_identity(root, owner, previous)

    def _reserved_identity(self, root, owner, previous) -> str:
        path = root / ".identity.json"
        try:
            record = json.loads(_read_file(path))
        except FileNotFoundError:
            identity = previous.publication_id if previous else uuid.uuid4().hex
            record = {"schema": 1, "owner": owner, "publication_id": identity}
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=root, prefix=".identity-", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(json.dumps(record, sort_keys=True).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                _sync_dir(root)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        if not isinstance(record, dict) or record.get("schema") != 1 or not re.fullmatch(r"[a-f0-9]{32}", str(record.get("publication_id", ""))):
            raise ValueError("Invalid employee identity reservation")
        if record.get("owner") != owner:
            raise PublicationForbidden("Employee publication name is reserved by another publisher")
        if previous is not None and record["publication_id"] != previous.publication_id:
            raise ValueError("Employee publication identity mismatch")
        return record["publication_id"]

    def _legacy(self, root: Path, name: str) -> PublicationSnapshot:
        config = _read_file(root / "config.yaml")
        soul = _read_file(root / "SOUL.md", optional=True)
        avatar = _read_file(root / "avatar.webp", optional=True)
        document = yaml.safe_load(config) or {}
        if not isinstance(document, dict):
            raise ValueError("Invalid publication config")
        owner = str(document.get("published_by_user_id") or "")
        digest = _digest(config, soul, avatar)
        identity = hashlib.sha256((name.lower() + "\0" + owner).encode()).hexdigest()[:32]
        return PublicationSnapshot(identity, "legacy-" + digest, None, owner, "published", "", root, document, soul.decode("utf-8"), digest, name)

    def _snapshot(self, root: Path, revision: str) -> PublicationSnapshot:
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise ValueError("Invalid publication revision")
        directory = root / ".revisions" / revision
        if directory.is_symlink():
            raise ValueError("Unsafe publication directory")
        metadata = json.loads(_read_file(directory / "revision.json"))
        if not isinstance(metadata, dict):
            raise ValueError("Invalid publication metadata")
        config = _read_file(directory / "config.yaml")
        soul = _read_file(directory / "SOUL.md")
        avatar = _read_file(directory / "avatar.webp", optional=True)
        digest = _digest(config, soul, avatar)
        if metadata.get("revision") != revision or metadata.get("digest") != digest:
            raise ValueError("Publication integrity check failed")
        if metadata.get("state") not in ("published", "withdrawn"):
            raise ValueError("Invalid publication state")
        document = yaml.safe_load(config)
        if not isinstance(document, dict) or not isinstance(metadata.get("owner"), str):
            raise ValueError("Invalid publication config")
        if str(document.get("published_by_user_id") or "") != metadata["owner"]:
            raise ValueError("Publication owner mismatch")
        return PublicationSnapshot(
            metadata["publication_id"],
            revision,
            metadata["previous_revision"],
            metadata["owner"],
            metadata["state"],
            metadata["created_at"],
            directory,
            document,
            soul.decode("utf-8"),
            digest,
            root.name,
        )

    def head(self, name: str) -> PublicationSnapshot:
        root = self._directory(name)
        try:
            pointer = json.loads(_read_file(root / "current.json"))
        except FileNotFoundError:
            return self._legacy(root, name)
        if not isinstance(pointer, dict) or pointer.get("schema") != 1:
            raise ValueError("Unsupported publication schema")
        return self._snapshot(root, pointer["revision"])

    def read(self, name: str) -> PublicationSnapshot:
        snapshot = self.head(name)
        if snapshot.state != "published":
            raise FileNotFoundError("Published employee is withdrawn")
        return snapshot

    def freeze_current(self, name: str) -> PublicationSnapshot:
        """Archive an admitted legacy definition without rewriting its files."""
        root = self._directory(name)
        with _publication_lock(root / ".publication.lock"):
            current = self.read(name)
            if current.directory == root:
                (root / ".revisions").mkdir(exist_ok=True)
                self._stage(root, current, _read_file(root / "config.yaml"), _read_file(root / "SOUL.md", optional=True), _read_file(root / "avatar.webp", optional=True))
                current = self._snapshot(root, current.revision)
            return current

    def read_admitted(self, name: str, *, publication_id: str, revision: str, digest: str) -> PublicationSnapshot:
        """Read a server-owned thread receipt, NEVER arbitrary browser input.

        Withdrawal blocks subsequent runs without deleting their receipts. The
        digest binds the original admission, including archived legacy heads.
        This does not confer any Skill/knowledge resource authorization.
        """
        head = self.read(name)
        if head.publication_id != publication_id:
            raise ValueError("Published employee identity changed")
        snapshot = self._snapshot(self._directory(name), revision)
        if snapshot.publication_id != publication_id or snapshot.digest != digest or snapshot.state != "published" or snapshot.owner != head.owner:
            raise ValueError("Published employee admission no longer matches")
        return snapshot

    @staticmethod
    def _authorize(current: PublicationSnapshot, owner: str) -> None:
        if not owner or current.owner != owner:
            raise PublicationForbidden("Only the publisher may manage this publication")

    def history(self, name: str, *, owner: str, limit: int = 20) -> list[PublicationSnapshot]:
        if not 1 <= limit <= 100:
            raise ValueError("History limit must be between 1 and 100")
        current = self.head(name)
        self._authorize(current, owner)
        result = []
        while current and len(result) < limit:
            result.append(current)
            current = self._snapshot(self._directory(name), current.previous_revision) if current.previous_revision else None
        return result

    def read_revision(self, name: str, revision: str, *, owner: str) -> PublicationSnapshot:
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise ValueError("Invalid publication revision")
        current = self.head(name)
        self._authorize(current, owner)
        # Only committed lineage is addressable. A failed staging write is not
        # a publication, even if its random revision id somehow becomes known.
        for _ in range(1000):
            if current.revision == revision:
                return current
            if not current.previous_revision:
                break
            current = self._snapshot(self._directory(name), current.previous_revision)
        raise FileNotFoundError("Committed publication revision not found")

    def publish(self, name: str, *, owner: str, config: dict, soul: str, avatar: bytes | None, expected_revision: str | None, skill_bundle: Path | None = None) -> PublicationSnapshot:
        if not owner:
            raise PublicationForbidden("Authenticated publisher required")
        if skill_bundle is not None:
            from deerflow.skills.agent_publication import read_skill_bundle

            digest, _, _ = read_skill_bundle(skill_bundle, names=config.get("skills"))
            config = {**config, "skills_revision": digest}
        elif "skills_revision" in config:
            raise ValueError("A Skill snapshot claim requires its verified bundle")
        root = self._directory(name)
        with _publication_lock(root / ".publication.lock"):
            try:
                previous = self.head(name)
            except FileNotFoundError:
                # A broken pointer must never be treated as a fresh publish.
                if (root / "current.json").exists():
                    raise
                previous = None
            if previous:
                self._authorize(previous, owner)
            if (previous.revision if previous else None) != expected_revision:
                raise PublicationConflict("Publication changed; reload before publishing")
            return self._append(root, name, owner, previous, config, soul, avatar, "published", skill_bundle=skill_bundle)

    def unpublish(self, name: str, *, owner: str, expected_revision: str | None) -> PublicationSnapshot:
        root = self._directory(name)
        with _publication_lock(root / ".publication.lock"):
            previous = self.read(name)
            self._authorize(previous, owner)
            if previous.revision != expected_revision:
                raise PublicationConflict("Publication changed; reload before withdrawing")
            return self._append(root, name, owner, previous, previous.config, previous.soul, _read_file(previous.directory / "avatar.webp", optional=True), "withdrawn")

    def _append(self, root, name, owner, previous, config, soul, avatar, state, *, skill_bundle=None):
        revisions = root / ".revisions"
        revisions.mkdir(exist_ok=True)
        if previous and previous.revision.startswith("legacy-") and previous.directory == root:
            # Retain the exact historical bytes (including the old digest).
            self._stage(root, previous, _read_file(root / "config.yaml"), _read_file(root / "SOUL.md", optional=True), _read_file(root / "avatar.webp", optional=True))
        revision = uuid.uuid4().hex
        document = {**config, "name": name.lower(), "published_by_user_id": owner}
        config_bytes = yaml.safe_dump(document, allow_unicode=True, sort_keys=True).encode("utf-8")
        soul_bytes = soul.encode("utf-8")
        avatar = avatar or b""
        if sum(map(len, (config_bytes, soul_bytes, avatar))) > _MAX_BYTES:
            raise ValueError("Publication exceeds size limit")
        snapshot = PublicationSnapshot(
            self._reserved_identity(root, owner, previous),
            revision,
            previous.revision if previous else None,
            owner,
            state,
            datetime.now(UTC).isoformat(),
            revisions / revision,
            document,
            soul,
            _digest(config_bytes, soul_bytes, avatar),
            name,
        )
        if skill_bundle is None:
            self._stage(root, snapshot, config_bytes, soul_bytes, avatar)
        else:
            self._stage(root, snapshot, config_bytes, soul_bytes, avatar, skill_bundle=skill_bundle)
        self._commit_head(root, revision)
        return snapshot

    def _stage(self, root, snapshot, config, soul, avatar, *, skill_bundle=None):
        directory = root / ".revisions" / snapshot.revision
        if directory.exists():
            existing = self._snapshot(root, snapshot.revision)
            if existing.digest != snapshot.digest:
                raise ValueError("Publication integrity check failed")
            return
        # A crash while adopting a legacy version must not permanently poison
        # that deterministic revision id. Failed staging remains unreachable;
        # only a complete directory receives the immutable revision name.
        staging = Path(tempfile.mkdtemp(dir=directory.parent, prefix=".staging-"))
        if skill_bundle is not None:
            from deerflow.skills.agent_publication import copy_skill_bundle

            copy_skill_bundle(skill_bundle, staging, names=snapshot.config["skills"], digest=snapshot.config["skills_revision"])
        _write_file(staging / "config.yaml", config)
        _write_file(staging / "SOUL.md", soul)
        if avatar:
            _write_file(staging / "avatar.webp", avatar)
        metadata = {key: getattr(snapshot, key) for key in ("publication_id", "revision", "previous_revision", "owner", "state", "created_at", "digest")}
        _write_file(staging / "revision.json", json.dumps(metadata, sort_keys=True).encode())
        _sync_dir(staging)
        os.rename(staging, directory)
        _sync_dir(directory.parent)

    def _commit_head(self, root, revision):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=root, prefix=".current-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps({"schema": 1, "revision": revision}).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, root / "current.json")
            _sync_dir(root)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
