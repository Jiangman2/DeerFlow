"""Model-selected DataLake source scope, separate from server authorization."""

import hashlib
from collections.abc import Mapping

from langchain_core.messages import AIMessage, ToolMessage

from deerflow.config.datalake_tools import RETIRED_DATALAKE_TOOL_NAMES
from deerflow.utils.messages import get_original_user_content_text, is_real_user_message

DATA_TOOLS = frozenset({"datalake_smart_qa", "datalake_data_qa"}) | RETIRED_DATALAKE_TOOL_NAMES
STATE_KEY = "datalake_query_scope"
ACCESS_HINT = "请先点击数据湖按钮开启权限后再查询。可以继续处理已有的本地文件；不会使用外部查询替代数据湖查询。"
SOURCE_HINT = "企业数据只能从获准的数据湖来源获取。查询失败或为空须如实说明；可用 process_local_data 处理已获准的结果或当前对话上传文件，不能改用外部来源、任意脚本或编造数据。"
LOCAL_TOOLS = frozenset({"ask_clarification", "think", "write_todos", "read_file", "write_file", "str_replace", "ls", "glob", "grep", "view_image", "present_files", "task", "process_local_data"})
# These controls only record stage checkpoints. Their own controller still
# enforces the server canary, Skill ownership, receipts and budgets; they grant
# no data access and cannot execute code or make external requests.
STAGE_CONTROL_TOOLS = frozenset({"start_execution_stages", "advance_execution_stage"})
ALLOWED_TOOLS = DATA_TOOLS | LOCAL_TOOLS | STAGE_CONTROL_TOOLS
SCOPE_VERSION = "model_tool_choice_v1"


def merge_query_scope(left, right):
    """Persist host-observed tool selection; never infer authorization."""
    return right if right is not None else left


def query_scope(state, context=None):
    """Track current model tool selections without classifying human wording.

    Both active tools are eligible whenever authorized. Source restrictions start
    at a model-selected DataLake call (including mixed tool batches), or from a
    trusted parent task. A new human turn starts a new model decision boundary.
    Legacy keyword-based checkpoints cannot reintroduce the removed gate.
    """
    state = state or {}
    context = context if isinstance(context, Mapping) else {}
    messages = state.get("messages") or []
    index = next((i for i in reversed(range(len(messages))) if is_real_user_message(messages[i])), None)
    user = messages[index] if index is not None else None
    text = get_original_user_content_text(user.content, user.additional_kwargs) if user is not None else ""
    boundary = hashlib.sha256((str(getattr(user, "id", "") or "") + text).encode()).hexdigest()
    run_key = str(context.get("run_id") or boundary)
    previous = state.get(STATE_KEY) or {}
    inherited = context.get("datalake_query_only") is True
    retained = (previous.get("version") == SCOPE_VERSION and previous.get("run_key") == run_key
                and (previous.get("user_boundary") == boundary or user is None and bool(context.get("run_id")))
                and previous.get("required") is True)
    if retained and user is None:
        boundary = previous["user_boundary"]
    required = inherited or retained
    trigger = "parent_task" if inherited else "model_tool_choice" if retained else "model_decision"
    phase = previous.get("phase", "acquisition") if retained else "acquisition" if inherited else "ordinary"
    outcome = previous.get("outcome") if retained else None
    selected = set()
    for message in messages[(index + 1) if index is not None else len(messages):]:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                if call.get("name") in DATA_TOOLS and call.get("id"):
                    selected.add(call["id"])
                    required = True
                    trigger = "parent_task" if inherited else "model_tool_choice"
                    if phase == "ordinary":
                        phase = "acquisition"
        elif isinstance(message, ToolMessage):
            if message.tool_call_id in selected:
                phase = "processing"
                outcome = "error" if message.status == "error" else "returned"
            elif required and message.name == "present_files" and message.status != "error":
                phase = "complete"
    return {"version": SCOPE_VERSION, "user_boundary": boundary, "run_key": run_key,
            "required": bool(required), "phase": phase, "trigger": trigger, "outcome": outcome}
