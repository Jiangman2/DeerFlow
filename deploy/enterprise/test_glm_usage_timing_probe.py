"""Installed-adapter observation tests with real SDK, synthetic HTTP only."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.models.factory import create_chat_model

SPEC = importlib.util.spec_from_file_location(
    "usage_probe", Path(__file__).with_name("glm-usage-timing-probe.py")
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def config(
    cumulative=False, endpoint="https://aiapi.cnooc.com.cn/GLM-5.3-Flash/FCGLM-001/v1"
):
    return AppConfig(
        models=[
            ModelConfig(
                name="glm",
                use="deerflow.models.enterprise_glm:EnterpriseGLMChat",
                model="GLM-5.3-Flash",
                api_key="private-fixture-key",
                base_url=endpoint,
                stream_usage=True,
                cumulative_stream_usage=cumulative,
                supports_thinking=True,
                thinking_always_on=True,
            )
        ],
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
    )


def event(delta, finish=None, usage=None):
    return (
        "data: "
        + json.dumps(
            {
                "id": "fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "GLM-5.3-Flash",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                "usage": usage,
            }
        )
        + "\n\n"
    )


def fixture(monkeypatch, cfg, handler):
    import deerflow.config
    import deerflow.models.factory

    monkeypatch.setattr(deerflow.config, "get_app_config", lambda: cfg)
    original_read = Path.read_bytes

    def source(path):
        if str(path).startswith("/app/backend/packages/harness/deerflow/models/"):
            return b"fixture-source"
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", source)

    def factory(*args, **kwargs):
        kwargs["model_overrides"]["http_async_client"] = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        return create_chat_model(*args, **kwargs)

    monkeypatch.setattr(deerflow.models.factory, "create_chat_model", factory)


@pytest.mark.asyncio
@pytest.mark.parametrize("midstream", [False, True])
async def test_observes_usage_timing_and_no_content_export(monkeypatch, midstream):
    usage = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    body = event({"content": "private-answer"}, usage=usage if midstream else None)
    body += event({}, "stop", usage) + "data: [DONE]\n\n"
    calls = []

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["stream_options"]["include_usage"] is True
        assert payload.get("max_completion_tokens", payload.get("max_tokens")) == 512
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )

    cfg = config(cumulative=midstream)
    before = cfg.model_dump()
    fixture(monkeypatch, cfg, handler)
    result = await M.run_probe(True)
    assert result["status"] == "GLM_USAGE_TIMING_OBSERVED"
    assert result["provider_requests_attempted"] == 1 and len(calls) == 1
    assert result["timing"]["upstream_nonzero_usage_before_terminal"] is midstream
    assert set(result["timing"]["first_nonzero_usage"]) == {
        "upstream",
        "converted",
        "delivered",
    }
    assert "private-answer" not in json.dumps(
        result
    ) and "private-fixture-key" not in json.dumps(result)
    assert cfg.model_dump() == before


@pytest.mark.asyncio
async def test_preflight_no_provider_call(monkeypatch):
    def no_call(request):
        pytest.fail("unexpected request")

    fixture(monkeypatch, config(), no_call)
    result = await M.run_probe(False)
    assert (
        result["status"] == "PREFLIGHT_PASS"
        and result["provider_requests_attempted"] == 0
    )


@pytest.mark.asyncio
async def test_503_no_retry_no_raw_body(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"message": "private-server-error"}})

    fixture(monkeypatch, config(), handler)
    result = await M.run_probe(True)
    assert result["status"] == "INCOMPLETE" and len(calls) == 1
    assert result["provider_requests_attempted"] == 1
    assert "private-server-error" not in json.dumps(result)


@pytest.mark.asyncio
async def test_endpoint_change_refused_before_request(monkeypatch):
    fixture(
        monkeypatch,
        config(endpoint="https://wrong.invalid/v1"),
        lambda _: pytest.fail("unexpected request"),
    )
    with pytest.raises(AssertionError, match="ENDPOINT_MISMATCH"):
        await M.run_probe(True)


def test_observer_zero_and_terminal_are_not_live_nonzero_usage():
    observer = M.UsageObserver()
    observer.upstream(
        {
            "choices": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    )
    observer.upstream(
        {"choices": [{"finish_reason": "stop"}], "usage": {"total_tokens": 7}}
    )
    report = observer.summary()
    assert not report["upstream_nonzero_usage_before_terminal"]
    assert report["usage_frame_counts"]["upstream"] == 2
    assert report["first_nonzero_usage"]["upstream"]["frame"] == 2
