"""Offline evidence collection through the real installed model factory."""

import importlib.util
import json
import os
from pathlib import Path

import httpx
import pytest

FOLDER = Path(__file__).parent
spec = importlib.util.spec_from_file_location("evidence", FOLDER / "deepseek-v41-transport-evidence.py")
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def packaged():
    n = {"__name__": "test_diagnostic"}
    exec(compile(Path(os.environ["DIAG_SCRIPT"]).read_bytes(), "<diagnostic>", "exec"), n)
    app = {"__name__": "test_app_probe"}
    exec(compile(n["PROBE_SOURCE"], "<app-probe>", "exec"), app)
    return n, app


class Bytes(httpx.AsyncByteStream):
    def __init__(self, raw):
        self.raw = raw

    async def __aiter__(self):
        # Exercise UTF-8/SSE boundaries and split DONE markers.
        for i in range(0, len(self.raw), 7):
            yield self.raw[i : i + 7]


def response(index, done=True):
    if index == 0:
        return httpx.Response(200, stream=Bytes(json.dumps(dict(choices=[dict(finish_reason="stop", message=dict(content="V41_PROBE_OK"))])).encode()), headers={"content-type": "application/json"})
    delta = dict(role="assistant", content="RED BLUE" if index == 5 else "V41_PROBE_OK")
    if index in (2, 5):
        delta["reasoning_content"] = "private synthetic reasoning"
    if index == 3:
        delta.update(content="", tool_calls=[dict(index=0, id="test-call", type="function", function=dict(name="probe_echo", arguments='{"value":"V41_PROBE_OK"}'))])
    data = dict(id="test", model="DeepSeek-V4.1-Flash", choices=[dict(index=0, delta=delta, finish_reason="tool_calls" if index == 3 else "stop")])
    raw = ("data: " + json.dumps(data) + "\n\n" + ("data: [DONE]\n\n" if done else "")).encode()
    return httpx.Response(200, stream=Bytes(raw), headers={"content-type": "text/event-stream", "x-request-id": "upstream-123456789", "server": "nginx"})


async def run(error_index=None, missing_done=None, parent_adapter=False):
    n, app = packaged()
    if parent_adapter:
        n['PROFILE']['use'] = 'deerflow.models.robust_openai:RobustChatOpenAI'
    requests = []
    import types

    evidence = types.ModuleType("packaged_evidence")
    exec(compile(n["EVIDENCE_SOURCE"], "<evidence>", "exec"), evidence.__dict__)
    recorder = evidence.Recorder("test-secret-private", "test-op")

    def handler(request):
        index = len(requests)
        requests.append(request)
        if index == error_index:
            return httpx.Response(
                503, stream=Bytes(b'{"code":503,"data":"Service unavailable no fallback test-secret-private","message":"private"}'), headers={"content-type": "application/json", "x-request-id": "upstream-abcdef0123456789"}
            )
        return response(index, done=index != missing_done)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), event_hooks={"request": [recorder.request], "response": [recorder.response]}) as client:
        result = await evidence.diagnose("test-secret-private", n["PROFILE"], app["run_probe"], client, recorder)
    return result, requests


@pytest.mark.asyncio
async def test_six_requests_actual_factory_wire_and_done():
    result, requests = await run()
    assert len(requests) == 6
    assert result["status"] == "SAMPLE_ACCEPTED_NOT_DEPLOYED"
    assert result["wire_contract_accepted"] and result["stream_terminal_and_done"]
    assert all(r["request_started_utc"] and r["response_headers_utc"] for r in result["requests"])
    text = json.dumps(result)
    assert "test-secret-private" not in text and "private synthetic reasoning" not in text
    assert result["requests"][4]["request_shape"]["tool_result_ids_match"]


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [1, 4])
async def test_failure_has_correlation_and_body_features_without_retry(index):
    result, requests = await run(error_index=index)
    assert len(requests) == index + 1
    row = result["requests"][-1]
    assert row["http_status"] == 503
    assert row["headers"]["x-request-id"] == "upstream-abcdef0123456789"
    assert "no_fallback" in row["error_body_features"]["categories"]
    assert "test-secret-private" not in json.dumps(result)
    assert result["status"] == "DIAGNOSIS_INCOMPLETE"


@pytest.mark.asyncio
async def test_raw_failure_does_not_masquerade_as_healthy_application():
    result, requests = await run(error_index=0)
    assert len(requests) == 6
    assert result["application"]["status"] == "PROVIDER_ACCEPTED_NOT_DEPLOYED"
    assert result["status"] == "DIAGNOSIS_INCOMPLETE"


@pytest.mark.asyncio
async def test_missing_done_refuses_acceptance_even_with_stop():
    result, _ = await run(missing_done=1)
    assert result["status"] == "DIAGNOSIS_INCOMPLETE"
    assert not result["stream_terminal_and_done"]


def test_header_and_error_privacy():
    result = e.safe_headers(httpx.Headers({"authorization": "Bearer secret", "set-cookie": "private", "x-request-id": "secret", "server": "nginx secret", "via": "private"}), "secret")
    assert "secret" not in json.dumps(result) and "private" not in json.dumps(result)
    value = e.error_features(b'{"code":503,"data":"private credential","message":"private"}')
    assert value["code"] == 503 and "private" not in json.dumps(value)


