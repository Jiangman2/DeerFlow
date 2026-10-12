from __future__ import annotations

import pytest

from deerflow.runtime.runs.stream_cleanup import close_agent_stream


@pytest.mark.asyncio
async def test_close_agent_stream_ignores_stream_without_aclose():
    await close_agent_stream(object())


@pytest.mark.asyncio
async def test_close_agent_stream_awaits_async_cleanup():
    closed = False

    class Stream:
        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    await close_agent_stream(Stream())

    assert closed is True


@pytest.mark.asyncio
async def test_close_agent_stream_accepts_synchronous_cleanup():
    closed = False

    class Stream:
        def aclose(self) -> None:
            nonlocal closed
            closed = True

    await close_agent_stream(Stream())

    assert closed is True
