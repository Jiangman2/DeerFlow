import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.models.enterprise_glm import EnterpriseGLMChat
from deerflow.models.factory import create_chat_model


def model(**kwargs):
    return EnterpriseGLMChat(api_key="test-only", model="GLM-5.3-Flash", **kwargs)


def test_thinking_is_always_enabled_and_not_forwarded_as_false():
    payload = model(extra_body={"thinking": {"type": "disabled"}})._get_request_payload([HumanMessage("hello")])
    assert payload["extra_body"]["thinking"] == {"type": "enabled"}
    assert payload["model"] == "GLM-5.3-Flash"


def test_image_precedes_text_without_mutating_message():
    blocks = [{"type": "text", "text": "describe"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}]
    msg = HumanMessage(blocks)
    payload = model()._get_request_payload([msg])
    assert payload["messages"][0]["content"][0]["type"] == "image_url"
    assert msg.content == blocks


def test_multiple_images_fail_before_provider_request():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}
    with pytest.raises(ValueError, match="single image"):
        model()._get_request_payload([HumanMessage([image, image])])


@pytest.mark.parametrize("effort", ["low", "medium", "minimal"])
def test_unsupported_effort_is_explicit_not_silently_sent(effort):
    with pytest.raises(ValueError, match="reasoning_effort"):
        model()._get_request_payload([HumanMessage("hello")], reasoning_effort=effort)


@pytest.mark.parametrize("effort", ["high", "max"])
def test_supported_effort(effort):
    assert model()._get_request_payload([HumanMessage("hello")], reasoning_effort=effort)["reasoning_effort"] == effort


def test_tool_roundtrip_keeps_reasoning_and_function_call():
    assistant = AIMessage("", additional_kwargs={"reasoning_content": "test reasoning"}, tool_calls=[{"name": "lookup", "args": {"q": "x"}, "id": "call1"}])
    payload = model()._get_request_payload([HumanMessage("hi"), assistant, ToolMessage("result", tool_call_id="call1")])
    assert payload["messages"][1]["reasoning_content"] == "test reasoning"
    assert payload["messages"][1]["tool_calls"][0]["function"]["name"] == "lookup"
    assert payload["messages"][2]["tool_call_id"] == "call1"


def test_factory_does_not_send_ui_effort_or_presentation_flags():
    profile = ModelConfig(
        name="glm", use="deerflow.models.enterprise_glm:EnterpriseGLMChat", model="GLM-5.3-Flash", api_key="test-only", supports_thinking=True, thinking_always_on=True, supports_vision=True, supports_reasoning_effort=False
    )
    config = AppConfig(models=[profile], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))
    instance = create_chat_model("glm", thinking_enabled=False, reasoning_effort="low", app_config=config, attach_tracing=False)
    payload = instance._get_request_payload([HumanMessage("hi")])
    assert payload["extra_body"]["thinking"]["type"] == "enabled"
    assert "reasoning_effort" not in payload
    assert "thinking_always_on" not in payload
    assert "supports_vision" not in payload


@pytest.mark.asyncio
async def test_total_deadline_is_not_extended_by_keepalive(monkeypatch):
    import asyncio

    from deerflow.models.robust_openai import RobustChatOpenAI

    async def heartbeats(*args, **kwargs):
        while True:
            await asyncio.sleep(0.005)
            yield None

    monkeypatch.setattr(RobustChatOpenAI, "_astream", heartbeats)
    with pytest.raises(TimeoutError):
        async for _ in model(request_deadline_seconds=0.03)._astream([]):
            pass


@pytest.mark.asyncio
async def test_models_api_exposes_vision_and_fixed_thinking_but_no_credentials():
    from app.gateway.routers.models import get_model, list_models

    profile = ModelConfig(
        name="glm",
        use="deerflow.models.enterprise_glm:EnterpriseGLMChat",
        model="GLM-5.3-Flash",
        api_key="test-only-credential",
        base_url="https://private-model.example.test/v1",
        supports_thinking=True,
        thinking_always_on=True,
        supports_vision=True,
    )
    config = AppConfig(models=[profile], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))
    for result in ((await list_models(config)).models[0], await get_model("glm", config)):
        assert result.supports_vision is True and result.thinking_always_on is True
        assert "test-only-credential" not in result.model_dump_json()
        assert "private-model.example.test" not in result.model_dump_json()


@pytest.mark.parametrize("image_url", [None, "https://example.test/a.png", {"url": "https://example.test/a.png"}])
def test_malformed_or_remote_image_rejected_cleanly(image_url):
    with pytest.raises(ValueError, match="base64 image"):
        model()._get_request_payload([HumanMessage([{"type": "image_url", "image_url": image_url}])])
