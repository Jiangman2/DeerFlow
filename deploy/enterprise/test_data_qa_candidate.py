"""Real isolated Python/HTTPX candidate checks, with offline synthetic SSE."""

import ast
import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy/enterprise/probe-data-qa-candidate.py"
REMOTE = next(ast.literal_eval(n.value) for n in ast.parse(TEMPLATE.read_text()).body if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "REMOTE" for t in n.targets))


def bundle():
    result = {"base_sources": {"community/appforge/tools.py": hashlib.sha256(b"audit-source").hexdigest()}, "modules": {}}
    for name in ("config.datalake_tools", "runtime.datalake_policy", "community.appforge.data_qa_stream", "community.appforge.tools"):
        raw = (ROOT / ("backend/packages/harness/deerflow/" + name.replace(".", "/") + ".py")).read_text()
        key = "deerflow." + ("community.appforge._candidate_tools" if name == "community.appforge.tools" else name)
        result["modules"][key] = {"source": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    return result


def run_remote(events, *, run=True, mutate=None):
    payload = {"bundle": bundle(), "run": run, "key": "synthetic-secret-only", "operation": "offline-isolation-test"}
    if mutate:
        mutate(payload)
    body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    prefix = """import pathlib,httpx,json
import deerflow.community.appforge
original=pathlib.Path.read_bytes
pathlib.Path.read_bytes=lambda p: b'audit-source' if str(p)=='/app/backend/packages/harness/deerflow/community/appforge/tools.py' else original(p)
RealClient=httpx.Client
def mock(request):
 assert request.method=='POST'
 assert str(request.url)=='http://10.77.124.142:9080/appforge/openapi/v1/InvokeApp/app-v16p2syh'
 assert request.headers['Authorization']=='Bearer synthetic-secret-only'
 body=json.loads(request.content)
 assert body['id']=='app-v16p2syh' and body['inputs']=={'hytoken':'','hyaccount':''}
 assert body['query']=='查询作业井基本信息表中设计井深最大的2口井'
 assert body['conversationId'].startswith('SquareWorkflow_')
 return httpx.Response(200,headers={'Content-Type':'text/event-stream'},content=BODY.encode())
def client(**kw):
 assert kw['trust_env'] is False
 return RealClient(transport=httpx.MockTransport(mock),**kw)
httpx.Client=client
""".replace("BODY", repr(body))
    result = subprocess.run([sys.executable, "-B", "-c", prefix + "\n" + REMOTE], input=json.dumps(payload).encode(), capture_output=True, timeout=35)
    assert result.returncode == 0, result.stderr.decode()
    assert b"synthetic-secret-only" not in result.stdout + result.stderr
    assert b"private-well-value" not in result.stdout + result.stderr
    return json.loads(result.stdout)


def test_candidate_preflight_never_calls_provider():
    result = run_remote([], run=False)
    assert result["status"] == "PREFLIGHT_PASS"
    assert result["provider_requests_attempted"] == 0


def test_real_candidate_calls_new_api_once_and_redacts_business_values():
    result = run_remote([{"type": "answer", "content": "private-well-value"}, {"type": "done"}])
    assert result["provider_requests_attempted"] == 1
    assert result["result_status"] == "done"
    assert result["answer_present"] is True
    assert result["protocol_sample"][0]["content"] == "<redacted-str>"
    assert result["business_values_verified"] is False


@pytest.mark.parametrize(
    "events,code",
    [
        ([{"type": "answer", "content": "private-well-value"}], "incomplete_workflow"),
        ([{"type": "error", "message": "private-well-value"}], "upstream_failed"),
    ],
)
def test_bad_response_is_observed_without_false_acceptance(events, code):
    result = run_remote(events)
    assert result["error_code"] == code
    assert not result["answer_present"]


def test_source_mismatch_prevents_provider_request():
    result = run_remote([], mutate=lambda p: p["bundle"]["base_sources"].update({"community/appforge/tools.py": "0" * 64}))
    assert result["status"] == "INCOMPLETE"
    assert result["provider_requests_attempted"] == 0


def test_bundle_mismatch_prevents_provider_request():
    result = run_remote([], mutate=lambda p: p["bundle"]["modules"]["deerflow.config.datalake_tools"].update(sha256="0" * 64))
    assert result["status"] == "INCOMPLETE"
    assert result["provider_requests_attempted"] == 0
