"""Run-scoped source restriction for enterprise DataLake questions.

Explicit source/table wording is a conservative deterministic precheck, not a
general semantic classifier. Model guidance covers other formulations; an
attempted DataLake call locks the source for the rest of that run regardless
of whether it succeeds, fails or returns no records.
"""

import hashlib
import re
from collections.abc import Mapping

from langchain_core.messages import AIMessage, ToolMessage

from deerflow.config.datalake_tools import RETIRED_DATALAKE_TOOL_NAMES
from deerflow.utils.messages import get_original_user_content_text, is_real_user_message

DATA_TOOLS = frozenset({"datalake_smart_qa", "datalake_data_qa"}) | RETIRED_DATALAKE_TOOL_NAMES
STATE_KEY = "datalake_query_scope"
ACCESS_HINT = "请先点击数据湖按钮开启权限，然后重新发送这个问题。我不会使用外部查询替代数据湖查询。"
SOURCE_HINT = "该问题需要数据湖数据。未查询到结果或调用失败时，应说明情况并请用户补充条件，不能改用外部搜索、浏览器、其他检索服务或脚本查询，也不能编造企业数据。"
# Allow local presentation/inspection and a guarded delegate; unknown tools,
# external MCP, browser and arbitrary code execution are excluded by default.
LOCAL_TOOLS = frozenset({"ask_clarification", "think", "write_todos", "read_file", "write_file", "str_replace", "ls", "glob", "grep", "view_image", "present_files", "task"})
ALLOWED_TOOLS = DATA_TOOLS | LOCAL_TOOLS
_QUERY = re.compile(r"查询|查一下|查下|查找|检索|统计|汇总|筛选|调取|提取|排名|列出|找出|获取|问数|调用|使用|利用|通过|从|有哪些|是多少|有多少|最大|最小|最高|最低|\b(?:query|retrieve|search|fetch)\b", re.I)
_SOURCE = re.compile(r"数据湖|data\s*lake|datalake_(?:smart|data|detail|metrics?)_qa", re.I)
_TABLE = re.compile(r"(?:基本信息|业务数据|生产数据|作业井|油气田|平台|井史|企业内部|公司内部)(?:[\w\s]{0,12})表|(?:内部|企业|公司)(?:数据库|数据表)")


def explicit_data_question(text: str) -> bool:
    # General explanations of the DataLake product need no data permission.
    if re.fullmatch(r"\s*(?:什么是数据湖|数据湖是什么|介绍一下数据湖|what is (?:a )?data lake)[？?。.!\s]*", text, re.I):
        return False
    return bool(_QUERY.search(text) and (_SOURCE.search(text) or _TABLE.search(text)))


def merge_query_scope(left, right):
    """Parallel rejected tools may record the same source restriction."""
    if not left or left.get("run_key") != right.get("run_key"):
        return right
    return {"run_key": right["run_key"], "required": left.get("required") is True or right.get("required") is True}


def query_scope(state, context=None):
    state = state or {}
    context = context if isinstance(context, Mapping) else {}
    messages = state.get("messages") or []
    index = next((i for i in reversed(range(len(messages))) if is_real_user_message(messages[i])), None)
    user = messages[index] if index is not None else None
    text = get_original_user_content_text(user.content, user.additional_kwargs) if user is not None else ""
    run_key = str(context.get("run_id") or hashlib.sha256(text.encode()).hexdigest())
    previous = state.get(STATE_KEY) or {}
    required = context.get("datalake_query_only") is True or explicit_data_question(text)
    if previous.get("run_key") == run_key:
        required |= previous.get("required") is True
    elif text.strip() in {"继续", "继续查询", "再查一下", "重试", "已开启", "权限已开启"}:
        required |= previous.get("required") is True
    for message in messages[(index + 1) if index is not None else len(messages) :]:
        if isinstance(message, AIMessage):
            required |= any(call.get("name") in DATA_TOOLS for call in message.tool_calls)
        elif isinstance(message, ToolMessage):
            required |= message.name in DATA_TOOLS
    return {"run_key": run_key, "required": bool(required)}
