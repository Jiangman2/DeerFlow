import json

import httpx
import pytest

from app.gateway.enterprise_glm_probe import BASE_URL, PROBE_MAX_TOKENS, error_category, main, run_probe


def reply(content="", tool=False, finish=None, reasoning=None):
    delta = {"role": "assistant", "content": content}
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    if tool:
        delta["tool_calls"] = [{"index": 0, "id": "probe1", "type": "function", "function": {"name": "probe_echo", "arguments": '{"value":"GLM_PROBE_OK"}'}}]
    data = dict(id="probe", object="chat.completion.chunk", created=1, model="GLM-5.3-Flash", choices=[dict(index=0, delta=delta, finish_reason=finish or ("tool_calls" if tool else "stop"))])
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n")


@pytest.mark.asyncio
async def test_probe_four_bounded_requests_using_real_adapter_and_no_business_tools():
    requests = []

    def respond(request):
        assert str(request.url) == BASE_URL + "/chat/completions"
        body = json.loads(request.content)
        requests.append(body)
        assert body["stream"] is True and body["model"] == "GLM-5.3-Flash"
        assert body["thinking"]["type"] == "enabled"
        assert body.get("max_completion_tokens", body.get("max_tokens")) == PROBE_MAX_TOKENS == 1000
        return [reply("GLM_PROBE_OK"), reply(tool=True), reply("GLM_PROBE_OK"), reply("红色")][len(requests) - 1]

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await run_probe("not-a-real-key", http, request_deadline=1)
    assert result["glm_canary"] == "PASS"
    assert result["provider_requests_attempted"] == 4
    assert result["business_tools_executed"] == 0
    assert result["usage"] == "NOT_REPORTED"
    assert set(result["stage_results"].values()) == {"PASS"}
    assert "not-a-real-key" not in json.dumps(result)
    assert requests[2]["messages"][-1]["role"] == "tool"
    assert requests[3]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_probe_redacts_provider_error_and_does_not_retry():
    requests = []

    def fail(request):
        requests.append(request)
        return httpx.Response(500, json={"error": {"message": "PRIVATE-SECRET-CONTENT", "type": "fixture"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        result = await run_probe("secret", http, request_deadline=1)
    assert len(requests) == 1 and result["glm_canary"] == "FAIL"
    assert "PRIVATE-SECRET-CONTENT" not in json.dumps(result)
    assert result["raw_output_printed"] is False


@pytest.mark.asyncio
async def test_probe_missing_tool_call_is_failure_not_fabricated_success():
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        return reply("GLM_PROBE_OK")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await run_probe("fixture", http, request_deadline=1)
    assert result["glm_canary"] == "FAIL" and result["stage"] == "tool_call"
    assert calls == 2


def test_cli_requires_explicit_run_and_server_side_key(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        main([])
    monkeypatch.delenv("GLM_5_3_FLASH_API_KEY", raising=False)
    assert main(["--run"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["provider_requests_attempted"] == 0


def test_error_categories_keep_tls_cause_without_leaking_details():
    outer = RuntimeError("PRIVATE-SECRET")
    outer.__cause__ = OSError("[SSL: CERTIFICATE_VERIFY_FAILED] PRIVATE-SECRET")
    assert error_category(outer) == "TLS_CERTIFICATE_VERIFICATION"
    outer.__cause__ = TimeoutError("PRIVATE-SECRET")
    assert error_category(outer) == "TIMEOUT"
    outer.status_code = 429
    assert error_category(outer) == "RATE_OR_QUOTA_LIMIT_NOT_DISTINGUISHED"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "GLM_PROBE_OK"])
async def test_output_limit_is_not_success_even_with_expected_marker(content):
    calls = []

    def respond(request):
        calls.append(request)
        return reply(content, finish="length", reasoning="PRIVATE-REASONING")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await run_probe("fixture", http, request_deadline=1)
    assert len(calls) == 1
    assert result["glm_canary"] == "FAIL"
    assert result["error_class"] == "OUTPUT_LIMIT_REACHED_NOT_CAPABILITY_VERDICT"
    assert result["stage_results"] == {"text": "INCOMPLETE", "tool_call": "NOT_ATTEMPTED", "tool_resume": "NOT_ATTEMPTED", "image": "NOT_ATTEMPTED"}
    assert result["checks"][0]["reasoning_present"] is True
    assert "PRIVATE-REASONING" not in json.dumps(result)


@pytest.mark.asyncio
async def test_filtered_expected_marker_is_not_pass():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: reply("GLM_PROBE_OK", finish="content_filter"))) as http:
        result = await run_probe("fixture", http, request_deadline=1)
    assert result["glm_canary"] == "FAIL" and result["provider_requests_attempted"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_flash_reasoning", [False, True])
async def test_modes_probe_five_requests_or_stops_on_ignored_disabled(bad_flash_reasoning):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        index = len(requests) - 1
        assert body["thinking"]["type"] == ("enabled" if index in (1, 4) else "disabled")
        assert "reasoning_effort" not in body
        return [reply("GLM_PROBE_OK", reasoning="unexpected" if bad_flash_reasoning else None),
                reply("GLM_PROBE_OK", reasoning="synthetic"), reply(tool=True), reply("GLM_PROBE_OK"), reply("红色")][index]

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await run_probe("fixture", http, request_deadline=1, verify_modes=True)
    assert result["glm_canary"] == ("FAIL" if bad_flash_reasoning else "PASS")
    assert len(requests) == (1 if bad_flash_reasoning else 5)
    assert "unexpected" not in json.dumps(result)
