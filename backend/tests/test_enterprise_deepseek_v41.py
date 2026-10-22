"""Qualify the configured provider and selector metadata without live credentials."""

import json
from pathlib import Path

import httpx
import pytest
import yaml
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.models.factory import create_chat_model

PROFILE = Path(__file__).resolve().parents[2] / "deploy/enterprise/deepseek-v4.1-flash.model.yaml"


def configuration():
    row = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))["models"][0]
    assert row["api_key"] == "$DEEPSEEK_V4_1_FLASH_API_KEY"
    row["api_key"] = "synthetic-test-key"
    return AppConfig(models=[ModelConfig(**row)], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))


def model(thinking=False, **kwargs):
    return create_chat_model("deepseek-v4.1-flash", thinking_enabled=thinking, app_config=configuration(), attach_tracing=False, **kwargs)


@pytest.mark.parametrize("thinking", [False, True])
def test_thinking_mode_payload_and_no_unsupported_effort(thinking):
    instance = model(thinking, reasoning_effort="high")
    body = instance._get_request_payload([HumanMessage("hello")])
    assert str(instance.openai_api_base) == "https://aiapi.cnooc.com.cn/DeepSeek-V4.1-Flash/FCV-41/v1"
    assert instance.use_responses_api is False
    assert body["model"] == "DeepSeek-V4.1-Flash"
    assert body["extra_body"] == {"enable_thinking": thinking}
    assert "reasoning_effort" not in body
    assert "thinking_always_on" not in body


def test_multi_image_and_tool_roundtrip_are_preserved():
    images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture", "detail": "low"}}] * 2
    assistant = AIMessage("", tool_calls=[{"name": "probe_echo", "args": {"value": "OK"}, "id": "call_1"}])
    body = model()._get_request_payload([HumanMessage(images + [{"type": "text", "text": "compare"}]), assistant, ToolMessage("OK", tool_call_id="call_1")])
    assert len(body["messages"][0]["content"]) == 3
    assert body["messages"][1]["tool_calls"][0]["function"]["name"] == "probe_echo"
    assert body["messages"][2]["tool_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_selector_can_offer_flash_and_vision_without_credentials():
    from app.gateway.routers.models import list_models

    response = await list_models(configuration())
    row = response.models[0]
    assert row.name == "deepseek-v4.1-flash" and row.display_name == "DeepSeek-V4.1-Flash"
    assert row.supports_thinking and row.supports_vision and not row.thinking_always_on
    assert not row.supports_reasoning_effort
    assert "synthetic-test-key" not in response.model_dump_json()
    assert "aiapi.cnooc.com.cn" not in response.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("thinking", [False, True])
async def test_actual_sdk_stream_wire_and_empty_choices_usage(thinking):
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        assert str(request.url) == "https://aiapi.cnooc.com.cn/DeepSeek-V4.1-Flash/FCV-41/v1/chat/completions"
        chunks = []
        if thinking:
            chunks.append({"choices": [{"index": 0, "delta": {"reasoning_content": "fixture"}, "finish_reason": None}]})
        chunks.extend(
            [
                {"choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
            ]
        )
        raw = "".join("data: " + json.dumps(dict(id="test", model="DeepSeek-V4.1-Flash", **c)) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=raw)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        instance = model(thinking, http_async_client=client)
        result = None
        async for chunk in instance.astream([HumanMessage("hello")]):
            result = chunk if result is None else result + chunk
    assert len(seen) == 1
    assert seen[0]["enable_thinking"] is thinking
    assert "extra_body" not in seen[0]
    assert "thinking_budget" not in seen[0]
    assert result.content == "OK"
    assert bool(result.additional_kwargs.get("reasoning_content")) is thinking
    assert result.usage_metadata["total_tokens"] == 5
