"""Unit tests for content-free run-stage timing diagnostics."""

from __future__ import annotations

import asyncio
import inspect
import logging
from types import SimpleNamespace

import pytest

import deerflow.runtime.runs.worker as runs_worker
from deerflow.llm_timing_context import reset_current_llm_timing_context, set_current_llm_timing_context
from deerflow.run_timing import (
    atime_middleware_after_agent,
    begin_checkpoint_timing,
    checkpoint_db_session_label,
    consume_checkpoint_timing,
    consume_tool_timing,
    log_run_timing,
    record_checkpoint_timing,
    record_sandbox_acquire_timing,
    record_tool_timing,
    time_middleware_after_agent,
)
from deerflow.runtime.runs.worker import (
    _install_llm_timing_metadata,
    _iter_graph_task_events,
    _pending_async_task_summary,
    _positive_timing_env,
    _time_run_stage,
)


def test_run_timing_log_includes_current_run_and_safe_operational_fields(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DEERFLOW_RUN_TIMING_LOGS", "true")
    caplog.set_level("INFO", logger="test.run_timing")
    logger = logging.getLogger("test.run_timing")
    token = set_current_llm_timing_context(thread_id="thread-123", run_id="run-456")
    try:
        log_run_timing(logger, "tool_end", elapsed_ms=17, tool_name="bash", outcome="completed", unsafe_value="prompt text")
    finally:
        reset_current_llm_timing_context(token)

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "RUN_TIMING event=tool_end" in rendered
    assert "deerflow_thread_id=thread-123" in rendered
    assert "deerflow_run_id=run-456" in rendered
    assert "elapsed_ms=17" in rendered
    assert "tool_name=bash" in rendered
    assert "outcome=completed" in rendered
    assert "unsafe_value=-" in rendered
    assert "prompt text" not in rendered


def test_run_timing_log_is_disabled_by_default(monkeypatch, caplog) -> None:
    monkeypatch.delenv("DEERFLOW_RUN_TIMING_LOGS", raising=False)
    caplog.set_level("INFO", logger="test.run_timing")

    log_run_timing(logging.getLogger("test.run_timing"), "run_start")

    assert not caplog.records


def test_after_agent_timers_preserve_outcomes_without_accessing_hook_state(monkeypatch) -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    def capture(_logger: object, event: str, **fields: object) -> None:
        emitted.append((event, fields))

    monkeypatch.setattr("deerflow.run_timing.log_run_timing", capture)
    logger = logging.getLogger("test.run_timing")

    with time_middleware_after_agent(logger, middleware="sandbox"):
        pass

    async def cancelled() -> None:
        with pytest.raises(asyncio.CancelledError):
            async with atime_middleware_after_agent(logger, middleware="todo"):
                raise asyncio.CancelledError()

    asyncio.run(cancelled())

    assert emitted[0] == ("middleware_after_agent_start", {"middleware": "sandbox"})
    assert emitted[1][0] == "middleware_after_agent_end"
    assert emitted[1][1]["middleware"] == "sandbox"
    assert emitted[1][1]["outcome"] == "completed"
    assert emitted[2] == ("middleware_after_agent_start", {"middleware": "todo"})
    assert emitted[3][0] == "middleware_after_agent_end"
    assert emitted[3][1]["middleware"] == "todo"
    assert emitted[3][1]["outcome"] == "cancelled"
    assert emitted[3][1]["error_type"] == "CancelledError"


def test_worker_installs_reserved_llm_timing_metadata_without_losing_trace_metadata() -> None:
    config = {"metadata": {"deerflow_trace_id": "trace-123", "caller_note": "kept"}}

    _install_llm_timing_metadata(config, thread_id="thread-123", run_id="run-456")

    assert config["metadata"] == {
        "deerflow_trace_id": "trace-123",
        "caller_note": "kept",
        "deerflow_timing_thread_id": "thread-123",
        "deerflow_timing_run_id": "run-456",
    }


def test_worker_stage_timer_records_completed_and_failed_stages(monkeypatch) -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    def capture(_logger: object, event: str, **fields: object) -> None:
        emitted.append((event, fields))

    monkeypatch.setattr("deerflow.runtime.runs.worker.log_run_timing", capture)

    async def complete() -> str:
        return "done"

    async def fail() -> None:
        raise RuntimeError("expected test failure")

    assert asyncio.run(_time_run_stage("journal_flush", complete)) == "done"
    with pytest.raises(RuntimeError, match="expected test failure"):
        asyncio.run(_time_run_stage("title_sync", fail))

    assert emitted[0] == ("journal_flush_start", {})
    assert emitted[1][0] == "journal_flush_end"
    assert emitted[1][1]["outcome"] == "completed"
    assert emitted[2] == ("title_sync_start", {})
    assert emitted[3][0] == "title_sync_end"
    assert emitted[3][1]["outcome"] == "error"
    assert emitted[3][1]["error_type"] == "RuntimeError"


def test_graph_task_events_retain_only_lifecycle_metadata() -> None:
    started = _iter_graph_task_events(
        {
            "id": "task-1",
            "name": "after_model",
            "input": {"messages": ["must never be logged"]},
        }
    )
    completed = _iter_graph_task_events(
        {
            "id": "task-1",
            "name": "after_model",
            "result": {"messages": ["must never be retained"]},
        }
    )
    failed = _iter_graph_task_events(
        {
            "id": "task-2",
            "name": "tools",
            "error": "tool payload is intentionally ignored",
        }
    )

    assert [(event.task_id, event.node_name, event.terminal, event.outcome) for event in started] == [("task-1", "after_model", False, "completed")]
    assert [(event.task_id, event.node_name, event.terminal, event.outcome) for event in completed] == [("task-1", "after_model", True, "completed")]
    assert [(event.task_id, event.node_name, event.terminal, event.outcome) for event in failed] == [("task-2", "tools", True, "error")]


def test_worker_passes_stream_kind_to_the_private_graph_stream_consumer() -> None:
    source = inspect.getsource(runs_worker)

    assert "async def _consume_locked_stream(\n            input_payload: Any,\n            stream_config: RunnableConfig,\n            *,\n            stream_kind: str," in source
    assert "stream_config,\n                            stream_kind=stream_kind," in source


def test_stream_idle_timing_thresholds_are_positive_and_overrideable(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_RUN_TIMING_IDLE_WAIT_SECONDS", "12.5")
    monkeypatch.setenv("DEERFLOW_RUN_TIMING_SLOW_BRIDGE_PUBLISH_MS", "0")

    assert _positive_timing_env("DEERFLOW_RUN_TIMING_IDLE_WAIT_SECONDS", 30.0) == 12.5
    assert _positive_timing_env("DEERFLOW_RUN_TIMING_SLOW_BRIDGE_PUBLISH_MS", 1000.0) == 1000.0
    monkeypatch.setenv("DEERFLOW_RUN_TIMING_IDLE_WAIT_SECONDS", "inf")
    assert _positive_timing_env("DEERFLOW_RUN_TIMING_IDLE_WAIT_SECONDS", 30.0) == 30.0


def test_pending_async_task_summary_exposes_only_bounded_coroutine_labels_and_counts() -> None:
    async def inspect_snapshot() -> tuple[int, str, str]:
        blocker = asyncio.Event()

        async def wait_for_blocker() -> None:
            await blocker.wait()

        tasks = [asyncio.create_task(wait_for_blocker()) for _ in range(3)]
        try:
            await asyncio.sleep(0)
            return _pending_async_task_summary()
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with pytest.raises(asyncio.CancelledError):
                    await task

    pending_count, pending_task_kinds, pending_task_top_counts = asyncio.run(inspect_snapshot())

    assert pending_count >= 1
    assert "wait_for_blocker" in pending_task_kinds
    assert "Event" not in pending_task_kinds
    assert len(pending_task_kinds) <= 128
    assert "3:wait_for_blocker" in pending_task_top_counts
    assert len(pending_task_top_counts) <= 128


def test_worker_installs_idle_wait_and_slow_publish_diagnostics() -> None:
    source = inspect.getsource(runs_worker)

    assert "agent_graph_stream_idle_wait_watchdog" in source
    assert "agent_graph_stream_idle_wait_end" in source
    assert "agent_stream_bridge_publish_slow" in source
    assert "DEERFLOW_RUN_TIMING_IDLE_WAIT_SECONDS" in source
    assert "DEERFLOW_RUN_TIMING_SLOW_BRIDGE_PUBLISH_MS" in source


def test_tool_totals_classify_sandbox_and_gateway_tools(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_RUN_TIMING_LOGS", "true")
    runtime = SimpleNamespace(context={"thread_id": "thread-123", "run_id": "run-456"})

    record_tool_timing(runtime, tool_name="bash", elapsed_ms=120, outcome="completed")
    record_tool_timing(runtime, tool_name="write_file", elapsed_ms=30, outcome="error")
    record_tool_timing(runtime, tool_name="web_search", elapsed_ms=999, outcome="completed")

    totals = consume_tool_timing(thread_id="thread-123", run_id="run-456")

    assert totals.sandbox_tool_count == 2
    assert totals.sandbox_tool_execution_total_ms == 150
    assert totals.sandbox_tool_acquire_overlap_ms == 0
    assert totals.sandbox_tool_noncompleted_count == 1
    assert totals.non_sandbox_tool_count == 1
    assert totals.non_sandbox_tool_total_ms == 999
    assert totals.non_sandbox_tool_noncompleted_count == 0
    assert consume_tool_timing(thread_id="thread-123", run_id="run-456").sandbox_tool_count == 0


def test_checkpoint_totals_remain_run_scoped_and_content_free(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DEERFLOW_RUN_TIMING_LOGS", "true")
    monkeypatch.setenv("DEERFLOW_CHECKPOINTER_TIMING_SLOW_MS", "20")
    caplog.set_level("INFO", logger="deerflow.run_timing")
    token = set_current_llm_timing_context(thread_id="thread-123", run_id="run-456")
    try:
        record_checkpoint_timing(operation="aput", elapsed_ms=12, outcome="completed")
        record_checkpoint_timing(operation="aput", elapsed_ms=24, outcome="error")
        record_checkpoint_timing(operation="pool_acquire", elapsed_ms=7, outcome="completed")
        record_checkpoint_timing(operation="pool_hold", elapsed_ms=11, outcome="completed")
        record_checkpoint_timing(operation="sql_execute", elapsed_ms=9, outcome="completed")
        record_checkpoint_timing(operation="not_a_trusted_category", elapsed_ms=999, outcome="completed")
    finally:
        reset_current_llm_timing_context(token)

    totals = consume_checkpoint_timing(thread_id="thread-123", run_id="run-456")

    assert totals.checkpoint_aput_count == 2
    assert totals.checkpoint_aput_total_ms == 36
    assert totals.checkpoint_aput_max_ms == 24
    assert totals.checkpoint_aput_noncompleted_count == 1
    assert totals.checkpoint_pool_acquire_count == 1
    assert totals.checkpoint_pool_acquire_total_ms == 7
    assert totals.checkpoint_pool_hold_count == 1
    assert totals.checkpoint_pool_hold_total_ms == 11
    assert totals.checkpoint_sql_execute_count == 1
    assert totals.checkpoint_sql_execute_total_ms == 9
    assert "RUN_TIMING event=checkpoint_aput_slow" in "\n".join(record.getMessage() for record in caplog.records)


def test_checkpoint_inflight_max_is_run_scoped_and_attached_to_slow_records(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DEERFLOW_RUN_TIMING_LOGS", "true")
    monkeypatch.setenv("DEERFLOW_CHECKPOINTER_TIMING_SLOW_MS", "20")
    caplog.set_level("INFO", logger="deerflow.run_timing")
    token = set_current_llm_timing_context(thread_id="thread-123", run_id="run-456")
    try:
        first = begin_checkpoint_timing(operation="aput_writes")
        second = begin_checkpoint_timing(operation="aput_writes")
        assert first is not None
        assert second is not None
        record_checkpoint_timing(operation="aput_writes", elapsed_ms=24, outcome="completed", token=second)
        record_checkpoint_timing(operation="aput_writes", elapsed_ms=21, outcome="completed", token=first)
    finally:
        reset_current_llm_timing_context(token)

    totals = consume_checkpoint_timing(thread_id="thread-123", run_id="run-456")
    rendered = "\n".join(record.getMessage() for record in caplog.records)

    assert totals.checkpoint_aput_writes_count == 2
    assert totals.checkpoint_aput_writes_inflight_max == 2
    assert "RUN_TIMING event=checkpoint_aput_writes_slow" in rendered
    assert "inflight_at_start=2" in rendered


def test_checkpoint_db_session_label_contains_only_the_current_run(monkeypatch) -> None:
    """The database-side correlation label must exclude thread and user data."""
    monkeypatch.setenv("DEERFLOW_CHECKPOINTER_DB_SESSION_LABELS", "true")
    token = set_current_llm_timing_context(thread_id="thread-secret", run_id="run-456")
    try:
        assert checkpoint_db_session_label() == "dfcp:run-456"
    finally:
        reset_current_llm_timing_context(token)

    assert checkpoint_db_session_label() == "dfcp:system"


def test_sandbox_tool_execution_excludes_overlapping_acquisition(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_RUN_TIMING_LOGS", "true")
    runtime = SimpleNamespace(context={"thread_id": "thread-123", "run_id": "run-456"})
    token = set_current_llm_timing_context(thread_id="thread-123", run_id="run-456")
    try:
        record_sandbox_acquire_timing(started_at=1.000, ended_at=1.120)
        record_tool_timing(
            runtime,
            tool_name="bash",
            elapsed_ms=200,
            outcome="completed",
            ended_at=1.150,
        )
    finally:
        reset_current_llm_timing_context(token)

    totals = consume_tool_timing(thread_id="thread-123", run_id="run-456")

    assert totals.sandbox_tool_count == 1
    assert totals.sandbox_tool_execution_total_ms == 80
    assert totals.sandbox_tool_acquire_overlap_ms == 120
