import asyncio
import stat
import threading
from unittest.mock import MagicMock

import pytest

from app.channels import attachment_storage as storage
from deerflow.config.paths import Paths


def test_shared_mount_does_not_overwrite_as_sandbox_user(tmp_path):
    provider = MagicMock(uses_thread_data_mounts=True)
    provider.acquire.side_effect = AssertionError("No Sandbox needed for shared upload")
    paths = Paths(base_dir=tmp_path)
    result = asyncio.run(storage.store_attachment(paths, lambda: provider, "thread", "owner", "report.md", b"data"))
    target = paths.sandbox_uploads_dir("thread", user_id="owner") / "report.md"
    assert target.read_bytes() == b"data"
    assert stat.S_IMODE(target.stat().st_mode) & stat.S_IROTH
    assert not list(target.parent.glob(".feishu-*"))
    assert result == "/mnt/user-data/uploads/report.md"
    provider.acquire.assert_not_called()
    provider.get.assert_not_called()


def test_remote_permission_error_is_not_reported_as_delivery(tmp_path):
    provider = MagicMock(uses_thread_data_mounts=False)
    provider.acquire.return_value = "remote"
    provider.get.return_value.update_file.side_effect = PermissionError("synthetic")
    with pytest.raises(PermissionError):
        asyncio.run(storage.store_attachment(Paths(base_dir=tmp_path), lambda: provider, "thread", "owner", "file.md", b"data"))
    provider.acquire.assert_called_once_with("thread", user_id="owner")
    assert storage.CAPACITY.active == 0


def test_remote_io_keeps_loop_responsive_and_cancellation_owns_worker(tmp_path):
    entered, finish = threading.Event(), threading.Event()

    def write(*_):
        entered.set()
        assert finish.wait(3)

    provider = MagicMock(uses_thread_data_mounts=False)
    provider.acquire.return_value = "remote"
    provider.get.return_value.update_file.side_effect = write

    async def run():
        task = asyncio.create_task(storage.store_attachment(Paths(base_dir=tmp_path), lambda: provider, "thread", "owner", "x.md", b"x"))
        try:
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            # This represents /health and run lease heartbeat scheduling.
            await asyncio.wait_for(asyncio.sleep(0.01), 0.2)
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            assert storage.CAPACITY.active == 1
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert storage.CAPACITY.active == 0

    asyncio.run(run())


@pytest.mark.parametrize("name", ["../escape", ".", "..", ""])
def test_invalid_attachment_name(tmp_path, name):
    with pytest.raises(ValueError):
        storage.persist_attachment(Paths(base_dir=tmp_path), lambda: None, "thread", "owner", name, b"data")
