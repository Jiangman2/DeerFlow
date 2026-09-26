"""A tool batch is publishable only after syntax and the supplied schema pass."""

import asyncio
import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway import codex_responses_adapter as adapter


def request_payload():
    return {
        "model": "DeepSeek-V4-Flash",
        "stream": True,
        "input": [{"role": "user", "content": "Compute 1+1 in the existing workspace."}],
        "tools": [
            {
                "type": "namespace",
                "name": "functions",
                "tools": [
                    {
                        "type": "function",
                        "name": "exec_command",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "cmd": {"type": "string"},
                                "yield_time_ms": {"type": "integer", "minimum": 0},
                            },
                            "required": ["cmd"],
                            "additionalProperties": False,
                        },
                    }
                ],
            }
        ],
    }


def tool(arguments, call_id="call_good", name="functions__exec_command", index=0):
    return {"index": index, "id": call_id, "function": {"name": name, "arguments": arguments}}


def response_lines(calls, *, finish="tool_calls", usage=None):
    return [
        "data: "
        + json.dumps(
            {
                "choices": [{"delta": {"tool_calls": calls}, "finish_reason": finish}],
                "usage": usage or {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }
        ),
        "data: [DONE]",
    ]


def events(frames):
    return [json.loads(line[6:]) for frame in frames for line in frame.splitlines() if line.startswith("data: ")]


def setup_upstream(monkeypatch, responses):
    sent = []

    class Response:
        status_code = 200

        def __init__(self, lines):
            self.lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_lines(self):
            for line in self.lines:
                if isinstance(line, Exception):
                    raise line
                yield line

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            sent.append(copy.deepcopy(kwargs["json"]))
            return Response(responses[min(len(sent) - 1, len(responses) - 1)])

    async def no_sleep(_):
        pass

    monkeypatch.setattr(adapter.httpx, "AsyncClient", Client)
    monkeypatch.setattr(adapter.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_upstream_key", lambda: "UPSTREAM_SECRET_NOT_FOR_LOGS")
    monkeypatch.setenv("PPT_MASTER_DEEPSEEK_ATTEMPTS", "2")
    return sent


def collect(converted):
    async def run():
        return [frame async for frame in adapter.stream_deepseek_responses(converted)]

    return events(asyncio.run(run()))


@pytest.mark.parametrize(
    "bad",
    [
        "{}",
        '{"command":"SECRET_CMD"}',
        '{"cmd":42}',
        '{"cmd":"SECRET_CMD","yield_time_ms":true}',
        '{"cmd":"SECRET_CMD","yield_time_ms":-1}',
        '{"cmd":"SECRET_CMD",',
        '{"cmd":"A","cmd":"B"}',
        '{"cmd":"A","yield_time_ms":NaN}',
        "[]",
    ],
)
def test_repair_before_dispatch_does_not_guess_or_leak_arguments(monkeypatch, caplog, bad):
    sent = setup_upstream(
        monkeypatch,
        [
            response_lines([tool(bad, "call_rejected")]),
            response_lines([tool('{"cmd":"printf 2"}')]),
        ],
    )
    converted = adapter.convert_responses_request(request_payload())
    before = copy.deepcopy(converted.payload)
    result = collect(converted)
    assert len(sent) == 2
    assert result[-1]["type"] == "response.completed"
    items = [event["item"] for event in result if event["type"] == "response.output_item.done"]
    assert len(items) == 1
    assert items[0]["call_id"] == "call_good"
    assert items[0]["namespace"] == "functions"
    assert items[0]["name"] == "exec_command"
    assert json.loads(items[0]["arguments"]) == {"cmd": "printf 2"}
    assert "call_rejected" not in json.dumps(result)
    assert "SECRET_CMD" not in json.dumps(sent[1]) + caplog.text
    assert "No tool" in sent[1]["messages"][-1]["content"]
    assert sent[0]["tools"] == sent[1]["tools"]
    assert converted.payload == before
    assert result[-1]["response"]["usage"]["total_tokens"] == 28


def test_invalid_sibling_blocks_entire_batch_and_recovery_is_bounded(monkeypatch):
    sent = setup_upstream(
        monkeypatch,
        [
            response_lines(
                [
                    tool('{"cmd":"must-not-execute"}', "call_valid_sibling"),
                    tool("{}", "call_bad", index=1),
                ]
            )
        ],
    )
    result = collect(adapter.convert_responses_request(request_payload()))
    assert len(sent) == 3  # initial attempt plus at most two corrections
    assert [event["type"] for event in result] == ["response.created", "response.in_progress", "response.failed"]
    assert result[-1]["response"]["error"]["code"] == "invalid_tool_arguments"
    assert result[-1]["response"]["usage"]["total_tokens"] == 42
    assert "must-not-execute" not in json.dumps(result)


def test_success_is_never_retried_and_argument_events_are_complete(monkeypatch):
    sent = setup_upstream(monkeypatch, [response_lines([tool('{"cmd":"printf 2"}')])])
    result = collect(adapter.convert_responses_request(request_payload()))
    assert len(sent) == 1
    added = next(e for e in result if e["type"] == "response.output_item.added")
    done = next(e for e in result if e["type"] == "response.function_call_arguments.done")
    assert added["item"]["arguments"] == ""
    assert done["arguments"] == '{"cmd":"printf 2"}'
    assert [e["sequence_number"] for e in result] == list(range(len(result)))


def test_schema_references_remain_local_and_checked(monkeypatch):
    payload = request_payload()
    schema = payload["tools"][0]["tools"][0]["parameters"]
    schema["$defs"] = {"command": {"type": "string"}}
    schema["properties"]["cmd"] = {"$ref": "#/$defs/command"}
    setup_upstream(monkeypatch, [response_lines([tool('{"cmd":1}')])])
    assert collect(adapter.convert_responses_request(payload))[-1]["type"] == "response.failed"
    schema["properties"]["cmd"] = {"$ref": "https://invalid.example/never-fetch"}
    with pytest.raises(adapter.AdapterRequestError, match="schema"):
        adapter.convert_responses_request(payload)


def test_invalid_schema_rejected_without_upstream_request():
    payload = request_payload()
    payload["tools"][0]["tools"][0]["parameters"]["required"] = "cmd"
    with pytest.raises(adapter.AdapterRequestError, match="schema"):
        adapter.convert_responses_request(payload)


def test_reference_named_property_is_data_not_external_schema_reference():
    payload = request_payload()
    schema = payload["tools"][0]["tools"][0]["parameters"]
    schema["properties"]["$ref"] = {"type": "string", "default": "ordinary data"}
    schema["examples"] = [{"$ref": "https://example.invalid/not-a-schema"}]
    assert adapter.convert_responses_request(payload).tool_targets["functions__exec_command"].parameters == schema


def test_completed_fragments_keep_unicode_quotes_and_history(monkeypatch):
    original_cmd = "printf '%s' '中文 \\\"quoted\\\"'"
    arguments = json.dumps({"cmd": original_cmd}, ensure_ascii=False)
    first = tool(arguments[:9])
    second = {"index": 0, "function": {"arguments": arguments[9:]}}
    lines = response_lines([first], finish=None)[:1] + response_lines([second])
    sent = setup_upstream(monkeypatch, [lines])
    payload = request_payload()
    payload["input"].extend(
        [
            {"type": "function_call", "namespace": "functions", "name": "exec_command", "call_id": "call_previous", "arguments": '{"cmd":"previous command"}'},
            {"type": "function_call_output", "call_id": "call_previous", "output": "already executed"},
        ]
    )
    result = collect(adapter.convert_responses_request(payload))
    assert len(sent) == 1
    assert sent[0]["messages"][-1]["content"] == "already executed"
    item = next(e["item"] for e in result if e["type"] == "response.output_item.done")
    assert json.loads(item["arguments"])["cmd"] == original_cmd


def test_custom_tool_schema_repaired_but_raw_patch_is_never_rewritten(monkeypatch):
    payload = request_payload()
    payload["tools"] = [{"type": "custom", "name": "apply_patch"}]
    patch = "*** Begin Patch\n中文\n*** End Patch"
    sent = setup_upstream(
        monkeypatch,
        [
            response_lines([tool('{"cmd":"not a patch"}', name="apply_patch")]),
            response_lines([tool(json.dumps({"input": patch}), name="apply_patch")]),
        ],
    )
    result = collect(adapter.convert_responses_request(payload))
    assert len(sent) == 2
    item = next(e["item"] for e in result if e["type"] == "response.output_item.done")
    assert item["type"] == "custom_tool_call"
    assert item["input"] == patch


def test_unknown_tool_cannot_be_executed_or_silently_renamed(monkeypatch):
    sent = setup_upstream(monkeypatch, [response_lines([tool('{"cmd":"anything"}', name="invented")])])
    result = collect(adapter.convert_responses_request(request_payload()))
    assert len(sent) == 1
    assert result[-1]["response"]["error"]["code"] == "unknown_tool_call"
    assert not any(e["type"].startswith("response.output_item") for e in result)


@pytest.mark.parametrize("finish", ["length", "content_filter", None])
def test_incomplete_turn_is_never_published_or_repaired_as_tool_arguments(monkeypatch, finish):
    sent = setup_upstream(monkeypatch, [response_lines([tool('{"cmd":"printf 2"}')], finish=finish)])
    result = collect(adapter.convert_responses_request(request_payload()))
    assert result[-1]["type"] == "response.failed"
    assert not any(e["type"].startswith("response.output_item") for e in result)
    assert len(sent) <= 2


def test_authenticated_http_route_recovers_without_exposing_invalid_tool(monkeypatch):
    sent = setup_upstream(monkeypatch, [response_lines([tool("{}")]), response_lines([tool('{"cmd":"printf 2"}')])])
    monkeypatch.setenv("PPT_MASTER_RESPONSES_RELAY_KEY", "relay-test-key")
    app = FastAPI()
    app.include_router(adapter.router)
    with TestClient(app) as client:
        response = client.post("/api/codex/deepseek/v1/responses", json=request_payload(), headers={"Authorization": "Bearer relay-test-key"})
    result = events([response.text])
    assert response.status_code == 200
    assert len(sent) == 2
    assert result[-1]["type"] == "response.completed"
    assert response.headers["cache-control"] == "no-store"


def test_transport_retry_and_correction_share_bounded_budget_and_history(monkeypatch):
    sent = setup_upstream(
        monkeypatch,
        [
            response_lines([tool("{}")]),
            [adapter.httpx.ReadError("DO_NOT_LEAK_PROVIDER_DETAILS")],
            response_lines([tool('{"cmd":"printf 2"}')]),
        ],
    )
    result = collect(adapter.convert_responses_request(request_payload()))
    assert len(sent) == 3
    assert sent[1] == sent[2]
    assert "cmd" in sent[1]["messages"][-1]["content"]
    assert result[-1]["type"] == "response.completed"
    assert result[-1]["response"]["usage"]["total_tokens"] == 28


def test_cancellation_does_not_replay_the_model_request(monkeypatch):
    sent = setup_upstream(monkeypatch, [response_lines([tool('{"cmd":"printf 2"}')])])

    async def cancel_before_dispatch():
        stream = adapter.stream_deepseek_responses(adapter.convert_responses_request(request_payload()))
        await anext(stream)
        await stream.aclose()

    asyncio.run(cancel_before_dispatch())
    assert sent == []


def test_total_deadline_stops_hung_stream_without_retry(monkeypatch):
    class HangingClient:
        calls = 0
        status_code = 200

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            self.__class__.calls += 1
            return self

        async def aiter_lines(self):
            await asyncio.Event().wait()
            yield "never"

    monkeypatch.setattr(adapter.httpx, "AsyncClient", HangingClient)
    monkeypatch.setattr(adapter, "_upstream_key", lambda: "test-key")
    monkeypatch.setenv("PPT_MASTER_DEEPSEEK_TURN_TIMEOUT_SECONDS", "0.03")
    result = collect(adapter.convert_responses_request(request_payload()))
    assert HangingClient.calls == 1
    assert result[-1]["response"]["error"]["code"] == "upstream_deadline"
