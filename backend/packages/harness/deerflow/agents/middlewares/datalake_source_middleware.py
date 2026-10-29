"""Keep a DataLake query on its authorized enterprise data source."""

import json
import logging
from typing import Annotated, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import Command

from deerflow.runtime.datalake_policy import ACCESS_HINT, ALLOWED_TOOLS, DATA_TOOLS, SOURCE_HINT, STATE_KEY, explicit_data_question, merge_query_scope, query_scope

logger = logging.getLogger(__name__)


class DataLakeSourceState(AgentState):
    datalake_query_scope: NotRequired[Annotated[dict, merge_query_scope]]


def _context(runtime):
    value = getattr(runtime, "context", None)
    return value if isinstance(value, dict) else {}


class DataLakeSourceMiddleware(AgentMiddleware[DataLakeSourceState]):
    state_schema = DataLakeSourceState

    def _update(self, state, runtime):
        scope = query_scope(state, _context(runtime))
        return {STATE_KEY: scope}

    def before_agent(self, state, runtime):
        return self._update(state, runtime)

    async def abefore_agent(self, state, runtime):
        return self._update(state, runtime)

    def after_model(self, state, runtime):
        return self._update(state, runtime)

    async def aafter_model(self, state, runtime):
        return self._update(state, runtime)

    def _before(self, state, runtime):
        scope = query_scope(state, _context(runtime))
        return {STATE_KEY: scope}

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        return self._before(state, runtime)

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        return self._before(state, runtime)

    def _prepare(self, request):
        scope = query_scope(request.state, _context(request.runtime))
        enabled = _context(request.runtime).get("datalake_enabled") is True
        instruction = (
            "数据湖查询规则：先判断用户是否在查询企业数据湖中的业务数据、记录或文档。"
            "若明确需要数据湖工具，必须使用 datalake_data_qa 或 datalake_smart_qa；"
            "权限未开启时直接提示用户：请先点击数据湖按钮开启权限。"
            "即使没有可见的数据湖工具，也不得把这类问题转去外部查询。"
            "未开启/无权限、结果为空、报错或超时均不是更换数据来源的授权。"
            "无法确定数据来源时先澄清；普通公开知识问题不因此受限。"
            f"当前数据湖授权状态：{'已开启' if enabled else '未开启'}。"
        )
        if scope["required"]:
            instruction += SOURCE_HINT + f"当前步骤：{scope['phase']}。查询返回后可继续本地加工，无关的新任务不继承此限制。"
        instruction += "普通写作、上传文件处理、讨论代码或数据湖概念，不得仅因行业关键词而查询数据湖。来源不明确时先简短澄清。需要本地计算或 Word/Excel/图表时使用 process_local_data；被拒绝的同一获取请求不要反复重试。"
        system = request.system_message
        blocks = list(system.content_blocks) if system is not None else []
        blocks.append({"type": "text", "text": instruction})
        allowed = []
        for tool in request.tools:
            name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
            if name in DATA_TOOLS and (not enabled or not scope["required"]):
                continue
            if not scope["required"] or name in ALLOWED_TOOLS:
                allowed.append(tool)
        return request.override(system_message=SystemMessage(content=blocks), tools=allowed)

    def wrap_model_call(self, request, handler):
        return handler(self._prepare(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._prepare(request))

    def _blocked(self, request):
        scope = query_scope(request.state, _context(request.runtime))
        name = request.tool_call.get("name")
        args = request.tool_call.get("args") or {}
        query = args.get("query") if isinstance(args, dict) else None
        authorized = _context(request.runtime).get("datalake_enabled") is True
        code = None
        if name in DATA_TOOLS and not authorized:
            code = "datalake_access_required"
        elif name in DATA_TOOLS and not scope["required"]:
            code = "datalake_not_requested"
        elif name not in ALLOWED_TOOLS and (scope["required"] or isinstance(query, str) and explicit_data_question(query)):
            code = "datalake_external_fallback_forbidden"
        if code is None:
            return None
        hint = ACCESS_HINT if code == "datalake_access_required" else "当前任务没有要求查询数据湖，请继续原本的写作或本地文件处理。" if code == "datalake_not_requested" else SOURCE_HINT
        logger.info("DataLake tool denied code=%s trigger=%s phase=%s", code, scope["trigger"], scope["phase"])
        result = ToolMessage(
            content=json.dumps({"code": code, "error": hint, "retryable": False, "next_step": "local_processing_or_clarification"}, ensure_ascii=False),
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )
        return Command(update={"messages": [result]})

    def wrap_tool_call(self, request, handler):
        return self._blocked(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        blocked = self._blocked(request)
        return blocked if blocked is not None else await handler(request)
