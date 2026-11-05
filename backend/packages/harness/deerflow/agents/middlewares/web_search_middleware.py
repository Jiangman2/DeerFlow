"""Enforce the current turn's web-search choice at model and execution boundaries."""

import json

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage, ToolMessage

from deerflow.runtime.web_search_policy import WEB_TOOLS, filter_web_tools, web_search_enabled


class WebSearchMiddleware(AgentMiddleware):
    def _prepare(self, request):
        context = getattr(request.runtime, "context", None)
        if web_search_enabled(context):
            return request
        instruction = (
            "本轮用户关闭了联网搜索，禁止调用 web_search 和 web_fetch。"
            "不得通过委派子任务或其他工具绕过此选择进行公开网页搜索或抓取。"
            "可以继续普通聊天、分析用户附件或使用已授权的企业知识库/数据湖。"
            "确需实时网页信息时，请说明需要用户开启输入框的‘联网搜索’，不要假称已经检索。"
        )
        blocks = list(request.system_message.content_blocks) if request.system_message else []
        blocks.append({"type": "text", "text": instruction})
        return request.override(system_message=SystemMessage(content=blocks), tools=filter_web_tools(request.tools, context))

    def wrap_model_call(self, request, handler):
        return handler(self._prepare(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._prepare(request))

    def _blocked(self, request):
        if request.tool_call.get("name") not in WEB_TOOLS or web_search_enabled(getattr(request.runtime, "context", None)):
            return None
        return ToolMessage(
            content=json.dumps({"code": "web_search_disabled", "error": "请开启输入框的联网搜索后重试。", "retryable": False}, ensure_ascii=False),
            name=request.tool_call["name"],
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        blocked = self._blocked(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        blocked = self._blocked(request)
        return blocked if blocked is not None else await handler(request)
