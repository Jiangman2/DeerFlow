"""Private server-installed recorders, never caller-selected attribution."""

from typing import Any

LOOP_DETECTION_RECORDER_CONTEXT_KEY = "__run_loop_detection_recorder"
TOOL_PROMOTION_RECORDER_CONTEXT_KEY = "__run_tool_promotion_recorder"
TOOL_PROGRESS_RECORDER_CONTEXT_KEY = "__run_tool_progress_recorder"
SERVER_OWNED_AUDIT_CONTEXT_KEYS = frozenset({"is_subagent", "agent_id", LOOP_DETECTION_RECORDER_CONTEXT_KEY, TOOL_PROMOTION_RECORDER_CONTEXT_KEY, TOOL_PROGRESS_RECORDER_CONTEXT_KEY})


def resolve_audit_recorder(context: object, *, recorder_key: str) -> tuple[Any | None, bool, str | None]:
    if not isinstance(context, dict):
        return None, False, None
    recorder = context.get(recorder_key)
    if recorder is not None:
        agent_id = context.get("agent_id")
        return recorder, True, agent_id if isinstance(agent_id, str) else None
    return context.get("__run_journal"), False, None
