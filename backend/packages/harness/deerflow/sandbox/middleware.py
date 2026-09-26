import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace as dc_replace
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command, Overwrite

from deerflow.agents.thread_state import SandboxStateField, ThreadDataState
from deerflow.authz.sandbox_authz import authorize_sandbox_execution, authorize_sandbox_execution_async, safe_app_config, safe_app_config_async
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.sandbox import get_sandbox_provider
from deerflow.sandbox.exceptions import SandboxAuthorizationError
from deerflow.sandbox.lease import ensure_sandbox_lease_owner, get_sandbox_lease_manager, sandbox_lease_owner

logger = logging.getLogger(__name__)


class SandboxMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    sandbox: SandboxStateField
    thread_data: NotRequired[ThreadDataState | None]


class SandboxMiddleware(AgentMiddleware[SandboxMiddlewareState]):
    """Create a sandbox environment and assign it to an agent.

    Lifecycle Management:
    - With lazy_init=True (default): Sandbox is acquired on first tool call
    - With lazy_init=False: Sandbox is acquired on first agent invocation (before_agent)
    - Sandbox is reused across multiple turns within the same thread
    - Sandbox is NOT released after each agent call to avoid wasteful recreation
    - Cleanup happens at application shutdown via SandboxProvider.shutdown()
    """

    state_schema = SandboxMiddlewareState

    def __init__(self, lazy_init: bool = True):
        """Initialize sandbox middleware.

        Args:
            lazy_init: If True, defer sandbox acquisition until first tool call.
                      If False, acquire sandbox eagerly in before_agent().
                      Default is True for optimal performance.
        """
        super().__init__()
        self._lazy_init = lazy_init

    def _acquire_sandbox(self, thread_id: str, *, user_id: str, owner_id: str | None) -> str:
        provider = get_sandbox_provider()
        sandbox_id = get_sandbox_lease_manager(provider).acquire(owner_id, thread_id, user_id=user_id) if owner_id is not None else provider.acquire(thread_id, user_id=user_id)
        logger.info(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id

    async def _acquire_sandbox_async(self, thread_id: str, *, user_id: str, owner_id: str | None) -> str:
        provider = get_sandbox_provider()
        sandbox_id = await get_sandbox_lease_manager(provider).acquire_async(owner_id, thread_id, user_id=user_id) if owner_id is not None else await provider.acquire_async(thread_id, user_id=user_id)
        logger.info(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id

    async def _release_sandbox_async(self, sandbox_id: str) -> None:
        await asyncio.to_thread(get_sandbox_provider().release, sandbox_id)

    @staticmethod
    def _runtime_scope(runtime: Runtime) -> tuple[str, str] | None:
        context = runtime.context or {}
        thread_id = context.get("thread_id") or (getattr(runtime, "config", None) or {}).get("configurable", {}).get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            return None
        return thread_id, resolve_runtime_user_id(runtime)

    @classmethod
    def _scoped_sandbox_id(cls, state: SandboxMiddlewareState, runtime: Runtime) -> str | None:
        scope = cls._runtime_scope(runtime)
        if scope is None:
            return None
        thread_id, user_id = scope
        context_id = (runtime.context or {}).get("sandbox_id")
        state_id = cls._read_sandbox_id_from_state(state)
        provider = get_sandbox_provider()
        for sandbox_id in (context_id, state_id):
            if isinstance(sandbox_id, str) and provider.get_scoped(sandbox_id, thread_id=thread_id, user_id=user_id) is not None:
                return sandbox_id
        return None

    @classmethod
    def _state_sandbox_is_scoped(cls, state: SandboxMiddlewareState, scope: tuple[str, str]) -> bool:
        sandbox_id = cls._read_sandbox_id_from_state(state)
        if sandbox_id is None:
            return False
        thread_id, user_id = scope
        return get_sandbox_provider().get_scoped(sandbox_id, thread_id=thread_id, user_id=user_id) is not None

    @override
    def before_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        owner_id = ensure_sandbox_lease_owner(runtime.context)
        # Skip acquisition if lazy_init is enabled
        if self._lazy_init:
            return super().before_agent(state, runtime)

        try:
            authorize_sandbox_execution(context=runtime.context or {}, app_config=safe_app_config())
        except SandboxAuthorizationError:
            # Ordinary non-sandbox chat remains usable; every tool entry checks
            # again and returns its own visible denial, including reused ids.
            return None

        scope = self._runtime_scope(runtime)
        if scope is None:
            return super().before_agent(state, runtime)
        thread_id, user_id = scope
        previous_id = self._read_sandbox_id_from_state(state)
        if previous_id is not None and owner_id is not None:
            sandbox_id = get_sandbox_lease_manager(get_sandbox_provider()).reuse_or_acquire(owner_id, previous_id, thread_id=thread_id, user_id=user_id)
        elif self._state_sandbox_is_scoped(state, scope):
            sandbox_id = previous_id
        else:
            sandbox_id = self._acquire_sandbox(thread_id, user_id=user_id, owner_id=owner_id)
        if runtime.context is not None:
            runtime.context["sandbox_id"] = sandbox_id
        if sandbox_id == previous_id:
            return super().before_agent(state, runtime)
        logger.info(f"Assigned sandbox {sandbox_id} to thread {thread_id}")
        value = {"sandbox_id": sandbox_id}
        return {"sandbox": Overwrite(value) if previous_id is not None else value}

    @override
    async def abefore_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        owner_id = ensure_sandbox_lease_owner(runtime.context)
        # Skip acquisition if lazy_init is enabled
        if self._lazy_init:
            return await super().abefore_agent(state, runtime)

        try:
            await authorize_sandbox_execution_async(context=runtime.context or {}, app_config=await safe_app_config_async())
        except SandboxAuthorizationError:
            return None

        scope = self._runtime_scope(runtime)
        if scope is None:
            return await super().abefore_agent(state, runtime)
        thread_id, user_id = scope
        previous_id = self._read_sandbox_id_from_state(state)
        if previous_id is not None and owner_id is not None:
            sandbox_id = await get_sandbox_lease_manager(get_sandbox_provider()).reuse_or_acquire_async(owner_id, previous_id, thread_id=thread_id, user_id=user_id)
        elif self._state_sandbox_is_scoped(state, scope):
            sandbox_id = previous_id
        else:
            sandbox_id = await self._acquire_sandbox_async(thread_id, user_id=user_id, owner_id=owner_id)
        if runtime.context is not None:
            runtime.context["sandbox_id"] = sandbox_id
        if sandbox_id == previous_id:
            return await super().abefore_agent(state, runtime)
        logger.info(f"Assigned sandbox {sandbox_id} to thread {thread_id}")
        value = {"sandbox_id": sandbox_id}
        return {"sandbox": Overwrite(value) if previous_id is not None else value}

    @override
    def after_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        owner_id = sandbox_lease_owner(runtime.context)
        if owner_id is not None:
            get_sandbox_lease_manager(get_sandbox_provider()).release(owner_id)
            return None
        sandbox_id = self._scoped_sandbox_id(state, runtime)
        if sandbox_id is not None:
            logger.info(f"Releasing sandbox {sandbox_id}")
            get_sandbox_provider().release(sandbox_id)
            return None

        # No sandbox to release
        return super().after_agent(state, runtime)

    @override
    async def aafter_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        owner_id = sandbox_lease_owner(runtime.context)
        if owner_id is not None:
            await get_sandbox_lease_manager(get_sandbox_provider()).release_async(owner_id)
            return None
        sandbox_id = self._scoped_sandbox_id(state, runtime)
        if sandbox_id is not None:
            logger.info(f"Releasing sandbox {sandbox_id}")
            await self._release_sandbox_async(sandbox_id)
            return None

        # No sandbox to release
        return await super().aafter_agent(state, runtime)

    # ------------------------------------------------------------------
    # Tool-call wrappers: persist lazily-acquired sandbox state into the
    # graph state via Command(update=...).
    #
    # Background:
    #   ``ensure_sandbox_initialized*`` in ``deerflow.sandbox.tools`` mutates
    #   ``runtime.state["sandbox"]`` directly. That mutation is local to the
    #   current tool invocation and is NOT picked up by LangGraph's channel
    #   reducer, so subsequent graph steps (and downstream consumers such as
    #   ``ToolOutputBudgetMiddleware`` and the sub-agent ``task_tool``)
    #   cannot observe the sandbox id. Wrapping the tool call lets us detect
    #   a fresh lazy init by diffing the state snapshot before/after the
    #   handler and emit a proper state update via ``Command``.
    # ------------------------------------------------------------------

    @staticmethod
    def _read_sandbox_id_from_state(state: object) -> str | None:
        if not isinstance(state, dict):
            return None
        sandbox_state = state.get("sandbox")
        if not isinstance(sandbox_state, dict):
            return None
        sandbox_id = sandbox_state.get("sandbox_id")
        return sandbox_id if isinstance(sandbox_id, str) else None

    @staticmethod
    def _attach_sandbox_update(result: ToolMessage | Command, sandbox_id: str, *, overwrite: bool = False) -> ToolMessage | Command:
        """Wrap or merge ``result`` so that ``sandbox.sandbox_id`` is persisted.

        - ``ToolMessage`` -> ``Command(update={"sandbox": ..., "messages": [msg]})``
        - ``Command`` with dict update -> merge ``sandbox`` key, preserve all
          existing fields (``messages``, ``goto``, ``graph``, ``resume``, ...).
        - ``Command`` with non-dict / None update -> leave it untouched to
          avoid silent data loss on unknown update shapes.
        """
        sandbox_value = {"sandbox_id": sandbox_id}
        sandbox_update = {"sandbox": Overwrite(sandbox_value) if overwrite else sandbox_value}

        if isinstance(result, ToolMessage):
            return Command(update={**sandbox_update, "messages": [result]})

        existing_update = result.update
        if isinstance(existing_update, dict):
            merged_update = {**existing_update, **sandbox_update}
            return dc_replace(result, update=merged_update)
        return result

    @staticmethod
    def _read_sandbox_id_from_request(request: ToolCallRequest) -> str | None:
        """Read sandbox_id from runtime.state (where ensure_sandbox_initialized writes)."""
        runtime = request.runtime
        if runtime is None or runtime.state is None:
            return None
        return SandboxMiddleware._read_sandbox_id_from_state(runtime.state)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        prev_sandbox_id = self._read_sandbox_id_from_request(request)
        result = handler(request)
        curr_sandbox_id = self._read_sandbox_id_from_request(request)
        if curr_sandbox_id is None or curr_sandbox_id == prev_sandbox_id:
            return result
        return self._attach_sandbox_update(result, curr_sandbox_id, overwrite=prev_sandbox_id is not None)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        prev_sandbox_id = self._read_sandbox_id_from_request(request)
        result = await handler(request)
        curr_sandbox_id = self._read_sandbox_id_from_request(request)
        if curr_sandbox_id is None or curr_sandbox_id == prev_sandbox_id:
            return result
        return self._attach_sandbox_update(result, curr_sandbox_id, overwrite=prev_sandbox_id is not None)
