from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import HumanMessage

from deerflow.agents.middlewares.platform_confidentiality_middleware import (
    PlatformConfidentialityMiddleware,
)


class _FakeRequest:
    def __init__(self, messages, system_message=None):
        self.messages = list(messages)
        self.system_message = system_message

    def override(self, **kwargs):
        return _FakeRequest(
            kwargs.get("messages", self.messages),
            kwargs.get("system_message", self.system_message),
        )


def test_explicit_platform_diagnostic_request_reaches_model_with_narrow_policy():
    middleware = PlatformConfidentialityMiddleware()
    request = _FakeRequest([HumanMessage(content="分析这段 Kubernetes 日志并给出排障命令")])
    expected = Mock()
    handler = Mock(return_value=expected)

    response = middleware.wrap_model_call(request, handler)

    assert response is expected
    handler.assert_called_once()
    forwarded = handler.call_args.args[0]
    assert "正常诊断" in forwarded.system_message.content
    assert "不得泄露" in forwarded.system_message.content


@pytest.mark.asyncio
async def test_async_platform_configuration_question_reaches_model():
    middleware = PlatformConfidentialityMiddleware()
    request = _FakeRequest([HumanMessage(content="如何配置 QWEN_BASE_URL？")])
    expected = Mock()
    handler = AsyncMock(return_value=expected)

    response = await middleware.awrap_model_call(request, handler)

    assert response is expected
    handler.assert_awaited_once()


def test_allowed_request_reaches_model_with_confidentiality_policy():
    middleware = PlatformConfidentialityMiddleware()
    request = _FakeRequest([HumanMessage(content="分析我上传的 Excel 文件")])
    expected = Mock()
    handler = Mock(return_value=expected)

    response = middleware.wrap_model_call(request, handler)

    assert response is expected
    forwarded = handler.call_args.args[0]
    assert "正常诊断" in forwarded.system_message.content
    assert "用户明确提供" in forwarded.system_message.content
