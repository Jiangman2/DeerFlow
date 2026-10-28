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
_QUERY = re.compile(r"查询|查一下|查下|查找|检索|统计|汇总|筛选|调取|提取|排名|列出|找出|获取|问数|有哪些|是多少|有多少|最大|最小|最高|最低|\b(?:query|retrieve|search|fetch)\b", re.I)
_SOURCE = re.compile(r"数据湖|data\s*lake|datalake_(?:smart|data|detail|metrics?)_qa", re.I)
_TABLE = re.compile(r"(?:基本信息|业务数据|生产数据|作业井|油气田|平台|井史|企业内部|公司内部)(?:[\w\s]{0,12})表|(?:内部|企业|公司)(?:数据库|数据表)")
_CONTINUATIONS = frozenset({"继续", "继续查询", "再查一下", "重试", "已开启", "权限已开启"})
_EVIDENCE_QUOTE = re.compile(r"(?:报错|错误消息|提示(?:语|词|文字)?|日志|文案|这句话|这句|代码)\s*[：:]?\s*(?:“[^”]*”|‘[^’]*’|\"[^\"]*\"|`[^`]*`)")
_PRODUCT_REFERENCE = re.compile(r"(?:数据湖|data\s*lake)(?:的)?(?:使用方法|使用说明|权限|按钮|接口|配置|功能|原理|是什么)", re.I)
_DIAGNOSTIC_REFERENCE = re.compile(r"(?:误判为|误判成|识别为|被当成)\s*数据湖(?:查询|问数)?|数据湖查询(?:的)?(?:为什么|为何|失败|报错|错误|异常|原因|被拦截)")


def explicit_data_question(text: str) -> bool:
    # General explanations of the DataLake product need no data permission.
    if re.fullmatch(r"\s*(?:什么是数据湖|数据湖是什么|介绍一下数据湖|what is (?:a )?data lake)[？?。.!\s]*", text, re.I):
        return False
    # Quoted diagnostics are evidence, not requests. Keep independent clauses so
    # a diagnostic preface cannot exempt an accompanying real business query.
    text = text.replace(ACCESS_HINT, "").replace(SOURCE_HINT, "")
    text = _EVIDENCE_QUOTE.sub("", text)
    text = _PRODUCT_REFERENCE.sub("", text)
    text = _DIAGNOSTIC_REFERENCE.sub("", text)
    return any(_QUERY.search(clause) and (_SOURCE.search(clause) or _TABLE.search(clause)) for clause in re.split(r"[，,。！？!?；;\n]", text))


def _query_id(user, text):
    return str(user.id or hashlib.sha256(text.encode()).hexdigest())


def _prior_query(messages):
    """Recover only the latest substantive task, including legacy checkpoints."""
    for index in reversed(range(len(messages))):
        user = messages[index]
        if not is_real_user_message(user):
            continue
        text = get_original_user_content_text(user.content, user.additional_kwargs)
        if text.strip() in _CONTINUATIONS:
            continue
        attempted = any((isinstance(m, AIMessage) and any(c.get("name") in DATA_TOOLS for c in m.tool_calls)) or (isinstance(m, ToolMessage) and m.name in DATA_TOOLS) for m in messages[index + 1 :])
        explicit = explicit_data_question(text)
        return {"query_id": _query_id(user, text), "origin": "user_query" if explicit else "data_tool" if attempted else None}
    return None


def merge_query_scope(left, right):
    """Parallel rejected tools may record the same source restriction."""
    if not left or left.get("run_key") != right.get("run_key"):
        return right
    merged = {**left, **right, "required": left.get("required") is True or right.get("required") is True}
    # A concurrent ordinary update must not erase a refusal or its query owner.
    for key in ("query_id", "origin"):
        if not right.get(key) and left.get(key):
            merged[key] = left[key]
    for status in ("waiting_access", "source_blocked"):
        if status in {left.get("status"), right.get("status")}:
            merged["status"] = status
            break
    return merged


def query_scope(state, context=None):
    state = state or {}
    context = context if isinstance(context, Mapping) else {}
    messages = state.get("messages") or []
    index = next((i for i in reversed(range(len(messages))) if is_real_user_message(messages[i])), None)
    user = messages[index] if index is not None else None
    text = get_original_user_content_text(user.content, user.additional_kwargs) if user is not None else ""
    run_key = str(context.get("run_id") or hashlib.sha256(text.encode()).hexdigest())
    previous = state.get(STATE_KEY) or {}
    explicit = explicit_data_question(text)
    required = context.get("datalake_query_only") is True or explicit
    owner = {"query_id": _query_id(user, text) if user is not None else run_key, "origin": "user_query" if explicit else "delegate"} if required else {}
    status = "active"
    if previous.get("run_key") == run_key:
        required |= previous.get("required") is True
        if previous.get("required"):
            owner = {k: previous[k] for k in ("query_id", "origin") if k in previous}
            status = previous.get("status", status)
    elif text.strip() in _CONTINUATIONS and previous.get("required") is True:
        prior = _prior_query(messages[:index]) if index is not None else None
        saved_owner = {k: previous[k] for k in ("query_id", "origin") if k in previous}
        # Legacy boolean-only locks need real query evidence. New checkpoints
        # retain task identity even when compaction removed its original text.
        saved_matches = saved_owner.get("query_id") and saved_owner.get("origin") in {"user_query", "data_tool", "delegate"} and (not prior or prior["query_id"] == saved_owner["query_id"])
        if (prior and prior["origin"]) or saved_matches:
            required = True
            owner = prior if prior and prior["origin"] else saved_owner
    for message in messages[(index + 1) if index is not None else len(messages) :]:
        if isinstance(message, AIMessage):
            attempted = any(call.get("name") in DATA_TOOLS for call in message.tool_calls)
        elif isinstance(message, ToolMessage):
            attempted = message.name in DATA_TOOLS
        else:
            continue
        if attempted:
            if not owner.get("query_id"):
                owner = {"query_id": _query_id(user, text) if user is not None else run_key, "origin": "data_tool"}
            required = True
    return {"run_key": run_key, "required": bool(required), "status": status if required else "inactive", **owner}
