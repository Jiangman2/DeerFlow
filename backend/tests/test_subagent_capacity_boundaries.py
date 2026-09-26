"""Enterprise lifecycle boundaries around the pinned native capacity controller."""

import asyncio

import pytest
from pydantic import ValidationError

from deerflow.config.app_config import AppConfig
from deerflow.config.subagent_runtime_config import SubagentRuntimeConfig
from deerflow.subagents import capacity as module


@pytest.mark.parametrize(
    "values",
    [
        {"max_running": 0},
        {"max_running": 65},
        {"max_queued": -1},
        {"max_queued": 10001},
        {"queue_timeout_seconds": 0},
        {"queue_timeout_seconds": 86401},
        {"admission_policy": "unbounded"},
    ],
)
def test_schema_rejects_unbounded_configuration(values):
    with pytest.raises(ValidationError):
        SubagentRuntimeConfig(**values)


def test_config_defaults_and_null_section_remain_backward_compatible():
    config = AppConfig.model_validate({"sandbox": {"use": "test.Sandbox"}, "subagent_runtime": None})
    assert config.subagent_runtime.model_dump() == {
        "max_running": 3,
        "max_queued": 64,
        "admission_policy": "queue",
        "queue_timeout_seconds": 300,
    }


def test_controller_owns_configuration_snapshot():
    config = SubagentRuntimeConfig(max_running=1, max_queued=2)
    controller = module.SubagentExecutionCapacity(config)
    config.max_running = 64
    config.max_queued = 10000
    assert controller.snapshot().max_running == 1
    assert controller.snapshot().max_queued == 2


@pytest.mark.asyncio
async def test_zero_queue_rejects_without_scheduling_waiter():
    controller = module.SubagentExecutionCapacity(SubagentRuntimeConfig(max_running=1, max_queued=0))
    async with controller.slot():
        with pytest.raises(module.SubagentCapacityRejected):
            async with controller.slot():
                pytest.fail("must not admit work")
        assert controller.snapshot().queued == 0
    assert controller.snapshot().running == 0


@pytest.mark.asyncio
async def test_fifo_skips_cancelled_waiter_and_never_exceeds_capacity():
    controller = module.SubagentExecutionCapacity(SubagentRuntimeConfig(max_running=1, max_queued=5))
    entered = []

    async def worker(index):
        async with controller.slot():
            entered.append(index)
            assert controller.snapshot().running == 1

    async with controller.slot():
        pending = []
        for index in range(5):
            pending.append(asyncio.create_task(worker(index)))
            await asyncio.sleep(0)
        pending[2].cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending[2]
        assert controller.snapshot().queued == 4
    await asyncio.gather(*(task for index, task in enumerate(pending) if index != 2))
    assert entered == [0, 1, 3, 4]
    assert controller.snapshot().running == controller.snapshot().queued == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
async def test_repeated_cancel_during_transferred_slot_withdrawal(monkeypatch, interruption):
    """Transfer + timeout/cancel + second cancel must not strand a running slot."""
    controller = module.SubagentExecutionCapacity(SubagentRuntimeConfig(max_running=1))
    transferred = asyncio.Event()

    async def interrupted_wait(future, timeout):
        await future
        # Deterministically model the transfer/deadline race while cleanup must
        # wait on the lock. The second cancellation arrives at that boundary.
        await controller._lock.acquire()
        transferred.set()
        if interruption == "timeout":
            raise TimeoutError
        raise asyncio.CancelledError

    monkeypatch.setattr(module.asyncio, "wait_for", interrupted_wait)
    await controller._acquire()
    pending = asyncio.create_task(controller._acquire())
    await asyncio.sleep(0)
    await controller._release()
    await transferred.wait()
    pending.cancel()
    await asyncio.sleep(0)
    controller._lock.release()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert controller.snapshot().running == 0
    assert controller.snapshot().queued == 0


@pytest.mark.asyncio
async def test_wrong_loop_cannot_bypass_busy_singleton(monkeypatch):
    monkeypatch.setattr(module, "_config", SubagentRuntimeConfig(max_running=1))
    monkeypatch.setattr(module, "_controller", None)
    monkeypatch.setattr(module, "_controller_loop", None)
    controller = module.get_subagent_execution_capacity()

    def other_loop():
        async def attempt():
            return module.get_subagent_execution_capacity()

        return asyncio.run(attempt())

    async with controller.slot():
        with pytest.raises(RuntimeError, match="cannot move event loops"):
            await asyncio.to_thread(other_loop)
        assert module.get_subagent_execution_capacity() is controller
    rebound = await asyncio.to_thread(other_loop)
    assert rebound is not controller
    assert rebound.snapshot().running == 0


@pytest.mark.asyncio
async def test_startup_snapshot_is_copied_and_identical_reconfigure_preserves_queue(monkeypatch):
    monkeypatch.setattr(module, "_config", SubagentRuntimeConfig())
    monkeypatch.setattr(module, "_controller", None)
    monkeypatch.setattr(module, "_controller_loop", None)
    config = SubagentRuntimeConfig(max_running=1)
    module.configure_subagent_execution_capacity(config)
    config.max_running = 5
    controller = module.get_subagent_execution_capacity()
    async with controller.slot():
        queued = asyncio.create_task(controller._acquire())
        await asyncio.sleep(0)
        module.configure_subagent_execution_capacity(SubagentRuntimeConfig(max_running=1))
        assert module.get_subagent_execution_capacity() is controller
        assert controller.snapshot().queued == 1
        with pytest.raises(RuntimeError, match="while executions are active"):
            module.configure_subagent_execution_capacity(config)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
    assert module.configured_subagent_max_running() == 1
    assert controller.snapshot().running == controller.snapshot().queued == 0
