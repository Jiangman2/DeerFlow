"""Repeated-cancellation regressions for Gateway run draining on shutdown."""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_run_drain_logs_failure_and_returns_without_cancellation(caplog) -> None:
    """A drain failure alone keeps the helper's existing best-effort contract."""
    from app.gateway import deps

    class _RunManager:
        async def shutdown(self, *, timeout: float) -> None:
            assert timeout == deps._RUN_DRAIN_TIMEOUT_SECONDS
            raise RuntimeError("drain failed")

    await deps._drain_inflight_runs(_RunManager())

    assert "Failed to drain in-flight runs during shutdown" in caplog.text
    assert "drain failed" in caplog.text


@pytest.mark.asyncio
async def test_run_drain_failure_preserves_remembered_cancellation(caplog) -> None:
    """A later drain error cannot replace cancellation already requested."""
    from app.gateway import deps

    shutdown_started = asyncio.Event()
    allow_shutdown_failure = asyncio.Event()

    class _RunManager:
        async def shutdown(self, *, timeout: float) -> None:
            assert timeout == deps._RUN_DRAIN_TIMEOUT_SECONDS
            shutdown_started.set()
            await allow_shutdown_failure.wait()
            raise RuntimeError("drain failed after cancellation")

    drain_task = asyncio.create_task(deps._drain_inflight_runs(_RunManager()))
    await asyncio.wait_for(shutdown_started.wait(), timeout=1.0)

    drain_task.cancel("shutdown requested")
    await asyncio.sleep(0)
    assert not drain_task.done()

    allow_shutdown_failure.set()
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await drain_task

    assert exc_info.value.args == ("shutdown requested",)
    assert "In-flight run drain failed after shutdown cancellation" in caplog.text
    assert "drain failed after cancellation" in caplog.text
