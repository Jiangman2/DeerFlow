"""Small shared-volume display configuration with bounded locking and CAS.

Uses the same RWX userdata root as employee metadata. GET never initializes or
repairs storage. Production requires flock and atomic rename on that volume.
"""

import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from deerflow.config.paths import get_paths


class DisplayStoreError(RuntimeError):
    pass


class DisplayConflict(DisplayStoreError):
    pass


class SelectedEmployee(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    publication: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(min_length=1, max_length=256)


class DisplayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0, strict=True)
    title: str = Field(default="数字员工比赛展示", min_length=1, max_length=60)
    enabled: StrictBool = False
    employees: list[SelectedEmployee] = Field(default_factory=list, max_length=100)
    updated_at: str | None = Field(default=None, max_length=64)
    updated_by: str | None = Field(default=None, max_length=128)

    @field_validator("title")
    @classmethod
    def meaningful_title(cls, value):
        if not value.strip():
            raise ValueError("display_title_required")
        return value.strip()


def display_path() -> Path:
    configured = os.environ.get("COMPETITION_DISPLAY_PATH")
    path = Path(configured) if configured else get_paths().base_dir / "competition" / "display.json"
    if not path.is_absolute():
        raise DisplayStoreError("display_path_must_be_absolute")
    return path


def _check_parent(path: Path):
    if any(parent.is_symlink() for parent in (path.parent, *path.parent.parents)):
        raise DisplayStoreError("display_parent_symlink")


def read_display() -> DisplayConfig:
    path = display_path()
    _check_parent(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return DisplayConfig()
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 131072 or path.is_symlink():
            raise DisplayStoreError("invalid_display_file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(131073)
        if len(data) > 131072:
            raise DisplayStoreError("invalid_display_size")
        return DisplayConfig.model_validate_json(data)
    finally:
        os.close(fd)


@contextmanager
def _lock(path: Path):
    # Production Linux; Windows support is for local rendering/testing only.
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    acquired = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink():
            raise DisplayStoreError("invalid_display_lock")
        if os.name == "nt":
            import msvcrt

            if info.st_size == 0:
                os.write(fd, b"0")
        else:
            import fcntl
        deadline = time.monotonic() + 2
        while True:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= deadline:
                    raise DisplayStoreError("display_edit_busy") from None
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def save_display(config: DisplayConfig, actor: str) -> DisplayConfig:
    path = display_path()
    _check_parent(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _check_parent(path)
    with _lock(path.with_suffix(".lock")):
        previous = read_display()
        if previous.revision != config.revision:
            raise DisplayConflict("display_revision_changed")
        result = config.model_copy(update={"revision": previous.revision + 1, "updated_at": datetime.now(UTC).isoformat(), "updated_by": actor})
        payload = json.dumps(result.model_dump(), ensure_ascii=False).encode()
        if len(payload) > 131072:
            raise DisplayStoreError("display_too_large")
        fd, temporary = tempfile.mkstemp(prefix=".display-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return result
