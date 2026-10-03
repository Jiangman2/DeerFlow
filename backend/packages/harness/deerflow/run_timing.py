"""Content-free, run-correlated timing logs for Gateway diagnostics.

The model adapter, sandbox provider, and tool middleware are intentionally
separate layers.  This helper gives them one opt-in log format without passing
request content, tool arguments, file paths, URLs, or credentials between
those layers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, Final

from deerflow.llm_timing_context import get_current_llm_timing_context

RUN_TIMING_LOGS_ENV: Final[str] = "DEERFLOW_RUN_TIMING_LOGS"
CHECKPOINT_TIMING_SLOW_MS_ENV: Final[str] = "DEERFLOW_CHECKPOINTER_TIMING_SLOW_MS"
CHECKPOINT_DB_SESSION_LABELS_ENV: Final[str] = "DEERFLOW_CHECKPOINTER_DB_SESSION_LABELS"
DEFAULT_CHECKPOINT_TIMING_SLOW_MS: Final[int] = 1000
_SAFE_TOKEN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SANDBOX_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {"bash", "ls", "glob", "grep", "read_file", "write_file", "str_replace"}
)


@dataclass(slots=True)
class ToolTimingTotals:
    """Content-free aggregate split by Sandbox versus Gateway tool execution."""

    sandbox_tool_count: int = 0
    sandbox_tool_execution_total_ms: int = 0
    sandbox_tool_acquire_overlap_ms: int = 0
    sandbox_tool_noncompleted_count: int = 0
    non_sandbox_tool_count: int = 0
    non_sandbox_tool_total_ms: int = 0
    non_sandbox_tool_noncompleted_count: int = 0
    sandbox_acquire_intervals: list[tuple[float, float]] | None = None


@dataclass(slots=True)
class CheckpointTimingTotals:
    """Content-free PostgreSQL checkpointer timings accumulated per run."""

    checkpoint_aput_count: int = 0
    checkpoint_aput_total_ms: int = 0
    checkpoint_aput_max_ms: int = 0
    checkpoint_aput_noncompleted_count: int = 0
    checkpoint_aput_inflight_max: int = 0
    checkpoint_aput_writes_count: int = 0
    checkpoint_aput_writes_total_ms: int = 0
    checkpoint_aput_writes_max_ms: int = 0
    checkpoint_aput_writes_noncompleted_count: int = 0
    checkpoint_aput_writes_inflight_max: int = 0
    checkpoint_pool_acquire_count: int = 0
    checkpoint_pool_acquire_total_ms: int = 0
    checkpoint_pool_acquire_max_ms: int = 0
    checkpoint_pool_acquire_noncompleted_count: int = 0
    checkpoint_pool_acquire_inflight_max: int = 0
    checkpoint_pool_hold_count: int = 0
    checkpoint_pool_hold_total_ms: int = 0
    checkpoint_pool_hold_max_ms: int = 0
    checkpoint_pool_hold_noncompleted_count: int = 0
    checkpoint_pool_hold_inflight_max: int = 0
    checkpoint_sql_execute_count: int = 0
    checkpoint_sql_execute_total_ms: int = 0
    checkpoint_sql_execute_max_ms: int = 0
    checkpoint_sql_execute_noncompleted_count: int = 0
    checkpoint_sql_execute_inflight_max: int = 0


@dataclass(frozen=True, slots=True)
class _CheckpointTimingToken:
    """Run-scoped, content-free in-flight ownership for one checkpoint call."""

    thread_id: str
    run_id: str
    operation: str
    inflight_at_start: int


_tool_timing_totals: dict[tuple[str, str], ToolTimingTotals] = {}
_tool_timing_totals_lock = threading.Lock()
_checkpoint_timing_totals: dict[tuple[str, str], CheckpointTimingTotals] = {}
_checkpoint_timing_totals_lock = threading.Lock()
_checkpoint_timing_inflight: dict[tuple[str, str, str], int] = {}
_timing_logger = logging.getLogger(__name__)
_CHECKPOINT_OPERATION_PREFIXES: Final[dict[str, str]] = {
    "aput": "checkpoint_aput",
    "aput_writes": "checkpoint_aput_writes",
    "pool_acquire": "checkpoint_pool_acquire",
    "pool_hold": "checkpoint_pool_hold",
    "sql_execute": "checkpoint_sql_execute",
}


def run_timing_logs_enabled() -> bool:
    """Return whether content-free run-stage timing logs are enabled."""

    return os.getenv(RUN_TIMING_LOGS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def checkpoint_db_session_labels_enabled() -> bool:
    """Return whether pooled checkpointer sessions carry an opt-in Run label."""

    return os.getenv(CHECKPOINT_DB_SESSION_LABELS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def checkpoint_db_session_label() -> str | None:
    """Return a bounded, content-free PostgreSQL ``application_name`` label.

    A label is written only while the diagnostic flag is enabled. It lets a
    database-side ``pg_stat_activity`` sample correlate an active pooled
    checkpointer session to the DeerFlow Run already present in opt-in timing
    logs. The label intentionally excludes user, thread, SQL, and checkpoint
    state data.
    """

    if not checkpoint_db_session_labels_enabled():
        return None
    context = get_current_llm_timing_context()
    if context is None:
        return "dfcp:system"
    run_id = _safe_token(context.run_id)
    # PostgreSQL limits application_name to NAMEDATALEN - 1 (normally 63
    # bytes). DeerFlow Run UUIDs fit intact; the slice makes malformed custom
    # IDs safe without relying on PostgreSQL's silent truncation.
    return "dfcp:system" if run_id == "-" else f"dfcp:{run_id[:58]}"


def _checkpoint_timing_slow_ms() -> int:
    """Return a positive slow-operation threshold without startup fragility."""

    raw = os.getenv(CHECKPOINT_TIMING_SLOW_MS_ENV)
    try:
        threshold = int(raw) if raw is not None else DEFAULT_CHECKPOINT_TIMING_SLOW_MS
    except ValueError:
        return DEFAULT_CHECKPOINT_TIMING_SLOW_MS
    return threshold if threshold > 0 else DEFAULT_CHECKPOINT_TIMING_SLOW_MS


def _safe_token(value: object) -> str:
    """Return a bounded token suitable for a key=value operational log."""

    rendered = str(value)
    return rendered if _SAFE_TOKEN.fullmatch(rendered) else "-"


def _runtime_timing_ids(runtime: Any) -> tuple[str, str]:
    """Read trusted run IDs from ``ToolRuntime`` with a ContextVar fallback."""

    runtime_context = getattr(runtime, "context", None)
    if isinstance(runtime_context, dict):
        thread_id = _safe_token(runtime_context.get("thread_id"))
        run_id = _safe_token(runtime_context.get("run_id"))
        if thread_id != "-" and run_id != "-":
            return thread_id, run_id

    context = get_current_llm_timing_context()
    if context is None:
        return "-", "-"
    return context.thread_id, context.run_id


def _interval_overlap_ms(started_at: float, ended_at: float, intervals: list[tuple[float, float]]) -> int:
    """Return overlap with the union of intervals, in milliseconds."""

    clipped = sorted(
        (max(started_at, interval_start), min(ended_at, interval_end))
        for interval_start, interval_end in intervals
        if interval_end > started_at and interval_start < ended_at
    )
    if not clipped:
        return 0

    total = 0.0
    active_start, active_end = clipped[0]
    for interval_start, interval_end in clipped[1:]:
        if interval_start <= active_end:
            active_end = max(active_end, interval_end)
        else:
            total += active_end - active_start
            active_start, active_end = interval_start, interval_end
    total += active_end - active_start
    return max(0, round(total * 1000))


def record_sandbox_acquire_timing(*, started_at: float, ended_at: float) -> None:
    """Retain one successful Sandbox-acquisition interval for a Gateway run."""

    if not run_timing_logs_enabled() or ended_at <= started_at:
        return
    context = get_current_llm_timing_context()
    if context is None:
        return

    with _tool_timing_totals_lock:
        totals = _tool_timing_totals.setdefault((context.thread_id, context.run_id), ToolTimingTotals())
        if totals.sandbox_acquire_intervals is None:
            totals.sandbox_acquire_intervals = []
        totals.sandbox_acquire_intervals.append((started_at, ended_at))


def record_tool_timing(
    runtime: Any,
    *,
    tool_name: str,
    elapsed_ms: int,
    outcome: str,
    ended_at: float | None = None,
) -> None:
    """Accumulate one tool invocation without retaining call content.

    The built-in Sandbox API tools are statically classified from their trusted
    registrations. Everything else executes in the Gateway process or through
    an external connector and is counted separately. This avoids inferring an
    execution location from user-controlled tool arguments or mutable runtime
    state.
    """

    if not run_timing_logs_enabled():
        return
    thread_id, run_id = _runtime_timing_ids(runtime)
    if thread_id == "-" or run_id == "-":
        return

    elapsed_ms = max(0, int(elapsed_ms))
    ended_at = time.monotonic() if ended_at is None else ended_at
    started_at = ended_at - elapsed_ms / 1000.0
    with _tool_timing_totals_lock:
        totals = _tool_timing_totals.setdefault((thread_id, run_id), ToolTimingTotals())
        if tool_name in SANDBOX_TOOL_NAMES:
            totals.sandbox_tool_count += 1
            acquire_overlap_ms = _interval_overlap_ms(started_at, ended_at, totals.sandbox_acquire_intervals or [])
            totals.sandbox_tool_acquire_overlap_ms += acquire_overlap_ms
            totals.sandbox_tool_execution_total_ms += max(0, elapsed_ms - acquire_overlap_ms)
            if outcome != "completed":
                totals.sandbox_tool_noncompleted_count += 1
        else:
            totals.non_sandbox_tool_count += 1
            totals.non_sandbox_tool_total_ms += elapsed_ms
            if outcome != "completed":
                totals.non_sandbox_tool_noncompleted_count += 1


def consume_tool_timing(*, thread_id: str, run_id: str) -> ToolTimingTotals:
    """Return and remove the aggregate for a completed Gateway run."""

    key = (_safe_token(thread_id), _safe_token(run_id))
    with _tool_timing_totals_lock:
        return _tool_timing_totals.pop(key, ToolTimingTotals())


def begin_checkpoint_timing(*, operation: str) -> _CheckpointTimingToken | None:
    """Record one checkpoint operation entering the awaited in-flight set.

    The token carries only trusted run IDs, an implementation category, and a
    count. It lets the completion path distinguish this Run's concurrent writes
    from the whole event loop's task snapshot.
    """

    if not run_timing_logs_enabled():
        return None
    context = get_current_llm_timing_context()
    field_prefix = _CHECKPOINT_OPERATION_PREFIXES.get(operation)
    if context is None or context.thread_id == "-" or context.run_id == "-" or field_prefix is None:
        return None

    thread_id = _safe_token(context.thread_id)
    run_id = _safe_token(context.run_id)
    if thread_id == "-" or run_id == "-":
        return None

    with _checkpoint_timing_totals_lock:
        totals = _checkpoint_timing_totals.setdefault((thread_id, run_id), CheckpointTimingTotals())
        inflight_key = (thread_id, run_id, operation)
        inflight_at_start = _checkpoint_timing_inflight.get(inflight_key, 0) + 1
        _checkpoint_timing_inflight[inflight_key] = inflight_at_start
        setattr(
            totals,
            f"{field_prefix}_inflight_max",
            max(getattr(totals, f"{field_prefix}_inflight_max"), inflight_at_start),
        )
    return _CheckpointTimingToken(thread_id, run_id, operation, inflight_at_start)


def record_checkpoint_timing(
    *,
    operation: str,
    elapsed_ms: int,
    outcome: str,
    token: _CheckpointTimingToken | None = None,
) -> None:
    """Accumulate one PostgreSQL checkpointer operation for its owning run.

    ``operation`` is deliberately a fixed implementation category. SQL text,
    bind parameters, checkpoint state, and connection identifiers never enter
    this diagnostic path.
    """

    if token is None and not run_timing_logs_enabled():
        return
    field_prefix = _CHECKPOINT_OPERATION_PREFIXES.get(operation)
    if token is not None:
        if token.operation != operation:
            return
        thread_id, run_id = token.thread_id, token.run_id
    else:
        context = get_current_llm_timing_context()
        if context is None or context.thread_id == "-" or context.run_id == "-":
            return
        thread_id, run_id = _safe_token(context.thread_id), _safe_token(context.run_id)
    if field_prefix is None or thread_id == "-" or run_id == "-":
        return

    elapsed_ms = max(0, int(elapsed_ms))
    with _checkpoint_timing_totals_lock:
        totals = _checkpoint_timing_totals.setdefault((thread_id, run_id), CheckpointTimingTotals())
        setattr(totals, f"{field_prefix}_count", getattr(totals, f"{field_prefix}_count") + 1)
        setattr(totals, f"{field_prefix}_total_ms", getattr(totals, f"{field_prefix}_total_ms") + elapsed_ms)
        setattr(totals, f"{field_prefix}_max_ms", max(getattr(totals, f"{field_prefix}_max_ms"), elapsed_ms))
        if outcome != "completed":
            setattr(
                totals,
                f"{field_prefix}_noncompleted_count",
                getattr(totals, f"{field_prefix}_noncompleted_count") + 1,
            )
        if token is not None:
            inflight_key = (thread_id, run_id, operation)
            remaining = _checkpoint_timing_inflight.get(inflight_key, 0) - 1
            if remaining > 0:
                _checkpoint_timing_inflight[inflight_key] = remaining
            else:
                _checkpoint_timing_inflight.pop(inflight_key, None)
    if elapsed_ms >= _checkpoint_timing_slow_ms():
        fields: dict[str, object] = {"outcome": outcome}
        if token is not None:
            fields["inflight_at_start"] = token.inflight_at_start
        log_run_timing(
            _timing_logger,
            f"checkpoint_{operation}_slow",
            elapsed_ms=elapsed_ms,
            **fields,
        )


def consume_checkpoint_timing(*, thread_id: str, run_id: str) -> CheckpointTimingTotals:
    """Return and remove PostgreSQL checkpointer timing totals for a finished run."""

    key = (_safe_token(thread_id), _safe_token(run_id))
    with _checkpoint_timing_totals_lock:
        totals = _checkpoint_timing_totals.pop(key, CheckpointTimingTotals())
        for inflight_key in [entry for entry in _checkpoint_timing_inflight if entry[:2] == key]:
            _checkpoint_timing_inflight.pop(inflight_key, None)
        return totals


def log_run_timing(
    logger: logging.Logger,
    event: str,
    *,
    elapsed_ms: int | None = None,
    **fields: object,
) -> None:
    """Emit one redacted ``RUN_TIMING`` event when diagnostics are enabled.

    Callers must supply only stable operational categories (for example a tool
    name or exception class), never user content.  Tokens are defensively
    constrained again here so an accidental raw value cannot enter the log.
    """

    if not run_timing_logs_enabled():
        return

    context = get_current_llm_timing_context()
    thread_id = "-" if context is None else context.thread_id
    run_id = "-" if context is None else context.run_id
    parts = [
        f"event={_safe_token(event)}",
        f"deerflow_thread_id={thread_id}",
        f"deerflow_run_id={run_id}",
    ]
    if elapsed_ms is not None:
        parts.append(f"elapsed_ms={max(0, int(elapsed_ms))}")
    parts.extend(f"{_safe_token(key)}={_safe_token(value)}" for key, value in fields.items())
    logger.info("RUN_TIMING %s", " ".join(parts))


@contextmanager
def time_middleware_after_agent(logger: logging.Logger, *, middleware: str) -> Iterator[None]:
    """Time one DeerFlow-owned synchronous ``after_agent`` hook.

    The wrapper deliberately has no state or message access.  It preserves the
    wrapped hook's exception semantics while making graph-finalization stalls
    attributable to the fixed middleware name.
    """

    started_at = time.monotonic()
    log_run_timing(logger, "middleware_after_agent_start", middleware=middleware)
    try:
        yield
    except BaseException as exc:
        log_run_timing(
            logger,
            "middleware_after_agent_end",
            elapsed_ms=(time.monotonic() - started_at) * 1000,
            outcome="cancelled" if isinstance(exc, asyncio.CancelledError) else "error",
            middleware=middleware,
            error_type=type(exc).__name__,
        )
        raise
    else:
        log_run_timing(
            logger,
            "middleware_after_agent_end",
            elapsed_ms=(time.monotonic() - started_at) * 1000,
            outcome="completed",
            middleware=middleware,
        )


@asynccontextmanager
async def atime_middleware_after_agent(logger: logging.Logger, *, middleware: str) -> AsyncIterator[None]:
    """Async counterpart of :func:`time_middleware_after_agent`."""

    with time_middleware_after_agent(logger, middleware=middleware):
        yield
