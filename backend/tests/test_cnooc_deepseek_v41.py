import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from langchain_core.messages import HumanMessage

from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.models.cnooc_deepseek_v41 import ENDPOINT, CnoocDeepSeekV41Chat
from deerflow.models.factory import create_chat_model


def test_full_endpoint_bearer_and_explicit_thinking_off():
    seen = []

    def serve(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "model": "DeepSeek-V4.1-Flash-1017",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )

    model = CnoocDeepSeekV41Chat(model="DeepSeek-V4.1-Flash-1017", api_key="test-not-secret", transport=httpx.MockTransport(serve))
    assert model.invoke("hi").content == "ok"
    assert str(seen[0].url) == ENDPOINT
    assert seen[0].headers["authorization"] == "Bearer test-not-secret"
    payload = json.loads(seen[0].content)
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload


def test_thinking_on_uses_flat_effort_and_keeps_vision():
    model = CnoocDeepSeekV41Chat(model="DeepSeek-V4.1-Flash-1017", api_key="test", reasoning_effort="high", extra_body={"thinking": {"type": "enabled"}})
    payload = model._get_request_payload([HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}])])
    assert payload["reasoning_effort"] == "high"
    assert payload["extra_body"]["thinking"]["type"] == "enabled"
    assert payload["messages"][0]["content"][0]["type"] == "image_url"


def test_invalid_ca_and_endpoint_fail_closed(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        CnoocDeepSeekV41Chat(base_url="https://elsewhere.example/v1", api_key="test")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "missing.pem"))
    with pytest.raises(OSError):
        CnoocDeepSeekV41Chat(api_key="test")


@pytest.mark.asyncio
async def test_sse_usage_and_exact_async_endpoint():
    def serve(request):
        assert str(request.url) == ENDPOINT
        assert json.loads(request.content)["stream"] is True
        frames = [
            {"id": "x", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]},
            {"id": "x", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "completion_tokens_details": {"reasoning_tokens": 3}}},
        ]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join("data: " + json.dumps(f) + "\n\n" for f in frames) + "data: [DONE]\n\n")

    model = CnoocDeepSeekV41Chat(model="DeepSeek-V4.1-Flash-1017", api_key="test", async_transport=httpx.MockTransport(serve))
    chunks = [chunk async for chunk in model.astream("hi")]
    combined = chunks[0]
    for chunk in chunks[1:]:
        combined += chunk
    assert combined.content == "ok"
    assert combined.usage_metadata["total_tokens"] == 15


@pytest.mark.parametrize("thinking,effort", [(False, "high"), (True, "low"), (True, "high"), (True, None)])
def test_release_profile_uses_existing_key_and_factory_selection(monkeypatch, thinking, effort):
    source = Path(__file__).resolve().parents[2] / "deploy/enterprise/deepseek-v4.1-flash-1017.model.yaml"
    profile = yaml.safe_load(source.read_text())
    assert profile["api_key"] == "$DeepSeek_V4_API_KEY"
    monkeypatch.setenv("DeepSeek_V4_API_KEY", "existing-v4-test-key")
    model_config = ModelConfig(**AppConfig.resolve_env_variables(profile))
    config = SimpleNamespace(models=[SimpleNamespace(name="existing-default"), model_config], get_model_config=lambda _: model_config)
    model = create_chat_model(name=model_config.name, thinking_enabled=thinking, reasoning_effort=effort, app_config=config, attach_tracing=False)
    assert config.models[0].name == "existing-default"
    assert model.openai_api_key.get_secret_value() == "existing-v4-test-key"
    payload = model._get_request_payload("hi")
    assert payload["extra_body"]["thinking"]["type"] == ("enabled" if thinking else "disabled")
    assert payload.get("reasoning_effort") == ((effort or "medium") if thinking else None)
