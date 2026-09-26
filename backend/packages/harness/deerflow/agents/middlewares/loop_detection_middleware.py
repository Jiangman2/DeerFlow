"""Middleware to detect and break repetitive tool call loops.

P0 safety: prevents the agent from calling the same tool with the same
arguments indefinitely until the recursion limit kills the run.

Detection strategy:
  1. After each model response, hash the tool calls (name + args).
  2. Track recent hashes in a sliding window.
  3. If the same hash appears >= warn_threshold times, queue a
     "you are repeating yourself — wrap up" warning for the current
     thread/run. The warning is **injected at the next model call** (in
     ``wrap_model_call``) as a ``HumanMessage`` appended to the message
     list, *after* all ToolMessage responses to the previous
     AIMessage(tool_calls).
  4. If it appears >= hard_limit times, strip all tool_calls from the
     response so the agent is forced to produce a final text answer.

Why the warning is injected at ``wrap_model_call`` instead of
``after_model``:

  ``after_model`` fires immediately after the model emits an
  ``AIMessage`` that may carry ``tool_calls``. The tools node has not
  run yet, so no matching ``ToolMessage`` exists in the history. Any
  message we add here lands *between* the assistant's tool_calls and
  their responses. OpenAI/Moonshot reject the next request with
  ``"tool_call_ids did not have response messages"`` because their
  validators require the assistant's tool_calls to be followed
  immediately by tool messages. Anthropic also disallows mid-stream
  ``SystemMessage``. By deferring the warning to ``wrap_model_call``,
  every prior ToolMessage is already present in the request's message
  list and the warning is appended at the end — pairing intact, no
  ``AIMessage`` semantics are mutated.

Queued warnings are intentionally transient. If a run ends before the
next model request drains a queued warning, ``after_agent`` drops it
instead of carrying it into a later invocation for the same thread. The
hard-stop path still forces termination when the configured safety limit
is reached.

Stop-reason surfacing (#3875 Phase 2):
  Like the token-budget guard, the loop hard stop does NOT raise — it
  strips ``tool_calls`` so the agent loop terminates naturally with a
  final answer. To let the caller (the subagent executor) distinguish a
  loop-capped completion from a clean one, the run that triggered the hard
  stop is recorded in ``_stop_reason`` and exposed via
  :meth:`consume_stop_reason`. The executor collects that reason alongside
  the token-budget guard's so a loop-capped run surfaces as
  ``completed + loop_capped`` and the lead/ledger can tell it was capped
  without parsing result text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import re
import threading
from collections import Counter, OrderedDict, defaultdict, deque
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares._bounded_dict import BoundedDict
from deerflow.agents.middlewares.audit_context import LOOP_DETECTION_RECORDER_CONTEXT_KEY, resolve_audit_recorder
from deerflow.runtime.events.catalog import MIDDLEWARE_LOOP_DETECTION_TAG

if TYPE_CHECKING:
    from deerflow.config.loop_detection_config import LoopDetectionConfig

logger = logging.getLogger(__name__)

# Defaults — can be overridden via constructor
_DEFAULT_WARN_THRESHOLD = 3  # inject warning after 3 identical calls
_DEFAULT_HARD_LIMIT = 5  # force-stop after 5 identical calls
_DEFAULT_WINDOW_SIZE = 20  # track last N tool calls
_DEFAULT_MAX_TRACKED_THREADS = 100  # LRU eviction limit
_DEFAULT_TOOL_FREQ_WARN = 30  # warn after 30 calls to the same tool type
_DEFAULT_TOOL_FREQ_HARD_LIMIT = 50  # force-stop after 50 calls to the same tool type
_MAX_PENDING_WARNINGS_PER_RUN = 4
_DEFAULT_NO_PROGRESS_WARN = 3
_DEFAULT_NO_PROGRESS_HARD_LIMIT = 5
_MAX_NO_PROGRESS_KEYS = 200
_OPEN_ENDED_READ = "end"


def _normalize_tool_call_args(raw_args: object) -> tuple[dict, str | None]:
    """Normalize tool call args to a dict plus an optional fallback key.

    Some providers serialize ``args`` as a JSON string instead of a dict.
    We defensively parse those cases so loop detection does not crash while
    still preserving a stable fallback key for non-dict payloads.
    """
    if isinstance(raw_args, dict):
        return raw_args, None

    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}, raw_args

        if isinstance(parsed, dict):
            return parsed, None
        return {}, json.dumps(parsed, sort_keys=True, default=str)

    if raw_args is None:
        return {}, None

    return {}, json.dumps(raw_args, sort_keys=True, default=str)


def _coerce_line_number(value: object) -> int | None:
    """Parse one ``read_file`` line bound, clamping usable values to line 1."""
    try:
        line = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return max(line, 1)


def _normalized_read_range(args: dict) -> tuple[int, int | None]:
    """Normalize a read into its exact line window.

    Exact windows keep adjacent pages distinct.  An omitted ``end_line`` is
    open-ended, while reversed explicit bounds describe the same window in
    either order.  Per-tool frequency limits still catch calls that jitter
    their ranges without making progress.
    """
    start_line = _coerce_line_number(args.get("start_line"))
    end_line = _coerce_line_number(args.get("end_line"))
    if start_line is None:
        start_line = 1
    if end_line is not None and end_line < start_line:
        start_line, end_line = end_line, start_line
    return start_line, end_line


def _stable_tool_key(name: str, args: dict, fallback_key: str | None) -> str:
    """Derive a stable key from salient args without overfitting to noise."""
    if name == "read_file" and fallback_key is None:
        path = args.get("path") or ""
        start_line, end_line = _normalized_read_range(args)
        return f"{path}:{start_line}-{end_line if end_line is not None else _OPEN_ENDED_READ}"

    # write_file / str_replace are content-sensitive: same path may be updated
    # with different payloads during iteration. Using only salient fields (path)
    # can collapse distinct calls, so we hash full args to reduce false positives.
    if name in {"write_file", "str_replace"}:
        if fallback_key is not None:
            return fallback_key
        return json.dumps(args, sort_keys=True, default=str)

    salient_fields = ("path", "url", "query", "command", "pattern", "glob", "cmd")
    stable_args = {field: args[field] for field in salient_fields if args.get(field) is not None}
    if stable_args:
        return json.dumps(stable_args, sort_keys=True, default=str)

    if fallback_key is not None:
        return fallback_key

    return json.dumps(args, sort_keys=True, default=str)


def _hash_tool_calls(tool_calls: list[dict]) -> str:
    """Deterministic hash of a set of tool calls (name + stable key).

    This is intended to be order-independent: the same multiset of tool calls
    should always produce the same hash, regardless of their input order.
    """
    # Normalize each tool call to a stable (name, key) structure.
    normalized: list[str] = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args, fallback_key = _normalize_tool_call_args(tc.get("args", {}))
        key = _stable_tool_key(name, args, fallback_key)

        normalized.append(f"{name}:{key}")

    # Sort so permutations of the same multiset of calls yield the same ordering.
    normalized.sort()
    blob = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()[:12]


_WARNING_MSG = "[LOOP DETECTED] You are repeating the same tool calls. Stop calling tools and produce your final answer now. If you cannot complete the task, summarize what you accomplished so far."

_NO_PROGRESS_WARNING_MSG = (
    "[NO PROGRESS DETECTED] The same tool failure has recurred across repair attempts. Re-read the owning input/output, change the repair strategy, or report a concrete blocking reason; do not repeat an unchanged validation cycle."
)
_NO_PROGRESS_HARD_STOP_MSG = "[FORCED STOP] The same tool failure recurred after the bounded repair limit. Producing a final answer with the last known state and blocking reason."


def _problem_result_signature(result: object) -> str | None:
    """Return a stable fingerprint for a repeated failing tool result.

    Only error-like results are tracked and only a digest is retained. This catches
    semantic repair loops where commands and paths change while the tool reports the
    same failure, which exact call hashes and per-tool frequency cannot see.
    """
    status = getattr(result, "status", None)
    additional_kwargs = getattr(result, "additional_kwargs", None)
    meta = additional_kwargs.get("deerflow_tool_meta") if isinstance(additional_kwargs, dict) else None
    meta_status = meta.get("status") if isinstance(meta, dict) else None
    content = getattr(result, "content", "")
    if isinstance(content, list):
        content = " ".join(str(item) for item in content)
    if not isinstance(content, str):
        content = str(content)
    is_problem = status in {"error", "partial_success"} or meta_status in {"error", "partial_success"} or "[TOOL_BLOCKED]" in content
    if not is_problem or not content.strip():
        return None

    normalized = content.lower()
    normalized = re.sub(r"/mnt/[^\s'\"`<>]+", "<path>", normalized)
    normalized = re.sub(r"\b[0-9a-f]{8,}(?:-[0-9a-f-]{4,}){1,}\b", "<id>", normalized)
    normalized = re.sub(r"\b\d+\b", "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]


def _tool_result_succeeded(result: object) -> bool:
    """Return whether a tool result represents a successful operation."""
    status = getattr(result, "status", None)
    additional_kwargs = getattr(result, "additional_kwargs", None)
    meta = additional_kwargs.get("deerflow_tool_meta") if isinstance(additional_kwargs, dict) else None
    meta_status = meta.get("status") if isinstance(meta, dict) else None
    if status in {"error", "partial_success"} or meta_status in {"error", "partial_success"}:
        return False
    content = getattr(result, "content", "")
    if isinstance(content, list):
        content = " ".join(str(item) for item in content)
    text = content if isinstance(content, str) else str(content)
    return not text.lstrip().startswith("Error:") and "[TOOL_BLOCKED]" not in text


def _matching_tool_call(messages: list, result: object) -> dict | None:
    """Find the AI tool call that produced *result*."""
    tool_call_id = str(getattr(result, "tool_call_id", None) or "")
    if not tool_call_id:
        return None
    for message in reversed(messages):
        if getattr(message, "type", None) != "ai":
            continue
        for tool_call in reversed(getattr(message, "tool_calls", None) or []):
            if str(tool_call.get("id", "")) == tool_call_id:
                return tool_call
    return None


def _latest_read_mark_hash(messages: list, path: str) -> str | None:
    """Return the newest read-before-write content hash for *path*."""
    normalized_path = posixpath.normpath(path)
    for message in reversed(messages):
        if getattr(message, "type", None) != "tool":
            continue
        additional_kwargs = getattr(message, "additional_kwargs", None)
        mark = additional_kwargs.get("deerflow_read_mark") if isinstance(additional_kwargs, dict) else None
        if not isinstance(mark, dict) or mark.get("path") != normalized_path:
            continue
        mark_hash = mark.get("hash")
        return mark_hash if isinstance(mark_hash, str) else None
    return None


def _successful_changed_file_mutation(messages: list, result: object) -> tuple[str, str] | None:
    """Identify a successful file mutation that changed durable content.

    This is deliberately narrow: only ``write_file`` and ``str_replace`` can
    reset repetition accounting, and a full-file write matching the latest
    read mark is treated as a no-op. Repeated reads interleaved with unchanged
    writes therefore remain detectable.
    """
    if not _tool_result_succeeded(result):
        return None
    tool_call = _matching_tool_call(messages, result)
    if tool_call is None:
        return None
    name = str(tool_call.get("name", ""))
    if name not in {"write_file", "str_replace"}:
        return None
    args, _ = _normalize_tool_call_args(tool_call.get("args", {}))
    path = args.get("path")
    if not isinstance(path, str) or not path:
        return None

    if name == "write_file":
        content = args.get("content")
        if not isinstance(content, str):
            return None
        if bool(args.get("append")):
            return (name, path) if content else None
        previous_hash = _latest_read_mark_hash(messages, path)
        if previous_hash is not None and hashlib.sha256(content.encode("utf-8")).hexdigest() == previous_hash:
            return None
        return name, path

    old_str = args.get("old_str")
    new_str = args.get("new_str")
    if not isinstance(old_str, str) or not isinstance(new_str, str) or old_str == new_str:
        return None
    return name, path


_TOOL_FREQ_WARNING_MSG = (
    "[LOOP DETECTED] You have called {tool_name} {count} times without producing a final answer. Stop calling tools and produce your final answer now. If you cannot complete the task, summarize what you accomplished so far."
)

_HARD_STOP_MSG = "[FORCED STOP] Repeated tool calls exceeded the safety limit. Producing final answer with results collected so far."

_TOOL_FREQ_HARD_STOP_MSG = "[FORCED STOP] Tool {tool_name} called {count} times — exceeded the per-tool safety limit. Producing final answer with results collected so far."


@dataclass(frozen=True)
class _LoopDecision:
    message: str
    action: str
    detection_layer: str
    tool_names: tuple[str, ...]
    count: int
    threshold: int

    @property
    def hard_stop(self) -> bool:
        return self.action == "hard_stop"


class LoopDetectionMiddleware(AgentMiddleware[AgentState]):
    """Detects and breaks repetitive tool call loops.

    Threshold parameters are validated upstream by :class:`LoopDetectionConfig`;
    construct via :meth:`from_config` to ensure values pass Pydantic validation.

    Args:
        warn_threshold: Number of identical tool call sets before injecting
            a warning message. Default: 3.
        hard_limit: Number of identical tool call sets before stripping
            tool_calls entirely. Default: 5.
        window_size: Size of the sliding window for tracking calls.
            Default: 20.
        max_tracked_threads: Maximum number of threads to track before
            evicting the least recently used. Default: 100.
        tool_freq_warn: Maximum number of same-tool-type calls within a
            sliding window of ``_tool_freq_window`` before injecting a
            frequency warning. Catches cross-file read loops that
            hash-based detection misses. Default: 30 (within a window
            of 50).
        tool_freq_hard_limit: Maximum number of same-tool-type calls within
            a sliding window of ``_tool_freq_window`` before forcing a
            stop. Default: 50 (within a window of 50).
        tool_freq_overrides: Per-tool overrides for frequency thresholds,
            keyed by tool name. Each value is a ``(warn, hard_limit)`` tuple
            that replaces ``tool_freq_warn`` / ``tool_freq_hard_limit`` for
            that specific tool. Tools not listed here fall back to the global
            thresholds. Useful for raising limits on intentionally
            high-frequency tools (e.g. ``bash`` in batch pipelines) without
            weakening protection on all other tools. Default: ``None``
            (no overrides).
    """

    def __init__(
        self,
        warn_threshold: int = _DEFAULT_WARN_THRESHOLD,
        hard_limit: int = _DEFAULT_HARD_LIMIT,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        max_tracked_threads: int = _DEFAULT_MAX_TRACKED_THREADS,
        tool_freq_warn: int = _DEFAULT_TOOL_FREQ_WARN,
        tool_freq_hard_limit: int = _DEFAULT_TOOL_FREQ_HARD_LIMIT,
        tool_freq_overrides: dict[str, tuple[int, int]] | None = None,
    ):
        super().__init__()
        self.warn_threshold = warn_threshold
        self.hard_limit = hard_limit
        self.window_size = window_size
        self.max_tracked_threads = max_tracked_threads
        self.tool_freq_warn = tool_freq_warn
        self.tool_freq_hard_limit = tool_freq_hard_limit
        self._tool_freq_overrides: dict[str, tuple[int, int]] = tool_freq_overrides or {}
        # Layer 2's windowed frequency count can never exceed the deque length,
        # so the deque MUST be at least as long as the largest hard limit it is
        # compared against — otherwise the hard-stop branch is dead code. Do NOT
        # reuse Layer 1's ``window_size`` (which is unrelated and defaults below
        # the freq thresholds, e.g. 20 < hard 50); size the frequency window to
        # the largest hard limit in play (global + every per-tool override) so a
        # tight burst can actually reach it while spread-out calls still decay
        # out of the window. Warn thresholds are intentionally excluded: a sane
        # config enforces warn <= hard (covered by sizing to hard), and a misconfig
        # with warn > hard would hard-stop first anyway, so an unreachable warn
        # is harmless and must not inflate the window.
        self._tool_freq_window = max(
            self.window_size,
            self.tool_freq_hard_limit,
            *(hard for _, hard in self._tool_freq_overrides.values()),
        )
        self._lock = threading.Lock()
        self._history: OrderedDict[str, list[str]] = OrderedDict()
        self._warned: dict[str, set[str]] = defaultdict(set)
        # Windowed per-tool-type frequency: recent tool names per thread,
        # trimmed to ``window_size`` so the count decays instead of growing
        # monotonically (replaces the old monotonic ``_tool_freq`` integer).
        self._tool_name_history: defaultdict[str, deque[str]] = defaultdict(deque)
        # Per-thread Counter mirroring the deque so freq_count is O(1) instead
        # of scanning the whole window on every tool call. A single high
        # per-tool override (e.g. bash: {hard_limit: 1000}) inflates the window
        # globally, so the scan would cost 1000 per call for every tool; Counter
        # increments on append and decrements on popleft.
        self._tool_name_counter: defaultdict[str, Counter[str]] = defaultdict(Counter)
        # Per-thread set of tool names already warned about in Layer 2, so a
        # frequency warning is enqueued once rather than on every subsequent
        # call. Cleared per name when the windowed count decays back below the
        # warn threshold, mirroring the hash-layer ``_warned`` pruning.
        self._tool_freq_warned: dict[str, set[str]] = defaultdict(set)
        # The lead middleware instance is reused across multiple user runs in a
        # thread. Repetition history is run-scoped even though storage remains
        # thread-keyed for backwards-compatible diagnostics.
        self._active_run_by_thread: OrderedDict[str, str] = OrderedDict()
        # Per-thread/run queue of warnings to inject at the next model call.
        # Populated by ``after_model`` (detection) and drained by
        # ``wrap_model_call`` (injection); see module docstring.
        self._pending_warnings: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._pending_warning_touch_order: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._max_pending_warning_keys = max(1, self.max_tracked_threads * 2)
        # Stop reason set when a hard-stop fires (#3875 Phase 2). Keyed by run_id
        # (matching ``TokenBudgetMiddleware``) and bounded — the lead agent's
        # middleware instance is long-lived across many runs, so without a cap
        # an entry would accumulate for every looped lead run. Intentionally NOT
        # cleared by ``after_agent``/``_clear_current_run_pending_warnings`` so
        # the subagent executor can consume it after the run returns; ``reset()``
        # still drops it.
        self._stop_reason: BoundedDict[str, str] = BoundedDict(1000)
        # Run-scoped semantic repair-loop state. A successful result clears this
        # state; an unchanged error increments it even when each retry uses a
        # different command or path.
        self._no_progress: OrderedDict[tuple[str, str], tuple[str, int]] = OrderedDict()
        self._no_progress_hard: set[tuple[str, str]] = set()
        self._last_result_marker: dict[tuple[str, str], str] = {}

    @classmethod
    def from_config(cls, config: LoopDetectionConfig) -> LoopDetectionMiddleware:
        """Construct from a Pydantic-validated config, trusting its validation."""
        return cls(
            warn_threshold=config.warn_threshold,
            hard_limit=config.hard_limit,
            window_size=config.window_size,
            max_tracked_threads=config.max_tracked_threads,
            tool_freq_warn=config.tool_freq_warn,
            tool_freq_hard_limit=config.tool_freq_hard_limit,
            tool_freq_overrides={name: (o.warn, o.hard_limit) for name, o in config.tool_freq_overrides.items()},
        )

    def _get_thread_id(self, runtime: Runtime) -> str:
        """Extract thread_id from runtime context for per-thread tracking."""
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id:
            return str(thread_id)
        return "default"

    def _get_run_id(self, runtime: Runtime) -> str:
        """Extract run_id from runtime context for per-run warning scoping.

        Keyed by presence, not truthiness: ``SubagentExecutor`` sets
        ``context["run_id"] = self.run_id`` unconditionally (no truthiness
        guard), so an embedded/TUI-dispatched subagent — whose ``run_id`` is
        never assigned per ``AGENTS.md``'s description of the embedded
        ``DeerFlowClient`` — runs with a context that legitimately carries
        ``run_id=None`` (the key is *present*, not absent). The executor
        later reads the stop reason back with the raw attribute,
        ``consume_stop_reason(self.run_id)``, so this must return exactly
        that value (``None`` included) when the key is present, rather than
        collapsing it to a shared fallback indistinguishable from an absent
        key. A truthiness check (``if run_id:``) previously conflated
        "present but None/falsy" with "absent", both mapping to the same
        literal ``"default"`` — so a genuine ``run_id=None`` hard-stop was
        recorded under ``"default"`` here but looked up under ``None`` by
        the executor, silently losing the ``loop_capped`` stop reason.
        Mirrors ``TokenBudgetMiddleware._get_run_id``.
        """
        ctx = getattr(runtime, "context", None)
        if isinstance(ctx, dict) and "run_id" in ctx:
            return ctx["run_id"]
        # Fallback to runtime object ID to prevent collisions across embedded client runs
        return str(id(runtime))

    def consume_stop_reason(self, run_id: str | None) -> str | None:
        """Pop and return the stop reason the hard-stop set for this run.

        Returns ``"loop_capped"`` when a repeated tool-call loop tripped the hard
        stop during the run — the run still completed with a forced final answer
        (the hard stop strips ``tool_calls`` rather than raising). The subagent
        executor calls this after the run returns so a loop-capped completion
        carries ``stop_reason=loop_capped`` to the lead instead of looking like
        a clean ``completed``. Mirrors ``TokenBudgetMiddleware.consume_stop_reason``;
        popping keeps the dict from accumulating on a reused instance.
        """
        with self._lock:
            return self._stop_reason.pop(run_id, None)

    def _pending_key(self, runtime: Runtime) -> tuple[str, str]:
        """Return the pending-warning key for the current thread/run."""
        return self._get_thread_id(runtime), self._get_run_id(runtime)

    def _evict_if_needed(self) -> None:
        """Evict least recently used threads if over the limit.

        Must be called while holding self._lock.
        """
        while len(self._history) > self.max_tracked_threads:
            evicted_id, _ = self._history.popitem(last=False)
            self._warned.pop(evicted_id, None)
            self._tool_name_history.pop(evicted_id, None)
            self._tool_name_counter.pop(evicted_id, None)
            self._tool_freq_warned.pop(evicted_id, None)
            self._active_run_by_thread.pop(evicted_id, None)
            for key in list(self._no_progress):
                if key[0] == evicted_id:
                    self._no_progress.pop(key, None)
                    self._no_progress_hard.discard(key)
                    self._last_result_marker.pop(key, None)
            for key in list(self._pending_warnings):
                if key[0] == evicted_id:
                    self._drop_pending_warning_key_locked(key)
            logger.debug("Evicted loop tracking for thread %s (LRU)", evicted_id)

    def _drop_pending_warning_key_locked(self, key: tuple[str, str]) -> None:
        """Drop all pending-warning bookkeeping for one thread/run key.

        Must be called while holding self._lock.
        """
        self._pending_warnings.pop(key, None)
        self._pending_warning_touch_order.pop(key, None)

    def _touch_pending_warning_key_locked(self, key: tuple[str, str]) -> None:
        """Mark a pending-warning key as recently used.

        Must be called while holding self._lock.
        """
        self._pending_warning_touch_order[key] = None
        self._pending_warning_touch_order.move_to_end(key)

    def _prune_pending_warning_state_locked(self, protected_key: tuple[str, str]) -> None:
        """Cap pending-warning state across abnormal or concurrent runs.

        Must be called while holding self._lock.
        """
        overflow = len(self._pending_warning_touch_order) - self._max_pending_warning_keys
        if overflow <= 0:
            return

        candidates = [key for key in self._pending_warning_touch_order if key != protected_key]
        for key in candidates[:overflow]:
            self._drop_pending_warning_key_locked(key)

    def _queue_pending_warning_locked(self, pending_key: tuple[str, str], warning: str) -> None:
        """Queue one transient warning while ``self._lock`` is already held."""
        warnings = self._pending_warnings[pending_key]
        if warning not in warnings:
            warnings.append(warning)
        if len(warnings) > _MAX_PENDING_WARNINGS_PER_RUN:
            del warnings[: len(warnings) - _MAX_PENDING_WARNINGS_PER_RUN]
        self._touch_pending_warning_key_locked(pending_key)
        self._prune_pending_warning_state_locked(protected_key=pending_key)

    def _queue_pending_warning(self, runtime: Runtime, warning: str) -> None:
        """Queue one transient warning for the current thread/run with caps."""
        pending_key = self._pending_key(runtime)
        with self._lock:
            self._queue_pending_warning_locked(pending_key, warning)

    def _record_tool_result_progress(self, result: object, runtime: Runtime) -> None:
        """Track an unchanged error across tools and queue a bounded repair hint."""
        key = self._pending_key(runtime)
        signature = _problem_result_signature(result)
        with self._lock:
            if signature is None:
                self._no_progress.pop(key, None)
                self._no_progress_hard.discard(key)
                return

            previous = self._no_progress.get(key)
            count = previous[1] + 1 if previous is not None and previous[0] == signature else 1
            self._no_progress[key] = (signature, count)
            self._no_progress.move_to_end(key)
            while len(self._no_progress) > _MAX_NO_PROGRESS_KEYS:
                evicted_key, _ = self._no_progress.popitem(last=False)
                self._no_progress_hard.discard(evicted_key)

            if count == _DEFAULT_NO_PROGRESS_WARN:
                self._queue_pending_warning_locked(key, _NO_PROGRESS_WARNING_MSG)
                logger.warning(
                    "Unchanged tool failure detected across repair attempts",
                    extra={"thread_id": key[0], "run_id": key[1], "count": count},
                )
            elif count >= _DEFAULT_NO_PROGRESS_HARD_LIMIT:
                self._no_progress_hard.add(key)
                logger.error(
                    "Repair loop hard limit reached",
                    extra={"thread_id": key[0], "run_id": key[1], "count": count},
                )
        if count == _DEFAULT_NO_PROGRESS_WARN:
            # Audit outside the state lock; never persist the error signature,
            # tool arguments, validator output, or project contents.
            self._record_audit_event(_LoopDecision(_NO_PROGRESS_WARNING_MSG, "warn", "unchanged_failure", (), count, _DEFAULT_NO_PROGRESS_WARN), runtime)

    def _clear_repetition_state_locked(self, thread_id: str) -> None:
        """Clear exact/frequency repetition accounting for one thread."""
        self._history.pop(thread_id, None)
        self._warned.pop(thread_id, None)
        self._tool_name_history.pop(thread_id, None)
        self._tool_name_counter.pop(thread_id, None)
        self._tool_freq_warned.pop(thread_id, None)

    def _record_changed_file_mutation_progress(self, messages: list, result: object, runtime: Runtime) -> None:
        """Reset repetition windows after a successful durable file change."""
        mutation = _successful_changed_file_mutation(messages, result)
        if mutation is None:
            return
        tool_name, path = mutation
        thread_id = self._get_thread_id(runtime)
        with self._lock:
            self._clear_repetition_state_locked(thread_id)
        logger.info(
            "Durable file mutation reset repetition window",
            extra={"thread_id": thread_id, "run_id": self._get_run_id(runtime), "tool_name": tool_name, "path": path},
        )

    def _observe_last_tool_result(self, messages: list, runtime: Runtime) -> None:
        """Observe the newest tool result once before evaluating the next model call."""
        if len(messages) < 2:
            return
        last_tool = next(
            (message for message in reversed(messages[:-1]) if getattr(message, "type", None) == "tool"),
            None,
        )
        if last_tool is None:
            return
        key = self._pending_key(runtime)
        marker = str(getattr(last_tool, "id", None) or "")
        if not marker:
            marker = hashlib.sha256(f"{getattr(last_tool, 'name', '')}\n{getattr(last_tool, 'content', '')}".encode("utf-8", errors="replace")).hexdigest()
        with self._lock:
            if self._last_result_marker.get(key) == marker:
                return
            self._last_result_marker[key] = marker
        self._record_tool_result_progress(last_tool, runtime)
        self._record_changed_file_mutation_progress(messages, last_tool, runtime)

    def _track_and_check(self, state: AgentState, runtime: Runtime) -> _LoopDecision | None:
        """Track tool calls and check for loops.

        Two detection layers:
          1. **Hash-based** (existing): catches identical tool call sets.
          2. **Frequency-based** (new): catches the same *tool type* being
             called many times with varying arguments (e.g. ``read_file``
             on 40 different files).

        Returns:
            A structured warning/stop transition, without sensitive arguments.
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        self._observe_last_tool_result(messages, runtime)

        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None

        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return None

        thread_id = self._get_thread_id(runtime)
        pending_key = self._pending_key(runtime)

        with self._lock:
            repair_hard_stop = pending_key in self._no_progress_hard
            repair_count = self._no_progress.get(pending_key, (None, 0))[1]
        if repair_hard_stop:
            logger.error(
                "Repeated unchanged tool failure forcing stop",
                extra={"thread_id": thread_id, "run_id": pending_key[1]},
            )
            return _LoopDecision(_NO_PROGRESS_HARD_STOP_MSG, "hard_stop", "unchanged_failure", (), repair_count, _DEFAULT_NO_PROGRESS_HARD_LIMIT)

        call_hash = _hash_tool_calls(tool_calls)

        with self._lock:
            # Touch / create entry (move to end for LRU)
            if thread_id in self._history:
                self._history.move_to_end(thread_id)
            else:
                self._history[thread_id] = []
                self._evict_if_needed()

            history = self._history[thread_id]
            history.append(call_hash)
            if len(history) > self.window_size:
                history[:] = history[-self.window_size :]

            warned_hashes = self._warned.get(thread_id)
            if warned_hashes is not None:
                warned_hashes.intersection_update(history)
                if not warned_hashes:
                    self._warned.pop(thread_id, None)

            count = history.count(call_hash)
            tool_names = [str(tc.get("name") or "?") for tc in tool_calls]

            # --- Layer 1: hash-based (identical call sets) ---
            if count >= self.hard_limit:
                logger.error(
                    "Loop hard limit reached — forcing stop",
                    extra={
                        "thread_id": thread_id,
                        "call_hash": call_hash,
                        "count": count,
                        "tools": tool_names,
                    },
                )
                return _LoopDecision(_HARD_STOP_MSG, "hard_stop", "identical_call_set", tuple(tool_names), count, self.hard_limit)

            if count >= self.warn_threshold:
                warned = self._warned[thread_id]
                if call_hash not in warned:
                    warned.add(call_hash)
                    logger.warning(
                        "Repetitive tool calls detected — injecting warning",
                        extra={
                            "thread_id": thread_id,
                            "call_hash": call_hash,
                            "count": count,
                            "tools": tool_names,
                        },
                    )
                    return _LoopDecision(_WARNING_MSG, "warn", "identical_call_set", tuple(tool_names), count, self.warn_threshold)

            # --- Layer 2: per-tool-type frequency (windowed) ---
            tool_name_history = self._tool_name_history[thread_id]
            name_counter = self._tool_name_counter[thread_id]
            for tc in tool_calls:
                name = tc.get("name", "")
                if not name:
                    continue
                # Windowed counting: append the name and trim to the frequency
                # window (>= the largest threshold) so the count can reach the
                # warn/hard limits on a tight burst yet still decay for
                # spread-out calls. A mirrored Counter gives O(1) freq_count
                # even when a per-tool override inflates the window globally.
                tool_name_history.append(name)
                name_counter[name] += 1
                while len(tool_name_history) > self._tool_freq_window:
                    old = tool_name_history.popleft()
                    c = name_counter[old] - 1
                    if c <= 0:
                        del name_counter[old]
                    else:
                        name_counter[old] = c
                freq_count = name_counter.get(name, 0)

                if name in self._tool_freq_overrides:
                    eff_warn, eff_hard = self._tool_freq_overrides[name]
                else:
                    eff_warn, eff_hard = self.tool_freq_warn, self.tool_freq_hard_limit

                if freq_count >= eff_hard:
                    logger.error(
                        "Tool frequency hard limit reached — forcing stop",
                        extra={
                            "thread_id": thread_id,
                            "tool_name": name,
                            "count": freq_count,
                        },
                    )
                    return _LoopDecision(_TOOL_FREQ_HARD_STOP_MSG.format(tool_name=name, count=freq_count), "hard_stop", "tool_frequency", (name,), freq_count, eff_hard)

                if freq_count >= eff_warn:
                    freq_warned = self._tool_freq_warned[thread_id]
                    if name not in freq_warned:
                        freq_warned.add(name)
                        logger.warning(
                            "Tool frequency warning — too many calls to same tool type",
                            extra={
                                "thread_id": thread_id,
                                "tool_name": name,
                                "count": freq_count,
                            },
                        )
                        return _LoopDecision(_TOOL_FREQ_WARNING_MSG.format(tool_name=name, count=freq_count), "warn", "tool_frequency", (name,), freq_count, eff_warn)
                else:
                    # Windowed count decayed below the warn threshold; allow a
                    # future burst of this tool to warn again.
                    self._tool_freq_warned[thread_id].discard(name)

        return None

    @staticmethod
    def _append_text(content: str | list | None, text: str) -> str | list:
        """Append *text* to AIMessage content, handling str, list, and None.

        When content is a list of content blocks (e.g. Anthropic thinking mode),
        we append a new ``{"type": "text", ...}`` block instead of concatenating
        a string to a list, which would raise ``TypeError``.
        """
        if content is None:
            return text
        if isinstance(content, list):
            return [*content, {"type": "text", "text": f"\n\n{text}"}]
        if isinstance(content, str):
            return content + f"\n\n{text}"
        # Fallback: coerce unexpected types to str to avoid TypeError
        return str(content) + f"\n\n{text}"

    @staticmethod
    def _build_hard_stop_update(last_msg, content: str | list) -> dict:
        """Clear tool-call metadata so forced-stop messages serialize as plain assistant text."""
        update = {
            "tool_calls": [],
            "content": content,
        }

        additional_kwargs = dict(getattr(last_msg, "additional_kwargs", {}) or {})
        for key in ("tool_calls", "function_call"):
            additional_kwargs.pop(key, None)
        # Downstream after_model middleware must preserve this as a terminal
        # safety decision rather than deleting the message and jumping back to
        # the model. Runtime context carries the same signal for the live run;
        # the message marker keeps the boundary explicit and durable.
        additional_kwargs["deerflow_terminal_stop"] = True
        additional_kwargs["error_reason"] = "loop_capped"
        update["additional_kwargs"] = additional_kwargs

        response_metadata = deepcopy(getattr(last_msg, "response_metadata", {}) or {})
        if response_metadata.get("finish_reason") == "tool_calls":
            response_metadata["finish_reason"] = "stop"
        update["response_metadata"] = response_metadata

        return update

    def _record_audit_event(self, decision: _LoopDecision, runtime: Runtime) -> None:
        recorder, is_subagent, agent_id = resolve_audit_recorder(getattr(runtime, "context", None), recorder_key=LOOP_DETECTION_RECORDER_CONTEXT_KEY)
        if recorder is None:
            return
        try:
            recorder.record_middleware(
                tag=MIDDLEWARE_LOOP_DETECTION_TAG,
                name=type(self).__name__,
                hook="after_model",
                action=decision.action,
                changes={
                    "is_subagent": is_subagent,
                    "agent_id": agent_id,
                    "detection_layer": decision.detection_layer,
                    "tool_names": list(decision.tool_names),
                    "count": decision.count,
                    "threshold": decision.threshold,
                },
            )
        except Exception:
            logger.warning("Failed to record middleware:loop_detection event")

    def _apply(self, state: AgentState, runtime: Runtime) -> dict | None:
        decision = self._track_and_check(state, runtime)
        if decision is None:
            return None
        self._record_audit_event(decision, runtime)
        warning = decision.message

        if decision.hard_stop:
            # Record the stop reason so the executor can surface
            # ``stop_reason=loop_capped`` after the run returns (#3875 Phase 2).
            # The hard stop does not raise — it strips tool_calls and lets the
            # run finish with a forced final answer — so without this the caller
            # would see a clean ``completed``. See ``consume_stop_reason``.
            # Written under the lock to match ``TokenBudgetMiddleware``: the lead
            # agent's middleware instance is shared across concurrent Gateway
            # threads, so the bounded-dict write needs the same guard.
            run_id = self._get_run_id(runtime)
            with self._lock:
                self._stop_reason[run_id] = "loop_capped"
            # Also write to runtime.context so the lead worker can read it
            # without needing a reference to this middleware instance (#4176).
            ctx = getattr(runtime, "context", None)
            if isinstance(ctx, dict):
                ctx["stop_reason"] = "loop_capped"
            # Strip tool_calls from the last AIMessage to force text output.
            # Once tool_calls are stripped, the AIMessage no longer requires
            # matching ToolMessage responses, so mutating it in place here
            # is safe for OpenAI/Moonshot pairing validators.
            messages = state.get("messages", [])
            last_msg = messages[-1]
            content = self._append_text(last_msg.content, warning or _HARD_STOP_MSG)
            stripped_msg = last_msg.model_copy(update=self._build_hard_stop_update(last_msg, content))
            return {"messages": [stripped_msg]}

        if warning:
            # Defer injection to the next model call. We must NOT alter the
            # AIMessage(tool_calls=...) here (would put framework words in
            # the model's mouth, polluting downstream consumers like
            # MemoryMiddleware), nor insert a separate non-tool message
            # (would break OpenAI/Moonshot tool-call pairing because the
            # tools node has not produced ToolMessage responses yet). The
            # warning is delivered via ``wrap_model_call`` below.
            self._queue_pending_warning(runtime, warning)
            return None

        return None

    def _clear_other_run_pending_warnings(self, runtime: Runtime) -> None:
        """Drop stale pending warnings for previous runs in this thread."""
        thread_id, current_run_id = self._pending_key(runtime)
        with self._lock:
            for key in list(self._pending_warnings):
                if key[0] == thread_id and key[1] != current_run_id:
                    self._drop_pending_warning_key_locked(key)

    def _begin_run_repetition_scope(self, runtime: Runtime) -> None:
        """Start fresh repetition accounting when a thread enters a new run."""
        thread_id, current_run_id = self._pending_key(runtime)
        with self._lock:
            previous_run_id = self._active_run_by_thread.get(thread_id)
            if previous_run_id != current_run_id:
                self._clear_repetition_state_locked(thread_id)
                self._active_run_by_thread[thread_id] = current_run_id
            self._active_run_by_thread.move_to_end(thread_id)
            while len(self._active_run_by_thread) > self.max_tracked_threads:
                self._active_run_by_thread.popitem(last=False)

    def _clear_current_run_pending_warnings(self, runtime: Runtime) -> None:
        """Drop pending warnings owned by the current thread/run."""
        pending_key = self._pending_key(runtime)
        with self._lock:
            self._drop_pending_warning_key_locked(pending_key)

    def _clear_current_run_progress(self, runtime: Runtime) -> None:
        """Drop semantic progress state after a normal run termination."""
        key = self._pending_key(runtime)
        with self._lock:
            self._no_progress.pop(key, None)
            self._no_progress_hard.discard(key)
            self._last_result_marker.pop(key, None)
            if self._active_run_by_thread.get(key[0]) == key[1]:
                self._clear_repetition_state_locked(key[0])
                self._active_run_by_thread.pop(key[0], None)

    @staticmethod
    def _format_warning_message(warnings: list[str]) -> str:
        """Merge pending warnings into one prompt message."""
        deduped = list(dict.fromkeys(warnings))
        return "\n\n".join(deduped)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_run_pending_warnings(runtime)
        self._begin_run_repetition_scope(runtime)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_run_pending_warnings(runtime)
        self._begin_run_repetition_scope(runtime)
        return None

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_current_run_pending_warnings(runtime)
        self._clear_current_run_progress(runtime)
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_current_run_pending_warnings(runtime)
        self._clear_current_run_progress(runtime)
        return None

    def _drain_pending_warnings(self, runtime: Runtime) -> list[str]:
        """Pop and return all queued warnings for *runtime*'s thread/run."""
        pending_key = self._pending_key(runtime)
        with self._lock:
            warnings = self._pending_warnings.pop(pending_key, [])
            self._pending_warning_touch_order.pop(pending_key, None)
        return warnings

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        """Append queued loop warnings (if any) to the outgoing message list.

        The warning is placed *after* every existing message, including the
        ToolMessage responses to the previous AIMessage(tool_calls). This
        keeps ``assistant tool_calls -> tool_messages`` pairing intact for
        OpenAI/Moonshot, avoids the Anthropic mid-stream SystemMessage
        restriction (we use HumanMessage), and never mutates an existing
        AIMessage.
        """
        warnings = self._drain_pending_warnings(request.runtime)
        if not warnings:
            return request
        new_messages = [
            *request.messages,
            HumanMessage(content=self._format_warning_message(warnings), name="loop_warning"),
        ]
        return request.override(messages=new_messages)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._augment_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._augment_request(request))

    def reset(self, thread_id: str | None = None) -> None:
        """Clear tracking state. If thread_id given, clear only that thread."""
        with self._lock:
            if thread_id:
                self._clear_repetition_state_locked(thread_id)
                self._active_run_by_thread.pop(thread_id, None)
                for key in list(self._no_progress):
                    if key[0] == thread_id:
                        self._no_progress.pop(key, None)
                        self._no_progress_hard.discard(key)
                        self._last_result_marker.pop(key, None)
                for key in list(self._pending_warnings):
                    if key[0] == thread_id:
                        self._drop_pending_warning_key_locked(key)
            else:
                self._history.clear()
                self._warned.clear()
                self._tool_name_history.clear()
                self._tool_name_counter.clear()
                self._tool_freq_warned.clear()
                self._active_run_by_thread.clear()
                self._pending_warnings.clear()
                self._pending_warning_touch_order.clear()
                self._stop_reason.clear()
                self._no_progress.clear()
                self._no_progress_hard.clear()
                self._last_result_marker.clear()
