"""Persist channel attachments once; sync only when the provider requires it."""

import os
import tempfile
from pathlib import Path

from deerflow.utils.bounded_work import WorkCapacity, owned_call

CAPACITY = WorkCapacity(active=2, waiting=4)


def persist_attachment(paths, provider_factory, thread_id, user_id, filename, content):
    if not filename or Path(filename).name != filename or filename in (".", ".."):
        raise ValueError("Invalid attachment filename")
    paths.ensure_thread_dirs(thread_id, user_id=user_id)
    directory = paths.sandbox_uploads_dir(thread_id, user_id=user_id).resolve()
    target = directory / filename
    if target.is_symlink():
        raise ValueError("Symlink attachment destination rejected")
    fd, name = tempfile.mkstemp(prefix=".feishu-", suffix=".tmp", dir=directory)
    pending = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Same read policy as browser uploads. Sandbox never needs to overwrite
        # a Gateway-owned source file on a shared mount.
        pending.chmod(0o644)
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)
    virtual_path = f"/mnt/user-data/uploads/{filename}"
    provider = provider_factory()
    if provider.uses_thread_data_mounts:
        return virtual_path
    sandbox_id = provider.acquire(thread_id, user_id=user_id)
    if sandbox_id != "local":
        sandbox = provider.get(sandbox_id)
        if sandbox is None:
            raise RuntimeError("Attachment sandbox unavailable")
        sandbox.update_file(virtual_path, content)
    return virtual_path


async def store_attachment(*args):
    await CAPACITY.acquire()
    try:
        return await owned_call(persist_attachment, *args)
    finally:
        CAPACITY.release()
