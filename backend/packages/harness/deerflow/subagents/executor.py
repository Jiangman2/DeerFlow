"""Subagent execution engine."""

import asyncio
import atexit
import logging
import os
import sys
import threading
import uuid
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextvars import Context, copy_context
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain.tools import BaseTool
from langchain_core.callbacks.base import BaseCallbackManager
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.errors import GraphRecursionError

from deerflow.agents.language_policy import ensure_default_chinese_language
from deerflow.agents.thread_state import SandboxState, ThreadDataState, ThreadState
from deerflow.authz.principal import normalize_authz_attributes
from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from deerflow.models import create_chat_model
from deerflow.runtime.runs.stream_cleanup import close_agent_stream
from deerflow.sandbox.lease import release_sandbox_execution_lease_async
from deerflow.skills.types import Skill
from deerflow.subagents.bash_evidence import BASH_EVIDENCE_MAX_ENTRIES, harvest_bash_executions
from deerflow.subagents.capacity import SubagentCapacityError, SubagentExecutionCapacity, get_subagent_execution_capacity
from deerflow.subagents.config import SubagentConfig, resolve_subagent_model_name
from deerflow.subagents.context_snapshot import SNAPSHOT_SYSTEM_NOTE, ParentContextSnapshot
from deerflow.subagents.step_events import capture_new_step_messages
from deerflow.subagents.token_collector import SubagentTokenCollector
from deerflow.trace_context import DEERFLOW_TRACE_METADATA_KEY, ensure_trace_context, resolve_trace_id
from deerflow.tracing import build_tracing_callbacks, inject_langfuse_metadata
from deerflow.utils.messages import message_content_to_text

if TYPE_CHECKING:
    # Imported lazily at runtime inside _build_initial_state: importing
    # tool_search eagerly would run tools/builtins/__init__ -> task_tool ->
    # `from deerflow.subagents import SubagentExecutor`, which re-enters this
    # still-initializing package. Type-only here keeps the annotation precise.
    from deerflow.tools.builtins.tool_search import DeferredToolSetup

logger = logging.getLogger(__name__)


_previous_shutdown_isolated_subagent_loop = globals().get("_shutdown_isolated_subagent_loop")
if callable(_previous_shutdown_isolated_subagent_loop):
    atexit.unregister(_previous_shutdown_isolated_subagent_loop)
    _previous_shutdown_isolated_subagent_loop()


