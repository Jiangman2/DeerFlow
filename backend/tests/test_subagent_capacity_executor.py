"""Real isolated-loop admission and registry tests without a model or provider."""

import asyncio
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_subagent_callback_isolation import executor_module as executor_module

from deerflow.config.subagent_runtime_config import SubagentRuntimeConfig
from deerflow.subagents import capacity as capacity_module
from deerflow.subagents.capacity import SubagentExecutionCapacity
from deerflow.subagents.config import SubagentConfig


def eventually(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail("isolated execution did not reach the expected boundary")
        time.sleep(0.001)


def child(module, controller=None, *, timeout=5, user="alice"):
    return module.SubagentExecutor(
        SubagentConfig(name="test", description="test", model="basic", timeout_seconds=timeout),
        tools=[],
        thread_id="thread-" + user,
        user_id=user,
        execution_capacity=controller,
    )


async def wait_event(event):
    while not event.is_set():
        await asyncio.sleep(0.001)


def test_background_fifo_pending_bounded_and_duplicate_provider_ids(executor_module, monkeypatch):
    module = executor_module
    controller = SubagentExecutionCapacity(SubagentRuntimeConfig(max_running=1, max_queued=1))
    release = threading.Event()
    entered = []

    async def body(task, result):
        entered.append(task)
        await wait_event(release)
        result.try_set_terminal(module.SubagentStatus.COMPLETED, result=task)
        return result

    ids = []
    try:
        for name in ("alice", "bob", "charlie"):
            executor = child(module, controller, user=name)
            monkeypatch.setattr(executor, "_aexecute_in_trace", body)
            ids.append(executor.execute_async(name, task_id="same-provider-id"))
            if name == "alice":
                eventually(lambda: entered == ["alice"])
            elif name == "bob":
                eventually(lambda: controller.snapshot().queued == 1)
        first, queued, rejected = (module.get_background_task_result(value) for value in ids)
        eventually(lambda: rejected.status.is_terminal)
        assert len(set(ids)) == 3
        assert first.status.value == "running" and first.started_at is not None
        assert queued.status.value == "pending" and queued.started_at is None
        assert rejected.status.value == "failed" and rejected.admission_failure
        assert rejected.started_at is None
        assert all(result.external_task_id == "same-provider-id" for result in (first, queued, rejected))
        release.set()
        eventually(lambda: queued.status.is_terminal and not module._background_futures)
        assert entered == ["alice", "bob"]
        assert first.result == "alice" and queued.result == "bob"
        assert controller.snapshot().running == controller.snapshot().queued == 0
    finally:
        release.set()
        for value in ids:
            module.request_cancel_background_task(value)
        eventually(lambda: controller.snapshot().running == controller.snapshot().queued == 0)
        for value in ids:
            module.cleanup_background_task(value)


def test_queued_cancel_never_starts_setup_or_cancels_other_owner(executor_module, monkeypatch):
    module = executor_module
    controller = SubagentExecutionCapacity(SubagentRuntimeConfig(max_running=1))
    release = threading.Event()
    entered = []

    async def body(task, result):
        entered.append(task)
        await wait_event(release)
        result.try_set_terminal(module.SubagentStatus.COMPLETED, result=task)
        return result

    executor = child(module, controller)
    monkeypatch.setattr(executor, "_aexecute_in_trace", body)
    first = executor.execute_async("first", "same")
    eventually(lambda: entered == ["first"])
    queued = executor.execute_async("queued", "same")
    try:
        eventually(lambda: controller.snapshot().queued == 1)
        module.request_cancel_background_task(queued)
        result = module.get_background_task_result(queued)
        eventually(lambda: result.status.is_terminal and controller.snapshot().queued == 0)
        assert result.status.value == "cancelled" and result.started_at is None
        assert not module.get_background_task_result(first).cancel_event.is_set()
        assert entered == ["first"]
    finally:
        release.set()
        eventually(lambda: not module._background_futures and controller.snapshot().running == 0)
        module.cleanup_background_task(first)
        module.cleanup_background_task(queued)


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_slot_is_held_until_admitted_cleanup_finishes(executor_module, monkeypatch, mode):
    module = executor_module
    controller = SubagentExecutionCapacity(SubagentRuntimeConfig(max_running=1))
    entered = threading.Event()
    cleanup = threading.Event()
    release_cleanup = threading.Event()

    async def body(_task, result):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await wait_event(release_cleanup)

    executor = child(module, controller, timeout=0.05 if mode == "timeout" else 5)
    monkeypatch.setattr(executor, "_aexecute_in_trace", body)
    execution = executor.execute_async("task")
    try:
        assert entered.wait(5)
        if mode == "cancel":
            module.request_cancel_background_task(execution)
        assert cleanup.wait(5)
        result = module.get_background_task_result(execution)
        assert controller.snapshot().running == 1
        assert not result.status.is_terminal
        # Repeated parent cancellation does not tear down in-flight cleanup.
        if mode == "cancel":
            module.request_cancel_background_task(execution)
        release_cleanup.set()
        eventually(lambda: result.status.is_terminal and controller.snapshot().running == 0)
        assert result.status.value == ("timed_out" if mode == "timeout" else "cancelled")
        assert not result.admission_failure
    finally:
        release_cleanup.set()
        eventually(lambda: controller.snapshot().running == 0)
        module.cleanup_background_task(execution)


def test_cancel_before_first_coroutine_instruction_is_terminal(executor_module, monkeypatch):
    module = executor_module
    future = Future()
    factories = []

    def submit(context, factory):
        factories.append((context, factory))
        return future

    monkeypatch.setattr(module, "_submit_to_isolated_loop_in_context", submit)
    executor = child(module)
    execution = executor.execute_async("task")
    module.request_cancel_background_task(execution)
    result = module.get_background_task_result(execution)
    assert result.status.value == "cancelled"
    assert execution not in module._background_futures
    context, factory = factories[0]
    assert context.run(lambda: asyncio.run(factory())) is result
    assert result.started_at is None
    module.cleanup_background_task(execution)
    assert execution not in module._background_tasks


@pytest.mark.parametrize("error", [RuntimeError("no loop"), asyncio.CancelledError()])
def test_submission_failure_drops_registry_entry(executor_module, monkeypatch, error):
    def reject(*_args):
        raise error

    monkeypatch.setattr(executor_module, "_submit_to_isolated_loop_in_context", reject)
    with pytest.raises(type(error)):
        child(executor_module).execute_async("task")
    assert not executor_module._background_tasks
    assert not executor_module._background_futures


def test_sync_and_background_share_process_singleton(executor_module, monkeypatch):
    module = executor_module
    monkeypatch.setattr(capacity_module, "_config", SubagentRuntimeConfig(max_running=1))
    monkeypatch.setattr(capacity_module, "_controller", None)
    monkeypatch.setattr(capacity_module, "_controller_loop", None)
    release = threading.Event()
    entered = []
    loops = []

    async def body(task, result):
        entered.append(task)
        loops.append(asyncio.get_running_loop())
        await wait_event(release)
        result.try_set_terminal(module.SubagentStatus.COMPLETED, result=task)
        return result

    executor = child(module)
    monkeypatch.setattr(executor, "_aexecute_in_trace", body)
    execution = executor.execute_async("background")
    eventually(lambda: entered == ["background"])
    with ThreadPoolExecutor(max_workers=1) as callers:
        synchronous = callers.submit(executor.execute, "sync")
        try:
            eventually(lambda: capacity_module._controller.snapshot().queued == 1)
            assert entered == ["background"]
        finally:
            release.set()
        assert synchronous.result(timeout=5).result == "sync"
    eventually(lambda: not module._background_futures)
    assert entered == ["background", "sync"]
    assert loops[0] is loops[1] is module._get_isolated_subagent_loop()
    assert capacity_module._controller.snapshot().running == 0
    module.cleanup_background_task(execution)


@pytest.mark.asyncio
async def test_explicit_capacity_and_queue_timeout_do_not_load_models(executor_module, monkeypatch):
    controller = SubagentExecutionCapacity(SubagentRuntimeConfig(max_running=1, queue_timeout_seconds=1))
    executor = child(executor_module, controller)

    async def forbidden(*_args):
        pytest.fail("admission timeout must precede setup")

    monkeypatch.setattr(executor, "_aexecute_in_trace", forbidden)
    async with controller.slot():
        result = await executor._aexecute("task")
    assert result.status.value == "failed" and result.admission_failure
    assert result.started_at is None
    assert controller.snapshot().queued == controller.snapshot().running == 0


def test_embedded_client_installs_startup_snapshot_only(monkeypatch):
    import deerflow.client as client_module

    snapshots = []
    config = SimpleNamespace(subagent_runtime=SubagentRuntimeConfig(max_running=2))
    monkeypatch.setattr(client_module, "get_app_config", lambda: config)
    monkeypatch.setattr(client_module, "configure_subagent_execution_capacity", snapshots.append)
    client = client_module.DeerFlowClient()
    assert snapshots == [config.subagent_runtime]
    assert client._subagent_execution_capacity == 2
    config.subagent_runtime = SubagentRuntimeConfig(max_running=64)
    client.reset_agent()
    assert client._subagent_execution_capacity == 2
    assert len(snapshots) == 1


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_cancel_preserves_usage_received_before_next_state_chunk(executor_module, monkeypatch, mode):
    from langchain_core.messages import AIMessage

    module = executor_module
    controller = SubagentExecutionCapacity(SubagentRuntimeConfig(max_running=1))
    executor = child(module, controller, timeout=0.1 if mode == "timeout" else 5)
    usage_received = threading.Event()

    async def initial(_task):
        return {"messages": []}, [], None

    async def stream(_state, *, config, **_kwargs):
        message = AIMessage(content="partial", usage_metadata={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12})
        response = SimpleNamespace(generations=[[SimpleNamespace(message=message)]])
        config["callbacks"][0].on_llm_end(response, run_id="real-child-usage")
        usage_received.set()
        await asyncio.Event().wait()
        yield {"messages": [message]}

    monkeypatch.setattr(executor, "_get_resolved_app_config", lambda: SimpleNamespace(verification=None))
    monkeypatch.setattr(executor, "_build_initial_state", initial)
    monkeypatch.setattr(executor, "_create_agent", lambda *_args, **_kwargs: SimpleNamespace(astream=stream))
    monkeypatch.setattr(module, "build_tracing_callbacks", lambda: [])
    monkeypatch.setattr(module, "inject_langfuse_metadata", lambda *_args, **_kwargs: None)
    execution = executor.execute_async("task")
    assert usage_received.wait(5)
    if mode == "cancel":
        module.request_cancel_background_task(execution)
    result = module.get_background_task_result(execution)
    eventually(lambda: result.status.is_terminal and controller.snapshot().running == 0)
    assert result.status.value == ("cancelled" if mode == "cancel" else "timed_out")
    assert len(result.token_usage_records) == 1
    assert result.token_usage_records[0]["source_run_id"] == "real-child-usage"
    assert result.token_usage_records[0]["total_tokens"] == 12
    module.cleanup_background_task(execution)