@pytest.mark.asyncio
async def test_wrong_endpoint_and_seventh_request_rejected():
    recorder = e.Recorder("secret", "test")
    with pytest.raises(ValueError):
        await recorder.request(httpx.Request("POST", "https://example.com/", json={}))
    recorder.rows = [{}] * 6
    with pytest.raises(ValueError):
        await recorder.request(httpx.Request("POST", e.ENDPOINT, json={}))


def test_diagnostic_host_requires_stable_rollout():
    n, _ = packaged()
    d = dict(metadata=dict(uid=n["GATEWAY_UID"], generation=382), spec=dict(replicas=70), status=dict(observedGeneration=382, replicas=70, updatedReplicas=70, readyReplicas=70, availableReplicas=70))
    n["stable"](d)
    d["status"]["updatedReplicas"] = 69
    with pytest.raises(n["Refused"], match="rollout_in_progress"):
        n["stable"](d)


def test_original_r4_explicit_direct_transport():
    n, _ = packaged()
    assert "trust_env=False" in n["DIAGNOSTIC_REMOTE"]
    assert "follow_redirects=False" in n["DIAGNOSTIC_REMOTE"]
    assert n["PROFILE"]["max_retries"] == 0


def test_candidate_isolated_and_bound():
    n, _ = packaged()
    if "CANDIDATE_SOURCE" not in n:
        pytest.skip("original transport-only package")
    import hashlib

    assert hashlib.sha256(n["CANDIDATE_SOURCE"].encode()).hexdigest() == n["CANDIDATE_SHA"]
    assert "sys.modules[module.__name__]=module" in n["DIAGNOSTIC_REMOTE"]
    assert "timeout=180" in n["DIAGNOSTIC_REMOTE"]
    with pytest.raises(n["Refused"], match="candidate_gateway_baseline_changed"):
        n["claim_once"](Path("/nonexistent"), "unused", {"gateway_before": {}})


@pytest.mark.asyncio
async def test_candidate_wire_uses_documented_limit():
    n, _ = packaged()
    if "CANDIDATE_SOURCE" not in n:
        pytest.skip("original transport-only package")
    result, requests = await run()
    for request in requests:
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 1000
        assert "max_completion_tokens" not in payload
    assert result["wire_contract_accepted"]


@pytest.mark.parametrize("bad", ["marker", "report"])
def test_candidate_never_claims_after_bad_historical_receipt(tmp_path, bad):
    n, _ = packaged()
    if "CANDIDATE_SOURCE" not in n:
        pytest.skip("original transport-only package")
    operation = "ce9c7e15fa854b4da7d417a01e774f96"
    marker = tmp_path / "portal-deepseek-v41-transport-20261021-r1.once"
    marker.write_text(json.dumps({"operation": operation if bad == "report" else "wrong"}))
    marker.chmod(0o600)
    directory = tmp_path / ("portal-deepseek-v41-transport-" + operation)
    directory.mkdir(mode=0o700)
    report = directory / "REPORT.json"
    report.write_text("{}")
    report.chmod(0o600)
    claimed = []
    n["original_claim_once"] = lambda *args: claimed.append(True)
    baseline = dict(generation=382, spec_sha256="699119d2d3e7a2e30aad980d5c4f37560be9022b00059a2712b6512e4b3816ff", uid=n["GATEWAY_UID"])
    with pytest.raises(n["Refused"], match="prior_" + bad + "_changed"):
        n["claim_once"](tmp_path, "candidate.once", {"gateway_before": baseline})
    assert not claimed


@pytest.mark.asyncio
async def test_candidate_rejects_parent_serialization_even_if_provider_accepts():
    n, _ = packaged()
    if 'CANDIDATE_SOURCE' not in n:
        pytest.skip('original transport-only package')
    result, requests = await run(parent_adapter=True)
    assert len(requests) == 6
    assert result['application']['status'] == 'PROVIDER_ACCEPTED_NOT_DEPLOYED'
    assert result['stream_terminal_and_done']
    assert not result['wire_contract_accepted']
    assert result['status'] == 'DIAGNOSIS_INCOMPLETE'


def test_candidate_temporary_module_load(monkeypatch):
    n, _ = packaged()
    if 'CANDIDATE_SOURCE' not in n:
        pytest.skip('original transport-only package')
    import sys
    import types
    import importlib
    name = 'deerflow.models.enterprise_deepseek_v41'
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = types.ModuleType(name)
    exec(compile(n['CANDIDATE_SOURCE'], '<candidate>', 'exec'), module.__dict__)
    monkeypatch.setitem(sys.modules, name, module)
    cls = getattr(importlib.import_module(name), 'EnterpriseDeepSeekV41Chat')
    from langchain_core.messages import HumanMessage
    obj = cls(model='DeepSeek-V4.1-Flash', api_key='synthetic', max_tokens=1000)
    assert obj._get_request_payload([HumanMessage('test')])['max_tokens'] == 1000
