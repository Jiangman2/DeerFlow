import logging
from typing import NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import SandboxState, ThreadDataState
from deerflow.sandbox import get_sandbox_provider

logger = logging.getLogger(__name__)


class SandboxMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    sandbox: NotRequired[SandboxState | None]
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

    def _acquire_sandbox(self, thread_id: str) -> str:
        provider = get_sandbox_provider()
        sandbox_id = provider.acquire(thread_id)
        logger.info(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id

    def before_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        ctx = runtime.context if isinstance(runtime.context, dict) else {}
        # Skip acquisition if lazy_init is enabled
        if self._lazy_init:
            return super().before_agent(state, runtime)

        # Eager initialization (original behavior)
        if "sandbox" not in state or state["sandbox"] is None:
            thread_id = ctx.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                return super().before_agent(state, runtime)
            sandbox_id = self._acquire_sandbox(thread_id)
            logger.info(f"Assigned sandbox {sandbox_id} to thread {thread_id}")
            return {"sandbox": {"sandbox_id": sandbox_id}}
        return super().before_agent(state, runtime)

    def after_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        ctx = runtime.context if isinstance(runtime.context, dict) else {}
        sandbox = state.get("sandbox")
        if isinstance(sandbox, dict):
            sandbox_id = sandbox.get("sandbox_id")
            if isinstance(sandbox_id, str) and sandbox_id:
                logger.info(f"Releasing sandbox {sandbox_id}")
                get_sandbox_provider().release(sandbox_id)
                return None

        sandbox_id_from_context = ctx.get("sandbox_id")
        if isinstance(sandbox_id_from_context, str) and sandbox_id_from_context:
            logger.info(f"Releasing sandbox {sandbox_id_from_context} from context")
            get_sandbox_provider().release(sandbox_id_from_context)
            return None

        # No sandbox to release
        return super().after_agent(state, runtime)
