"""Child-loop recorder lifecycle and real durable loop-guard events."""

import asyncio
import importlib
import threading
from enum import Enum
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from deerflow.agents.middlewares.audit_context import LOOP_DETECTION_RECORDER_CONTEXT_KEY, resolve_audit_recorder
from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.middleware_recorder import ParentLoopMiddlewareRecorder
from deerflow.subagents.config import SubagentConfig


def state(index=0):
    return {"messages": [AIMessage(content="", tool_calls=[{"name": "bash", "args": {"command": "SECRET_COMMAND_" + str(index)}, "id": "tc"}])]}


@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("frequency", [False, True])
def test_loop_events_persist_on_owner_loop_without_tool_payload(child, frequency):
    async def run():
        store = MemoryRunEventStore()
        journal = RunJournal("r", "t", store, flush_threshold=100)
        loop = asyncio.get_running_loop()
        original = journal.record_middleware

        def record(**kwargs):
            assert asyncio.get_running_loop() is loop
            original(**kwargs)

        journal.record_middleware = record
        recorder = ParentLoopMiddlewareRecorder(journal, loop)
        # A caller's is_subagent/agent_id alone is not trusted attribution.
        context = {"thread_id": "t", "run_id": "r", "is_subagent": True, "agent_id": "forged", "__run_journal": journal}
        if child:
            context.pop("__run_journal")
            context.update({LOOP_DETECTION_RECORDER_CONTEXT_KEY: recorder, "agent_id": "worker"})
        runtime = SimpleNamespace(context=context)
        guard = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4, tool_freq_warn=2 if frequency else 100, tool_freq_hard_limit=4 if frequency else 200)

        async def exercise():
            update = None
            for index in range(4):
                update = await guard.aafter_model(state(index if frequency else 0), runtime)
            assert update["messages"][0].tool_calls == []
            assert runtime.context["stop_reason"] == "loop_capped"

        if child:
            await asyncio.to_thread(lambda: asyncio.run(exercise()))
        else:
            await exercise()
        await recorder.aclose()
        await journal.flush()
        events = await store.list_events("t", "r")
        content = [e["content"] for e in events if e["event_type"] == "middleware:loop_detection"]
        assert [e["action"] for e in content] == ["warn", "hard_stop"]
        assert "SECRET_COMMAND" not in repr(events)
        for event in content:
            assert event["changes"]["is_subagent"] is child
            assert event["changes"]["agent_id"] == ("worker" if child else None)
            assert event["changes"]["detection_layer"] == ("tool_frequency" if frequency else "identical_call_set")
        await journal.close()

    asyncio.run(run())


def test_enterprise_unchanged_failure_guard_keeps_stop_and_redacts_receipts():
    journal = SimpleNamespace(record_middleware=MagicMock())
    runtime = SimpleNamespace(context={"thread_id": "t", "run_id": "r", "__run_journal": journal})
    guard = LoopDetectionMiddleware()
    for index in range(5):
        guard._record_tool_result_progress(ToolMessage(content="Error: SECRET_DIAGNOSTIC", status="error", tool_call_id=str(index), name="bash"), runtime)
    result = guard.after_model(state(), runtime)
    assert result["messages"][0].tool_calls == []
    assert [call.kwargs["action"] for call in journal.record_middleware.call_args_list] == ["warn", "hard_stop"]
    assert all(call.kwargs["changes"]["detection_layer"] == "unchanged_failure" for call in journal.record_middleware.call_args_list)
    assert "SECRET_DIAGNOSTIC" not in repr(journal.record_middleware.call_args_list)


def test_recorder_failure_cannot_disable_hard_stop():
    runtime = SimpleNamespace(context={"__run_journal": SimpleNamespace(record_middleware=MagicMock(side_effect=RuntimeError("SECRET")))})
    guard = LoopDetectionMiddleware(warn_threshold=1, hard_limit=2)
    assert guard.after_model(state(), runtime) is None
    assert guard.after_model(state(), runtime)["messages"][0].tool_calls == []


