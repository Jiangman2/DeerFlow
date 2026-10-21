"""FCV-41 token-field compatibility without changing other model adapters."""

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from deerflow.models.enterprise_deepseek_v41 import EnterpriseDeepSeekV41Chat
from deerflow.models.robust_openai import RobustChatOpenAI


def model(**kw):
    return EnterpriseDeepSeekV41Chat(model="DeepSeek-V4.1-Flash", api_key="synthetic-test", base_url="https://aiapi.cnooc.com.cn/DeepSeek-V4.1-Flash/FCV-41/v1", **kw)


@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
def test_constructor_aliases_use_documented_wire_key(field):
    payload = model(**{field: 1000})._get_request_payload([HumanMessage("test")])
    assert payload["max_tokens"] == 1000
    assert "max_completion_tokens" not in payload


def test_per_call_limit_and_thinking_body_preserved():
    original = {"enable_thinking": False}
    m = model(max_tokens=1000, extra_body=original)
    payload = m._get_request_payload([HumanMessage("test")], max_tokens=500)
    assert payload["max_tokens"] == 500
    assert payload["extra_body"] == original == {"enable_thinking": False}
    assert m.max_tokens == 1000


def test_extra_body_limit_does_not_override_silently():
    with pytest.raises(ValueError, match="consistent"):
        model(max_tokens=1000, extra_body={"max_tokens": 500})._get_request_payload([HumanMessage("test")])
    p = model(max_tokens=1000, extra_body={"enable_thinking": True, "max_tokens": 1000})._get_request_payload([HumanMessage("test")])
    assert p["max_tokens"] == 1000
    assert p["extra_body"] == {"enable_thinking": True}


def test_no_limit_is_not_invented():
    p = model()._get_request_payload([HumanMessage("test")])
    assert "max_tokens" not in p and "max_completion_tokens" not in p


def test_other_models_keep_parent_behavior():
    p = RobustChatOpenAI(model="another-model", api_key="test", max_tokens=1000)._get_request_payload([HumanMessage("test")])
    assert p["max_completion_tokens"] == 1000


def test_tool_history_and_image_content_preserved():
    history = [HumanMessage("test"), AIMessage(content="", tool_calls=[dict(id="call1", name="echo", args={})]), ToolMessage("value", tool_call_id="call1")]
    p = model(max_tokens=1000)._get_request_payload(history)
    assert p["messages"][-1]["tool_call_id"] == p["messages"][-2]["tool_calls"][0]["id"] == "call1"
    images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}, {"type": "text", "text": "test"}]
    assert model()._get_request_payload([HumanMessage(images)])["messages"][0]["content"] == images


def test_responses_and_wrong_model_refused():
    with pytest.raises(ValidationError):
        model(use_responses_api=True)
    m = model()
    with pytest.raises(ValueError, match="dedicated profile"):
        m._get_request_payload([HumanMessage("test")], model="another-model")


@pytest.mark.asyncio
async def test_real_http_serialization_has_only_max_tokens():
    import json

    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=dict(id="test", model="DeepSeek-V4.1-Flash", choices=[dict(index=0, message=dict(role="assistant", content="OK"), finish_reason="stop")]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await model(max_tokens=1000, http_async_client=client).ainvoke([HumanMessage("test")])
    assert result.content == "OK"
    assert seen[0]["max_tokens"] == 1000 and "max_completion_tokens" not in seen[0]