class SubagentStatus(Enum):
    """Status of a subagent execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in {
            type(self).COMPLETED,
            type(self).FAILED,
            type(self).CANCELLED,
            type(self).TIMED_OUT,
        }


@dataclass
class SubagentResult:
    """Result of a subagent execution.

    Attributes:
        task_id: Unique identifier for this execution.
        trace_id: Trace ID for distributed tracing (links parent and subagent logs).
        status: Current status of the execution.
        result: The final result message (if completed).
        error: Error message (if failed).
        stop_reason: Why a guardrail cap ended the run early
            (``token_capped`` / ``turn_capped`` / ``loop_capped``), or ``None``
            for a clean run. A capped run keeps a normal status — ``completed``
            when it produced usable output (the partial work survives on
            ``result``), ``failed`` when it did not — and carries the cap here
            so the lead can tell "finished" from "capped" (#3875 Phase 2).
        started_at: When execution started.
        completed_at: When execution completed.
        ai_messages: List of complete AI messages (as dicts) generated during execution.
    """

    task_id: str
    trace_id: str
    status: SubagentStatus
    result: str | None = None
    error: str | None = None
    stop_reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    ai_messages: list[dict[str, Any]] | None = None
    token_usage_records: list[dict[str, int | str | None]] = field(default_factory=list)
    usage_reported: bool = False
    # None means no trustworthy citing snapshot; [] means an empty ledger.
    tool_receipts: list[dict[str, Any]] | None = field(default=None, kw_only=True)
    bash_executions: list[dict[str, Any]] | None = field(default=None, kw_only=True)
    admission_failure: bool = field(default=False, kw_only=True)
    external_task_id: str | None = field(default=None, kw_only=True)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _state_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.ai_messages is None:
            self.ai_messages = []

    def update_token_usage_records(self, records: list[dict[str, int | str | None]]) -> None:
        """Publish the latest cumulative collector snapshot while still running."""
        with self._state_lock:
            if not self.status.is_terminal:
                self.token_usage_records = list(records)

    def update_tool_receipts(self, receipts: list[dict[str, Any]] | None) -> None:
        if receipts is None:
            return
        with self._state_lock:
            if not self.status.is_terminal:
                self.tool_receipts = [dict(receipt) for receipt in receipts]

    def snapshot_tool_receipts(self) -> list[dict[str, Any]] | None:
        with self._state_lock:
            return None if self.tool_receipts is None else [dict(receipt) for receipt in self.tool_receipts]

    def update_bash_executions(self, executions: list[dict[str, Any]] | None) -> None:
        """Accumulate across compaction; the first terminal transition fences IO."""
        if executions is None:
            return
        with self._state_lock:
            if self.status.is_terminal:
                return
            merged = {entry["tool_call_id"]: dict(entry) for entry in self.bash_executions or []}
            for execution in executions:
                merged[execution["tool_call_id"]] = dict(execution)
            self.bash_executions = list(merged.values())[-BASH_EVIDENCE_MAX_ENTRIES:]

    def snapshot_bash_executions(self) -> list[dict[str, Any]] | None:
        with self._state_lock:
            return None if self.bash_executions is None else [dict(entry) for entry in self.bash_executions]

    def try_set_terminal(
        self,
        status: SubagentStatus,
        *,
        result: str | None = None,
        error: str | None = None,
        stop_reason: str | None = None,
        completed_at: datetime | None = None,
        ai_messages: list[dict[str, Any]] | None = None,
        token_usage_records: list[dict[str, int | str | None]] | None = None,
        tool_receipts: list[dict[str, Any]] | None = None,
        admission_failure: bool = False,
    ) -> bool:
        """Set a terminal status exactly once.

        Background timeout/cancellation and the execution worker can race on the
        same result holder.  The first terminal transition wins; late terminal
        writes must not change status or payload fields.
        """
        if not status.is_terminal:
            raise ValueError(f"Status {status} is not terminal")

        with self._state_lock:
            if self.status.is_terminal:
                return False

            if result is not None:
                self.result = result
            if error is not None:
                self.error = error
            if stop_reason is not None:
                self.stop_reason = stop_reason
            if ai_messages is not None:
                self.ai_messages = ai_messages
            if token_usage_records is not None:
                self.token_usage_records = token_usage_records
            if tool_receipts is not None:
                self.tool_receipts = [dict(receipt) for receipt in tool_receipts]
            elif status == SubagentStatus.COMPLETED:
                # Never reuse a running, renumbered scan when the actual
                # completed report lacks its citing-turn snapshot.
                self.tool_receipts = None
            self.completed_at = completed_at or datetime.now()
            self.admission_failure = admission_failure
            self.status = status
            return True


def _harvest_tool_receipts(final_state: Any, *, prefer_citing_turn: bool = False) -> list[dict[str, Any]] | None:
    """Harvest execution evidence without inventing a post-compaction id map."""
    messages = final_state.get("messages") if isinstance(final_state, dict) else None
    if not messages:
        return None
    try:
        from deerflow.agents.middlewares.tool_receipt import extract_citing_turn_receipts, extract_tool_receipts

        message_list = list(messages)
        citing_messages = message_list if prefer_citing_turn else [message_list[-1]]
        citing = extract_citing_turn_receipts(citing_messages) if prefer_citing_turn or isinstance(message_list[-1], AIMessage) else None
        receipts = citing if prefer_citing_turn else citing if citing is not None else extract_tool_receipts(message_list)
        return None if receipts is None else [dict(receipt) for receipt in receipts]
    except Exception:
        logger.warning("Failed to harvest subagent tool receipts")
        return None


def _extract_final_result(final_state: Any, *, trace_id: str, name: str) -> str:
    """Extract a human-readable result string from the streamed subagent state.

    Finds the last ``AIMessage`` in the conversation and stringifies its
    content via the shared :func:`message_content_to_text` helper; falls back
    to the last message of any type when no AIMessage is present. Returns a
    sentinel string (``"No response generated"``) when there is nothing to
    extract — including when the shared helper yields an empty string — so
    callers never confuse a missing result with a legitimately empty one.

    Used on both the normal-completion path and the max-turns path
    (#3875 Phase 2): when ``recursion_limit`` aborts the run mid-flight,
    ``final_state`` holds the last chunk streamed before the limit fired, so
    this recovers the partial work instead of dropping it.
    """
    if final_state is None:
        logger.warning(f"[trace={trace_id}] Subagent {name} no final state")
        return "No response generated"

    messages = final_state.get("messages", [])
    logger.info(f"[trace={trace_id}] Subagent {name} final messages count: {len(messages)}")

    last_ai_message = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            last_ai_message = msg
            break

    if last_ai_message is not None:
        text = message_content_to_text(last_ai_message.content)
        return text if text else "No response generated"

    if messages:
        last_message = messages[-1]
        logger.warning(f"[trace={trace_id}] Subagent {name} no AIMessage found, using last message: {type(last_message)}")
        raw_content = last_message.content if hasattr(last_message, "content") else str(last_message)
        text = message_content_to_text(raw_content)
        return text if text else "No response generated"

    logger.warning(f"[trace={trace_id}] Subagent {name} no messages in final state")
    return "No response generated"


def _extract_llm_error_fallback(final_state: Any) -> str | None:
    """Return the user-facing error for a terminal LLM fallback message.

    ``LLMErrorHandlingMiddleware`` converts provider exceptions into marked
    ``AIMessage`` objects so the graph can terminate cleanly. Clean graph
    termination is not task success, however: subagent callers need the
    structured marker translated into the existing failed terminal state.

    Only the last assistant message is authoritative, and scanning just the
    tail (rather than all messages) is deliberate. Subagents share the
    parent's ``thread_id`` (see ``_aexecute``'s ``run_config``), and LangGraph
    replays the full parent message history through ``stream_mode="values"``,
    so ``final_state`` can contain a *stale* fallback marker left by an earlier
    parent-history turn. The lead-agent run path scans every message and must
    mask those stale markers via ``pre_existing_message_ids``
    (``runtime/runs/worker.py::_extract_llm_error_fallback_message``). Here no
    masking is needed: a fallback ``AIMessage`` carries no ``tool_calls``, so it
    always terminates the run, and a subagent always appends at least its own
    terminal assistant message — the last ``AIMessage`` is therefore never a
    stale parent-history marker. Do not "fix" this by scanning all messages;
    that reintroduces the stale-marker false positive worker.py guards against.

    Error-looking message text without the marker remains ordinary output.
    """
    if final_state is None:
        return None

    for message in reversed(final_state.get("messages", [])):
        if not isinstance(message, AIMessage):
            continue

        metadata = message.additional_kwargs
        if metadata.get("deerflow_error_fallback") is not True:
            return None

        content = message_content_to_text(message.content).strip()
        if content:
            return content

        # Defensive: ``_build_error_fallback_message`` always sets a non-empty
        # user-facing ``content`` (and ``error_detail`` via ``_extract_error_detail``,
        # which falls back to the exception class name). These branches only
        # guard against a future middleware that emits an empty fallback.
        detail = metadata.get("error_detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        return "LLM request failed"

    return None


# Global storage for background task results
_background_tasks: dict[str, SubagentResult] = {}
_background_tasks_lock = threading.Lock()

# Background execution waits asynchronously for the bounded process capacity.
_background_futures: dict[str, Future[SubagentResult]] = {}

# Persistent event loop for isolated subagent executions triggered from an
# already-running parent loop. Reusing one long-lived loop avoids creating a
# fresh loop per execution and then closing async resources bound to it.
_isolated_subagent_loop: asyncio.AbstractEventLoop | None = None
_isolated_subagent_loop_thread: threading.Thread | None = None
_isolated_subagent_loop_started: threading.Event | None = None
_isolated_subagent_loop_lock = threading.Lock()


def _run_isolated_subagent_loop(
    loop: asyncio.AbstractEventLoop,
    started_event: threading.Event,
) -> None:
    """Run the persistent isolated subagent loop in a dedicated daemon thread."""
    asyncio.set_event_loop(loop)
    loop.call_soon(started_event.set)
    try:
        loop.run_forever()
    finally:
        started_event.clear()


def _shutdown_isolated_subagent_loop() -> None:
    """Stop and close the persistent isolated subagent loop."""
    global _isolated_subagent_loop, _isolated_subagent_loop_thread, _isolated_subagent_loop_started

    with _isolated_subagent_loop_lock:
        loop = _isolated_subagent_loop
        thread = _isolated_subagent_loop_thread
        _isolated_subagent_loop = None
        _isolated_subagent_loop_thread = None
        _isolated_subagent_loop_started = None

    if loop is None:
        return

    if loop.is_running():
        loop.call_soon_threadsafe(loop.stop)

    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=1)

    thread_stopped = thread is None or not thread.is_alive()
    loop_stopped = not loop.is_running()

    if not loop.is_closed():
        if thread_stopped and loop_stopped:
            loop.close()
        else:
            logger.warning(
                "Skipping close of isolated subagent loop because shutdown did not complete within timeout (thread_alive=%s, loop_running=%s)",
                thread is not None and thread.is_alive(),
                loop.is_running(),
            )


atexit.register(_shutdown_isolated_subagent_loop)


def _get_isolated_subagent_loop() -> asyncio.AbstractEventLoop:
    """Return the persistent event loop used by isolated subagent executions."""
    global _isolated_subagent_loop, _isolated_subagent_loop_thread, _isolated_subagent_loop_started
    with _isolated_subagent_loop_lock:
        thread_is_alive = _isolated_subagent_loop_thread is not None and _isolated_subagent_loop_thread.is_alive()
        loop_is_usable = _isolated_subagent_loop is not None and not _isolated_subagent_loop.is_closed() and _isolated_subagent_loop.is_running() and thread_is_alive

        if not loop_is_usable:
            loop = asyncio.new_event_loop()
            started_event = threading.Event()
            thread = threading.Thread(
                target=_run_isolated_subagent_loop,
                args=(loop, started_event),
                name="subagent-persistent-loop",
                daemon=True,
            )
            thread.start()
            if not started_event.wait(timeout=5):
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=1)
                loop.close()
                raise RuntimeError("Timed out starting isolated subagent event loop")
            _isolated_subagent_loop = loop
            _isolated_subagent_loop_thread = thread
            _isolated_subagent_loop_started = started_event

        if _isolated_subagent_loop is None:
            raise RuntimeError("Isolated subagent event loop is not initialized")
        return _isolated_subagent_loop


def _submit_to_isolated_loop_in_context(
    context: Context,
    coro_factory: Callable[[], Coroutine[Any, Any, SubagentResult]],
) -> Future[SubagentResult]:
    """Preserve context and release coroutines rejected before scheduling.

    Resolve the loop before creating the coroutine: a startup failure must not
    retain an unscheduled coroutine and its captured parent resources.
    """

    def submit() -> Future[SubagentResult]:
        loop = _get_isolated_subagent_loop()
        coroutine = coro_factory()
        try:
            return asyncio.run_coroutine_threadsafe(coroutine, loop)
        except BaseException:
            coroutine.close()
            raise

    return context.run(submit)


def _copy_isolated_subagent_context() -> Context:
    """Copy identity/lineage/stream context without loop-bound parent callbacks.

    An explicit child collector alone is insufficient: LangGraph merges it with
    the inherited callback manager. Copy that manager before filtering either
    handler list so the parent's own journal and framework streaming stay intact.
    """
    context = copy_context()
    inherited_config = context.get(var_child_runnable_config)
    if inherited_config is None or "callbacks" not in inherited_config:
        return context

    callbacks = inherited_config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        isolated_callbacks = callbacks.copy()
        isolated_callbacks.handlers = [handler for handler in callbacks.handlers if not getattr(handler, "deerflow_loop_bound", False)]
        isolated_callbacks.inheritable_handlers = [handler for handler in callbacks.inheritable_handlers if not getattr(handler, "deerflow_loop_bound", False)]
    elif isinstance(callbacks, (list, tuple)):
        isolated_callbacks = [handler for handler in callbacks if not getattr(handler, "deerflow_loop_bound", False)]
    elif getattr(callbacks, "deerflow_loop_bound", False):
        isolated_callbacks = None
    else:
        isolated_callbacks = callbacks

    isolated_config = inherited_config.copy()
    if isolated_callbacks:
        isolated_config["callbacks"] = isolated_callbacks
    else:
        isolated_config.pop("callbacks", None)
    context.run(var_child_runnable_config.set, isolated_config)
    return context


def _filter_tools(
    all_tools: list[BaseTool],
    allowed: list[str] | None,
    disallowed: list[str] | None,
) -> list[BaseTool]:
    """Filter tools based on subagent configuration.

    Args:
        all_tools: List of all available tools.
        allowed: Optional allowlist of tool names. If provided, only these tools are included.
        disallowed: Optional denylist of tool names. These tools are always excluded.

    Returns:
        Filtered list of tools.
    """
    filtered = all_tools

    # Apply allowlist if specified
    if allowed is not None:
        allowed_set = set(allowed)
        filtered = [t for t in filtered if t.name in allowed_set]

    # Apply denylist
    if disallowed is not None:
        disallowed_set = set(disallowed)
        filtered = [t for t in filtered if t.name not in disallowed_set]

    return filtered


class SubagentExecutor:
    """Executor for running subagents."""

    def __init__(
        self,
        config: SubagentConfig,
        tools: list[BaseTool],
        app_config: AppConfig | None = None,
        parent_model: str | None = None,
        sandbox_state: SandboxState | None = None,
        thread_data: ThreadDataState | None = None,
        thread_id: str | None = None,
        trace_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        oauth_provider: str | None = None,
        oauth_id: str | None = None,
        run_id: str | None = None,
        channel_user_id: str | None = None,
        is_internal: bool = False,
        authz_attributes: Mapping[str, Any] | None = None,
        deerflow_trace_id: str | None = None,
        datalake_enabled: bool = True,
        loop_detection_recorder: Any | None = None,
        tool_promotion_recorder: Any | None = None,
        tool_progress_recorder: Any | None = None,
        acceptance_criteria: list[str] | None = None,
        execution_capacity: SubagentExecutionCapacity | None = None,
        context_snapshot: ParentContextSnapshot | None = None,
    ):
        """Initialize the executor.

        Args:
            config: Subagent configuration.
            tools: List of all available tools (will be filtered).
            app_config: Resolved AppConfig. When None, ``_create_agent`` falls
                back to ``get_app_config()`` (matches the lead-agent factory's
                pattern).
            parent_model: The parent agent's model name for inheritance.
            sandbox_state: Sandbox state from parent agent.
            thread_data: Thread data from parent agent.
            thread_id: Thread ID for sandbox operations.
            trace_id: Trace ID from parent for distributed tracing.
            user_id: User ID captured from the parent tool's runtime context.
                When None, the tracing layer falls back to DEFAULT_USER_ID.
            user_role: Authenticated user's role, propagated so GuardrailMiddleware
                on the subagent can apply role-aware policy to delegated calls.
            oauth_provider: External identity provider, when authenticated via SSO.
            oauth_id: Subject id at the external identity provider.
            run_id: Parent run id, so delegated guardrail decisions attribute to
                the same run as the lead agent.
            deerflow_trace_id: DeerFlow request-level correlation id propagated
                from the parent run for Langfuse metadata correlation.
            datalake_enabled: Whether AppForge Data Lake tools are available to
                this delegated run.
            loop_detection_recorder: Private parent-loop audit bridge, never a
                raw journal or caller-supplied runtime context.
            tool_promotion_recorder: Private bridge with child-scoped discovery
                claims; no parent run's mutable claim state is shared.
            tool_progress_recorder: Private bridge for child progress decisions.
            acceptance_criteria: Untrusted task requirements, never system authority.
            execution_capacity: Explicit shared controller, otherwise the startup singleton.
            context_snapshot: Immutable parent history captured at dispatch, rendered
                as background data, never child evidence or inherited authority.
        """
        self.config = config
        self.execution_capacity = execution_capacity
        self.context_snapshot = context_snapshot
        from deerflow.subagents.report_contract import normalize_acceptance_criteria

        self.acceptance_criteria = normalize_acceptance_criteria(acceptance_criteria)
        self.app_config = app_config
        self._resolved_app_config = app_config
        self.parent_model = parent_model
        # Resolve eagerly only when it does not require loading config.yaml; otherwise defer
        # to _create_agent (which already loads app_config) so unit tests can construct
        # executors without a config file present.
        if config.model != "inherit" or parent_model is not None or app_config is not None:
            self.model_name: str | None = resolve_subagent_model_name(config, parent_model, app_config=app_config)
        else:
            self.model_name = None
        self.sandbox_state = sandbox_state
        self.thread_data = thread_data
        self.thread_id = thread_id
        # Generate trace_id if not provided (for top-level calls)
        self.trace_id = trace_id or str(uuid.uuid4())[:8]
        self.user_id = user_id
        # Guardrail attribution propagated from the parent runtime context.
        self.user_role = user_role
        self.oauth_provider = oauth_provider
        self.oauth_id = oauth_id
        self.run_id = run_id
        # IM-channel sender identity captured at task_tool dispatch: group
        # chats share one thread across senders, so delegated bash commands
        # must export the dispatching turn's id, not none at all.
        self.channel_user_id = channel_user_id
        # Authorization identity propagated from the parent runtime context.
        # is_internal is written unconditionally (including False) so the
        # subagent's GuardrailMiddleware sees the same provenance as the lead.
        self.is_internal = is_internal
        self.authz_attributes = normalize_authz_attributes(authz_attributes)
        self.deerflow_trace_id = resolve_trace_id(deerflow_trace_id)
        self.datalake_enabled = datalake_enabled is not False
        # Never pass the parent journal itself across isolated execution loops.
        self.loop_detection_recorder = loop_detection_recorder
        self.tool_promotion_recorder = tool_promotion_recorder
        self.tool_progress_recorder = tool_progress_recorder

        from deerflow.runtime.employee_resources import read_employee_publication, scope_employee_tools

        self.employee_publication = read_employee_publication(thread_id, user_id)
        self._base_tools = _filter_tools(
            scope_employee_tools(tools, self.employee_publication),
            config.tools,
            config.disallowed_tools,
        )
        from deerflow.tools.tools import filter_tools_by_datalake_scope

        self._base_tools = filter_tools_by_datalake_scope(
            self._base_tools,
            enabled=self.datalake_enabled,
        )
        self.tools = self._base_tools
        # Prompt discovery and both activation/policy consumers share this
        # exact catalog. An empty set is deny-all, never an unrestricted None.
        self._available_skill_names: set[str] = set()
        self._authorization_provider = None
        # Guard middlewares that expose ``consume_stop_reason`` (currently
        # ``TokenBudgetMiddleware`` and ``LoopDetectionMiddleware``), captured in
        # ``_create_agent`` so ``_aexecute`` can read each after the run and
        # surface whichever cap fired (token_capped / loop_capped) to the lead
        # (#3875 Phase 2). Collected as a list — every guard must be checked,
        # not just the first — because the v2 contract advertises more than one
        # cap reason.
        self._stop_reason_middlewares: list[Any] = []

        logger.info(f"[trace={self.trace_id}] SubagentExecutor initialized: {config.name} with {len(self.tools)} tools")

    def _get_resolved_app_config(self):
        if self._resolved_app_config is None:
            self._resolved_app_config = get_app_config()
        return self._resolved_app_config

    def _create_agent(self, tools: list[BaseTool] | None = None, *, deferred_setup: "DeferredToolSetup | None" = None):
        """Create the agent instance.

        ``deferred_setup`` (assembled in ``_build_initial_state``) carries the
        deferred MCP tool names + catalog hash so the subagent gets the same
        DeferredToolFilterMiddleware the lead agent has. ``None`` is a no-op.
        """
        app_config = self._get_resolved_app_config()
        if self.model_name is None:
            self.model_name = resolve_subagent_model_name(self.config, self.parent_model, app_config=app_config)
        model = create_chat_model(name=self.model_name, thinking_enabled=False, app_config=app_config, attach_tracing=False)

        from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares

        # Reuse shared middleware composition with lead agent. ``agent_name``
        # lets the builder resolve the per-agent token_budget override.
        mcp_routing_middleware = None
        if deferred_setup is not None and deferred_setup.deferred_names:
            from deerflow.tools.builtins.tool_search import build_mcp_routing_middleware

            mcp_routing_middleware = build_mcp_routing_middleware(
                tools if tools is not None else self.tools,
                deferred_setup,
                top_k=app_config.tool_search.auto_promote_top_k,
            )
        middleware_kwargs = {
            "app_config": app_config,
            "model_name": self.model_name,
            "lazy_init": True,
            "deferred_setup": deferred_setup,
            "agent_name": self.config.name,
            "available_skills": self._available_skill_names,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "authorization_provider": self._authorization_provider,
        }
        if mcp_routing_middleware is not None:
            middleware_kwargs["mcp_routing_middleware"] = mcp_routing_middleware
        middlewares = build_subagent_runtime_middlewares(**middleware_kwargs)
        if self.employee_publication is not None:
            from deerflow.agents.middlewares.employee_publication_middleware import EmployeePublicationMiddleware
            from deerflow.agents.middlewares.weknora_scope_middleware import WeKnoraScopeMiddleware
            from deerflow.runtime.employee_resources import has_delegated_knowledge

            middlewares.insert(0, EmployeePublicationMiddleware(thread_id=self.thread_id, user_id=self.user_id))
            middlewares.append(WeKnoraScopeMiddleware(delegated=has_delegated_knowledge(self.employee_publication)))
        # Collect every guard middleware that exposes ``consume_stop_reason``
        # (TokenBudgetMiddleware, LoopDetectionMiddleware) so _aexecute can read
        # each after the run and surface whichever cap fired. Duck-typed
        # (``hasattr``) so this file needs no import of the middleware classes;
        # a list (not ``next(...)``) so every guard is checked and a later one
        # is picked up automatically.
        self._stop_reason_middlewares = [m for m in middlewares if hasattr(m, "consume_stop_reason")]

        # system_prompt is included in initial state messages (see _build_initial_state)
        # to avoid multiple SystemMessages which some LLM APIs don't support.
        return create_agent(
            model=model,
            tools=tools if tools is not None else self.tools,
            middleware=middlewares,
            system_prompt=None,
            state_schema=ThreadState,
            checkpointer=False,
        )

    def _consume_guard_stop_reason(self) -> str | None:
        """Pop and return the guard-cap stop reason set during the last run.

        Checks every guard middleware that exposes ``consume_stop_reason``
        (collected in :meth:`_create_agent`) and returns the first non-``None``
        reason — ``"token_capped"`` when the token-budget hard stop fired,
        ``"loop_capped"`` when loop detection forced a stop, otherwise ``None``.
        Each guard's cap does not raise (the run still completes with a final
        answer), so this is how the executor learns a completion was actually
        capped. Typically at most one guard fires per run, but checking all of
        them keeps the contract's full cap vocabulary reachable.
        """
        for mw in self._stop_reason_middlewares:
            reason = mw.consume_stop_reason(self.run_id)
            if reason is not None:
                return reason
        return None

    async def _load_skills(self) -> list[Skill]:
        """Load enabled skill metadata based on config.skills."""
        if self.config.skills is not None and len(self.config.skills) == 0:
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} skills=[] — skipping skill loading")
            return []

        try:
            from deerflow.skills.agent_publication import get_thread_published_skill_storage
            from deerflow.skills.storage import get_or_new_skill_storage, get_or_new_user_skill_storage

            storage_kwargs = {"app_config": self.app_config} if self.app_config is not None else {}
            storage = None
            if self.user_id:
                storage = await asyncio.to_thread(get_thread_published_skill_storage, self.thread_id, user_id=self.user_id, **storage_kwargs)
            if storage is None:
                if self.user_id:
                    storage = await asyncio.to_thread(get_or_new_user_skill_storage, self.user_id, **storage_kwargs)
                else:
                    storage = await asyncio.to_thread(get_or_new_skill_storage, **storage_kwargs)
            # Use asyncio.to_thread to avoid blocking the event loop (LangGraph ASGI requirement)
            all_skills = await asyncio.to_thread(storage.load_skills, enabled_only=True)
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} loaded {len(all_skills)} enabled skills from disk")
        except Exception:
            logger.exception(f"[trace={self.trace_id}] Failed to load skills for subagent {self.config.name}")
            raise

        if not all_skills:
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} no enabled skills found")
            return []

        # Filter by config.skills whitelist
        if self.config.skills is not None:
            allowed = set(self.config.skills)
            return [s for s in all_skills if s.name in allowed]
        return all_skills

    async def _build_initial_state(self, task: str) -> tuple[dict[str, Any], list[BaseTool], "DeferredToolSetup"]:
        """Build the initial state for agent execution.

        Args:
            task: The task description.

        Returns:
            ``(state, final_tools, deferred_setup)``. ``final_tools`` is the
            scoped tool list with discovery helpers appended when deferral
            applies; ``deferred_setup`` is consumed by ``_create_agent``
            so the agent build and the injected ``<available-deferred-tools>``
            section share one catalog/hash.
        """
        # Lazy import: see the TYPE_CHECKING note at the top of this module -
        # importing tool_search runs tools/builtins/__init__, which would
        # re-enter this package during its own initialization.
        from deerflow.tools.builtins.tool_search import assemble_deferred_tools, get_deferred_tools_prompt_section, get_mcp_routing_hints_prompt_section

        # Enabled skills are discovery metadata, not an active workflow or a
        # global union of allowed-tools. Middleware enforces the active policy
        # at both model binding and execution time.
        skills = await self._load_skills()
        self._available_skill_names = {skill.name for skill in skills}
        resolved_app_config = self._get_resolved_app_config()
        from deerflow.skills.describe import build_skill_search_setup, get_skill_index_prompt_section

        skill_setup = build_skill_search_setup(
            skills,
            enabled=resolved_app_config.skills.deferred_discovery,
            container_base_path=resolved_app_config.skills.container_path,
        )
        filtered_tools = list(self._base_tools)
        if skill_setup.describe_skill_tool is not None:
            filtered_tools.append(skill_setup.describe_skill_tool)
        from deerflow.authz.model_authorization import authorize_model_name_async
        from deerflow.authz.runtime import construct_authorization_provider, resolve_authorization_provider_spec
        from deerflow.authz.tool_filter import apply_tool_authorization

        # Import discovery can block; loop-bound providers must be constructed
        # here, on the child execution loop, not inside a worker thread.
        authz_spec = await asyncio.to_thread(resolve_authorization_provider_spec, resolved_app_config.authorization) if resolved_app_config.authorization.enabled is True else None
        self._authorization_provider = construct_authorization_provider(authz_spec, resolved_app_config.authorization)
        authz_context = {
            "user_id": self.user_id,
            "user_role": self.user_role,
            "oauth_provider": self.oauth_provider,
            "oauth_id": self.oauth_id,
            "channel_user_id": self.channel_user_id,
            "is_internal": self.is_internal,
            "authz_attributes": self.authz_attributes,
        }
        self.model_name = await authorize_model_name_async(self.model_name, context=authz_context, app_config=resolved_app_config, authorization_provider=self._authorization_provider)
        filtered_tools, self._authorization_provider = apply_tool_authorization(
            filtered_tools,
            context=authz_context,
            app_config=resolved_app_config,
            authorization_provider=self._authorization_provider,
        )
        # Assemble deferred tool_search AFTER enterprise/config filtering,
        # mirroring the lead path so subagents stop binding full MCP schemas.
        # The generated tool_search helper is intentionally not subject to the
        # subagent's name-level allow/deny (config.tools / disallowed_tools):
        # its catalog is built from the already-filtered list, so it can never
        # surface a tool the policy denied. This matches the lead agent.
        enabled = resolved_app_config.tool_search.enabled
        final_tools, deferred_setup = assemble_deferred_tools(filtered_tools, enabled=enabled)

        # Combine system_prompt and discovery metadata into one SystemMessage.
        # Some LLM APIs reject multiple SystemMessages with
        # "System message must be at the beginning."
        system_parts: list[str] = []
        if self.config.system_prompt:
            system_parts.append(self.config.system_prompt)
        if self.context_snapshot is not None:
            system_parts.append(SNAPSHOT_SYSTEM_NOTE)
        from deerflow.subagents.report_contract import build_acceptance_criteria_system_note, build_report_contract_section, render_acceptance_criteria_block

        verification_cfg = getattr(resolved_app_config, "verification", None)
        receipts_enabled = getattr(verification_cfg, "receipts_enabled", True)
        system_parts.append(build_report_contract_section(receipts_enabled=receipts_enabled))
        criteria_block = render_acceptance_criteria_block(self.acceptance_criteria)
        if criteria_block:
            system_parts.append(build_acceptance_criteria_system_note(receipts_enabled=receipts_enabled))
        if skills:
            if skill_setup.skill_names:
                skills_section = get_skill_index_prompt_section(
                    skill_names=skill_setup.skill_names,
                    container_base_path=resolved_app_config.skills.container_path,
                )
            else:
                from deerflow.agents.lead_agent.prompt import get_skills_prompt_section

                # Keep the same business thread and identity as _load_skills;
                # a published employee must never resolve the viewer's namesake.
                skills_section = await asyncio.to_thread(
                    get_skills_prompt_section,
                    self._available_skill_names,
                    app_config=resolved_app_config,
                    user_id=self.user_id,
                    thread_id=self.thread_id,
                )
            if skills_section:
                system_parts.append(skills_section)
        # Name the deferred MCP tools in the prompt; their schemas stay withheld
        # until tool_search promotes them. Empty set -> "" -> appends nothing.
        deferred_section = get_deferred_tools_prompt_section(deferred_names=deferred_setup.deferred_names)
        if deferred_section:
            system_parts.append(deferred_section)
        mcp_routing_hints_section = get_mcp_routing_hints_prompt_section(filtered_tools, deferred_names=deferred_setup.deferred_names)
        if mcp_routing_hints_section:
            system_parts.append(mcp_routing_hints_section)

        messages: list[Any] = []
        if system_parts:
            messages.append(SystemMessage(content=ensure_default_chinese_language("\n\n".join(system_parts))))

        if self.context_snapshot is not None:
            messages.append(self.context_snapshot.to_message())

        # Then the actual task
        task_content = f"{task}\n\n{criteria_block}" if criteria_block else task
        messages.append(HumanMessage(content=task_content))

        state: dict[str, Any] = {
            "messages": messages,
        }

        # Pass through sandbox and thread data from parent
        if self.sandbox_state is not None:
            state["sandbox"] = self.sandbox_state
        if self.thread_data is not None:
            state["thread_data"] = self.thread_data

        return state, final_tools, deferred_setup

    async def _aexecute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Bound native work before model, Skill or sandbox setup; keep queued work pending."""
        result = result_holder or SubagentResult(task_id=str(uuid.uuid4()), trace_id=self.trace_id, status=SubagentStatus.PENDING)
        with ensure_trace_context(self.deerflow_trace_id):
            if result.status.is_terminal:
                return result
            if result.cancel_event.is_set():
                result.try_set_terminal(SubagentStatus.CANCELLED, error="Cancelled by user")
                return result
            try:
                capacity = self.execution_capacity or get_subagent_execution_capacity()
                async with capacity.slot():
                    with result._state_lock:
                        if result.status.is_terminal:
                            return result
                        if not result.cancel_event.is_set():
                            result.status = SubagentStatus.RUNNING
                            result.started_at = datetime.now()
                    if result.cancel_event.is_set():
                        result.try_set_terminal(SubagentStatus.CANCELLED, error="Cancelled by user")
                        return result
                    # Keep the slot through existing stream and lease cleanup.
                    return await self._aexecute_in_trace(task, result)
            except SubagentCapacityError as exc:
                result.try_set_terminal(SubagentStatus.FAILED, error=str(exc), admission_failure=True)
                return result

    async def _aexecute_in_trace(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Execute a task asynchronously.

        Args:
            task: The task description for the subagent.
            result_holder: Optional pre-created result object to update during execution.

        Returns:
            SubagentResult with the execution result.
        """
        if result_holder is not None:
            # Use the provided result holder (for async execution with real-time updates)
            result = result_holder
        else:
            # Create a new result for synchronous execution
            task_id = str(uuid.uuid4())[:8]
            result = SubagentResult(
                task_id=task_id,
                trace_id=self.trace_id,
                status=SubagentStatus.RUNNING,
                started_at=datetime.now(),
            )
        ai_messages = result.ai_messages
        if ai_messages is None:
            ai_messages = []
            result.ai_messages = ai_messages
        # O(1) duplicate detection for streamed AI messages. ``stream_mode="values"``
        # re-yields the full state every super-step, so the same trailing message is
        # re-examined on each chunk; an id-keyed set keeps that check O(1) instead of
        # rescanning the append-only ``ai_messages`` list (O(n) per chunk -> O(n^2)
        # over a run, which reaches max_turns=150 for deep-research subagents).
        seen_message_ids: set[str] = {mid for msg in ai_messages if (mid := msg.get("id"))}
        # Cursor into the append-only message history so each ``values``-mode
        # chunk only re-scans the newly-appended tail (see capture_new_step_messages).
        processed_message_count = 0

        collector: SubagentTokenCollector | None = None
        context: dict[str, Any] | None = None
        final_state = None
        verification_cfg = None

        def terminal_receipts(*, prefer_citing_turn: bool = False):
            if not getattr(verification_cfg, "receipts_enabled", True):
                return None
            return _harvest_tool_receipts(final_state, prefer_citing_turn=prefer_citing_turn)

        try:
            verification_cfg = getattr(self._get_resolved_app_config(), "verification", None)
            state, final_tools, deferred_setup = await self._build_initial_state(task)
            agent = self._create_agent(final_tools, deferred_setup=deferred_setup)

            # Token collector for subagent LLM calls
            collector_caller = f"subagent:{self.config.name}"
            collector = SubagentTokenCollector(caller=collector_caller)

            # Do not put checkpoint coordinates (thread_id/checkpoint_ns/etc.)
            # in the child config. LangGraph inherits those coordinates from
            # the ambient parent run so this execution keeps its subgraph
            # namespace. Business consumers receive thread_id via ``context``
            # below instead.
            run_config: RunnableConfig = {
                "recursion_limit": self.config.max_turns,
                "callbacks": [collector],
                "tags": [collector_caller],
                "metadata": {DEERFLOW_TRACE_METADATA_KEY: self.deerflow_trace_id},
            }

            # Inject tracing callbacks at the graph level so a single subagent run
            # produces one trace with all node / LLM / tool calls as child spans.
            # This mirrors the lead agent pattern: graph-level tracing paired with
            # attach_tracing=False on the model avoids double-counted traces.
            tracing_callbacks = build_tracing_callbacks()
            if tracing_callbacks:
                existing_callbacks = list(run_config.get("callbacks") or [])
                run_config["callbacks"] = [*existing_callbacks, *tracing_callbacks]

            # Normalize subagent name for tracing so it matches the lead-agent
            # naming shape (lowercase, hyphens only). Inline because there is no
            # shared helper — runtime/runs/naming.py only handles lead-agent runs.
            if self.config.name:
                normalized_name = self.config.name.strip().lower().replace("_", "-")
                assistant_id = f"subagent:{normalized_name}"
            else:
                assistant_id = "subagent"

            # Inject Langfuse trace-attribute metadata so the subagent trace
            # links to the parent thread and carries the correct session/user IDs.
            inject_langfuse_metadata(
                run_config,
                thread_id=self.thread_id,
                user_id=self.user_id,
                assistant_id=assistant_id,
                model_name=self.model_name,
                environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
                deerflow_trace_id=self.deerflow_trace_id,
            )

            context: dict[str, Any] = {}
            if self.thread_id:
                context["thread_id"] = self.thread_id
            if self.app_config is not None:
                context["app_config"] = self.app_config
            # Propagate guardrail attribution so delegated tool calls are
            # evaluated with the parent run's identity (role-aware policy,
            # audit). user_id reuses the resolved tracing id; on every
            # authenticated/IM path this equals the parent context value.
            context["user_id"] = self.user_id
            context["user_role"] = self.user_role
            context["oauth_provider"] = self.oauth_provider
            context["oauth_id"] = self.oauth_id
            context["run_id"] = self.run_id
            if self.channel_user_id:
                context["channel_user_id"] = self.channel_user_id
            # Authorization identity: is_internal written unconditionally
            # (including False); attributes copied again on write-back.
            context["is_internal"] = self.is_internal
            context["authz_attributes"] = dict(self.authz_attributes)
            context[DEERFLOW_TRACE_METADATA_KEY] = self.deerflow_trace_id
            context["datalake_enabled"] = self.datalake_enabled
            context["is_subagent"] = True
            if self.loop_detection_recorder is not None:
                from deerflow.agents.middlewares.audit_context import LOOP_DETECTION_RECORDER_CONTEXT_KEY

                context[LOOP_DETECTION_RECORDER_CONTEXT_KEY] = self.loop_detection_recorder
                context["agent_id"] = self.config.name
            if self.tool_promotion_recorder is not None:
                from deerflow.agents.middlewares.audit_context import TOOL_PROMOTION_RECORDER_CONTEXT_KEY

                context[TOOL_PROMOTION_RECORDER_CONTEXT_KEY] = self.tool_promotion_recorder
                context["agent_id"] = self.config.name
            if self.tool_progress_recorder is not None:
                from deerflow.agents.middlewares.audit_context import TOOL_PROGRESS_RECORDER_CONTEXT_KEY

                context[TOOL_PROGRESS_RECORDER_CONTEXT_KEY] = self.tool_progress_recorder
                context["agent_id"] = self.config.name

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} starting async execution with max_turns={self.config.max_turns}")

            # Use stream instead of invoke to get real-time updates
            # This allows us to collect AI messages as they are generated
            # Pre-check: bail out immediately if already cancelled before streaming starts
            if result.cancel_event.is_set():
                logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} cancelled before streaming")
                result.try_set_terminal(
                    SubagentStatus.CANCELLED,
                    error="Cancelled by user",
                    token_usage_records=collector.snapshot_records(),
                )
                return result

            cancelled_during_stream = False
            stream = agent.astream(state, config=run_config, context=context, stream_mode="values")  # type: ignore[arg-type]
            try:
                async for chunk in stream:
                    # A yielded chunk has already executed, including when
                    # cancellation raced with the last tool's completion.
                    final_state = chunk
                    result.update_tool_receipts(terminal_receipts())
                    if self.acceptance_criteria:
                        result.update_bash_executions(harvest_bash_executions(final_state, thread_id=self.thread_id, user_id=self.user_id))
                    # Cooperative cancellation: check if parent requested stop.
                    # Note: cancellation is only detected at astream iteration boundaries,
                    # so long-running tool calls within a single iteration will not be
                    # interrupted until the next chunk is yielded.
                    if result.cancel_event.is_set():
                        logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} cancelled by parent")
                        cancelled_during_stream = True
                        break

                    result.update_token_usage_records(collector.snapshot_records())

                    # Capture every step message (assistant turns AND tool outputs)
                    # appended since the last chunk. A single super-step can append
                    # several ToolMessages when the model emits multiple tool calls in
                    # one turn, so capturing only messages[-1] would drop all but the
                    # last output (#3779). Dedup/serialization live in capture_step_message.
                    messages = chunk.get("messages", [])
                    previous_count = len(ai_messages)
                    processed_message_count = capture_new_step_messages(messages, ai_messages, seen_message_ids, processed_message_count)
                    if len(ai_messages) > previous_count:
                        logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} captured {len(ai_messages) - previous_count} step message(s); total #{len(ai_messages)}")
            finally:
                active_error = sys.exception()
                cancel_requested = cancelled_during_stream or result.cancel_event.is_set()
                try:
                    await close_agent_stream(stream)
                except Exception:
                    cancel_requested = cancel_requested or result.cancel_event.is_set()
                    if active_error is None and not cancel_requested:
                        raise
                    logger.warning(
                        "[trace=%s] Could not close interrupted subagent stream %s",
                        self.trace_id,
                        self.config.name,
                        exc_info=True,
                    )

            if cancel_requested:
                result.try_set_terminal(
                    SubagentStatus.CANCELLED,
                    error="Cancelled by user",
                    token_usage_records=collector.snapshot_records(),
                    tool_receipts=terminal_receipts(),
                )
                return result

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} completed async execution")
            token_usage_records = collector.snapshot_records()
            llm_error = _extract_llm_error_fallback(final_state)
            if llm_error is not None:
                result.try_set_terminal(
                    SubagentStatus.FAILED,
                    error=llm_error,
                    token_usage_records=token_usage_records,
                    tool_receipts=terminal_receipts(),
                )
            else:
                final_result = _extract_final_result(final_state, trace_id=self.trace_id, name=self.config.name)
                # A guard hard-stop (token budget or loop detection) does not raise
                # — it strips tool_calls so the run completes with a final answer.
                # ``consume_stop_reason`` on each guard tells us whether that
                # happened so we can mark the completed result with the cap reason
                # (token_capped / loop_capped) for the lead (#3875 Phase 2). It
                # pops the reason, so keep it on the branch that consumes it — a
                # fallback carries no tool_calls, so no guard hard-stop can have
                # co-occurred on the FAILED branch anyway.
                stop_reason = self._consume_guard_stop_reason()
                result.try_set_terminal(
                    SubagentStatus.COMPLETED,
                    result=final_result,
                    stop_reason=stop_reason,
                    token_usage_records=token_usage_records,
                    tool_receipts=terminal_receipts(prefer_citing_turn=True),
                )

        except GraphRecursionError:
            # ``recursion_limit`` on run_config == ``self.config.max_turns``
            # (set above). Hitting it means the subagent exhausted its turn
            # budget. Route into the additive ``stop_reason`` channel (#3875
            # Phase 2) rather than a dedicated status enum (which would break v1
            # contract consumers). If the run streamed usable partial work,
            # surface it as ``completed``; otherwise ``failed``. Either way the
            # lead can tell "out of budget" from "broken subagent" without
            # parsing result text.
            #
            # Prefer a guard's stop reason if one already fired this run: a
            # token-budget / loop hard-stop strips tool_calls to force a final
            # answer, and if ``recursion_limit`` then trips on the next
            # super-step before that answer lands, the guard was the binding
            # constraint — not the turn budget. Consulting the guards here (same
            # lookup as the normal-completion path above) keeps the two paths
            # consistent and pops the reason so it is not orphaned in the dict.
            max_turns = self.config.max_turns
            logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} reached max_turns={max_turns} (GraphRecursionError); recovering partial result")
            records = collector.snapshot_records() if collector is not None else None
            stop_reason = self._consume_guard_stop_reason() or "turn_capped"

            # A handled LLM provider failure (#4042) carries non-empty
            # user-facing text on its terminal ``AIMessage`` just like genuine
            # partial output, so it must be checked here too or it is
            # indistinguishable from the raw-text scan below and gets
            # misclassified as a completed task. Consult the same marker the
            # normal-completion path above uses, before falling back to that scan.
            llm_error = _extract_llm_error_fallback(final_state)
            if llm_error is not None:
                result.try_set_terminal(
                    SubagentStatus.FAILED,
                    error=llm_error,
                    stop_reason=stop_reason,
                    token_usage_records=records,
                    tool_receipts=terminal_receipts(),
                )
            else:
                messages = (final_state or {}).get("messages", [])
                usable_partial: str | None = None
                for m in reversed(messages):
                    if isinstance(m, AIMessage):
                        text = message_content_to_text(m.content).strip()
                        if text:
                            usable_partial = text
                        break
                if usable_partial is not None:
                    result.try_set_terminal(
                        SubagentStatus.COMPLETED,
                        result=usable_partial,
                        stop_reason=stop_reason,
                        token_usage_records=records,
                        tool_receipts=terminal_receipts(prefer_citing_turn=True),
                    )
                else:
                    result.try_set_terminal(
                        SubagentStatus.FAILED,
                        error=f"Reached max_turns={max_turns}",
                        stop_reason=stop_reason,
                        token_usage_records=records,
                        tool_receipts=terminal_receipts(),
                    )

        except asyncio.CancelledError:
            # A callback may have received billable usage before astream emits
            # its next state. Preserve that snapshot before outer timeout/cancel
            # terminalization; never turn cancellation into task completion.
            if collector is not None:
                result.update_token_usage_records(collector.snapshot_records())
            result.update_tool_receipts(terminal_receipts())
            raise
        except Exception as e:
            logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} async execution failed")
            result.try_set_terminal(
                SubagentStatus.FAILED,
                error=str(e),
                token_usage_records=collector.snapshot_records() if collector is not None else None,
                tool_receipts=terminal_receipts(),
            )
        finally:
            if context is not None:
                try:
                    await release_sandbox_execution_lease_async(context)
                except Exception:
                    logger.warning("[trace=%s] Could not release subagent sandbox lease", self.trace_id, exc_info=True)

        return result

    def _execute_in_isolated_loop(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Execute the subagent on the persistent isolated event loop.

        This method is used by the sync ``execute()`` path when the caller is
        already running inside an event loop. Because ``execute()`` is a sync
        API, this path blocks the caller while the actual coroutine runs on the
        long-lived isolated loop. Reusing that loop keeps shared async clients
        from being tied to a short-lived loop that gets closed per execution.
        """
        future: Future[SubagentResult] | None = None
        parent_context = _copy_isolated_subagent_context()
        try:
            future = _submit_to_isolated_loop_in_context(
                parent_context,
                lambda: self._aexecute(task, result_holder),
            )
            return future.result(timeout=self.config.timeout_seconds)
        except FuturesTimeoutError:
            if result_holder is not None:
                result_holder.cancel_event.set()
            if future is not None:
                future.cancel()
            raise
        except Exception:
            if future is None:
                logger.debug(
                    f"[trace={self.trace_id}] Failed to submit subagent {self.config.name} to the isolated event loop",
                    exc_info=True,
                )
            else:
                logger.debug(
                    f"[trace={self.trace_id}] Subagent {self.config.name} failed while executing on the isolated event loop",
                    exc_info=True,
                )
            raise

    def execute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Execute a task synchronously (wrapper around async execution).

        All sync executions use the persistent isolated event loop. This keeps
        shared async clients and the process-wide admission controller bound to
        one long-lived loop instead of creating a short-lived loop per call.

        Args:
            task: The task description for the subagent.
            result_holder: Optional pre-created result object to update during execution.

        Returns:
            SubagentResult with the execution result.
        """
        try:
            return self._execute_in_isolated_loop(task, result_holder)
        except Exception as e:
            logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} execution failed")
            # Create a result with error if we don't have one
            if result_holder is not None:
                result = result_holder
            else:
                result = SubagentResult(
                    task_id=str(uuid.uuid4())[:8],
                    trace_id=self.trace_id,
                    status=SubagentStatus.RUNNING,
                )
            result.try_set_terminal(SubagentStatus.FAILED, error=str(e))
            return result

    def execute_async(self, task: str, task_id: str | None = None) -> str:
        """Start a task execution in the background.

        Args:
            task: The task description for the subagent.
            task_id: Optional external correlation ID for logs. It is never used
                as the process-wide background registry key because provider
                tool-call IDs can repeat across concurrent parent runs.

        Returns:
            Unique execution ID that can be used to check status later.
        """
        execution_id = str(uuid.uuid4())

        # Create initial pending result
        result = SubagentResult(
            task_id=execution_id,
            external_task_id=task_id,
            trace_id=self.trace_id,
            status=SubagentStatus.PENDING,
        )

        logger.info(
            "[trace=%s] Subagent %s starting async execution, execution_id=%s, external_task_id=%s, timeout=%ss",
            self.trace_id,
            self.config.name,
            execution_id,
            task_id,
            self.config.timeout_seconds,
        )

        # Copy the parent context before registering: context copying can
        # itself fail (callback-manager copy or loop-bound handler filtering),
        # and a failure after registration would strand a PENDING entry —
        # the caller never receives an execution_id to poll, and
        # cleanup_background_task() refuses non-terminal entries.
        parent_context = _copy_isolated_subagent_context()

        with _background_tasks_lock:
            _background_tasks[execution_id] = result

        execution_started = threading.Event()

        async def run_with_timeout() -> SubagentResult:
            execution_started.set()
            try:
                if result.status.is_terminal or result.cancel_event.is_set():
                    result.try_set_terminal(SubagentStatus.CANCELLED, error="Cancelled by user")
                    return result
                return await asyncio.wait_for(
                    self._aexecute(task, result),
                    timeout=self.config.timeout_seconds,
                )
            except TimeoutError:
                result.cancel_event.set()
                result.try_set_terminal(
                    SubagentStatus.TIMED_OUT,
                    error=f"Execution timed out after {self.config.timeout_seconds} seconds",
                    tool_receipts=result.snapshot_tool_receipts(),
                )
                return result
            except asyncio.CancelledError:
                result.cancel_event.set()
                result.try_set_terminal(
                    SubagentStatus.CANCELLED,
                    error="Cancelled by user",
                    tool_receipts=result.snapshot_tool_receipts(),
                )
                return result
            except Exception as exc:
                logger.exception("[trace=%s] Subagent %s async execution failed", self.trace_id, self.config.name)
                result.try_set_terminal(SubagentStatus.FAILED, error=str(exc))
                return result

        try:
            execution_future = _submit_to_isolated_loop_in_context(parent_context, run_with_timeout)
        except BaseException:
            # Submitting can fail before any coroutine starts (e.g. the
            # persistent loop failed to spin up). The caller then sees the
            # exception and never polls this execution_id, and
            # cleanup_background_task() refuses non-terminal entries — so the
            # just-registered entry must be dropped here, not left as a
            # PENDING zombie nothing will ever remove.
            with _background_tasks_lock:
                _background_tasks.pop(execution_id, None)
            raise
        with _background_tasks_lock:
            _background_futures[execution_id] = execution_future

        def forget_future(_future: Future[SubagentResult]) -> None:
            # Before the first coroutine instruction, its body cannot persist
            # cancellation. Do not strand a PENDING entry in that race.
            if _future.cancelled() and not execution_started.is_set():
                result.cancel_event.set()
                result.try_set_terminal(SubagentStatus.CANCELLED, error="Cancelled by user")
            with _background_tasks_lock:
                _background_futures.pop(execution_id, None)

        execution_future.add_done_callback(forget_future)
        return execution_id


MAX_CONCURRENT_SUBAGENTS = 3


def request_cancel_background_task(task_id: str) -> None:
    """Signal a running background task to stop.

    Sets the cancel_event on the task, which is checked cooperatively
    by ``_aexecute`` during ``agent.astream()`` iteration.  This allows
    subagent threads — which cannot be force-killed via ``Future.cancel()``
    — to stop at the next iteration boundary.

    Args:
        task_id: The task ID to cancel.
    """
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        future = _background_futures.get(task_id) if result is not None else None
    if result is not None:
        result.cancel_event.set()
        # cancel() may synchronously call forget_future, which takes this lock.
        if future is not None:
            future.cancel()
            logger.info("Requested cancellation for background task %s", task_id)


def get_background_task_result(task_id: str) -> SubagentResult | None:
    """Get the result of a background task.

    Args:
        task_id: The task ID returned by execute_async.

    Returns:
        SubagentResult if found, None otherwise.
    """
    with _background_tasks_lock:
        return _background_tasks.get(task_id)


def list_background_tasks() -> list[SubagentResult]:
    """List all background tasks.

    Returns:
        List of all SubagentResult instances.
    """
    with _background_tasks_lock:
        return list(_background_tasks.values())


def cleanup_background_task(task_id: str) -> None:
    """Remove a completed task from background tasks.

    Should be called by task_tool after it finishes polling and returns the result.
    This prevents memory leaks from accumulated completed tasks.

    Only removes tasks that are in a terminal state (COMPLETED/FAILED/TIMED_OUT)
    to avoid race conditions with the background executor still updating the task entry.

    Args:
        task_id: The task ID to remove.
    """
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is None:
            # Nothing to clean up; may have been removed already.
            logger.debug("Requested cleanup for unknown background task %s", task_id)
            return

        # Only clean up tasks that are in a terminal state to avoid races with
        # the background executor still updating the task entry.
        if result.status.is_terminal or result.completed_at is not None:
            del _background_tasks[task_id]
            logger.debug("Cleaned up background task: %s", task_id)
        else:
            logger.debug(
                "Skipping cleanup for non-terminal background task %s (status=%s)",
                task_id,
                result.status.value if hasattr(result.status, "value") else result.status,
            )
