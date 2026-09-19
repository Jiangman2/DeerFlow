"""Cancellation ownership regressions for Gateway memory shutdown."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_memory_shutdown_drains_workers_across_repeated_cancellation(monkeypatch) -> None:
    """Cancellation cannot detach config resolution or an in-flight flush."""
    from app.gateway import memory_shutdown

    loop_thread = threading.get_ident()
    config_started = threading.Event()
    allow_config = threading.Event()
    flush_started = threading.Event()
    allow_flush = threading.Event()
    flush_finished = threading.Event()
    manager_resolved = threading.Event()
    config = SimpleNamespace(
        memory=SimpleNamespace(
            enabled=True,
            shutdown_flush_timeout_seconds=5.0,
        )
    )

    def load_config():
        assert threading.get_ident() != loop_thread
        config_started.set()
        assert allow_config.wait(5.0)
        return config

    class _Manager:
        def shutdown_flush(self, timeout: float) -> bool:
            assert threading.get_ident() != loop_thread
            assert timeout == 5.0
            flush_started.set()
            assert allow_flush.wait(5.0)
            flush_finished.set()
            return True

    manager = _Manager()

    def load_manager():
        assert threading.get_ident() != loop_thread
        manager_resolved.set()
        return manager

    monkeypatch.setattr(memory_shutdown, "_load_app_config", load_config)
    monkeypatch.setattr(memory_shutdown, "_load_memory_manager", load_manager)

    shutdown_task = asyncio.create_task(memory_shutdown.shutdown_memory_backend())
    try:
        assert await asyncio.to_thread(config_started.wait, 1.0)

        shutdown_task.cancel("first shutdown cancellation")
        await asyncio.sleep(0)
        shutdown_task.cancel("cancellation during config resolution")
        for _ in range(10):
            await asyncio.sleep(0)

        assert not shutdown_task.done()
        assert not manager_resolved.is_set()
        allow_config.set()

        assert await asyncio.to_thread(flush_started.wait, 1.0)
        assert manager_resolved.is_set()
        shutdown_task.cancel("cancellation during memory flush")
        for _ in range(10):
            await asyncio.sleep(0)

        assert not shutdown_task.done()
        allow_flush.set()

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await shutdown_task

        assert exc_info.value.args == ("first shutdown cancellation",)
        assert flush_finished.is_set()
    finally:
        allow_config.set()
        allow_flush.set()
        await asyncio.gather(shutdown_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_memory_shutdown_contains_config_resolution_failure(monkeypatch, caplog) -> None:
    """An invalid shutdown-time config remains a best-effort failure."""
    from app.gateway import memory_shutdown

    def invalid_config():
        raise ValueError("invalid shutdown config")

    def unexpected_manager():
        raise AssertionError("manager must not be resolved after a config failure")

    monkeypatch.setattr(memory_shutdown, "_load_app_config", invalid_config)
    monkeypatch.setattr(memory_shutdown, "_load_memory_manager", unexpected_manager)

    await memory_shutdown.shutdown_memory_backend()

    assert "Failed to flush memory queue on shutdown" in caplog.text
    assert "invalid shutdown config" in caplog.text


@pytest.mark.asyncio
async def test_memory_shutdown_skips_manager_when_memory_is_disabled(monkeypatch) -> None:
    """Disabled memory does not instantiate a backend during shutdown."""
    from app.gateway import memory_shutdown

    config = SimpleNamespace(memory=SimpleNamespace(enabled=False))

    def unexpected_manager():
        raise AssertionError("disabled memory must not resolve a manager")

    monkeypatch.setattr(memory_shutdown, "_load_app_config", lambda: config)
    monkeypatch.setattr(memory_shutdown, "_load_memory_manager", unexpected_manager)

    await memory_shutdown.shutdown_memory_backend()
