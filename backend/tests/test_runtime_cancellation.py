"""Regression coverage for cancellation-safe async context cleanup (#5757)."""

import asyncio

import pytest

from deerflow.runtime.cancellation import drained_async_context


class _Context:
    def __init__(self, *, suppress=False, exit_error=None):
        self.suppress = suppress
        self.exit_error = exit_error
        self.exit_args = None

    async def __aenter__(self):
        return "resource"

    async def __aexit__(self, exc_type, exc, tb):
        self.exit_args = (exc_type, exc, tb)
        if self.exit_error is not None:
            raise self.exit_error
        return self.suppress


@pytest.mark.asyncio
async def test_context_exit_sees_body_exception_and_can_suppress_it():
    context = _Context(suppress=True)
    async with drained_async_context(context) as value:
        assert value == "resource"
        raise ValueError("body failed")
    assert context.exit_args[0] is ValueError


@pytest.mark.asyncio
async def test_context_exit_failure_replaces_body_failure():
    context = _Context(exit_error=RuntimeError("exit failed"))
    with pytest.raises(RuntimeError, match="exit failed") as error:
        async with drained_async_context(context):
            raise ValueError("body failed")
    assert isinstance(error.value.__context__, ValueError)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_exit", [False, True])
async def test_context_exit_drains_repeated_cancellation(fail_exit):
    entered = asyncio.Event()
    exit_started = asyncio.Event()
    allow_exit = asyncio.Event()
    exit_finished = asyncio.Event()

    class _BlockingContext:
        async def __aenter__(self):
            return "resource"

        async def __aexit__(self, _exc_type, _exc, _tb):
            exit_started.set()
            await allow_exit.wait()
            exit_finished.set()
            if fail_exit:
                raise RuntimeError("exit failed")
            return False

    async def owner():
        async with drained_async_context(_BlockingContext()):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(owner())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        await asyncio.wait_for(exit_started.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        allow_exit.set()
        if fail_exit:
            with pytest.raises(RuntimeError, match="exit failed") as error:
                await task
            assert isinstance(error.value.__context__, asyncio.CancelledError)
        else:
            with pytest.raises(asyncio.CancelledError):
                await task
        assert exit_finished.is_set()
    finally:
        allow_exit.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
