"""Shared-volume curated employee flags. No startup/GET reseeding, no SQL DDL.

Every Gateway must use the same directory mount with working distributed flock
and atomic rename. Provision and verify that mount before enabling this feature.
Windows byte locks support local development, not a production NFS qualification.
"""

import ctypes
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 10000


class FeaturedUnavailable(RuntimeError):
    """Unsafe, unconfigured, busy or unreadable storage; never silently reset."""


@lru_cache(maxsize=1)
def _linux_statx():
    # Resolve the already loaded C runtime, never a caller-selected library.
    try:
        function = ctypes.CDLL(None, use_errno=True).statx
    except AttributeError:
        raise FeaturedUnavailable("featured_metadata_sync_unavailable") from None
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                         ctypes.c_uint, ctypes.c_void_p)
    function.restype = ctypes.c_int
    return function


class _ReadIdentityChanged(RuntimeError):
    """Internal pre-commit read instability, never a write retry signal."""


class FeaturedConflict(RuntimeError):
    """A stale administrator edit must be reloaded, not overwritten."""


@dataclass(frozen=True)
class EmployeeIdentity:
    source: str
    name: str
    owner_id: str
    publication_id: str = ""

    @property
    def selection_key(self) -> str:
        # Curation survives updates/republication by the same publisher. The
        # publication-specific token below still rejects stale browser edits.
        raw = json.dumps([self.source, self.name, self.owner_id], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def token(self) -> str:
        raw = json.dumps([self.source, self.name, self.owner_id, self.publication_id], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal["external", "team", "legacy"]
    name: str = Field(min_length=1, max_length=256)
    owner_id: str = Field(max_length=256)
    publication_id: str = Field(max_length=256)
    is_hot: bool
    version: int = Field(ge=1)
    updated_by: str = Field(min_length=1, max_length=256)
    updated_at: str


class _Change(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=2)
    identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor: str = Field(min_length=1, max_length=256)
    is_hot: bool
    at: str


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    revision: int = Field(ge=1)
    initialized_at: str
    initialized_by: str = Field(min_length=1, max_length=256)
    seed_count: int = Field(ge=0, le=MAX_ENTRIES)
    entries: dict[str, _Entry] = Field(max_length=MAX_ENTRIES)
    changes: list[_Change] = Field(max_length=256)


@dataclass(frozen=True)
class FeaturedSnapshot:
    document: dict

    def flag(self, identity: EmployeeIdentity) -> tuple[bool, int]:
        if identity.source not in ("external", "team", "legacy"):
            return False, 0
        entry = self.document["entries"].get(identity.selection_key)
        # Only trusted public-catalog identities reach this boundary; drafts
        # remain private. A newly published employee is visible immediately.
        return (entry["is_hot"], entry["version"]) if entry else (True, 0)


class FeaturedStore:
    def __init__(self, path: str | Path | None = None, *, lock_timeout: float = 5):
        configured = path or os.environ.get("DEERFLOW_EMPLOYEE_FEATURED_PATH")
        if not configured:
            raise FeaturedUnavailable("featured_store_not_configured")
        self.path = Path(configured)
        if not self.path.is_absolute():
            raise FeaturedUnavailable("featured_store_requires_absolute_path")
        self.lock_timeout = lock_timeout

    def _check_parent(self):
        self._retry_stale_read(self._check_parent_once)

    @staticmethod
    def _retry_stale_read(operation):
        # Another NFS client can atomically replace the JSON while this client
        # still has its old dentry. Re-resolve only read-only checks/opens; never
        # replay a transaction or an uncertain rename/fsync after a write.
        for attempt in range(3):
            try:
                return operation()
            except _ReadIdentityChanged:
                if attempt == 2:
                    raise FeaturedUnavailable("featured_file_changed_during_read") from None
                time.sleep(0.025)
            except OSError as exc:
                if exc.errno != getattr(errno, "ESTALE", -1) or attempt == 2:
                    raise
                time.sleep(0.025)

    def _check_parent_once(self):
        parent = self.path.parent
        if parent.resolve() != parent or not parent.is_dir() or self.path.is_symlink():
            raise FeaturedUnavailable("unsafe_featured_path")
        if os.name != "nt":
            metadata = parent.stat()
            if metadata.st_uid not in (0, os.geteuid()) or metadata.st_mode & 0o022:
                raise FeaturedUnavailable("unsafe_featured_parent")

    @staticmethod
    def _check_metadata(metadata):
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise FeaturedUnavailable("unsafe_featured_file")
        if os.name != "nt" and (metadata.st_uid not in (0, os.geteuid()) or metadata.st_mode & 0o077):
            raise FeaturedUnavailable("unsafe_featured_permissions")
        return metadata

    @staticmethod
    def _check_file(fd):
        return FeaturedStore._check_metadata(os.fstat(fd))

    @staticmethod
    def _read_stamp(metadata):
        # Exclude atime: this read itself may update it. Identity and security
        # attributes, plus data size/change times, must remain stable.
        return (metadata.st_dev, metadata.st_ino, metadata.st_mode,
                metadata.st_uid, metadata.st_gid, metadata.st_nlink,
                metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)

    @classmethod
    def _same_read_file(cls, opened, named):
        # Reject a newly named symlink/nonregular target rather than treating
        # it as a recoverable rename. Validate permissions independently too.
        cls._check_metadata(named)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise _ReadIdentityChanged()
        if cls._read_stamp(opened) != cls._read_stamp(named):
            raise _ReadIdentityChanged()

    @contextmanager
    def _transaction(self):
        fd = None
        locked = False
        try:
            self._check_parent()
            lock_path = self.path.with_name(self.path.name + ".lock")
            if lock_path.is_symlink():
                raise FeaturedUnavailable("unsafe_featured_lock")
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            self._check_file(fd)
            # POSIX locks are on a stable separate inode, never on replaced JSON.
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise FeaturedUnavailable("featured_lock_busy") from None
                    time.sleep(0.025)
            yield
        except (OSError, ValidationError, ValueError) as exc:
            raise FeaturedUnavailable("featured_storage_unavailable") from exc
        finally:
            if fd is not None:
                if locked and os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                os.close(fd)

    def _load(self):
        return self._decode(self._retry_stale_read(self._read_bytes))

    def _refresh_path_metadata(self):
        if not sys.platform.startswith("linux"):
            return
        # Linux UAPI statx is a fixed 256-byte buffer. Request only BASIC_STATS,
        # with an aligned opaque result; ordinary stat/fstat below still own
        # all identity, permission and data-stability validation.
        result = (ctypes.c_uint64 * 32)()
        # AT_FDCWD, SYMLINK_NOFOLLOW | NO_AUTOMOUNT | STATX_FORCE_SYNC.
        rc = _linux_statx()(-100, os.fsencode(self.path), 0x2900, 0x7FF,
                            ctypes.byref(result))
        if rc != 0:
            code = ctypes.get_errno() or errno.EIO
            if code in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
                raise FeaturedUnavailable("featured_metadata_sync_unavailable")
            raise OSError(code, "featured_metadata_sync_failed")
        # Require all fields used for security/identity comparisons. Do not
        # silently use approximate fallback metadata on an unsupported mount.
        returned = ctypes.cast(ctypes.byref(result), ctypes.POINTER(ctypes.c_uint32))[0]
        if returned & 0x3DF != 0x3DF:
            raise FeaturedUnavailable("featured_metadata_sync_unavailable")

    def _read_bytes(self):
        try:
            # An ordinary cached stat/open can repeatedly select a removed
            # NFS handle after another client atomically replaces the JSON.
            # Force server attribute revalidation by pathname before opening.
            # This is read-only, inside the existing bounded pre-commit retry.
            self._refresh_path_metadata()
            self.path.stat(follow_symlinks=False)
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            raise FeaturedUnavailable("featured_store_not_initialized") from None
        with os.fdopen(fd, "rb") as stream:
            opened = self._check_file(stream.fileno())
            self._same_read_file(opened, self.path.stat(follow_symlinks=False))
            raw = stream.read(MAX_BYTES + 1)
            after = self._check_file(stream.fileno())
            if self._read_stamp(opened) != self._read_stamp(after):
                raise _ReadIdentityChanged()
            self._same_read_file(after, self.path.stat(follow_symlinks=False))
        return raw

    @staticmethod
    def _decode(raw):
        if len(raw) > MAX_BYTES:
            raise FeaturedUnavailable("featured_store_too_large")
        document = _Document.model_validate_json(raw)
        for token, entry in document.entries.items():
            identity = EmployeeIdentity(entry.source, entry.name, entry.owner_id, entry.publication_id)
            if token != identity.selection_key or entry.version > document.revision:
                raise FeaturedUnavailable("featured_identity_mismatch")
        return document.model_dump()

    def _save(self, document):
        document = _Document.model_validate(document).model_dump()
        raw = json.dumps(document, ensure_ascii=False, indent=2).encode()
        if len(raw) > MAX_BYTES:
            raise FeaturedUnavailable("featured_store_too_large")
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                dir_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def snapshot(self) -> FeaturedSnapshot:
        with self._transaction():
            return FeaturedSnapshot(self._load())

    def initialize(self, identities: list[EmployeeIdentity], *, actor: str) -> bool:
        """Release-only explicit snapshot. Repeats validate but never reseed."""
        if len(identities) > MAX_ENTRIES or len({identity.selection_key for identity in identities}) != len(identities):
            raise ValueError("invalid_featured_seed")
        if any(identity.source not in ("team", "legacy", "external") for identity in identities):
            raise ValueError("private_employee_not_eligible")
        with self._transaction():
            if self.path.exists():
                self._load()
                return False
            now = datetime.now(UTC).isoformat()
            entries = {
                identity.selection_key: dict(source=identity.source, name=identity.name, owner_id=identity.owner_id, publication_id=identity.publication_id, is_hot=True, version=1, updated_by=actor, updated_at=now)
                for identity in identities
            }
            self._save(dict(schema_version=1, revision=1, initialized_at=now, initialized_by=actor, seed_count=len(entries), entries=entries, changes=[]))
            return True

    def set_hot(self, identity: EmployeeIdentity, is_hot: bool, *, expected_version: int, actor: str) -> int:
        if identity.source not in ("team", "legacy", "external") or type(is_hot) is not bool or type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid_featured_update")
        with self._transaction():
            data = self._load()
            current_version = FeaturedSnapshot(data).flag(identity)[1]
            if current_version != expected_version:
                raise FeaturedConflict("featured_version_conflict")
            now = datetime.now(UTC).isoformat()
            version = current_version + 1
            data["revision"] += 1
            data["entries"][identity.selection_key] = dict(source=identity.source, name=identity.name, owner_id=identity.owner_id, publication_id=identity.publication_id, is_hot=is_hot, version=version, updated_by=actor, updated_at=now)
            data["changes"] = (data["changes"] + [dict(revision=data["revision"], identity=identity.token, actor=actor, is_hot=is_hot, at=now)])[-256:]
            self._save(data)
            return version