def test_proxy_fences_late_events_and_snapshots_accepted_payload():
    async def run():
        journal = SimpleNamespace(record_middleware=MagicMock())
        proxy = ParentLoopMiddlewareRecorder(journal, asyncio.get_running_loop())
        changes = {"tool_names": ["before"]}
        worker = threading.Thread(target=lambda: proxy.record_middleware(tag="loop_detection", name="test", hook="after_model", action="warn", changes=changes))
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        changes["tool_names"][:] = ["after"]
        await proxy.aclose()
        assert proxy.is_closed
        proxy.record_middleware(tag="loop_detection", changes={})
        assert journal.record_middleware.call_count == 1
        assert journal.record_middleware.call_args.kwargs["changes"] == {"tool_names": ["before"]}
        with pytest.raises(ValueError, match="Unsupported"):
            proxy.record_middleware(tag="unrelated")

    asyncio.run(run())


def test_proxy_rejects_different_journal_owner_and_wrong_close_loop():
    other = asyncio.new_event_loop()

    async def run():
        with pytest.raises(ValueError, match="owner loop"):
            ParentLoopMiddlewareRecorder(SimpleNamespace(_owner_loop=other), asyncio.get_running_loop())
        proxy = ParentLoopMiddlewareRecorder(SimpleNamespace(), other)
        with pytest.raises(RuntimeError, match="owner loop"):
            await proxy.aclose()

    try:
        asyncio.run(run())
    finally:
        other.close()


def test_resolver_ignores_untrusted_subagent_flags():
    assert resolve_audit_recorder({"is_subagent": True, "agent_id": "fake"}, recorder_key=LOOP_DETECTION_RECORDER_CONTEXT_KEY) == (None, False, None)


@pytest.mark.parametrize("failure", [None, "constructor", "submission", "writer", "poll", "cancel"])
def test_task_boundary_closes_recorder_on_all_exits(monkeypatch, failure):
    module = importlib.import_module("deerflow.tools.builtins.task_tool")
    captured = []
    cancelled = []
    cleanup = []

    class Status(Enum):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"
        TIMED_OUT = "timed_out"

    monkeypatch.setattr(module, "SubagentStatus", Status)

    class Executor:
        def __init__(self, **kwargs):
            assert kwargs["tool_promotion_recorder"] is kwargs["loop_detection_recorder"]
            assert kwargs["tool_progress_recorder"] is kwargs["loop_detection_recorder"]
            captured.append(kwargs["loop_detection_recorder"])
            if failure == "constructor":
                raise RuntimeError("constructor")

        def execute_async(self, prompt, task_id=None):
            if failure == "submission":
                raise RuntimeError("submission")
            captured[0].record_middleware(tag="loop_detection", name="guard", hook="after_model", action="warn", changes={"count": 2})
            return task_id

    monkeypatch.setattr(module, "SubagentExecutor", Executor)
    monkeypatch.setattr(module, "get_subagent_config", lambda name: SubagentConfig(name="general-purpose", description="test", model="test"))
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(module, "cleanup_background_task", lambda *args: None)
    monkeypatch.setattr(module, "request_cancel_background_task", cancelled.append)
    monkeypatch.setattr(module, "_schedule_deferred_subagent_cleanup", lambda *args: cleanup.append(args))
    result = SimpleNamespace(status=module.SubagentStatus.COMPLETED, result="done", ai_messages=[], token_usage_records=[], usage_reported=False, stop_reason=None)

    def poll(task):
        if failure == "poll":
            raise RuntimeError("poll")
        return result

    def writer(event):
        if failure == "writer":
            raise RuntimeError("writer")
        if failure == "cancel":
            raise asyncio.CancelledError("cancel")

    monkeypatch.setattr(module, "get_background_task_result", poll)
    monkeypatch.setattr(module, "get_stream_writer", lambda: writer)

    async def run():
        journal = SimpleNamespace(record_middleware=MagicMock())
        runtime = SimpleNamespace(context={"thread_id": "t", "__run_journal": journal}, state={}, config={"metadata": {"model_name": "test"}})
        call = module.task_tool.coroutine(runtime=runtime, description="test", prompt="test", subagent_type="general-purpose", tool_call_id="tc")
        if failure:
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else RuntimeError, match=failure):
                await call
        else:
            await call
        assert captured[0].is_closed
        assert journal.record_middleware.call_count == (0 if failure in ("constructor", "submission") else 1)
        if failure in ("writer", "poll", "cancel"):
            assert cancelled == ["tc"]
        if failure in ("writer", "poll"):
            assert len(cleanup) == 1

    asyncio.run(run())
