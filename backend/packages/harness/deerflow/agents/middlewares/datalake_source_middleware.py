"""Keep a DataLake query on its authorized enterprise data source."""

import json
import logging
from typing import Annotated, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import Command

from deerflow.runtime.datalake_policy import ACCESS_HINT, ALLOWED_TOOLS, DATA_TOOLS, SOURCE_HINT, STATE_KEY, merge_query_scope, query_scope

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
            "根据用户完整问题和上下文，自主判断是否需要查询企业业务事实、记录或文档，并选择合适工具。"
            "工具可用性只取决于服务端授权，不以关键词或句式判断；示例不是触发条件或白名单。"
            "用户不必说出‘数据湖’或‘数据湖查询’；‘是什么’与‘是多少’不会改变查询资格。例如‘天津公司有多少气田’是主数据问答，"
            "公司/油气田/区块/井的数量、清单、归属，以及产量、指标、明细，统一使用 datalake_data_qa。"
            "井史、报告、地质/完井等非结构化资料内容检索使用 datalake_smart_qa。"
            "涉及具体井的套管程序、井身结构等事实时，按信息需求选择上述工具，必要时结合表格与井史文档。"
            "省略式追问结合对话上下文理解，缺少必要对象才澄清。不要用未经检索的推断代替具体业务记录。"
            "需要企业业务数据时使用 datalake_data_qa 或 datalake_smart_qa；"
            "权限未开启时直接提示用户：请先点击数据湖按钮开启权限。"
            "未授权时不得把企业查询转去外部来源。已授权却缺少工具属于配置/运行问题，不要声称用户没开权限。"
            "区分权限拒绝、工具未配置、空结果、上游故障和超时，按实际回执说明，不要一律要求重新开权限。"
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
            if name in DATA_TOOLS and (not enabled or name not in {"datalake_data_qa", "datalake_smart_qa"}):
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
        authorized = _context(request.runtime).get("datalake_enabled") is True
        code = None
        if name in DATA_TOOLS and not authorized:
            code = "datalake_access_required"
        elif name not in ALLOWED_TOOLS and scope["required"]:
            code = "datalake_external_fallback_forbidden"
        if code is None:
            return None
        hint = ACCESS_HINT if code == "datalake_access_required" else SOURCE_HINT
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
