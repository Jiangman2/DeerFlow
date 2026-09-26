"""Real POSIX permission assertions, independent of Gateway/router startup.

These are unchanged security assertions split from the router IO tests so they
can also run in an offline Linux container without the whole harness fixture
graph. Normal collection still applies the blocking_io conftest. A dedicated
``--noconftest`` run checks filesystem permissions only, not the async IO gate.
Windows mode bits are not a POSIX/ACL security proof; do not relax 0600 or mark
these tests passed there. Linux fixtures must live on a native filesystem,
not a Windows bind mount.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest import mock

import pytest

from app.channels.runtime_config_store import ChannelRuntimeConfigStore

pytestmark = pytest.mark.asyncio


async def test_runtime_config_store_file_is_owner_only(tmp_path) -> None:
    path = tmp_path / "channels" / "runtime-config.json"
    store = await asyncio.to_thread(ChannelRuntimeConfigStore, path)

    await asyncio.to_thread(
        store.set_provider_config,
        "slack",
        {"enabled": True, "bot_token": "xoxb-ui", "app_token": "xapp-ui"},
    )

    mode = await asyncio.to_thread(lambda: path.stat().st_mode & 0o777)
    assert mode == 0o600


async def test_runtime_config_store_overwrites_loose_existing_file(tmp_path) -> None:
    """A pre-existing world-readable file is tightened to 0o600 after a save.

    ``NamedTemporaryFile`` would yield 0o600 on a fresh path regardless of the
    code under test, so seed the destination at 0o644 first: only the store's
    atomic 0o600-temp + replace path produces an owner-only file here.
    """
    path = tmp_path / "channels" / "runtime-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)

    store = await asyncio.to_thread(ChannelRuntimeConfigStore, path)
    await asyncio.to_thread(
        store.set_provider_config,
        "slack",
        {"enabled": True, "bot_token": "xoxb-ui"},
    )

    mode = await asyncio.to_thread(lambda: path.stat().st_mode & 0o777)
    assert mode == 0o600


async def test_runtime_config_store_chmod_failure_is_logged_not_fatal(tmp_path, caplog) -> None:
    """Failed pre-rename chmod retains mkstemp's private mode after replace."""
    path = tmp_path / "channels" / "runtime-config.json"
    store = await asyncio.to_thread(ChannelRuntimeConfigStore, path)

    real_chmod = Path.chmod

    def chmod_spy(self: Path, mode: int, *args, **kwargs):
        if self.suffix == ".tmp":
            raise OSError("chmod unsupported on this filesystem")
        return real_chmod(self, mode, *args, **kwargs)

    def _save_with_failing_temp_chmod() -> None:
        with caplog.at_level(logging.DEBUG, logger="app.channels.runtime_config_store"), mock.patch.object(Path, "chmod", chmod_spy):
            store.set_provider_config("slack", {"enabled": True, "bot_token": "xoxb-ui"})

    await asyncio.to_thread(_save_with_failing_temp_chmod)

    assert any("Unable to chmod temporary channel runtime config store" in record.getMessage() for record in caplog.records)
    mode = await asyncio.to_thread(lambda: path.stat().st_mode & 0o777)
    assert mode == 0o600
    assert await asyncio.to_thread(store.get_provider_config, "slack") == {"enabled": True, "bot_token": "xoxb-ui"}
