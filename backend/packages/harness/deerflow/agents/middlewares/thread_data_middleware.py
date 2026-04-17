import os
import sqlite3
from pathlib import Path
from typing import NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import ThreadDataState
from deerflow.config.paths import Paths, get_paths


def _db_path() -> Path:
    base = os.getenv("DEER_FLOW_HOME") or os.path.join(Path.home(), ".deer-flow")
    return Path(base).expanduser() / "deerflow.db"


def _resolve_thread_owner(thread_id: str) -> str | None:
    try:
        conn = sqlite3.connect(_db_path().as_posix())
        try:
            cur = conn.cursor()
            cur.execute("SELECT owner_user_id FROM threads WHERE thread_id = ?", (thread_id,))
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


class ThreadDataMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    thread_data: NotRequired[ThreadDataState | None]


class ThreadDataMiddleware(AgentMiddleware[ThreadDataMiddlewareState]):
    """Create thread data directories for each thread execution.

    Creates the following directory structure:
    - {base_dir}/threads/{thread_id}/user-data/workspace
    - {base_dir}/threads/{thread_id}/user-data/uploads
    - {base_dir}/threads/{thread_id}/user-data/outputs

    Lifecycle Management:
    - With lazy_init=True (default): Only compute paths, directories created on-demand
    - With lazy_init=False: Eagerly create directories in before_agent()
    """

    state_schema = ThreadDataMiddlewareState

    def __init__(self, base_dir: str | None = None, lazy_init: bool = True):
        """Initialize the middleware.

        Args:
            base_dir: Base directory for thread data. Defaults to Paths resolution.
            lazy_init: If True, defer directory creation until needed.
                      If False, create directories eagerly in before_agent().
                      Default is True for optimal performance.
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()
        self._lazy_init = lazy_init

    def _get_thread_paths(self, thread_id: str, user_id: str | None) -> dict[str, str]:
        """Get the paths for a thread's data directories.

        Args:
            thread_id: The thread ID.

        Returns:
            Dictionary with workspace_path, uploads_path, and outputs_path.
        """
        return {
            "workspace_path": str(self._paths.sandbox_work_dir(thread_id, user_id=user_id)),
            "uploads_path": str(self._paths.sandbox_uploads_dir(thread_id, user_id=user_id)),
            "outputs_path": str(self._paths.sandbox_outputs_dir(thread_id, user_id=user_id)),
        }

    def _create_thread_directories(self, thread_id: str, user_id: str | None) -> dict[str, str]:
        """Create the thread data directories.

        Args:
            thread_id: The thread ID.

        Returns:
            Dictionary with the created directory paths.
        """
        self._paths.ensure_thread_dirs(thread_id, user_id=user_id)
        return self._get_thread_paths(thread_id, user_id)

    def before_agent(self, state: ThreadDataMiddlewareState, runtime: Runtime) -> dict | None:
        context = runtime.context or {}
        thread_id = context.get("thread_id")
        user_id = context.get("user_id") or context.get("x_df_user")
        if thread_id is None:
            config = get_config()
            cfg_ctx = config.get("context", {}) if isinstance(config, dict) else {}
            cfg_conf = config.get("configurable", {}) if isinstance(config, dict) else {}
            if isinstance(cfg_ctx, dict):
                thread_id = cfg_ctx.get("thread_id")
                if user_id is None:
                    user_id = cfg_ctx.get("user_id") or cfg_ctx.get("x_df_user")
            if thread_id is None and isinstance(cfg_conf, dict):
                thread_id = cfg_conf.get("thread_id")
                if user_id is None:
                    user_id = cfg_conf.get("user_id") or cfg_conf.get("x_df_user")
            if user_id is None:
                if isinstance(cfg_ctx, dict):
                    user_id = cfg_ctx.get("user_id") or cfg_ctx.get("x_df_user")
                if user_id is None and isinstance(cfg_conf, dict):
                    user_id = cfg_conf.get("user_id") or cfg_conf.get("x_df_user")

        if thread_id is None:
            raise ValueError("Thread ID is required in runtime context or config.configurable")

        if user_id is None:
            user_id = _resolve_thread_owner(thread_id)

        if self._lazy_init:
            # Lazy initialization: only compute paths, don't create directories
            paths = self._get_thread_paths(thread_id, user_id)
        else:
            # Eager initialization: create directories immediately
            paths = self._create_thread_directories(thread_id, user_id)
            print(f"Created thread data directories for thread {thread_id}")

        return {
            "thread_data": {
                **paths,
            }
        }
