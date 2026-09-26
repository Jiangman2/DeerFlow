"""Immutable private avatar assets, selected by the definition's CAS pointer.

Stage/fsync the bytes before changing the raw config reference. A failed CAS
leaves at most an unreferenced asset, never a half-replaced current portrait.
The old avatar.webp and superseded assets are deliberately retained. Legacy
images need an explicit compatibility adapter before rollback after first use.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path

from deerflow.persistence.agents.publications import _sync_dir

AVATAR_ASSET_KEY = "avatar_asset_sha256"
MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATAR_FORMAT_MARKER = b"avatar-reference-v1\n"


def _no_link(path: Path) -> None:
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("Unsafe employee avatar path")


def read_avatar_bytes(path: Path) -> bytes:
    _no_link(path.parent)
    _no_link(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_AVATAR_BYTES:
        raise ValueError("Invalid employee avatar")
    with path.open("rb") as stream:
        payload = stream.read(MAX_AVATAR_BYTES + 1)
    if len(payload) > MAX_AVATAR_BYTES:
        raise ValueError("Employee avatar exceeds size limit")
    return payload


def personal_avatar_path(root: Path, config: dict) -> Path | None:
    _no_link(root)
    if AVATAR_ASSET_KEY not in config:
        path = root / "avatar.webp"
        _no_link(path)
        return path if path.is_file() else None
    digest = config[AVATAR_ASSET_KEY]
    if digest is None:
        return None  # Explicit removal must not fall back to the old file.
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("Invalid employee avatar reference")
    path = root / ".avatar-assets" / (digest + ".webp")
    if hashlib.sha256(read_avatar_bytes(path)).hexdigest() != digest:
        raise ValueError("Employee avatar digest mismatch")
    return path


def mark_avatar_format(root: Path) -> Path:
    """Durably block unadapted legacy rollback before committing a pointer.

    Also required for explicit removal, which must not resurrect avatar.webp.
    A losing CAS can leave a conservative marker; this never changes the avatar.
    """
    _no_link(root)
    directory = root / ".avatar-assets"
    _no_link(directory)
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / "FORMAT-v1"
    _no_link(marker)
    if marker.exists():
        if read_avatar_bytes(marker) != AVATAR_FORMAT_MARKER:
            raise ValueError("Invalid employee avatar format marker")
        return directory
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".avatar-format-", delete=False) as stream:
            temporary = stream.name
            stream.write(AVATAR_FORMAT_MARKER)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
        _sync_dir(directory)
        _sync_dir(root)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return directory


def stage_avatar(root: Path, payload: bytes) -> str:
    """Only accepts already re-encoded server bytes, not a user-chosen path."""
    if not payload or len(payload) > MAX_AVATAR_BYTES:
        raise ValueError("Invalid employee avatar size")
    directory = mark_avatar_format(root)
    digest = hashlib.sha256(payload).hexdigest()
    target = directory / (digest + ".webp")
    _no_link(target)
    if target.exists():
        if read_avatar_bytes(target) != payload:
            raise ValueError("Existing employee avatar asset is damaged")
        return digest
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".avatar-", delete=False) as stream:
            temporary = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _sync_dir(directory)
        _sync_dir(root)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return digest
