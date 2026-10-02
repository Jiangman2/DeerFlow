"""Background agent execution.

Runs an agent graph inside an ``asyncio.Task``, publishing events to
a :class:`StreamBridge` as they are produced.

Uses ``graph.astream(stream_mode=[...])`` which gives correct full-state
snapshots for ``values`` mode, proper ``{node: writes}`` for ``updates``,
and ``(chunk, metadata)`` tuples for ``messages`` mode.

Note: ``events`` mode is not supported through the gateway — it requires
``graph.astream_events()`` which cannot simultaneously produce ``values``
snapshots.  The JS open-source LangGraph API server works around this via
internal checkpoint callbacks that are not exposed in the Python public API.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import math
import os
import threading
import time
import weakref
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache, partial
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.errors import GraphRecursionError

from deerflow.agents.goal_state import GoalEvaluation, GoalState
from deerflow.agents.human_input import read_human_input_response
from deerflow.config.app_config import AppConfig
from deerflow.llm_timing_context import (
    reset_current_llm_timing_context,
    set_current_llm_timing_context,
)
from deerflow.models.robust_openai import _TIMING_RUN_METADATA_KEY, _TIMING_THREAD_METADATA_KEY
from deerflow.run_timing import consume_checkpoint_timing, consume_tool_timing, log_run_timing, run_timing_logs_enabled
from deerflow.runtime.context_keys import CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY
from deerflow.runtime.goal import (
    DEFAULT_MAX_GOAL_CONTINUATIONS,
    DEFAULT_MAX_NO_PROGRESS_CONTINUATIONS,
    GoalWriteConflict,
    _call_checkpointer_method,
    _is_visible_message,
    _message_type,
    attach_goal_evaluation,
    compute_no_progress_count,
    create_goal_evaluator_model,
    evaluate_goal_completion,
    goal_thread_lock,
    latest_visible_assistant_signature,
    make_goal_continuation_message,
    read_thread_goal,
    should_continue_goal,
    visible_conversation_signature,
    write_thread_goal,
)
from deerflow.runtime.serialization import serialize
from deerflow.runtime.stream_bridge import StreamBridge
from deerflow.runtime.user_context import get_effective_user_id, resolve_runtime_user_id
from deerflow.runtime.weknora_context import (
    reset_current_weknora_access_token,
    reset_current_weknora_scope,
    set_current_weknora_access_token,
    set_current_weknora_scope,
)
from deerflow.sandbox import get_sandbox_provider
from deerflow.trace_context import DEERFLOW_TRACE_METADATA_KEY, get_current_trace_id, normalize_trace_id
from deerflow.tracing import inject_langfuse_metadata
from deerflow.utils.messages import message_to_text
from deerflow.workspace_changes import capture_workspace_snapshot, record_workspace_changes
from deerflow.workspace_changes.types import WorkspaceSnapshot

from .manager import RunManager, RunRecord
from .naming import resolve_root_run_name
from .schemas import RunStatus
from .stream_publish_batch import MessagePublishBatcher

logger = logging.getLogger(__name__)


async def _acquire_gateway_sandbox_lease(provider: Any, thread_id: str, user_id: str) -> tuple[str, Any]:
    """Acquire a run-long Sandbox lease without blocking the event loop.

    A cancelled waiter must release a lease that its worker thread acquires
    later; otherwise a cancelled run could protect its Sandbox indefinitely.
    """

    task = asyncio.create_task(asyncio.to_thread(provider.acquire_thread_execution_lease, thread_id, user_id=user_id))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:

        def release_late(completed: asyncio.Task) -> None:
            try:
                sandbox_id, lock_file = completed.result()
            except BaseException:
                return
            try:
                provider.release_execution_lease(lock_file, sandbox_id)
            except Exception:
                logger.exception("Failed to release late Sandbox execution lease for %s", sandbox_id)

        task.add_done_callback(release_late)
        raise


_checkpoint_locks_guard = threading.Lock()
_checkpoint_locks_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = weakref.WeakKeyDictionary()


def _positive_timing_env(name: str, default: float) -> float:
    """Read one positive diagnostic threshold without making startup fragile."""

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def _pending_async_task_summary(*, limit: int = 8) -> tuple[int, str, str]:
    """Return bounded coroutine labels for an event-loop snapshot.

    Task names can be supplied by callers and coroutine frames can contain
    request data, so diagnostics deliberately retain neither.  A coroutine
    code object's name is a stable implementation label and is enough to
    distinguish an idle graph wait from a busy model/tool task.
    """

    current_task = asyncio.current_task()
    label_counts: Counter[str] = Counter()
    pending_count = 0
    for task in asyncio.all_tasks():
        if task is current_task or task.done():
            continue
        pending_count += 1
        coroutine = task.get_coro()
        code = getattr(coroutine, "cr_code", None) or getattr(coroutine, "gi_code", None)
        label = getattr(code, "co_name", type(coroutine).__name__)
        safe_label = "".join(char if char.isalnum() or char in "_.:-" else "_" for char in str(label))[:48]
        label_counts[safe_label or "unknown"] += 1

    summary = ".".join(sorted(label_counts)[:limit])[:120]
    top_counts = ".".join(f"{count}:{label}" for label, count in sorted(label_counts.items(), key=lambda item: (-item[1], item[0]))[:limit])[:120]
    return pending_count, summary or "none", top_counts or "none"


@asynccontextmanager
async def _checkpoint_thread_lock(thread_id: str) -> AsyncIterator[None]:
    """Serialize checkpoint mutations for one thread without blocking goal commands."""
    loop = asyncio.get_running_loop()
    with _checkpoint_locks_guard:
        locks = _checkpoint_locks_by_loop.get(loop)
        if locks is None:
            locks = {}
            _checkpoint_locks_by_loop[loop] = locks
        lock = locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[thread_id] = lock

    async with lock:
        yield


async def _time_run_stage(event_name: str, operation: Any) -> Any:
    """Run one bounded worker stage and emit content-free timing boundaries."""
    started_at = time.monotonic()
    log_run_timing(logger, f"{event_name}_start")
    try:
        result = await operation()
    except asyncio.CancelledError as exc:
        log_run_timing(
            logger,
            f"{event_name}_end",
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
            outcome="cancelled",
            error_type=type(exc).__name__,
        )
        raise
    except Exception as exc:
        log_run_timing(
            logger,
            f"{event_name}_end",
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
            outcome="error",
            error_type=type(exc).__name__,
        )
        raise
    log_run_timing(
        logger,
        f"{event_name}_end",
        elapsed_ms=round((time.monotonic() - started_at) * 1000),
        outcome="completed",
    )
    return result


# Valid stream_mode values for LangGraph's graph.astream()
_VALID_LG_MODES = {"values", "updates", "checkpoints", "tasks", "debug", "messages", "custom"}
_GRAPH_TASK_TERMINAL_EVENTS = frozenset({"end", "error", "cancelled", "completed", "finished"})


@dataclass(frozen=True, slots=True)
class _GraphTaskEvent:
    """One content-free task lifecycle item from LangGraph ``tasks`` mode."""

    task_id: str
    node_name: str
    terminal: bool
    outcome: str


def _iter_graph_task_events(chunk: Any) -> list[_GraphTaskEvent]:
    """Extract task lifecycle metadata without retaining graph input or output.

    LangGraph emits a task mapping at task start and another at terminal state.
    Versions differ slightly in the optional status field, so terminal detection
    accepts both the documented ``result``/``error`` keys and known statuses.
    Only the internal task id and node name are retained in process memory; the
    run log receives the bounded node name and a local counter, never payload.
    """

    candidates = [chunk] if isinstance(chunk, dict) else chunk if isinstance(chunk, (list, tuple)) else []
    events: list[_GraphTaskEvent] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        task_id = candidate.get("id")
        node_name = candidate.get("name")
        if not isinstance(task_id, str) or not task_id or len(task_id) > 256:
            continue
        if not isinstance(node_name, str) or not node_name or len(node_name) > 128:
            continue
        state = str(candidate.get("event") or candidate.get("status") or "").lower()
        terminal = "result" in candidate or "error" in candidate or state in _GRAPH_TASK_TERMINAL_EVENTS
        if state == "cancelled":
            outcome = "cancelled"
        elif candidate.get("error") is not None or state == "error":
            outcome = "error"
        else:
            outcome = "completed"
        events.append(_GraphTaskEvent(task_id, node_name, terminal, outcome))
    return events


def _has_open_clarification(messages: list[Any]) -> bool:
    """Return whether a clarification request is still awaiting a response."""
    open_request_ids: list[str] = []

    for message in messages:
        if isinstance(message, ToolMessage) and message.name == "ask_clarification":
            artifact = message.artifact if isinstance(message.artifact, dict) else {}
            human_input = artifact.get("human_input")
            if isinstance(human_input, dict):
                request_id = human_input.get("request_id")
                if isinstance(request_id, str) and request_id:
                    open_request_ids.append(request_id)
                    continue
            if message.id:
                open_request_ids.append(str(message.id))
            continue

        if isinstance(message, HumanMessage):
            response = read_human_input_response(message.additional_kwargs)
            if response is not None and response["request_id"] in open_request_ids:
                open_request_ids = [request_id for request_id in open_request_ids if request_id != response["request_id"]]

    return bool(open_request_ids)


def _chunk_has_open_clarification(chunk: Any) -> bool:
    """Detect clarification in a raw stream chunk before checkpoint durability."""
    if isinstance(chunk, dict):
        messages = chunk.get("messages")
        if isinstance(messages, list) and _has_open_clarification(messages):
            return True
        return any(_chunk_has_open_clarification(value) for value in chunk.values())

    if isinstance(chunk, (list, tuple)):
        return any(_chunk_has_open_clarification(value) for value in chunk)

    return isinstance(chunk, ToolMessage) and chunk.name == "ask_clarification"


async def _checkpoint_has_open_clarification(checkpointer: Any, thread_id: str) -> bool:
    """Check durable thread state as a final clarification fallback."""
    if checkpointer is None:
        return False
    try:
        ckpt_tuple = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
        if ckpt_tuple is None:
            return False
        checkpoint = getattr(ckpt_tuple, "checkpoint", {}) or {}
        channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
        messages = channel_values.get("messages", []) if isinstance(channel_values, dict) else []
        return isinstance(messages, list) and _has_open_clarification(messages)
    except Exception:
        logger.debug("Could not inspect checkpoint for pending clarification", exc_info=True)
        return False


async def _resolve_pending_clarification(*, checkpointer: Any, thread_id: str, stream_clarification_seen: bool) -> bool:
    """Use durable state when available; stream chunks can replay answered requests."""
    if checkpointer is None:
        return stream_clarification_seen
    return await _checkpoint_has_open_clarification(checkpointer, thread_id)


async def _reconcile_interrupted_artifact_claim(*, journal: Any, checkpointer: Any, thread_id: str) -> bool:
    """Replace an interrupted, uncommitted fabricated file link in the journal."""
    if journal is None or checkpointer is None:
        return False
    message = journal.get_last_lead_ai_message()
    if not isinstance(message, AIMessage):
        return False
    try:
        checkpoint_tuple = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        )
        checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) if checkpoint_tuple is not None else {}
        channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
        state = channel_values if isinstance(channel_values, dict) else {}

        from deerflow.agents.middlewares.artifact_delivery_middleware import (
            build_artifact_delivery_fallback,
            has_invalid_artifact_claim,
        )

        if not has_invalid_artifact_claim(message, state):
            return False
        fallback = build_artifact_delivery_fallback(
            message,
            reason="Interrupted before artifact delivery validation completed",
        )
        journal.reconcile_ai_message(fallback, reason="interrupted_artifact_delivery")
        return True
    except Exception:
        logger.warning("Failed to reconcile interrupted artifact claim for run journal", exc_info=True)
        return False


def _build_runtime_context(
    thread_id: str,
    run_id: str,
    caller_context: Any | None,
    app_config: AppConfig | None = None,
) -> dict[str, Any]:
    """Build the dict that becomes ``ToolRuntime.context`` for the run.

    Always includes ``thread_id`` and ``run_id``. Additional keys from the caller's
    ``config['context']`` (e.g. ``agent_name`` for the bootstrap flow — issue #2677)
    are merged in but never override ``thread_id``/``run_id``. The resolved
    ``AppConfig`` is added by the worker so tools can consume it without ambient
    global lookups.

    langgraph 1.1+ surfaces this as ``runtime.context`` via the parent runtime stored
    under ``config['configurable']['__pregel_runtime']`` — see
    ``langgraph.pregel.main`` where ``parent_runtime.merge(...)`` is invoked.
    """
    runtime_ctx: dict[str, Any] = {"thread_id": thread_id, "run_id": run_id}
    if isinstance(caller_context, dict):
        for key, value in caller_context.items():
            if key == CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY:
                continue
            runtime_ctx.setdefault(key, value)
    if app_config is not None:
        runtime_ctx["app_config"] = app_config
    return runtime_ctx


@dataclass(frozen=True)
class RunContext:
    """Infrastructure dependencies for a single agent run.

    Groups checkpointer, store, and persistence-related singletons so that
    ``run_agent`` (and any future callers) receive one object instead of a
    growing list of keyword arguments.
    """

    checkpointer: Any
    store: Any | None = field(default=None)
    event_store: Any | None = field(default=None)
    run_events_config: Any | None = field(default=None)
    thread_store: Any | None = field(default=None)
    app_config: AppConfig | None = field(default=None)
    on_run_completed: Any | None = field(default=None)


def _install_runtime_context(config: dict, runtime_context: dict[str, Any]) -> None:
    existing_context = config.get("context")
    if isinstance(existing_context, dict):
        existing_context.setdefault("thread_id", runtime_context["thread_id"])
        existing_context.setdefault("run_id", runtime_context["run_id"])
        if DEERFLOW_TRACE_METADATA_KEY in runtime_context:
            existing_context.setdefault(DEERFLOW_TRACE_METADATA_KEY, runtime_context[DEERFLOW_TRACE_METADATA_KEY])
        if "app_config" in runtime_context:
            existing_context["app_config"] = runtime_context["app_config"]
        if CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY in runtime_context:
            existing_context[CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY] = runtime_context[CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY]
        return

    config["context"] = dict(runtime_context)


def _install_llm_timing_metadata(config: dict[str, Any], *, thread_id: str, run_id: str) -> None:
    """Persist run attribution through LangChain callback/task boundaries.

    ``ContextVar`` remains the lowest-overhead fast path.  These reserved
    metadata keys provide the same non-sensitive IDs to model callbacks which
    are created by LangGraph in a context where task-local state was not copied.
    """

    existing_metadata = config.get("metadata")
    metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
    metadata[_TIMING_THREAD_METADATA_KEY] = thread_id
    metadata[_TIMING_RUN_METADATA_KEY] = run_id
    config["metadata"] = metadata


def _compute_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return "app_config" in inspect.signature(agent_factory).parameters
    except (TypeError, ValueError):
        return False


@lru_cache(maxsize=128)
def _cached_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    return _compute_agent_factory_supports_app_config(agent_factory)


def _agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return _cached_agent_factory_supports_app_config(agent_factory)
    except TypeError:
        # Some callable instances are unhashable; fall back to a direct check.
        return _compute_agent_factory_supports_app_config(agent_factory)


class _SubagentEventBuffer:
    """Buffer subagent ``task_*`` step events and flush them in one locked batch (#3779).

    The live SSE bridge already forwards these events for real-time display; this
    additionally writes them so the subtask card's step history survives a reload.

    ``RunEventStore.put`` is documented as a low-frequency path — on Postgres each
    call opens its own transaction and takes a per-thread advisory lock. A deep
    subagent (``general-purpose`` runs up to ``max_turns=150``) emits hundreds of
    ``task_running`` steps on the hot stream loop, so persisting each with
    ``put()`` would serialize against the run's own message-batch writer. This
    accumulates recognized subagent events and writes them with ``put_batch``,
    which acquires the lock once per batch, honoring the store's contract.

    Best-effort: a missing store (run_events not configured) or an unrecognized
    chunk is a no-op, flush failures are logged but never propagate into the
    stream loop, and terminal ``subagent.end`` events flush eagerly so a completed
    subagent's step history is durable promptly rather than only at run end.
    """

    #: Flush once this many events are buffered, bounding memory and reload lag on
    #: a single deep subagent without paying a per-step lock.
    FLUSH_THRESHOLD = 25

    def __init__(self, event_store: Any | None, thread_id: str, run_id: str) -> None:
        self._event_store = event_store
        self._thread_id = thread_id
        self._run_id = run_id
        self._pending: list[dict[str, Any]] = []

    async def add(self, chunk: Any) -> None:
        """Buffer one custom stream chunk; flush on a terminal event or threshold."""
        if self._event_store is None:
            return
        # Lazy import: importing deerflow.subagents at module load triggers its
        # package __init__ (executor → agents → tools → task_tool), which imports
        # back from deerflow.subagents and deadlocks at gateway startup. Deferring
        # it to call time (after all modules are loaded) breaks that cycle.
        from deerflow.subagents.step_events import subagent_run_event

        record = subagent_run_event(chunk)
        if record is None:
            return
        self._pending.append({"thread_id": self._thread_id, "run_id": self._run_id, **record})
        if record["event_type"] in {"subagent.end", "subagent.usage"} or len(self._pending) >= self.FLUSH_THRESHOLD:
            await self.flush()

    async def flush(self) -> None:
        """Persist buffered events in one ``put_batch`` call; swallow store errors."""
        if self._event_store is None or not self._pending:
            return
        batch = self._pending
        self._pending = []
        try:
            await self._event_store.put_batch(batch)
        except Exception:
            # Re-buffer the failed batch (ahead of any events queued since) so a
            # transient store error does not silently drop subagent step events.
            self._pending = batch + self._pending
            logger.warning("Run %s: failed to persist %d subagent step event(s)", self._run_id, len(batch), exc_info=True)


async def run_agent(
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    *,
    ctx: RunContext,
    agent_factory: Any,
    graph_input: dict,
    config: dict,
    stream_modes: list[str] | None = None,
    stream_subgraphs: bool = False,
    interrupt_before: list[str] | Literal["*"] | None = None,
    interrupt_after: list[str] | Literal["*"] | None = None,
) -> None:
    """Execute an agent in the background, publishing events to *bridge*."""

    # Unpack infrastructure dependencies from RunContext.
    checkpointer = ctx.checkpointer
    store = ctx.store
    event_store = ctx.event_store
    run_events_config = ctx.run_events_config
    thread_store = ctx.thread_store

    run_id = record.run_id
    thread_id = record.thread_id
    run_started_at = time.monotonic()
    llm_timing_context_token = set_current_llm_timing_context(thread_id=thread_id, run_id=run_id)
    log_run_timing(logger, "run_start", worker_pid=os.getpid())
    requested_modes: set[str] = set(stream_modes or ["values"])
    pre_run_checkpoint_id: str | None = None
    pre_run_snapshot: dict[str, Any] | None = None
    pre_run_workspace_snapshot: WorkspaceSnapshot | None = None
    workspace_changes_user_id: str | None = None
    snapshot_capture_failed = False
    llm_error_fallback_message: str | None = None
    stream_clarification_seen = False
    pending_clarification = False
    stream_end_published = False
    run_sandbox_provider: Any | None = None
    run_sandbox_lease: tuple[str, Any] | None = None

    async def _publish_stream_end() -> None:
        """Publish the terminal stream event at most once."""
        nonlocal stream_end_published
        if stream_end_published:
            return
        await bridge.publish_end(run_id)
        stream_end_published = True

    # Message ids checkpointed *before* this run started. The stream loop uses
    # this set to mask out ``deerflow_error_fallback`` markers that belong to
    # earlier runs on the same thread — without it, one stale fallback in
    # history would mark every subsequent run on this thread as ``error``.
    pre_existing_message_ids: set[str] = set()

    journal = None
    weknora_token_context = None
    weknora_scope_context = None
    # Buffers subagent step events for batched persistence (#3779); assigned once
    # streaming starts and flushed in the finally block. Pre-bound to None so the
    # finally is safe even if an exception fires before streaming begins.
    subagent_events: _SubagentEventBuffer | None = None

    # Track whether "events" was requested but skipped
    if "events" in requested_modes:
        logger.info(
            "Run %s: 'events' stream_mode not supported in gateway (requires astream_events + checkpoint callbacks). Skipping.",
            run_id,
        )

    try:
        await run_manager.wait_for_prior_finalizing(thread_id, run_id)

        # Initialize RunJournal + write human_message event.
        # These are inside the try block so any exception (e.g. a DB
        # error writing the event) flows through the except/finally
        # path that publishes an "end" event to the SSE bridge —
        # otherwise a failure here would leave the stream hanging
        # with no terminator.
        if event_store is not None:
            from deerflow.runtime.journal import RunJournal

            journal = RunJournal(
                run_id=run_id,
                thread_id=thread_id,
                event_store=event_store,
                track_token_usage=getattr(run_events_config, "track_token_usage", True),
                progress_reporter=lambda snapshot: run_manager.update_run_progress(run_id, **snapshot),
            )

        # 1. Mark running
        await run_manager.set_status(run_id, RunStatus.running)

        if event_store is not None:
            workspace_changes_user_id = get_effective_user_id()
            try:
                pre_run_workspace_snapshot = await capture_workspace_snapshot(
                    thread_id,
                    user_id=workspace_changes_user_id,
                )
            except Exception:
                logger.warning("Could not capture pre-run workspace snapshot for run %s", run_id, exc_info=True)

        # Snapshot the latest pre-run checkpoint so rollback can restore it.
        if checkpointer is not None:
            try:
                config_for_check = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(config_for_check)
                if ckpt_tuple is not None:
                    ckpt_config = getattr(ckpt_tuple, "config", {}).get("configurable", {})
                    pre_run_checkpoint_id = ckpt_config.get("checkpoint_id")
                    pre_run_snapshot = {
                        "checkpoint_ns": ckpt_config.get("checkpoint_ns", ""),
                        "checkpoint": copy.deepcopy(getattr(ckpt_tuple, "checkpoint", {})),
                        "metadata": copy.deepcopy(getattr(ckpt_tuple, "metadata", {})),
                        "pending_writes": copy.deepcopy(getattr(ckpt_tuple, "pending_writes", []) or []),
                    }
                    pre_existing_message_ids = _collect_pre_existing_message_ids(pre_run_snapshot)
            except Exception:
                snapshot_capture_failed = True
                logger.warning("Could not capture pre-run checkpoint snapshot for run %s", run_id, exc_info=True)

        # 2. Publish metadata — useStream needs both run_id AND thread_id
        await bridge.publish(
            run_id,
            "metadata",
            {
                "run_id": run_id,
                "thread_id": thread_id,
            },
        )

        # 3. Build the agent
        from langchain_core.runnables import RunnableConfig
        from langgraph.runtime import Runtime

        # Inject runtime context so middlewares and tools (via ToolRuntime.context) can
        # access thread-level data. langgraph-cli does this automatically; we must do it
        # manually here because we drive the graph through ``agent.astream(config=...)``
        # without passing the official ``context=`` parameter.
        runtime_ctx = _build_runtime_context(thread_id, run_id, config.get("context"), ctx.app_config)
        weknora_scope_context = set_current_weknora_scope(runtime_ctx)
        weknora_access_token = runtime_ctx.get("weknora_access_token")
        if isinstance(weknora_access_token, str) and weknora_access_token.strip():
            weknora_token_context = set_current_weknora_access_token(weknora_access_token.strip())
        clarification_event = asyncio.Event()
        runtime_ctx["__clarification_event"] = clarification_event
        runtime_ctx[CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY] = frozenset(pre_existing_message_ids)
        incoming_metadata = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
        deerflow_trace_id = normalize_trace_id(incoming_metadata.get(DEERFLOW_TRACE_METADATA_KEY)) or get_current_trace_id()
        if deerflow_trace_id:
            runtime_ctx[DEERFLOW_TRACE_METADATA_KEY] = deerflow_trace_id
        # Expose the run-scoped journal under a sentinel key so middleware can
        # write audit events (e.g. SafetyFinishReasonMiddleware recording
        # suppressed tool calls). Double-underscore prefix marks it as a
        # runtime-internal channel; user code must not depend on the key name.
        if journal is not None:
            runtime_ctx["__run_journal"] = journal
        _install_runtime_context(config, runtime_ctx)
        _install_llm_timing_metadata(config, thread_id=thread_id, run_id=run_id)
        runtime = Runtime(context=cast(Any, runtime_ctx), store=store)
        config.setdefault("configurable", {})["__pregel_runtime"] = runtime

        # Every remote AIO Sandbox has a deterministic user/thread ID. Hold a
        # shared lease for the *entire* run, before a lazy tool can create the
        # Pod. Other Gateway processes may adopt the same Pod into their warm
        # pools, but their idle/capacity/shutdown paths cannot delete it while
        # this graph (including subagent tools) is executing.
        if getattr(getattr(ctx.app_config, "sandbox", None), "provisioner_url", None):
            run_sandbox_provider = await asyncio.to_thread(get_sandbox_provider)
            if not callable(getattr(run_sandbox_provider, "acquire_thread_execution_lease", None)):
                raise RuntimeError("Remote Sandbox provider does not support run execution leases")
            run_sandbox_lease = await _acquire_gateway_sandbox_lease(run_sandbox_provider, thread_id, resolve_runtime_user_id(runtime))

        # Inject RunJournal as a LangChain callback handler.
        # on_llm_end captures token usage; on_chain_start/end captures lifecycle.
        if journal is not None:
            config.setdefault("callbacks", []).append(journal)

        # Inject Langfuse trace-attribute metadata so the langchain CallbackHandler
        # can lift session_id / user_id / trace_name / tags onto the root trace.
        # Shared helper with ``DeerFlowClient.stream`` so both entry points stay
        # in sync; caller-provided metadata wins via setdefault inside the helper.
        inject_langfuse_metadata(
            config,
            thread_id=thread_id,
            user_id=resolve_runtime_user_id(runtime),
            assistant_id=record.assistant_id,
            model_name=record.model_name,
            environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
            deerflow_trace_id=deerflow_trace_id,
        )

        # Resolve after runtime context installation so context/configurable reflect
        # the agent name that this run will actually execute.
        config.setdefault("run_name", resolve_root_run_name(config, record.assistant_id))
        initial_runnable_config = RunnableConfig(**config)

        def _continuation_runnable_config() -> RunnableConfig:
            continuation_config = dict(config)
            configurable = dict(continuation_config.get("configurable", {}) or {})
            configurable["checkpoint_ns"] = ""
            configurable.pop("checkpoint_id", None)
            configurable.pop("checkpoint_map", None)
            continuation_config["configurable"] = configurable
            return RunnableConfig(**continuation_config)

        if ctx.app_config is not None and _agent_factory_supports_app_config(agent_factory):
            agent = agent_factory(config=initial_runnable_config, app_config=ctx.app_config)
        else:
            agent = agent_factory(config=initial_runnable_config)

        # Capture the effective (resolved) model name from the agent's metadata.
        # _resolve_model_name in agent.py may return the default model if the
        # requested name is not in the allowlist — this update ensures the
        # persisted model_name reflects the actual model used.
        resolved = getattr(agent, "metadata", {}) or {}
        if isinstance(resolved, dict):
            effective = resolved.get("model_name")
            if effective and effective != record.model_name:
                await run_manager.update_model_name(record.run_id, effective)

        # 4. Attach checkpointer and store
        if checkpointer is not None:
            agent.checkpointer = checkpointer
        if store is not None:
            agent.store = store

        # 5. Set interrupt nodes
        if interrupt_before:
            agent.interrupt_before_nodes = interrupt_before
        if interrupt_after:
            agent.interrupt_after_nodes = interrupt_after

        # 6. Build LangGraph stream_mode list
        #    "events" is NOT a valid astream mode — skip it
        #    "messages-tuple" maps to LangGraph's "messages" mode
        lg_modes: list[str] = []
        for m in requested_modes:
            if m == "messages-tuple":
                lg_modes.append("messages")
            elif m == "events":
                # Skipped — see log above
                continue
            elif m in _VALID_LG_MODES:
                lg_modes.append(m)
        if not lg_modes:
            lg_modes = ["values"]

        # ``tasks`` exposes LangGraph node lifecycle metadata.  It is added only
        # during the bounded content-free timing investigation and is consumed
        # locally below, never forwarded to the browser SSE stream.
        graph_task_timing_enabled = run_timing_logs_enabled()
        graph_task_mode_added = graph_task_timing_enabled and "tasks" not in lg_modes
        if graph_task_mode_added:
            lg_modes.append("tasks")

        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for m in lg_modes:
            if m not in seen:
                seen.add(m)
                deduped.append(m)
        lg_modes = deduped

        logger.info("Run %s: streaming with modes %s (requested: %s)", run_id, lg_modes, requested_modes)

        # Buffer subagent step events and persist them in batches (#3779) instead
        # of one low-frequency put() per step on the hot stream loop. Flushed in
        # the finally block so buffered steps survive abort/exception paths too.
        subagent_events = _SubagentEventBuffer(event_store, thread_id, run_id)

        graph_task_started_at: dict[str, tuple[float, int, str]] = {}
        graph_task_sequence = 0
        idle_wait_watchdog_seconds = _positive_timing_env(
            "DEERFLOW_RUN_TIMING_IDLE_WAIT_SECONDS",
            30.0,
        )
        slow_bridge_publish_ms = _positive_timing_env(
            "DEERFLOW_RUN_TIMING_SLOW_BRIDGE_PUBLISH_MS",
            1000.0,
        )

        def _active_graph_task_fields() -> dict[str, int | str]:
            active_nodes = {node_name for _, _, node_name in graph_task_started_at.values()}
            return {
                "active_task_count": len(graph_task_started_at),
                "active_node": next(iter(active_nodes)) if len(active_nodes) == 1 else "multiple",
            }

        def _observe_graph_tasks(chunk: Any, *, stream_kind: str) -> None:
            """Emit node timing from the private ``tasks`` stream mode."""

            nonlocal graph_task_sequence
            observed_at = time.monotonic()
            for task in _iter_graph_task_events(chunk):
                if task.terminal:
                    started = graph_task_started_at.pop(task.task_id, None)
                    if started is None:
                        log_run_timing(
                            logger,
                            "graph_task_end",
                            outcome=task.outcome,
                            node_name=task.node_name,
                            observed_without_start="true",
                            stream_kind=stream_kind,
                        )
                        continue
                    started_at, task_index, node_name = started
                    log_run_timing(
                        logger,
                        "graph_task_end",
                        elapsed_ms=round((observed_at - started_at) * 1000),
                        outcome=task.outcome,
                        node_name=node_name,
                        task_index=task_index,
                        stream_kind=stream_kind,
                    )
                    continue

                if task.task_id in graph_task_started_at:
                    continue
                graph_task_sequence += 1
                graph_task_started_at[task.task_id] = (observed_at, graph_task_sequence, task.node_name)
                log_run_timing(
                    logger,
                    "graph_task_start",
                    node_name=task.node_name,
                    task_index=graph_task_sequence,
                    stream_kind=stream_kind,
                )

        def _emit_idle_graph_wait_watchdog(*, started_at: float, stream_kind: str) -> None:
            """Snapshot a graph stream that is idle after all observed tasks ended."""

            # A model/tool node already has a graph-task span and is not the
            # silent-tail condition being investigated.  Snapshot only when
            # LangGraph has no active observed node but has not yielded again.
            if graph_task_started_at:
                return
            pending_task_count, pending_task_kinds, pending_task_top_counts = _pending_async_task_summary()
            log_run_timing(
                logger,
                "agent_graph_stream_idle_wait_watchdog",
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
                stream_kind=stream_kind,
                pending_task_count=pending_task_count,
                pending_task_kinds=pending_task_kinds,
                pending_task_top_counts=pending_task_top_counts,
                **_active_graph_task_fields(),
            )

        async def _next_graph_stream_item(
            iterator: AsyncIterator[Any],
            *,
            stream_kind: str,
        ) -> tuple[bool, Any | None]:
            """Await one graph item and expose an otherwise invisible idle wait.

            LangGraph can spend time after the last task receipt while its
            async generator waits for internal cleanup.  Do not use
            ``asyncio.wait_for`` here: timing out would cancel the live graph.
            A separate watchdog observes the wait without changing it.
            """

            started_at = time.monotonic() if graph_task_timing_enabled else 0.0
            watchdog: asyncio.TimerHandle | None = None
            if graph_task_timing_enabled and graph_task_sequence and not graph_task_started_at:
                watchdog = asyncio.get_running_loop().call_later(
                    idle_wait_watchdog_seconds,
                    partial(
                        _emit_idle_graph_wait_watchdog,
                        started_at=started_at,
                        stream_kind=stream_kind,
                    ),
                )
            outcome = "completed"
            error_type: str | None = None
            try:
                return True, await anext(iterator)
            except StopAsyncIteration:
                outcome = "stream_exhausted"
                return False, None
            except asyncio.CancelledError as exc:
                outcome = "cancelled"
                error_type = type(exc).__name__
                raise
            except Exception as exc:
                outcome = "error"
                error_type = type(exc).__name__
                raise
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                elapsed_ms = round((time.monotonic() - started_at) * 1000) if graph_task_timing_enabled else 0
                if graph_task_timing_enabled and elapsed_ms >= idle_wait_watchdog_seconds * 1000 and not graph_task_started_at:
                    fields: dict[str, int | str] = {
                        "outcome": outcome,
                        "stream_kind": stream_kind,
                        **_active_graph_task_fields(),
                    }
                    if error_type is not None:
                        fields["error_type"] = error_type
                    log_run_timing(logger, "agent_graph_stream_idle_wait_end", elapsed_ms=elapsed_ms, **fields)

        async def _publish_graph_items(
            events: Sequence[tuple[str, Any]],
            *,
            mode: str,
            stream_kind: str,
        ) -> None:
            """Publish ordered graph events while logging only slow backpressure.

            A start/end pair for every streamed token would materially distort
            a 100-concurrency load test.  The timer therefore emits only a
            bounded slow/error event while preserving normal publish behavior.
            """

            started_at = time.monotonic() if graph_task_timing_enabled else 0.0
            try:
                if len(events) == 1:
                    event, payload = events[0]
                    await bridge.publish(run_id, event, payload)
                else:
                    await bridge.publish_many(run_id, events)
            except asyncio.CancelledError as exc:
                if graph_task_timing_enabled:
                    log_run_timing(
                        logger,
                        "agent_stream_bridge_publish_end",
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                        outcome="cancelled",
                        error_type=type(exc).__name__,
                        event_mode=mode,
                        stream_kind=stream_kind,
                        event_count=len(events),
                    )
                raise
            except Exception as exc:
                if graph_task_timing_enabled:
                    log_run_timing(
                        logger,
                        "agent_stream_bridge_publish_end",
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                        outcome="error",
                        error_type=type(exc).__name__,
                        event_mode=mode,
                        stream_kind=stream_kind,
                        event_count=len(events),
                    )
                raise
            else:
                elapsed_ms = round((time.monotonic() - started_at) * 1000) if graph_task_timing_enabled else 0
                if graph_task_timing_enabled and elapsed_ms >= slow_bridge_publish_ms:
                    log_run_timing(
                        logger,
                        "agent_stream_bridge_publish_slow",
                        elapsed_ms=elapsed_ms,
                        event_mode=mode,
                        stream_kind=stream_kind,
                        event_count=len(events),
                    )

        goal_evaluator_model: Any | None = None

        def _get_goal_evaluator_model() -> Any:
            nonlocal goal_evaluator_model
            if goal_evaluator_model is None:
                goal_evaluator_model = create_goal_evaluator_model(
                    model_name=record.model_name,
                    app_config=ctx.app_config,
                )
            return goal_evaluator_model

        async def _consume_stream(
            input_payload: Any,
            stream_config: RunnableConfig,
            *,
            stream_kind: str,
        ) -> None:
            nonlocal llm_error_fallback_message, stream_clarification_seen
            lock_wait_started_at = time.monotonic()
            lock_acquired = False
            log_run_timing(logger, "checkpoint_lock_wait_start", stream_kind=stream_kind)
            try:
                async with _checkpoint_thread_lock(thread_id):
                    lock_acquired = True
                    log_run_timing(
                        logger,
                        "checkpoint_lock_wait_end",
                        elapsed_ms=round((time.monotonic() - lock_wait_started_at) * 1000),
                        outcome="completed",
                        stream_kind=stream_kind,
                    )
                    graph_stream_started_at = time.monotonic()
                    log_run_timing(logger, "agent_graph_stream_start", stream_kind=stream_kind)
                    try:
                        last_graph_event_at = await _consume_locked_stream(
                            input_payload,
                            stream_config,
                            stream_kind=stream_kind,
                        )
                    except asyncio.CancelledError as exc:
                        log_run_timing(
                            logger,
                            "agent_graph_stream_end",
                            elapsed_ms=round((time.monotonic() - graph_stream_started_at) * 1000),
                            outcome="cancelled",
                            error_type=type(exc).__name__,
                            stream_kind=stream_kind,
                        )
                        raise
                    except Exception as exc:
                        log_run_timing(
                            logger,
                            "agent_graph_stream_end",
                            elapsed_ms=round((time.monotonic() - graph_stream_started_at) * 1000),
                            outcome="error",
                            error_type=type(exc).__name__,
                            stream_kind=stream_kind,
                        )
                        raise
                    else:
                        log_run_timing(
                            logger,
                            "agent_graph_stream_tail",
                            elapsed_ms=round((time.monotonic() - (last_graph_event_at or graph_stream_started_at)) * 1000),
                            stream_kind=stream_kind,
                            **_active_graph_task_fields(),
                        )
                        graph_task_started_at.clear()
                        log_run_timing(
                            logger,
                            "agent_graph_stream_end",
                            elapsed_ms=round((time.monotonic() - graph_stream_started_at) * 1000),
                            outcome="completed",
                            stream_kind=stream_kind,
                        )
            except asyncio.CancelledError as exc:
                if not lock_acquired:
                    log_run_timing(
                        logger,
                        "checkpoint_lock_wait_end",
                        elapsed_ms=round((time.monotonic() - lock_wait_started_at) * 1000),
                        outcome="cancelled",
                        error_type=type(exc).__name__,
                        stream_kind=stream_kind,
                    )
                raise
            except Exception as exc:
                if not lock_acquired:
                    log_run_timing(
                        logger,
                        "checkpoint_lock_wait_end",
                        elapsed_ms=round((time.monotonic() - lock_wait_started_at) * 1000),
                        outcome="error",
                        error_type=type(exc).__name__,
                        stream_kind=stream_kind,
                    )
                raise

        async def _consume_locked_stream(
            input_payload: Any,
            stream_config: RunnableConfig,
            *,
            stream_kind: str,
        ) -> float | None:
            """Publish one graph stream while the thread checkpoint lock is held.

            Return the monotonic timestamp of the final published graph item so
            the caller can expose a silent tail after the final client-visible
            update without logging the update itself.
            """
            nonlocal llm_error_fallback_message, stream_clarification_seen
            last_graph_event_at: float | None = None
            message_batch = MessagePublishBatcher(
                publish_one=lambda event, data, mode: _publish_graph_items(((event, data),), mode=mode, stream_kind=stream_kind),
                publish_many=lambda events: _publish_graph_items(events, mode="messages", stream_kind=stream_kind),
                enabled=getattr(bridge, "supports_batched_publish", False),
                max_events=getattr(bridge, "max_publish_batch_size", 64),
            )
            primary_error: BaseException | None = None
            try:
                if len(lg_modes) == 1 and not stream_subgraphs:
                    # Single mode, no subgraphs: astream yields raw chunks
                    single_mode = lg_modes[0]
                    graph_stream = agent.astream(input_payload, config=stream_config, stream_mode=single_mode)
                    graph_iterator = aiter(graph_stream)
                    while True:
                        has_item, chunk = await message_batch.next_item(lambda: _next_graph_stream_item(graph_iterator, stream_kind=stream_kind))
                        if not has_item:
                            break
                        if record.abort_event.is_set():
                            logger.info("Run %s abort requested — stopping", run_id)
                            break
                        if single_mode == "tasks" and graph_task_timing_enabled:
                            _observe_graph_tasks(chunk, stream_kind=stream_kind)
                        llm_error_fallback_message = llm_error_fallback_message or _extract_llm_error_fallback_message(chunk, pre_existing_message_ids)
                        stream_clarification_seen = stream_clarification_seen or _chunk_has_open_clarification(chunk)
                        sse_event = _lg_mode_to_sse_event(single_mode)
                        await message_batch.publish(sse_event, serialize(chunk, mode=single_mode), single_mode)
                        last_graph_event_at = time.monotonic()
                        if single_mode == "custom":
                            await subagent_events.add(chunk)
                    return last_graph_event_at
                # Multiple modes or subgraphs: astream yields tuples
                graph_stream = agent.astream(
                    input_payload,
                    config=stream_config,
                    stream_mode=lg_modes,
                    subgraphs=stream_subgraphs,
                )
                graph_iterator = aiter(graph_stream)
                while True:
                    has_item, item = await message_batch.next_item(lambda: _next_graph_stream_item(graph_iterator, stream_kind=stream_kind))
                    if not has_item:
                        break
                    if record.abort_event.is_set():
                        logger.info("Run %s abort requested — stopping", run_id)
                        break

                    mode, chunk = _unpack_stream_item(item, lg_modes, stream_subgraphs)
                    if mode is None:
                        continue
                    if mode == "tasks" and graph_task_timing_enabled:
                        _observe_graph_tasks(chunk, stream_kind=stream_kind)
                        if graph_task_mode_added:
                            continue

                    llm_error_fallback_message = llm_error_fallback_message or _extract_llm_error_fallback_message(chunk, pre_existing_message_ids)
                    stream_clarification_seen = stream_clarification_seen or _chunk_has_open_clarification(chunk)
                    sse_event = _lg_mode_to_sse_event(mode)
                    await message_batch.publish(sse_event, serialize(chunk, mode=mode), mode)
                    last_graph_event_at = time.monotonic()
                    if mode == "custom":
                        await subagent_events.add(chunk)
                return last_graph_event_at
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                try:
                    await message_batch.finish(prior_error=primary_error)
                finally:
                    if graph_task_timing_enabled and message_batch.batch_count:
                        log_run_timing(
                            logger,
                            "agent_stream_bridge_batch_summary",
                            stream_kind=stream_kind,
                            batch_count=message_batch.batch_count,
                            event_count=message_batch.batched_event_count,
                            max_batch_size=message_batch.max_batch_size_observed,
                            source_message_count=message_batch.source_message_count,
                            coalesced_message_count=message_batch.coalesced_message_count,
                        )

        async def _stream_once(
            input_payload: Any,
            stream_config: RunnableConfig,
            *,
            stream_kind: str,
        ) -> None:
            nonlocal stream_clarification_seen
            stream_started_at = time.monotonic()
            log_run_timing(logger, "agent_stream_start", stream_kind=stream_kind)
            try:
                await _stream_once_inner(input_payload, stream_config, stream_kind=stream_kind)
            except asyncio.CancelledError as exc:
                log_run_timing(
                    logger,
                    "agent_stream_end",
                    elapsed_ms=round((time.monotonic() - stream_started_at) * 1000),
                    outcome="cancelled",
                    error_type=type(exc).__name__,
                    stream_kind=stream_kind,
                )
                raise
            except Exception as exc:
                log_run_timing(
                    logger,
                    "agent_stream_end",
                    elapsed_ms=round((time.monotonic() - stream_started_at) * 1000),
                    outcome="error",
                    error_type=type(exc).__name__,
                    stream_kind=stream_kind,
                )
                raise
            else:
                log_run_timing(
                    logger,
                    "agent_stream_end",
                    elapsed_ms=round((time.monotonic() - stream_started_at) * 1000),
                    outcome="completed",
                    stream_kind=stream_kind,
                )

        async def _stream_once_inner(
            input_payload: Any,
            stream_config: RunnableConfig,
            *,
            stream_kind: str,
        ) -> None:
            nonlocal stream_clarification_seen
            stream_task = asyncio.create_task(_consume_stream(input_payload, stream_config, stream_kind=stream_kind))
            event_task = asyncio.create_task(clarification_event.wait())
            try:
                done, _ = await asyncio.wait(
                    {stream_task, event_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if event_task in done and not stream_task.done():
                    stream_clarification_seen = True
                    try:
                        await asyncio.wait_for(asyncio.shield(stream_task), timeout=1.0)
                    except TimeoutError:
                        logger.warning(
                            "Run %s stream did not close after clarification; cancelling stream task",
                            run_id,
                        )
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                else:
                    await stream_task
            finally:
                # asyncio.wait does not propagate parent cancellation to its
                # children. Drain the stream before rollback/finalization can
                # release the checkpoint lock or the Sandbox execution lease.
                if not stream_task.done():
                    stream_task.cancel()
                    try:
                        await stream_task
                    except asyncio.CancelledError:
                        pass
                if not event_task.done():
                    event_task.cancel()
                    try:
                        await event_task
                    except asyncio.CancelledError:
                        pass

        # 7. Stream the requested turn, then optionally continue hidden goal turns.
        # Clear any stale stop_reason before the first (user-visible) turn only.
        # Continuation turns preserve a cap reason from the user turn: a run that
        # hits a cap during the user turn IS capped even if hidden goal-evaluator
        # turns complete cleanly afterward (#4176 review).
        if isinstance(runtime.context, dict):
            runtime.context.pop("stop_reason", None)
        await _stream_once(graph_input, initial_runnable_config, stream_kind="initial")
        pending_clarification = await _resolve_pending_clarification(
            checkpointer=checkpointer,
            thread_id=thread_id,
            stream_clarification_seen=stream_clarification_seen,
        )
        if pending_clarification:
            logger.info("Run %s paused goal continuation for pending clarification", run_id)
            await run_manager.set_status(run_id, RunStatus.success, stop_reason="awaiting_human_input")
            await _publish_stream_end()

        while not pending_clarification and not record.abort_event.is_set() and not llm_error_fallback_message and (journal is None or not journal.had_llm_error_fallback):
            continuation_input = await _prepare_goal_continuation_input(
                bridge=bridge,
                checkpointer=checkpointer,
                thread_id=thread_id,
                run_id=run_id,
                model_name=record.model_name,
                app_config=ctx.app_config,
                evaluator_model_factory=_get_goal_evaluator_model,
                abort_event=record.abort_event,
                user_id=resolve_runtime_user_id(runtime),
                deerflow_trace_id=deerflow_trace_id,
            )
            if continuation_input is None or record.abort_event.is_set():
                break
            await _stream_once(continuation_input, _continuation_runnable_config(), stream_kind="goal")
            pending_clarification = await _resolve_pending_clarification(
                checkpointer=checkpointer,
                thread_id=thread_id,
                stream_clarification_seen=stream_clarification_seen,
            )
            if pending_clarification:
                logger.info("Run %s paused goal continuation for pending clarification", run_id)
                await run_manager.set_status(run_id, RunStatus.success, stop_reason="awaiting_human_input")
                await _publish_stream_end()

        # 8. Final status
        if record.abort_event.is_set():
            await run_manager.set_finalizing(run_id, True)
            action = record.abort_action
            if action == "rollback":
                await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
                try:
                    await _rollback_to_pre_run_checkpoint(
                        checkpointer=checkpointer,
                        thread_id=thread_id,
                        run_id=run_id,
                        pre_run_checkpoint_id=pre_run_checkpoint_id,
                        pre_run_snapshot=pre_run_snapshot,
                        snapshot_capture_failed=snapshot_capture_failed,
                    )
                    logger.info("Run %s rolled back to pre-run checkpoint %s", run_id, pre_run_checkpoint_id)
                except Exception:
                    logger.warning("Failed to rollback checkpoint for run %s", run_id, exc_info=True)
            else:
                await run_manager.set_status(run_id, RunStatus.interrupted)
        elif llm_error_fallback_message or (journal is not None and journal.had_llm_error_fallback):
            error_msg = llm_error_fallback_message
            if error_msg is None and journal is not None:
                error_msg = journal.llm_error_fallback_message
            error_msg = error_msg or "LLM provider failed after retries"
            await run_manager.set_status(run_id, RunStatus.error, error=error_msg)
        elif pending_clarification:
            # Status and stream end were published when the open request was detected.
            pass
        else:
            runtime_context = runtime.context if isinstance(runtime.context, dict) else None
            # Guard middlewares that hard-stop a run by stripping tool_calls
            # stamp stop_reason into runtime.context so the worker can surface
            # it on the run record:
            #   loop_detection      -> "loop_capped"
            #   token_budget        -> "token_capped"
            #   safety_finish_reason -> "safety_capped"
            #   subagent_limit       -> "subagent_limit_capped"
            #
            # If more guards grow stop_reason semantics, consider a publish/
            # collect pattern (e.g. each guard middleware publishes its cap
            # reason to a dedicated runtime.context channel, and the worker
            # collects the most severe / first / all reasons) instead of each
            # guard writing directly to the same key.
            stop_reason = runtime_context.get("stop_reason") if runtime_context is not None else None
            await run_manager.set_status(run_id, RunStatus.success, stop_reason=stop_reason)

    except asyncio.CancelledError:
        await run_manager.set_finalizing(run_id, True)
        action = record.abort_action
        if action == "rollback":
            await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
            try:
                await _rollback_to_pre_run_checkpoint(
                    checkpointer=checkpointer,
                    thread_id=thread_id,
                    run_id=run_id,
                    pre_run_checkpoint_id=pre_run_checkpoint_id,
                    pre_run_snapshot=pre_run_snapshot,
                    snapshot_capture_failed=snapshot_capture_failed,
                )
                logger.info("Run %s was cancelled and rolled back", run_id)
            except Exception:
                logger.warning("Run %s cancellation rollback failed", run_id, exc_info=True)
        else:
            await run_manager.set_status(run_id, RunStatus.interrupted)
            logger.info("Run %s was cancelled", run_id)

    except GraphRecursionError as exc:
        checkpoint_tuple = None
        if checkpointer is not None:
            try:
                checkpoint_tuple = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
            except Exception:
                logger.warning("Could not inspect the last checkpoint after recursion cap for run %s", run_id, exc_info=True)
        recursion_limit = config.get("recursion_limit")
        error_msg = _recursion_limit_diagnostic(checkpoint_tuple, recursion_limit, exc)
        logger.exception("Run %s reached the LangGraph recursion cap: %s", run_id, error_msg)
        await run_manager.set_status(run_id, RunStatus.error, error=error_msg, stop_reason="recursion_capped")
        await bridge.publish(
            run_id,
            "error",
            {
                "message": error_msg,
                "name": type(exc).__name__,
                "stop_reason": "recursion_capped",
            },
        )

    except Exception as exc:
        error_msg = f"{exc}"
        logger.exception("Run %s failed: %s", run_id, error_msg)
        await run_manager.set_status(run_id, RunStatus.error, error=error_msg)
        await bridge.publish(
            run_id,
            "error",
            {
                "message": error_msg,
                "name": type(exc).__name__,
            },
        )

    finally:
        if run_sandbox_lease is not None:
            sandbox_id, lock_file = run_sandbox_lease
            try:
                run_sandbox_provider.release_execution_lease(lock_file, sandbox_id)
            except Exception:
                logger.exception("Failed to release Gateway Run Sandbox execution lease for %s", sandbox_id)
        finalization_started_at = time.monotonic()
        log_run_timing(logger, "run_finalize_start")
        if weknora_token_context is not None:
            reset_current_weknora_access_token(weknora_token_context)
        if weknora_scope_context is not None:
            reset_current_weknora_scope(weknora_scope_context)

        # Persist any subagent step events still buffered (#3779) — including on
        # abort/exception paths, where the stream loop broke before its own flush.
        if subagent_events is not None:
            await subagent_events.flush()

        if event_store is not None and pre_run_workspace_snapshot is not None:
            try:
                await record_workspace_changes(
                    event_store,
                    thread_id,
                    run_id,
                    pre_run_workspace_snapshot,
                    user_id=workspace_changes_user_id,
                )
            except Exception:
                logger.warning("Failed to record workspace changes for run %s", run_id, exc_info=True)

        # Provider callbacks can outpace checkpoint/middleware finalization. On
        # interruption, reconcile any uncommitted artifact claim before flushing
        # journal rows so history cannot preserve a fabricated download link.
        if journal is not None and record.status == RunStatus.interrupted:
            await _reconcile_interrupted_artifact_claim(
                journal=journal,
                checkpointer=checkpointer,
                thread_id=thread_id,
            )

        # Flush any buffered journal events and persist completion data
        if journal is not None:
            try:
                await _time_run_stage("journal_flush", journal.flush)
            except Exception:
                logger.warning("Failed to flush journal for run %s", run_id, exc_info=True)

            try:
                # Persist token usage + convenience fields to RunStore
                async def _persist_completion() -> None:
                    completion = journal.get_completion_data()
                    await run_manager.update_run_completion(run_id, status=record.status.value, **completion)

                await _time_run_stage("run_completion_persist", _persist_completion)
            except Exception:
                logger.warning("Failed to persist run completion for %s (non-fatal)", run_id, exc_info=True)

        if checkpointer is not None and record.status == RunStatus.interrupted:
            try:
                await run_manager.wait_for_prior_finalizing(thread_id, run_id)
                if not await run_manager.has_later_started_run(thread_id, run_id):
                    await _ensure_interrupted_title(checkpointer=checkpointer, thread_id=thread_id, app_config=ctx.app_config, graph_input=graph_input)
            except Exception:
                logger.debug("Failed to generate interrupted title for thread %s (non-fatal)", thread_id)

        # Sync title from checkpoint to threads_meta.display_name
        if checkpointer is not None and thread_store is not None:
            try:

                async def _sync_title() -> None:
                    ckpt_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                    ckpt_tuple = await checkpointer.aget_tuple(ckpt_config)
                    if ckpt_tuple is not None:
                        ckpt = getattr(ckpt_tuple, "checkpoint", {}) or {}
                        title = ckpt.get("channel_values", {}).get("title")
                        if title:
                            await thread_store.update_display_name(thread_id, title)

                await _time_run_stage("title_sync", _sync_title)
            except Exception:
                logger.debug("Failed to sync title for thread %s (non-fatal)", thread_id)

        # Persist run duration to checkpoint metadata so history reads
        # don't need to correlate runs and events.
        if checkpointer is not None and record.status == RunStatus.success:
            try:
                created = datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))
                updated = datetime.fromisoformat(record.updated_at.replace("Z", "+00:00"))
                # Match legacy history semantics: turn_duration is the whole
                # RunRecord lifetime in integer seconds, including admission
                # delay. Persist zero for sub-second successful turns.
                duration = max(0, int((updated - created).total_seconds()))
                await _time_run_stage(
                    "duration_persist",
                    lambda: _persist_run_duration(
                        checkpointer=checkpointer,
                        thread_id=thread_id,
                        run_id=run_id,
                        duration_seconds=duration,
                    ),
                )
            except Exception:
                logger.debug("Failed to persist run duration for thread %s run %s (non-fatal)", thread_id, run_id)

        # Update threads_meta status based on run outcome
        if thread_store is not None:
            try:
                final_status = "idle" if record.status == RunStatus.success else record.status.value
                await _time_run_stage("thread_status_update", lambda: thread_store.update_status(thread_id, final_status))
            except Exception:
                logger.debug("Failed to update thread_meta status for %s (non-fatal)", thread_id)

        if ctx.on_run_completed is not None:
            try:
                await _time_run_stage("on_run_completed", lambda: ctx.on_run_completed(record))
            except Exception:
                logger.warning("Run completion hook failed for %s (non-fatal)", run_id, exc_info=True)
        if record.finalizing:
            await run_manager.set_finalizing(run_id, False)

        await _publish_stream_end()
        log_run_timing(
            logger,
            "run_finalize_end",
            elapsed_ms=round((time.monotonic() - finalization_started_at) * 1000),
            outcome="completed",
        )
        asyncio.create_task(bridge.cleanup(run_id, delay=60))
        tool_timing = consume_tool_timing(thread_id=thread_id, run_id=run_id)
        checkpoint_timing = consume_checkpoint_timing(thread_id=thread_id, run_id=run_id)
        log_run_timing(
            logger,
            "run_end",
            elapsed_ms=round((time.monotonic() - run_started_at) * 1000),
            status=record.status.value,
            worker_pid=os.getpid(),
            sandbox_tool_execution_total_ms=tool_timing.sandbox_tool_execution_total_ms,
            sandbox_tool_acquire_overlap_ms=tool_timing.sandbox_tool_acquire_overlap_ms,
            sandbox_tool_count=tool_timing.sandbox_tool_count,
            sandbox_tool_noncompleted_count=tool_timing.sandbox_tool_noncompleted_count,
            non_sandbox_tool_total_ms=tool_timing.non_sandbox_tool_total_ms,
            non_sandbox_tool_count=tool_timing.non_sandbox_tool_count,
            non_sandbox_tool_noncompleted_count=tool_timing.non_sandbox_tool_noncompleted_count,
            checkpoint_aput_count=checkpoint_timing.checkpoint_aput_count,
            checkpoint_aput_total_ms=checkpoint_timing.checkpoint_aput_total_ms,
            checkpoint_aput_max_ms=checkpoint_timing.checkpoint_aput_max_ms,
            checkpoint_aput_noncompleted_count=checkpoint_timing.checkpoint_aput_noncompleted_count,
            checkpoint_aput_inflight_max=checkpoint_timing.checkpoint_aput_inflight_max,
            checkpoint_aput_writes_count=checkpoint_timing.checkpoint_aput_writes_count,
            checkpoint_aput_writes_total_ms=checkpoint_timing.checkpoint_aput_writes_total_ms,
            checkpoint_aput_writes_max_ms=checkpoint_timing.checkpoint_aput_writes_max_ms,
            checkpoint_aput_writes_noncompleted_count=checkpoint_timing.checkpoint_aput_writes_noncompleted_count,
            checkpoint_aput_writes_inflight_max=checkpoint_timing.checkpoint_aput_writes_inflight_max,
            checkpoint_pool_acquire_count=checkpoint_timing.checkpoint_pool_acquire_count,
            checkpoint_pool_acquire_total_ms=checkpoint_timing.checkpoint_pool_acquire_total_ms,
            checkpoint_pool_acquire_max_ms=checkpoint_timing.checkpoint_pool_acquire_max_ms,
            checkpoint_pool_acquire_noncompleted_count=checkpoint_timing.checkpoint_pool_acquire_noncompleted_count,
            checkpoint_pool_acquire_inflight_max=checkpoint_timing.checkpoint_pool_acquire_inflight_max,
            checkpoint_pool_hold_count=checkpoint_timing.checkpoint_pool_hold_count,
            checkpoint_pool_hold_total_ms=checkpoint_timing.checkpoint_pool_hold_total_ms,
            checkpoint_pool_hold_max_ms=checkpoint_timing.checkpoint_pool_hold_max_ms,
            checkpoint_pool_hold_noncompleted_count=checkpoint_timing.checkpoint_pool_hold_noncompleted_count,
            checkpoint_pool_hold_inflight_max=checkpoint_timing.checkpoint_pool_hold_inflight_max,
            checkpoint_sql_execute_count=checkpoint_timing.checkpoint_sql_execute_count,
            checkpoint_sql_execute_total_ms=checkpoint_timing.checkpoint_sql_execute_total_ms,
            checkpoint_sql_execute_max_ms=checkpoint_timing.checkpoint_sql_execute_max_ms,
            checkpoint_sql_execute_noncompleted_count=checkpoint_timing.checkpoint_sql_execute_noncompleted_count,
            checkpoint_sql_execute_inflight_max=checkpoint_timing.checkpoint_sql_execute_inflight_max,
        )
        reset_current_llm_timing_context(llm_timing_context_token)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _checkpoint_id(checkpoint_tuple: Any) -> str | None:
    config = getattr(checkpoint_tuple, "config", {}) or {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    checkpoint_id = configurable.get("checkpoint_id") if isinstance(configurable, dict) else None
    if isinstance(checkpoint_id, str):
        return checkpoint_id
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    if isinstance(checkpoint, dict) and isinstance(checkpoint.get("id"), str):
        return checkpoint["id"]
    return None


def _goal_instance_matches(left: GoalState | None, right: GoalState | None) -> bool:
    if not left or not right:
        return False
    same_status = left.get("status") == right.get("status") == "active"
    same_objective = left.get("objective") == right.get("objective")
    same_created_at = left.get("created_at") == right.get("created_at")
    return same_status and same_objective and same_created_at


def _read_checkpoint_messages(checkpoint_tuple: Any) -> list[Any]:
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    messages = channel_values.get("messages", []) if isinstance(channel_values, dict) else []
    return messages if isinstance(messages, list) else []


def _recursion_limit_diagnostic(checkpoint_tuple: Any | None, recursion_limit: object, error: Exception) -> str:
    """Describe the last durable action when LangGraph exhausts its step budget."""
    messages = _read_checkpoint_messages(checkpoint_tuple) if checkpoint_tuple is not None else []
    last = messages[-1] if messages else None
    message_type = getattr(last, "type", "unknown") if last is not None else "none"
    tool_names = [str(call.get("name", "?")) for call in (getattr(last, "tool_calls", None) or []) if isinstance(call, dict)] if last is not None else []
    action = ", ".join(tool_names) if tool_names else message_type
    limit = recursion_limit if isinstance(recursion_limit, int) else "configured"
    return (
        f"Agent execution reached the LangGraph recursion limit ({limit}) before a stop condition. "
        f"The last durable checkpoint contains {len(messages)} messages; last action: {action}. "
        "This can indicate either a convergence loop or an insufficient super-step budget for a "
        "progressing multi-stage graph; inspect the durable checkpoint and tool progress to distinguish them. "
        "This is not a successful completion. "
        f"Underlying error: {error}"
    )


def _read_checkpoint_goal(checkpoint_tuple: Any) -> GoalState | None:
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    raw_goal = channel_values.get("goal") if isinstance(channel_values, dict) else None
    return copy.deepcopy(raw_goal) if isinstance(raw_goal, dict) else None


def _has_durable_goal_turn_receipt(checkpoint_tuple: Any, messages: list[Any]) -> bool:
    """Return true when a completed visible assistant turn is safely checkpointed.

    ``pending_writes`` is the durability signal: a ``CheckpointTuple`` carries no
    ``tasks`` field (those live on a ``StateSnapshot``), so the presence of any
    queued writes is what tells us the turn is still in flight.
    """
    if _checkpoint_id(checkpoint_tuple) is None:
        return False
    if getattr(checkpoint_tuple, "pending_writes", None):
        return False
    visible_messages = []
    for message in messages:
        if _is_visible_message(message) and message_to_text(message).strip():
            visible_messages.append(message)
    if not visible_messages:
        return False
    return _message_type(visible_messages[-1]) == "ai"


def _stand_down_reason(goal: GoalState, evaluation: GoalEvaluation, no_progress_count: int) -> str | None:
    if evaluation["satisfied"]:
        return None
    if evaluation["blocker"] != "goal_not_met_yet":
        return f"blocked:{evaluation['blocker']}"
    # Default caps mirror should_continue_goal so the two gate functions agree on
    # a goal dict that is missing these fields.
    if int(goal.get("continuation_count", 0)) >= int(goal.get("max_continuations", DEFAULT_MAX_GOAL_CONTINUATIONS)):
        return "max_continuations_reached"
    if no_progress_count >= int(goal.get("max_no_progress_continuations", DEFAULT_MAX_NO_PROGRESS_CONTINUATIONS)):
        return "no_progress_detected"
    return None


async def _persist_goal_evaluation(
    *,
    bridge: StreamBridge,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    goal: GoalState,
    evaluation: GoalEvaluation,
    no_progress_count: int,
    continuation_count: int | None = None,
    stand_down_reason: str | None = None,
    evidence_signature: str = "",
) -> GoalState | None:
    try:
        async with goal_thread_lock(thread_id):
            checkpoint_tuple = await _call_checkpointer_method(
                checkpointer,
                "aget_tuple",
                "get_tuple",
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            )
            if checkpoint_tuple is None:
                return None
            current_goal = _read_checkpoint_goal(checkpoint_tuple)
            if current_goal is None or not _goal_instance_matches(goal, current_goal):
                return None
            # Defensive: compute continuation_count from the fresh current_goal
            # inside the lock.  The caller computed it from a possibly-stale goal
            # snapshot; a racing continuation may have already bumped the count.
            if continuation_count is not None:
                current_count = int(current_goal.get("continuation_count", 0))
                continuation_count = max(continuation_count, current_count + 1)
            expected_checkpoint_id = _checkpoint_id(checkpoint_tuple)
            updated_goal = attach_goal_evaluation(
                current_goal,
                evaluation,
                run_id=run_id,
                continuation_count=continuation_count,
                no_progress_count=no_progress_count,
                stand_down_reason=stand_down_reason,
                evidence_signature=evidence_signature,
            )
            values = await write_thread_goal(
                checkpointer,
                thread_id,
                updated_goal,
                as_node="goal_evaluator",
                expected_checkpoint_id=expected_checkpoint_id,
            )
        await bridge.publish(run_id, "values", serialize(values, mode="values"))
        return updated_goal
    except GoalWriteConflict:
        return None
    except Exception:
        logger.warning("Could not persist goal evaluation for thread %s", thread_id, exc_info=True)
        return None


async def _reread_goal_and_checkpoint(checkpointer: Any, thread_id: str) -> tuple[GoalState | None, Any]:
    """Re-read the goal and latest checkpoint together for a concurrency re-check."""
    goal = await read_thread_goal(checkpointer, thread_id)
    checkpoint_tuple = await _time_run_stage(
        "goal_checkpoint_read",
        lambda: _call_checkpointer_method(
            checkpointer,
            "aget_tuple",
            "get_tuple",
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        ),
    )
    return goal, checkpoint_tuple


async def _prepare_goal_continuation_input(
    *,
    bridge: StreamBridge,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    model_name: str | None,
    app_config: AppConfig | None,
    evaluator_model_factory: Any | None = None,
    abort_event: asyncio.Event | None = None,
    user_id: str | None = None,
    deerflow_trace_id: str | None = None,
) -> dict[str, Any] | None:
    """Evaluate the active goal and return a hidden continuation input if needed.

    NOTE: The re-reads below catch a racing user message or ``/goal clear``
    before we queue a continuation. Goal writes then serialize per thread and
    pass the checkpoint id they read from, so stale evaluator writes stand down
    instead of clobbering a newer goal change.
    """
    if checkpointer is None:
        return None
    if abort_event is not None and abort_event.is_set():
        return None

    try:
        goal = await read_thread_goal(checkpointer, thread_id)
    except Exception:
        logger.warning("Could not read goal for thread %s after run %s", thread_id, run_id, exc_info=True)
        return None
    if not goal or goal.get("status") != "active":
        return None

    async def _persist(
        goal: GoalState,
        evaluation: GoalEvaluation,
        no_progress_count: int,
        *,
        stand_down_reason: str | None = None,
        continuation_count: int | None = None,
    ) -> GoalState | None:
        """Record the evaluation against the still-current goal instance."""
        return await _persist_goal_evaluation(
            bridge=bridge,
            checkpointer=checkpointer,
            thread_id=thread_id,
            run_id=run_id,
            goal=goal,
            evaluation=evaluation,
            no_progress_count=no_progress_count,
            continuation_count=continuation_count,
            stand_down_reason=stand_down_reason,
            evidence_signature=evidence_signature,
        )

    try:
        checkpoint_tuple = await _time_run_stage(
            "goal_checkpoint_read",
            lambda: _call_checkpointer_method(
                checkpointer,
                "aget_tuple",
                "get_tuple",
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            ),
        )
        if checkpoint_tuple is None:
            return None
        checkpoint_id_before = _checkpoint_id(checkpoint_tuple)
        messages = _read_checkpoint_messages(checkpoint_tuple)
        conversation_signature_before = visible_conversation_signature(messages)
        evidence_signature = latest_visible_assistant_signature(messages)

        if not _has_durable_goal_turn_receipt(checkpoint_tuple, messages):
            evaluation = GoalEvaluation(
                satisfied=False,
                blocker="run_failed",
                reason="No durable assistant end-of-turn receipt was available.",
                evidence_summary="",
            )
            no_progress_count = compute_no_progress_count(goal, evaluation, evidence_signature=evidence_signature)
            await _persist(goal, evaluation, no_progress_count, stand_down_reason="no_durable_end_of_turn")
            return None

        if abort_event is not None and abort_event.is_set():
            return None
        evaluator_model = evaluator_model_factory() if evaluator_model_factory is not None else None
        goal_evaluation_started_at = time.monotonic()
        log_run_timing(logger, "goal_evaluate_start")
        try:
            evaluation = await evaluate_goal_completion(
                goal,
                messages,
                model=evaluator_model,
                model_name=model_name,
                app_config=app_config,
                thread_id=thread_id,
                user_id=user_id,
                deerflow_trace_id=deerflow_trace_id,
            )
        except asyncio.CancelledError as exc:
            log_run_timing(
                logger,
                "goal_evaluate_end",
                elapsed_ms=round((time.monotonic() - goal_evaluation_started_at) * 1000),
                outcome="cancelled",
                error_type=type(exc).__name__,
            )
            raise
        except Exception as exc:
            log_run_timing(
                logger,
                "goal_evaluate_end",
                elapsed_ms=round((time.monotonic() - goal_evaluation_started_at) * 1000),
                outcome="error",
                error_type=type(exc).__name__,
            )
            raise
        log_run_timing(
            logger,
            "goal_evaluate_end",
            elapsed_ms=round((time.monotonic() - goal_evaluation_started_at) * 1000),
            outcome="completed",
        )
        if abort_event is not None and abort_event.is_set():
            return None
    except Exception:
        logger.warning("Goal evaluator failed for thread %s after run %s", thread_id, run_id, exc_info=True)
        return None

    no_progress_count = compute_no_progress_count(goal, evaluation, evidence_signature=evidence_signature)

    # Re-check that neither the goal nor the visible conversation changed while the
    # evaluator ran — a user message or /goal clear racing the evaluation must win.
    try:
        current_goal, current_checkpoint_tuple = await _reread_goal_and_checkpoint(checkpointer, thread_id)
    except Exception:
        logger.warning("Could not re-check goal state for thread %s after evaluation", thread_id, exc_info=True)
        return None

    if not _goal_instance_matches(goal, current_goal) or current_checkpoint_tuple is None:
        return None

    checkpoint_changed = _checkpoint_id(current_checkpoint_tuple) != checkpoint_id_before
    messages_changed = visible_conversation_signature(_read_checkpoint_messages(current_checkpoint_tuple)) != conversation_signature_before
    if checkpoint_changed or messages_changed:
        await _persist(current_goal, evaluation, no_progress_count, stand_down_reason="thread_changed_after_evaluation")
        return None

    if evaluation["satisfied"]:
        try:
            async with goal_thread_lock(thread_id):
                latest_checkpoint_tuple = await _call_checkpointer_method(
                    checkpointer,
                    "aget_tuple",
                    "get_tuple",
                    {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
                )
                if latest_checkpoint_tuple is None:
                    return None
                latest_goal = _read_checkpoint_goal(latest_checkpoint_tuple)
                if latest_goal is None or not _goal_instance_matches(goal, latest_goal):
                    return None
                values = await write_thread_goal(
                    checkpointer,
                    thread_id,
                    None,
                    as_node="goal_evaluator",
                    expected_checkpoint_id=_checkpoint_id(latest_checkpoint_tuple),
                )
            await bridge.publish(run_id, "values", serialize(values, mode="values"))
        except GoalWriteConflict:
            return None
        except Exception:
            logger.warning("Could not clear satisfied goal for thread %s", thread_id, exc_info=True)
        return None

    stand_down_reason = _stand_down_reason(goal, evaluation, no_progress_count)
    if stand_down_reason is not None or not should_continue_goal(goal, evaluation, no_progress_count=no_progress_count):
        await _persist(goal, evaluation, no_progress_count, stand_down_reason=stand_down_reason)
        return None

    next_count = int(goal.get("continuation_count", 0)) + 1
    updated_goal = await _persist(goal, evaluation, no_progress_count, continuation_count=next_count)
    if updated_goal is None:
        return None

    # Final guard: the persist above bumped the checkpoint id, so only the visible
    # conversation signature is meaningful for detecting a racing user turn here.
    try:
        latest_goal, latest_checkpoint_tuple = await _reread_goal_and_checkpoint(checkpointer, thread_id)
    except Exception:
        logger.warning("Could not verify queued goal continuation for thread %s", thread_id, exc_info=True)
        return None
    if not _goal_instance_matches(updated_goal, latest_goal) or latest_checkpoint_tuple is None:
        return None
    if visible_conversation_signature(_read_checkpoint_messages(latest_checkpoint_tuple)) != conversation_signature_before:
        # Do not pass continuation_count here: the persist above already
        # committed it (as next_count). Re-passing next_count would make
        # _persist_goal_evaluation's race guard (#4088) see that same write as
        # a "current_count" bump and add another +1 on top of it, silently
        # double-counting this single continuation attempt against the
        # continuation budget even though it is being stood down, not
        # delivered. Omitting it leaves the already-committed count untouched,
        # matching every other stand-down call site in this function.
        await _persist(
            latest_goal,
            evaluation,
            no_progress_count,
            stand_down_reason="thread_changed_before_continuation",
        )
        return None

    logger.info(
        "Run %s continuing thread %s for active goal (%d/%d)",
        run_id,
        thread_id,
        updated_goal.get("continuation_count", next_count),
        updated_goal.get("max_continuations", 0),
    )
    return {"messages": [make_goal_continuation_message(updated_goal, evaluation)]}


async def _rollback_to_pre_run_checkpoint(
    *,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    pre_run_checkpoint_id: str | None,
    pre_run_snapshot: dict[str, Any] | None,
    snapshot_capture_failed: bool,
) -> None:
    """Restore thread state to the checkpoint snapshot captured before run start."""
    if checkpointer is None:
        logger.info("Run %s rollback requested but no checkpointer is configured", run_id)
        return

    if snapshot_capture_failed:
        logger.warning("Run %s rollback skipped: pre-run checkpoint snapshot capture failed", run_id)
        return

    if pre_run_snapshot is None:
        await _call_checkpointer_method(checkpointer, "adelete_thread", "delete_thread", thread_id)
        logger.info("Run %s rollback reset thread %s to empty state", run_id, thread_id)
        return

    checkpoint_to_restore = None
    metadata_to_restore: dict[str, Any] = {}
    checkpoint_ns = ""
    checkpoint = pre_run_snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        logger.warning("Run %s rollback skipped: invalid pre-run checkpoint snapshot", run_id)
        return
    checkpoint_to_restore = checkpoint
    if checkpoint_to_restore.get("id") is None and pre_run_checkpoint_id is not None:
        checkpoint_to_restore = {**checkpoint_to_restore, "id": pre_run_checkpoint_id}
    if checkpoint_to_restore.get("id") is None:
        logger.warning("Run %s rollback skipped: pre-run checkpoint has no checkpoint id", run_id)
        return
    restore_marker = _new_checkpoint_marker()
    checkpoint_to_restore = {
        **checkpoint_to_restore,
        "id": restore_marker["id"],
        "ts": restore_marker["ts"],
    }
    metadata = pre_run_snapshot.get("metadata", {})
    metadata_to_restore = metadata if isinstance(metadata, dict) else {}
    raw_checkpoint_ns = pre_run_snapshot.get("checkpoint_ns")
    checkpoint_ns = raw_checkpoint_ns if isinstance(raw_checkpoint_ns, str) else ""

    channel_versions = checkpoint_to_restore.get("channel_versions")
    new_versions = dict(channel_versions) if isinstance(channel_versions, dict) else {}

    restore_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
    restored_config = await _call_checkpointer_method(
        checkpointer,
        "aput",
        "put",
        restore_config,
        checkpoint_to_restore,
        metadata_to_restore if isinstance(metadata_to_restore, dict) else {},
        new_versions,
    )
    if not isinstance(restored_config, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config: expected dict")
    restored_configurable = restored_config.get("configurable", {})
    if not isinstance(restored_configurable, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config payload")
    restored_checkpoint_id = restored_configurable.get("checkpoint_id")
    if not restored_checkpoint_id:
        raise RuntimeError(f"Run {run_id} rollback restore did not return checkpoint_id")

    pending_writes = pre_run_snapshot.get("pending_writes", [])
    if not pending_writes:
        return

    writes_by_task: dict[str, list[tuple[str, Any]]] = {}
    for item in pending_writes:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write is not a 3-tuple: {item!r}")
        task_id, channel, value = item
        if not isinstance(channel, str):
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write has non-string channel: task_id={task_id!r}, channel={channel!r}")
        writes_by_task.setdefault(str(task_id), []).append((channel, value))

    for task_id, writes in writes_by_task.items():
        await _call_checkpointer_method(
            checkpointer,
            "aput_writes",
            "put_writes",
            restored_config,
            writes,
            task_id=task_id,
        )


def _new_checkpoint_marker() -> dict[str, str]:
    marker = empty_checkpoint()
    return {"id": marker["id"], "ts": marker["ts"]}


def _bump_channel_version(checkpointer: Any, current_version: Any) -> Any:
    """Return a strictly-different next version for a checkpoint channel.

    DB-backed LangGraph savers (PostgresSaver / v4 SqliteSaver blob layout)
    persist channel blobs keyed by ``channel_versions[<channel>]``, so the
    new value MUST differ from the prior value. We delegate to the
    checkpointer's ``get_next_version`` when available — that is the canonical
    versioning scheme each saver picks (int, monotonic float, or
    UUID-shaped string). When the checkpointer doesn't expose it (or it
    returns ``None``/an unchanged value), fall back to a defensive bump that
    still guarantees inequality.
    """
    get_next_version = getattr(checkpointer, "get_next_version", None)
    if callable(get_next_version):
        try:
            next_version = get_next_version(current_version, None)
        except Exception:
            next_version = None
        if next_version is not None and next_version != current_version:
            return next_version
        # fall through to defensive bump

    if isinstance(current_version, bool):
        # ``bool`` is a subclass of ``int``; treat True/False as 1/0 instead of
        # adding to the boolean itself, which would produce an int anyway but
        # via a path that surprises readers.
        return int(current_version) + 1
    if isinstance(current_version, int):
        return current_version + 1
    if isinstance(current_version, float):
        # Match LangGraph's default float versioning (monotonic increment).
        return current_version + 1.0
    if isinstance(current_version, str):
        try:
            return str(int(current_version) + 1)
        except ValueError:
            return f"{current_version}.1"
    return 1


def _checkpoint_identity(ckpt_tuple: Any | None, checkpoint: dict[str, Any]) -> str | None:
    tuple_config = getattr(ckpt_tuple, "config", {}) or {}
    tuple_configurable = tuple_config.get("configurable", {}) if isinstance(tuple_config, dict) else {}
    if isinstance(tuple_configurable, dict):
        checkpoint_id = tuple_configurable.get("checkpoint_id")
        if isinstance(checkpoint_id, str) and checkpoint_id:
            return checkpoint_id
    checkpoint_id = checkpoint.get("id")
    return checkpoint_id if isinstance(checkpoint_id, str) and checkpoint_id else None


def _checkpoint_namespace(ckpt_tuple: Any | None) -> str:
    tuple_config = getattr(ckpt_tuple, "config", {}) or {}
    tuple_configurable = tuple_config.get("configurable", {}) if isinstance(tuple_config, dict) else {}
    checkpoint_ns = tuple_configurable.get("checkpoint_ns", "") if isinstance(tuple_configurable, dict) else ""
    return checkpoint_ns if isinstance(checkpoint_ns, str) else ""


def _graph_input_messages(graph_input: Any | None) -> list[Any]:
    if not isinstance(graph_input, dict):
        return []
    messages = graph_input.get("messages")
    if isinstance(messages, list):
        return messages
    if isinstance(messages, tuple):
        return list(messages)
    return []


def _title_generation_state(channel_values: dict[str, Any], graph_input: Any | None) -> dict[str, Any]:
    state = dict(channel_values)
    messages = state.get("messages")
    if not messages:
        fallback_messages = _graph_input_messages(graph_input)
        if fallback_messages:
            state["messages"] = fallback_messages
    return state


def valid_duration_entry(run_id: Any, duration_seconds: Any) -> bool:
    """Check that (run_id, duration_seconds) is a well-formed duration entry."""
    return isinstance(run_id, str) and bool(run_id) and isinstance(duration_seconds, int) and not isinstance(duration_seconds, bool)


async def persist_run_durations(
    *,
    checkpointer: Any,
    thread_id: str,
    durations: dict[str, int],
) -> bool:
    """Merge validated run durations into a metadata-only checkpoint.

    Durations accumulate so the history fast path can serve every known turn
    from the latest checkpoint.  Per-entry overhead is negligible (~50 bytes
    per run_id) compared to the messages channel blob written on every graph
    checkpoint, so no pruning is needed.
    """
    updates = {run_id: max(0, duration_seconds) for run_id, duration_seconds in durations.items() if valid_duration_entry(run_id, duration_seconds)}
    if not updates:
        return False

    ckpt_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    async with _checkpoint_thread_lock(thread_id):
        for _attempt in range(3):
            ckpt_tuple = await _call_checkpointer_method(checkpointer, "aget_tuple", "get_tuple", ckpt_config)
            if ckpt_tuple is None:
                return False

            checkpoint = dict(getattr(ckpt_tuple, "checkpoint", {}) or {})
            metadata = dict(getattr(ckpt_tuple, "metadata", {}) or {})
            raw_run_durations = metadata.get("run_durations")
            run_durations = {key: value for key, value in raw_run_durations.items() if valid_duration_entry(key, value)} if isinstance(raw_run_durations, dict) else {}
            changed_durations = {run_id: duration for run_id, duration in updates.items() if run_durations.get(run_id) != duration}
            if not changed_durations:
                return False

            run_durations.update(changed_durations)
            parent_checkpoint_id = _checkpoint_identity(ckpt_tuple, checkpoint)
            latest_tuple = await _call_checkpointer_method(checkpointer, "aget_tuple", "get_tuple", ckpt_config)
            latest_checkpoint = dict(getattr(latest_tuple, "checkpoint", {}) or {}) if latest_tuple is not None else {}
            if _checkpoint_identity(latest_tuple, latest_checkpoint) != parent_checkpoint_id:
                continue

            checkpoint.update(_new_checkpoint_marker())
            metadata["source"] = "update"
            prev_step = metadata.get("step")
            metadata["step"] = (prev_step + 1) if isinstance(prev_step, int) else 1
            metadata["run_durations"] = run_durations
            metadata["writes"] = {"runtime_run_duration": {"run_ids": sorted(changed_durations)}}

            checkpoint_ns = _checkpoint_namespace(ckpt_tuple)
            write_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_checkpoint_id,
                }
            }
            await _call_checkpointer_method(
                checkpointer,
                "aput",
                "put",
                write_config,
                checkpoint,
                metadata,
                {},
            )
            return True
    return False


async def _persist_run_duration(
    *,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    duration_seconds: int,
) -> None:
    """Persist one completed run duration in the thread checkpoint metadata."""
    await persist_run_durations(
        checkpointer=checkpointer,
        thread_id=thread_id,
        durations={run_id: duration_seconds},
    )


async def _ensure_interrupted_title(*, checkpointer: Any, thread_id: str, app_config: AppConfig | None, graph_input: Any | None = None) -> str | None:
    """Persist a local fallback title for interrupted first-turn runs.

    Returns the title that is now persisted (existing or newly written), or
    ``None`` when no checkpoint is available or no title text can be derived.
    Idempotent: re-invoking against a checkpoint that already carries a title
    short-circuits without writing a new checkpoint.
    """
    from deerflow.agents.middlewares.title_middleware import TitleMiddleware

    middleware = TitleMiddleware(app_config=app_config) if app_config is not None else TitleMiddleware()
    ckpt_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    for _attempt in range(3):
        ckpt_tuple = await _call_checkpointer_method(checkpointer, "aget_tuple", "get_tuple", ckpt_config)
        checkpoint = copy.deepcopy(getattr(ckpt_tuple, "checkpoint", {}) or {}) if ckpt_tuple is not None else empty_checkpoint()
        channel_values = dict(checkpoint.get("channel_values", {}) or {})
        existing_title = channel_values.get("title")
        if existing_title:
            return existing_title

        result = middleware._generate_title_result(_title_generation_state(channel_values, graph_input), allow_partial_exchange=True)
        title = result.get("title") if isinstance(result, dict) else None
        if not title:
            return None

        # ``empty_checkpoint()`` creates a fresh id every time; only real tuples
        # carry an identity stable enough for the stale-snapshot comparison.
        base_identity = _checkpoint_identity(ckpt_tuple, checkpoint) if ckpt_tuple is not None else None
        latest_tuple = await _call_checkpointer_method(checkpointer, "aget_tuple", "get_tuple", ckpt_config)
        latest_checkpoint = copy.deepcopy(getattr(latest_tuple, "checkpoint", {}) or {}) if latest_tuple is not None else empty_checkpoint()
        latest_identity = _checkpoint_identity(latest_tuple, latest_checkpoint) if latest_tuple is not None else None
        if base_identity is None:
            if latest_identity is not None:
                continue
        elif latest_identity != base_identity:
            continue

        checkpoint = latest_checkpoint
        channel_values = dict(checkpoint.get("channel_values", {}) or {})
        existing_title = channel_values.get("title")
        if existing_title:
            return existing_title

        channel_values["title"] = title
        marker = _new_checkpoint_marker()
        checkpoint.update({"id": marker["id"], "ts": marker["ts"], "channel_values": channel_values})

        # Bump ``channel_versions["title"]`` and declare the bump in ``new_versions``
        # so DB-backed savers (SqliteSaver v4 / PostgresSaver) actually persist the
        # new blob — those savers strip inline ``channel_values`` from ``put`` and
        # only write blobs for channels listed in ``new_versions``. The legacy
        # single-table sqlite saver ignores ``new_versions`` and inlines the
        # snapshot, so this path is correct for both layouts. Mirrors
        # ``_rollback_to_pre_run_checkpoint`` in the same file.
        channel_versions = dict(checkpoint.get("channel_versions", {}) or {})
        next_title_version = _bump_channel_version(checkpointer, channel_versions.get("title"))
        channel_versions["title"] = next_title_version
        checkpoint["channel_versions"] = channel_versions

        metadata = dict(getattr(latest_tuple, "metadata", {}) or {})
        metadata["source"] = "update"
        prev_step = metadata.get("step")
        metadata["step"] = (prev_step + 1) if isinstance(prev_step, int) else 1
        metadata["writes"] = {"runtime_interrupt_title": {"title": title}}

        checkpoint_ns = _checkpoint_namespace(latest_tuple)
        write_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
        await _call_checkpointer_method(
            checkpointer,
            "aput",
            "put",
            write_config,
            checkpoint,
            metadata,
            {"title": next_title_version},
        )
        return title

    return None


def _lg_mode_to_sse_event(mode: str) -> str:
    """Map LangGraph internal stream_mode name to SSE event name.

    LangGraph's ``astream(stream_mode="messages")`` produces message
    tuples.  The SSE protocol calls this ``messages-tuple`` when the
    client explicitly requests it, but the default SSE event name used
    by LangGraph Platform is simply ``"messages"``.
    """
    # All LG modes map 1:1 to SSE event names — "messages" stays "messages"
    return mode


def _error_fallback_message_from_metadata(metadata: dict[str, Any], content: Any) -> str:
    detail = metadata.get("error_detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    reason = metadata.get("error_reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    if isinstance(content, str) and content.strip():
        return content.strip()[:2000]
    return "LLM provider failed after retries"


def _message_id(obj: Any) -> str | None:
    """Best-effort extraction of a stable message id from a message-like object."""
    msg_id = getattr(obj, "id", None)
    if isinstance(msg_id, str) and msg_id:
        return msg_id
    if isinstance(obj, dict):
        raw = obj.get("id")
        if isinstance(raw, str) and raw:
            return raw
    return None


def _try_extract_from_message(obj: Any, pre_existing_ids: set[str] | None = None) -> str | None:
    """Try to extract fallback marker from a single message object or dict.

    Messages whose id appears in ``pre_existing_ids`` are skipped — those are
    history checkpointed by a *prior* run on this thread and any fallback
    marker on them was already accounted for when that earlier run finished.
    Without this filter, a single past run that ended with a fallback marker
    would mark every subsequent run on the same thread as ``error``, because
    LangGraph replays the full message history through ``stream_mode="values"``.
    """
    if pre_existing_ids:
        msg_id = _message_id(obj)
        if msg_id is not None and msg_id in pre_existing_ids:
            return None

    additional_kwargs = getattr(obj, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict) and additional_kwargs.get("deerflow_error_fallback"):
        return _error_fallback_message_from_metadata(additional_kwargs, getattr(obj, "content", None))

    if isinstance(obj, dict):
        nested_kwargs = obj.get("additional_kwargs")
        if isinstance(nested_kwargs, dict) and nested_kwargs.get("deerflow_error_fallback"):
            return _error_fallback_message_from_metadata(nested_kwargs, obj.get("content"))
    return None


def _extract_llm_error_fallback_message(value: Any, pre_existing_ids: set[str] | None = None) -> str | None:
    """Find LLM fallback markers in streamed LangGraph chunks.

    Error fallback messages returned by model-call middleware are not guaranteed
    to pass through LLM end callbacks, but they do appear in graph state chunks.

    Messages whose id appears in ``pre_existing_ids`` are ignored — they are
    history from prior runs on the same thread (LangGraph replays the full
    messages channel in ``stream_mode="values"`` chunks), and any error
    fallback in that history was already resolved when its run finished.
    """
    # Fast path: large state chunks produced by stream_mode="values" have a
    # top-level "messages" list. Scanning only that list avoids expensive deep
    # recursion into large state dicts.
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, (list, tuple)):
            for msg in messages:
                result = _try_extract_from_message(msg, pre_existing_ids)
                if result is not None:
                    return result
            # Fallback marker is attached to an AI message in the messages
            # channel; it will never appear elsewhere in a values chunk.
            return None
        # No top-level "messages" — this is likely an "updates" chunk (small
        # dict keyed by node name). Fall through to deep walk, which is cheap
        # for these payloads.

    # Deep walk for updates / messages / tuple / list modes. Payloads are
    # small, so full recursion is acceptable here.
    seen: set[int] = set()

    def walk(obj: Any) -> str | None:
        oid = id(obj)
        if oid in seen:
            return None
        seen.add(oid)

        result = _try_extract_from_message(obj, pre_existing_ids)
        if result is not None:
            return result

        if isinstance(obj, dict):
            for item in obj.values():
                result = walk(item)
                if result is not None:
                    return result
            return None

        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                result = walk(item)
                if result is not None:
                    return result
        return None

    return walk(value)


def _collect_pre_existing_message_ids(snapshot: dict[str, Any] | None) -> set[str]:
    """Pull stable message ids out of a pre-run checkpoint snapshot.

    Used by :func:`run_agent` to mask stale ``deerflow_error_fallback`` markers
    on history messages so they don't trip the current run's failure path. A
    missing or malformed snapshot yields an empty set (best-effort — we
    intentionally never raise from this helper).
    """
    if not isinstance(snapshot, dict):
        return set()
    checkpoint = snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return set()
    channel_values = checkpoint.get("channel_values")
    if not isinstance(channel_values, dict):
        return set()
    messages = channel_values.get("messages")
    if not isinstance(messages, (list, tuple)):
        return set()
    ids: set[str] = set()
    for msg in messages:
        msg_id = _message_id(msg)
        if msg_id is not None:
            ids.add(msg_id)
    return ids


def _unpack_stream_item(
    item: Any,
    lg_modes: list[str],
    stream_subgraphs: bool,
) -> tuple[str | None, Any]:
    """Unpack a multi-mode or subgraph stream item into (mode, chunk).

    Returns ``(None, None)`` if the item cannot be parsed.
    """
    if stream_subgraphs:
        if isinstance(item, tuple) and len(item) == 3:
            _ns, mode, chunk = item
            return str(mode), chunk
        if isinstance(item, tuple) and len(item) == 2:
            mode, chunk = item
            return str(mode), chunk
        return None, None

    if isinstance(item, tuple) and len(item) == 2:
        mode, chunk = item
        return str(mode), chunk

    # Fallback: single-element output from first mode
    return lg_modes[0] if lg_modes else None, item
