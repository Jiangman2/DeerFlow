import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml

from app.gateway.enterprise_deepseek_v41_probe import BASE_URL, run_probe

PROFILE = yaml.safe_load((Path(__file__).resolve().parents[2] / "deploy/enterprise/deepseek-v4.1-flash.model.yaml").read_text(encoding="utf-8"))["models"][0]


def reply(content="", tool=False, reasoning=None, finish=None):
    delta = {"role": "assistant", "content": content}
    if reasoning:
        delta["reasoning_content"] = reasoning
    if tool:
        delta["tool_calls"] = [{"index": 0, "id": "probe1", "type": "function", "function": {"name": "probe_echo", "arguments": '{"value":"V41_PROBE_OK"}'}}]
    data = dict(id="probe", model="DeepSeek-V4.1-Flash", choices=[dict(index=0, delta=delta, finish_reason=finish or ("tool_calls" if tool else "stop"))])
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n")


@pytest.mark.asyncio
async def test_five_factory_requests_modes_tools_and_multiple_images():
    requests = []

    def respond(request):
        assert str(request.url) == BASE_URL + "/chat/completions"
        requests.append(json.loads(request.content))
        return [reply("V41_PROBE_OK"), reply("V41_PROBE_OK", reasoning="fixture"), reply(tool=True), reply("V41_PROBE_OK"), reply("RED BLUE")][len(requests) - 1]

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await run_probe("synthetic-secret", client, PROFILE, request_deadline=2)
    assert result["status"] == "PROVIDER_ACCEPTED_NOT_DEPLOYED"
    assert result["provider_requests_attempted"] == 5
    assert set(result["stage_results"].values()) == {"PASS"}
    assert [r["enable_thinking"] for r in requests] == [False, True, False, False, True]
    assert requests[3]["messages"][-1]["role"] == "tool"
    assert len([x for x in requests[4]["messages"][0]["content"] if x["type"] == "image_url"]) == 2
    assert "synthetic-secret" not in json.dumps(result)
    assert "fixture" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 503])
async def test_provider_failures_are_redacted_and_not_retried(status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"code": status, "data": "PRIVATE-CREDENTIAL", "message": "PRIVATE-CREDENTIAL"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await run_probe("synthetic-secret", client, PROFILE, request_deadline=2)
    assert result["status"] == "INCOMPLETE" and result["http_status"] == status
    assert len(calls) == 1
    assert "PRIVATE-CREDENTIAL" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("finish,reasoning", [("length", None), ("stop", "hidden fixture")])
async def test_truncation_or_flash_reasoning_cannot_pass(finish, reasoning):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: reply("V41_PROBE_OK", finish=finish, reasoning=reasoning))) as client:
        result = await run_probe("synthetic", client, PROFILE, request_deadline=2)
    assert result["status"] == "INCOMPLETE" and result["provider_requests_attempted"] == 1


@pytest.mark.asyncio
async def test_no_progress_has_total_deadline():
    async def respond(request):
        await asyncio.sleep(1)
        return reply("V41_PROBE_OK")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await run_probe("synthetic", client, PROFILE, request_deadline=0.02)
    assert result["status"] == "INCOMPLETE" and result["provider_requests_attempted"] == 1
    assert result["error_class"] == "TimeoutError"
