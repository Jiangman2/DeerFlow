"""Pinned v2.1 callback-loop boundary, without model/provider calls."""

import asyncio
import importlib
import inspect
import sys
import time
from contextvars import ContextVar, copy_context
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.callbacks.manager import AsyncCallbackManager
from langchain_core.runnables.config import var_child_runnable_config

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal


@pytest.fixture
def executor_module(monkeypatch):
    # conftest installs a cycle-breaking mock at the public module name. Load
    # the real file under an isolated name without replacing other tests' mocks.
    name = "_v21_callback_executor"
    path = Path(__file__).parents[1] / "packages/harness/deerflow/subagents/executor.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    from deerflow.trace_context import request_trace_context

    try:
        # Stand in for a scoped parent request, not an unbounded caller whose
        # minted constructor trace would contaminate subsequent test modules.
        with request_trace_context():
            yield module
    finally:
        module._shutdown_isolated_subagent_loop()
        module.atexit.unregister(module._shutdown_isolated_subagent_loop)


def test_journal_declares_its_loop_affinity():
    journal = RunJournal("run", "thread", MemoryRunEventStore())
    assert journal.deerflow_loop_bound is True


@pytest.mark.parametrize("shape", ["list", "tuple", "manager", "single", "empty", "unmarked"])
def test_context_copy_filters_only_loop_bound_callbacks(executor_module, shape):
    loop_bound = SimpleNamespace(deerflow_loop_bound=True)
    stream_handler = object()
    values = {
        "list": [loop_bound, stream_handler],
        "tuple": (loop_bound, stream_handler),
        "manager": AsyncCallbackManager(
            handlers=[loop_bound, stream_handler],
            inheritable_handlers=[stream_handler, loop_bound],
            tags=["trace-tag"],
            metadata={"trace": "parent"},
        ),
        "single": loop_bound,
        "empty": [],
        "unmarked": stream_handler,
    }
    callbacks = values[shape]
    parent = {"callbacks": callbacks, "configurable": {"thread_id": "parent", "checkpoint_ns": "task:child"}, "metadata": {"trace": "parent"}}
    identity = ContextVar("test-enterprise-identity")
    identity_token = identity.set({"user_id": "caller-b", "published_owner": "creator-a", "knowledge_scope": ["allowed-kb"]})
    config_token = var_child_runnable_config.set(parent)
    try:
        child_context = executor_module._copy_isolated_subagent_context()
        child = child_context.get(var_child_runnable_config)
        assert var_child_runnable_config.get() is parent
        assert parent["callbacks"] is callbacks
        assert child is not parent
        assert child["configurable"] == parent["configurable"]
        assert child["metadata"] == parent["metadata"]
        assert child_context.get(identity) == identity.get()
        if shape == "manager":
            child_manager = child["callbacks"]
            assert child_manager is not callbacks
            assert child_manager.handlers == [stream_handler]
            assert child_manager.inheritable_handlers == [stream_handler]
            assert child_manager.tags == ["trace-tag"]
            assert child_manager.metadata == {"trace": "parent"}
            assert callbacks.handlers == [loop_bound, stream_handler]
            assert callbacks.inheritable_handlers == [stream_handler, loop_bound]
        elif shape in {"list", "tuple"}:
            assert child["callbacks"] == [stream_handler]
            assert list(callbacks) == [loop_bound, stream_handler]
        elif shape == "unmarked":
            assert child["callbacks"] is stream_handler
        else:
            assert "callbacks" not in child
    finally:
        var_child_runnable_config.reset(config_token)
        identity.reset(identity_token)


@pytest.mark.parametrize("config", [None, {"metadata": {"trace": "alone"}}])
def test_callback_free_context_is_preserved(executor_module, config):
    token = var_child_runnable_config.set(config)
    try:
        assert executor_module._copy_isolated_subagent_context().get(var_child_runnable_config) is config
    finally:
        var_child_runnable_config.reset(token)


