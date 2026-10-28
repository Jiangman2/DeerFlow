"""Keep a DataLake query on its authorized enterprise data source."""

import json
from typing import Annotated, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from deerflow.runtime.datalake_policy import ACCESS_HINT, ALLOWED_TOOLS, DATA_TOOLS, SOURCE_HINT, STATE_KEY, explicit_data_question, merge_query_scope, query_scope


class DataLakeSourceState(AgentState):
    datalake_query_scope: NotRequired[Annotated[dict, merge_query_scope]]


def _context(runtime):
    value = getattr(runtime, "context", None)
    return value if isinstance(value, dict) else {}


SOURCE_STOP_HINT = "当前数据湖查询已停止：请求的工具不适用于受限的数据获取阶段，不能通过外部搜索、浏览器或 Bash 替代数据湖查询。请补充查询条件或明确提出独立的本地处理任务。"


class DataLakeSourceMiddleware(AgentMiddleware[DataLakeSourceState]):
    state_schema = DataLakeSourceState

    def _update(self, state, runtime):
        scope = query_scope(state, _context(runtime))
        return {STATE_KEY: scope}

    def before_agent(self, state, runtime):
        return self._update(state, runtime)

    async def abefore_agent(self, state, runtime):
        return self._update(state, runtime)

    @hook_config(can_jump_to=["end"])
    def after_model(self, state, runtime):
        return self._after(state, runtime)

    @hook_config(can_jump_to=["end"])
    async def aafter_model(self, state, runtime):
        return self._after(state, runtime)

    def _stop(self, scope, *, access, last=None):
        scope = {**scope, "status": "waiting_access" if access else "source_blocked"}
        kwargs = dict(last.additional_kwargs) if last is not None else {}
        for key in ("tool_calls", "function_call", "reasoning_content"):
            kwargs.pop(key, None)
        kwargs.update(deerflow_terminal_stop=True, deerflow_waiting_for_user=True, waiting_reason="datalake_access_required" if access else "datalake_external_fallback_forbidden")
        message = AIMessage(
            id=(last.id if last is not None else None) or f"datalake-stop-{scope['run_key']}",
            content=ACCESS_HINT if access else SOURCE_STOP_HINT,
            additional_kwargs=kwargs,
            response_metadata={"finish_reason": "stop"},
        )
        return {STATE_KEY: scope, "messages": [message], "jump_to": "end"}

    def _after(self, state, runtime):
        scope = query_scope(state, _context(runtime))
        messages = state.get("messages") or []
        last = messages[-1] if messages and isinstance(messages[-1], AIMessage) else None
        calls = last.tool_calls if last is not None else []
        # Inspect the entire proposed batch before any parallel tool can run.
        # A tool's query text alone is not durable task authority for later runs.
        if not scope["required"] and any(self._query_attempt(call) for call in calls):
            scope = {**scope, "required": True, "origin": "tool_query"}
        if scope["required"]:
            if _context(runtime).get("datalake_enabled") is not True:
                return self._stop(scope, access=True, last=last)
            if any(call.get("name") not in ALLOWED_TOOLS for call in calls):
                return self._stop(scope, access=False, last=last)
        return {STATE_KEY: scope}

    @staticmethod
    def _query_attempt(call):
        args = call.get("args") or {}
        query = args.get("query") if isinstance(args, dict) else None
        return isinstance(query, str) and explicit_data_question(query)

    def _before(self, state, runtime):
        scope = query_scope(state, _context(runtime))
        update = {STATE_KEY: scope}
        if scope["required"] and _context(runtime).get("datalake_enabled") is not True:
            return self._stop(scope, access=True)
        if scope["required"] and scope.get("status") == "source_blocked":
            return self._stop(scope, access=False)
        return update

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
            "引用报错、解释权限按钮或排查代码不等于请求查询数据湖；不要因为历史出现权限提示就拒绝普通任务。"
            f"当前数据湖授权状态：{'已开启' if enabled else '未开启'}。"
        )
        if scope["required"]:
            instruction += SOURCE_HINT
        system = request.system_message
        blocks = list(system.content_blocks) if system is not None else []
        blocks.append({"type": "text", "text": instruction})
        allowed = [t for t in request.tools if (t.get("name") if isinstance(t, dict) else getattr(t, "name", None)) in ALLOWED_TOOLS] if scope["required"] else request.tools
        return request.override(system_message=SystemMessage(content=blocks), tools=allowed)

    def wrap_model_call(self, request, handler):
        return handler(self._prepare(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._prepare(request))

    def _blocked(self, request):
        scope = query_scope(request.state, _context(request.runtime))
        name = request.tool_call.get("name")
        if name in DATA_TOOLS or self._query_attempt(request.tool_call):
            if not scope["required"]:
                scope["origin"] = "data_tool" if name in DATA_TOOLS else "tool_query"
            scope["required"] = True
        if not scope["required"]:
            return None
        authorized = _context(request.runtime).get("datalake_enabled") is True
        if authorized and name in ALLOWED_TOOLS:
            return None
        result = ToolMessage(
            content=json.dumps({"code": "datalake_access_required" if not authorized else "datalake_external_fallback_forbidden", "error": ACCESS_HINT if not authorized else SOURCE_HINT, "retryable": False}, ensure_ascii=False),
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )
        scope["status"] = "waiting_access" if not authorized else "source_blocked"
        # Do not jump from an individual parallel tool: let the batch settle its
        # paired ToolMessages, then before_model ends without another model call.
        return Command(update={STATE_KEY: scope, "messages": [result]})

    def wrap_tool_call(self, request, handler):
        return self._blocked(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        blocked = self._blocked(request)
        return blocked if blocked is not None else await handler(request)
