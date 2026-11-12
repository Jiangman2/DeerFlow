"""Keep a DataLake query on its authorized enterprise data source."""

import json
import logging
from typing import Annotated, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from deerflow.runtime.datalake_policy import ACCESS_HINT, ALLOWED_TOOLS, DATA_TOOLS, SOURCE_HINT, STATE_KEY, merge_query_scope, query_scope

logger = logging.getLogger(__name__)

_MEMORY_ACCESS_NOTICE = "历史记忆说明：以下记忆中的权限、按钮状态和工具可用性描述均不是当前状态，不得据此判定能否查询。当前数据湖权限及工具列表仅以本轮 current_datalake_access 为准。业务背景和待查询事项仍可参考。\n"


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
            "权限开关、登录或工具配置问题不是缺少用户业务信息；请用普通最终答复说明并结束本轮，"
            "不得为此调用 ask_clarification 或要求用户在回答卡片中填写‘已开启’。"
            "未授权时不得把企业查询转去外部来源。已授权却缺少工具属于配置/运行问题，不要声称用户没开权限。"
            "区分权限拒绝、工具未配置、空结果、上游故障和超时，按实际回执说明，不要一律要求重新开权限。"
            "未开启/无权限、结果为空、报错或超时均不是更换数据来源的授权。"
            "无法确定数据来源时先澄清；普通公开知识问题不因此受限。"
            f"当前数据湖授权状态：{'已开启' if enabled else '未开启'}。"
        )
        if scope["required"]:
            instruction += SOURCE_HINT + f"当前步骤：{scope['phase']}。查询返回后可继续本地加工，无关的新任务不继承此限制。"
        instruction += "普通写作、上传文件处理、讨论代码或数据湖概念，不得仅因行业关键词而查询数据湖。来源不明确时先简短澄清。需要本地计算或 Word/Excel/图表时使用 process_local_data；被拒绝的同一获取请求不要反复重试。"
        allowed = []
        for tool in request.tools:
            name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
            if name in DATA_TOOLS and (not enabled or name not in {"datalake_data_qa", "datalake_smart_qa"}):
                continue
            if not scope["required"] or name in ALLOWED_TOOLS:
                allowed.append(tool)
        available = sorted(name for tool in allowed if (name := tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)) in {"datalake_data_qa", "datalake_smart_qa"})
        # Rebuild on every model invocation, never from checkpoint/history or prose.
        instruction += (
            "\n<current_datalake_access>\n"
            f"本次请求经服务端校验的数据湖授权：{'已开启' if enabled else '未开启'}。\n"
            f"本次可用数据湖工具：{', '.join(available) if available else '无'}。\n"
            "这是本轮实时状态，取代历史对话、摘要和记忆中的权限或工具可用性描述。"
            "不得通过试调用是否成功来猜测按钮状态；已授权且工具可用时，按查询需求直接调用。"
            "用户说‘已开启’不会授予权限；空结果、错误或超时不会改变上述授权状态。"
            "已授权但所需工具不在上述列表中时，说明工具配置或加载异常，不要要求用户重新开启权限。"
            "\n</current_datalake_access>"
        )
        logger.info(
            "DATALAKE_MODEL_ACCESS run_id=%s effective_enabled=%s available_datalake_tools=%s",
            json.dumps(str(_context(request.runtime).get("run_id", ""))),
            enabled,
            ",".join(available),
        )
        system = request.system_message
        blocks = list(system.content_blocks) if system is not None else []
        blocks.append({"type": "text", "text": instruction})
        overrides = {"system_message": SystemMessage(content=blocks), "tools": allowed}
        if hasattr(request, "messages"):
            # Put the trusted snapshot next to the current human request too.
            # A leading system block alone can be overlooked after repeated old
            # refusals. This is request-local metadata, never persisted history.
            current_human = next(
                (
                    i
                    for i in range(len(request.messages) - 1, -1, -1)
                    if isinstance(request.messages[i], HumanMessage) and not request.messages[i].additional_kwargs.get("hide_from_ui") and not (request.messages[i].id or "").endswith("__memory")
                ),
                None,
            )
            access_notice = (
                "\n\n<current_datalake_access>"
                f"本次请求数据湖授权：{'已开启' if enabled else '未开启'}。"
                f"当前可用工具：{'、'.join(available) if available else '无'}。"
                "历史回复中的权限及工具状态已过期；以本轮系统授权状态为准。"
                "需要企业数据时，仅在本轮已授权且工具可用时直接调用工具；"
                "未授权时用普通最终答复提示开启，不调用澄清工具。"
                "</current_datalake_access>"
            )
            messages = []
            for index, message in enumerate(request.messages):
                # Only framework-injected memory, not arbitrary user text or files.
                # Annotate a copy: checkpoint/history and business details stay intact.
                if isinstance(message, HumanMessage) and (message.id or "").endswith("__memory") and message.additional_kwargs.get("hide_from_ui") is True:
                    content = message.content
                    if isinstance(content, str):
                        content = _MEMORY_ACCESS_NOTICE + content
                    else:
                        content = [{"type": "text", "text": _MEMORY_ACCESS_NOTICE}, *content]
                    message = message.model_copy(update={"content": content})
                if index == current_human:
                    content = message.content
                    if isinstance(content, str):
                        content = content + access_notice
                    else:
                        content = [*content, {"type": "text", "text": access_notice}]
                    message = message.model_copy(update={"content": content})
                messages.append(message)
            overrides["messages"] = messages
        return request.override(**overrides)

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