@pytest.mark.parametrize("background", [False, True])
def test_real_child_loop_keeps_streaming_and_enterprise_scope_without_parent_journal(executor_module, monkeypatch, background):
    from deerflow.runtime.user_context import get_current_user, reset_current_user, set_current_user
    from deerflow.runtime.weknora_context import get_current_weknora_access_token, reset_current_weknora_access_token, set_current_weknora_access_token
    from deerflow.subagents.config import SubagentConfig
    from deerflow.trace_context import get_current_trace_id, request_trace_context

    journal = RunJournal("run", "parent", MemoryRunEventStore())
    stream_handler = object()
    observed = {}
    executor = executor_module.SubagentExecutor(config=SubagentConfig(name="test-child", description="test", system_prompt="test", timeout_seconds=5), tools=[])

    async def child(_task, result_holder=None):
        observed["callbacks"] = var_child_runnable_config.get()["callbacks"]
        observed["configurable"] = var_child_runnable_config.get()["configurable"]
        observed["user"] = get_current_user()
        observed["token"] = get_current_weknora_access_token()
        observed["trace"] = get_current_trace_id()
        observed["loop"] = asyncio.get_running_loop()
        result = result_holder or executor_module.SubagentResult(task_id="child", trace_id="parent", status=executor_module.SubagentStatus.RUNNING)
        result.try_set_terminal(executor_module.SubagentStatus.COMPLETED, result="done")
        return result

    monkeypatch.setattr(executor, "_aexecute", child)
    user = SimpleNamespace(id="caller-b")
    user_token = set_current_user(user)
    kb_token = set_current_weknora_access_token("synthetic-scoped-test-token")
    config_token = var_child_runnable_config.set({"callbacks": [journal, stream_handler], "configurable": {"thread_id": "parent", "checkpoint_ns": "child"}})
    task_id = None
    try:
        with request_trace_context("parent-trace"):
            if background:
                task_id = executor.execute_async("task")
                result = executor_module.get_background_task_result(task_id)
                deadline = time.monotonic() + 3
                while not result.status.is_terminal and time.monotonic() < deadline:
                    time.sleep(0.001)
                assert result.status.is_terminal
            else:
                result = executor._execute_in_isolated_loop("task")
        assert result.result == "done"
        assert observed["callbacks"] == [stream_handler]
        assert observed["configurable"] == {"thread_id": "parent", "checkpoint_ns": "child"}
        assert observed["user"] is user
        assert observed["token"] == "synthetic-scoped-test-token"
        assert observed["trace"] == "parent-trace"
        assert observed["loop"] is executor_module._get_isolated_subagent_loop()
        assert var_child_runnable_config.get()["callbacks"] == [journal, stream_handler]
    finally:
        if task_id:
            executor_module.cleanup_background_task(task_id)
        var_child_runnable_config.reset(config_token)
        reset_current_weknora_access_token(kb_token)
        reset_current_user(user_token)
        executor_module._shutdown_isolated_subagent_loop()


def test_context_copy_failure_cannot_strand_a_pending_registry_entry(executor_module, monkeypatch):
    from deerflow.subagents.config import SubagentConfig

    executor = executor_module.SubagentExecutor(config=SubagentConfig(name="test", description="test", system_prompt="test"), tools=[])
    before = dict(executor_module._background_tasks)

    def reject_copy():
        raise RuntimeError("copy rejected")

    monkeypatch.setattr(executor_module, "_copy_isolated_subagent_context", reject_copy)
    with pytest.raises(RuntimeError, match="copy rejected"):
        executor.execute_async("task")
    assert executor_module._background_tasks == before


def test_loop_startup_failure_does_not_create_a_coroutine(executor_module, monkeypatch):
    calls = []

    def fail_loop():
        raise RuntimeError("loop rejected")

    async def coroutine():
        return None

    def factory():
        calls.append("created")
        return coroutine()

    monkeypatch.setattr(executor_module, "_get_isolated_subagent_loop", fail_loop)
    with pytest.raises(RuntimeError, match="loop rejected"):
        executor_module._submit_to_isolated_loop_in_context(copy_context(), factory)
    assert calls == []


@pytest.mark.parametrize("error", [RuntimeError("schedule rejected"), asyncio.CancelledError()])
def test_scheduling_rejection_closes_unscheduled_coroutine(executor_module, monkeypatch, error):
    created = []

    async def coroutine():
        raise AssertionError("must never start")

    def factory():
        result = coroutine()
        created.append(result)
        return result

    def reject_schedule(_coroutine, _loop):
        raise error

    monkeypatch.setattr(executor_module, "_get_isolated_subagent_loop", lambda: object())
    monkeypatch.setattr(executor_module.asyncio, "run_coroutine_threadsafe", reject_schedule)
    with pytest.raises(type(error)):
        executor_module._submit_to_isolated_loop_in_context(copy_context(), factory)
    assert len(created) == 1
    assert inspect.getcoroutinestate(created[0]) == inspect.CORO_CLOSED
