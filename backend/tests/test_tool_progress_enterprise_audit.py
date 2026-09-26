"""Real child-loop audit, retry hints, and enterprise Bash recovery boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage

from deerflow.agents.middlewares.audit_context import TOOL_PROGRESS_RECORDER_CONTEXT_KEY
from deerflow.agents.middlewares.tool_progress_middleware import ToolProgressMiddleware
from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.middleware_recorder import ParentLoopMiddlewareRecorder


def receipt(*, source="tool_return", stop=False, success=False):
    return ToolMessage(
        content="PRIVATE_RESULT",
        tool_call_id="tc",
        additional_kwargs={
            TOOL_META_KEY: {
                "status": "success" if success else "error",
                "error_type": None if success else "config",
                "recoverable_by_model": not stop,
                "recommended_next_action": "stop" if stop else "try_alternative",
                "source": source,
            }
        },
    )


def request(runtime, name="web_search"):
    return SimpleNamespace(runtime=runtime, tool_call={"name": name, "id": "tc", "args": {"query": "PRIVATE_QUERY"}})


@pytest.mark.parametrize("source,expected", [("tool_return", "warn"), ("content_analysis", "warn"), ("exception", "block")])
def test_bash_subprocess_failure_is_repairable_but_infrastructure_stop_is_not(source, expected):
    journal = SimpleNamespace(record_middleware=MagicMock())
    runtime = SimpleNamespace(context={"__run_journal": journal, "thread_id": "t", "run_id": "r"})
    guard = ToolProgressMiddleware(stagnation_threshold=1)
    result = receipt(source=source, stop=True)
    assert guard.wrap_tool_call(request(runtime, "bash"), lambda _: result) is result
    event = journal.record_middleware.call_args.kwargs
    assert event["action"] == expected
    assert event["changes"]["recoverable_by_model"] is (expected == "warn")
    assert "PRIVATE" not in repr(event)


@pytest.mark.asyncio
async def test_child_warn_block_reset_recovery_on_parent_journal_loop():
    store = MemoryRunEventStore()
    journal = RunJournal("r", "t", store, flush_threshold=100)
    owner_loop = asyncio.get_running_loop()
    record = journal.record_middleware

    def record_on_owner(**kwargs):
        assert asyncio.get_running_loop() is owner_loop
        record(**kwargs)

    journal.record_middleware = record_on_owner
    bridge = ParentLoopMiddlewareRecorder(journal, owner_loop)
    runtime = SimpleNamespace(context={"thread_id": "t", "run_id": "r", "agent_id": "worker", TOOL_PROGRESS_RECORDER_CONTEXT_KEY: bridge})
    guard = ToolProgressMiddleware(stagnation_threshold=1)

    async def exercise():
        async def error(_):
            return receipt()

        async def stop(_):
            return receipt(stop=True)

        async def good(_):
            return receipt(success=True)

        await guard.awrap_tool_call(request(runtime), error)
        await guard.awrap_tool_call(request(runtime), stop)
        await guard.abefore_agent({}, runtime)
        await guard.awrap_tool_call(request(runtime), error)
        await guard.awrap_tool_call(request(runtime), good)

    await asyncio.to_thread(lambda: asyncio.run(exercise()))
    await bridge.aclose()
    await journal.flush()
    events = await store.list_events("t", "r")
    assert [e["content"]["action"] for e in events] == ["warn", "block", "reset", "warn", "recover"]
    reset = events[2]["content"]
    assert reset["hook"] == "abefore_agent"
    assert reset["changes"]["consecutive_problems"] == 0
    assert reset["changes"]["threshold"] is None
    assert all(e["content"]["changes"]["is_subagent"] and e["content"]["changes"]["agent_id"] == "worker" for e in events)
    assert "PRIVATE" not in repr(events)
    await journal.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_failed_model_attempt_restores_progress_hint_once(asynchronous):
    guard = ToolProgressMiddleware(stagnation_threshold=1)
    runtime = SimpleNamespace(context={"thread_id": "t", "run_id": "r"})
    guard.wrap_tool_call(request(runtime), lambda _: receipt())
    captured = []
    model_request = SimpleNamespace(runtime=runtime, messages=[], override=lambda **kwargs: SimpleNamespace(**kwargs))

    def fail(req):
        captured.append(req.messages)
        raise RuntimeError("transient model failure")

    def good(req):
        captured.append(req.messages)
        return "done"

    async def run():
        async def afail(req):
            return fail(req)

        async def agood(req):
            return good(req)

        with pytest.raises(RuntimeError, match="transient"):
            await guard.awrap_model_call(model_request, afail)
        assert await guard.awrap_model_call(model_request, agood) == "done"

    if asynchronous:
        asyncio.run(run())
    else:
        with pytest.raises(RuntimeError, match="transient"):
            guard.wrap_model_call(model_request, fail)
        assert guard.wrap_model_call(model_request, good) == "done"
    assert len(captured) == 2
    assert len(captured[0]) == len(captured[1]) == 1
    assert captured[0][0].content == captured[1][0].content
    assert not guard._drain_pending(runtime)
