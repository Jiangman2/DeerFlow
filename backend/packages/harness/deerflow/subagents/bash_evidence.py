"""Bounded command evidence for the pinned v2.1 acceptance checklist.

Collected from each child state, not the parent's sandbox or the final summary.
This is execution evidence, not proof of runner semantics or claim correctness.
"""

import json
import logging
import re
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Overwrite

from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY

logger = logging.getLogger(__name__)

BASH_EVIDENCE_MAX_ENTRIES = 20
BASH_EVIDENCE_COMMAND_CHARS = 500
BASH_EVIDENCE_OUTPUT_TAIL_CHARS = 1000
_BASH_NAMES = frozenset({"bash", "bash_tool"})
_EXIT_CODE = re.compile(r"Exit Code: (-?[0-9]+)\s*$")
_EXITED_CODE = re.compile(r"Command exited with code (-?[0-9]+)")


def _bash_evidence_status(content: str, meta_status: str) -> tuple[str, str | None]:
    match = _EXIT_CODE.search(content) or _EXITED_CODE.fullmatch(content.strip())
    if match is None:
        return meta_status, None
    # No unbounded int conversion of tool-controlled output. A textual marker
    # cannot override an explicit failed/partial tool result with success.
    zero = not match.group(1).lstrip("-").strip("0")
    status = "success" if zero and meta_status == "success" else "error"
    return status, " ".join(match.group(0).split())[:160]


def _harvest_shell_persistence(final_state: Any, *, thread_id: str | None, user_id: str | None) -> bool | None:
    """Inspect only a cached, identity-scoped producing client; never acquire."""
    if not isinstance(final_state, dict) or not thread_id or not user_id:
        return None
    sandbox_state = final_state.get("sandbox")
    if isinstance(sandbox_state, Overwrite):
        sandbox_state = sandbox_state.value
    sandbox_id = sandbox_state.get("sandbox_id") if isinstance(sandbox_state, dict) else None
    if not isinstance(sandbox_id, str) or not sandbox_id:
        return None
    try:
        from deerflow.sandbox.sandbox_provider import get_sandbox_provider

        sandbox = get_sandbox_provider().get_scoped(sandbox_id, thread_id=thread_id, user_id=user_id)
        declared = getattr(sandbox, "persistent_shell_sessions", None)
        # A missing custom-provider declaration or a mock/truthy string is not
        # one-shot proof. The parent may never have acquired a sandbox at all.
        return declared if isinstance(declared, bool) else None
    except Exception:
        return None


def harvest_bash_executions(final_state: Any, *, thread_id: str | None = None, user_id: str | None = None) -> list[dict[str, Any]] | None:
    """Return bounded AI-call/ToolMessage pairs, or None if unavailable.

    The executor merges by call id under its result lock before observing
    cancellation. This survives later context compaction without retaining the
    whole graph state, command output or runtime client.
    """
    messages = final_state.get("messages") if isinstance(final_state, dict) else None
    if not messages:
        return None
    try:
        commands: dict[str, tuple[str, str, bool]] = {}
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            for call in message.tool_calls or []:
                name = call.get("name")
                call_id = call.get("id")
                if name not in _BASH_NAMES or not isinstance(call_id, str) or not call_id:
                    continue
                args = call.get("args")
                command = args.get("command") if isinstance(args, dict) else None
                if not isinstance(command, str) or not command:
                    continue
                commands[call_id] = (name, command[:BASH_EVIDENCE_COMMAND_CHARS], len(command) > BASH_EVIDENCE_COMMAND_CHARS)
        if not commands:
            return []
        executions: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            entry = commands.get(message.tool_call_id)
            if entry is None:
                continue
            name, command, truncated = entry
            if message.name is not None and message.name not in _BASH_NAMES:
                continue
            meta = (message.additional_kwargs or {}).get(TOOL_META_KEY) or {}
            meta_status = str(meta.get("status") or message.status or "success")
            content = message.content if isinstance(message.content, str) else json.dumps(message.content, sort_keys=True, default=str)
            status, marker = _bash_evidence_status(content, meta_status)
            executions.append(
                {
                    "tool_call_id": message.tool_call_id,
                    "tool_name": name,
                    "command": command,
                    "command_truncated": truncated,
                    "output_tail": content[-BASH_EVIDENCE_OUTPUT_TAIL_CHARS:],
                    "status": status,
                    "status_marker": marker,
                }
            )
        persistent = _harvest_shell_persistence(final_state, thread_id=thread_id, user_id=user_id) if executions else None
        for execution in executions:
            execution["shell_persistent"] = persistent
        return executions[-BASH_EVIDENCE_MAX_ENTRIES:]
    except Exception:
        logger.warning("Failed to harvest subagent bash execution evidence")
        return None
