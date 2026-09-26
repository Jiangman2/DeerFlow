"""Recheck admitted publication and grants before every model/tool boundary."""

import asyncio

from langchain.agents.middleware import AgentMiddleware

from deerflow.runtime.employee_resources import authorize_employee_execution


class EmployeePublicationMiddleware(AgentMiddleware):
    def __init__(self, *, thread_id: str, user_id: str):
        self.thread_id = thread_id
        self.user_id = user_id

    async def _check(self):
        return await authorize_employee_execution(self.thread_id, self.user_id)

    def _sync_check(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._check())
        raise RuntimeError("Published employee execution requires the asynchronous runtime")

    def wrap_model_call(self, request, handler):
        self._sync_check()
        return handler(request)

    async def awrap_model_call(self, request, handler):
        await self._check()
        return await handler(request)

    def wrap_tool_call(self, request, handler):
        self._sync_check()
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        await self._check()
        return await handler(request)
