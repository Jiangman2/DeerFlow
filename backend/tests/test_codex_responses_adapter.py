from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway import codex_responses_adapter as adapter


def _responses_payload(*, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "model": "DeepSeek-V4-Flash",
        "instructions": "Follow the project instructions.",
        "input": [{"type": "message", "role": "user", "content": "Inspect the project."}],
        "tools": tools or [],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "max_output_tokens": 64000,
        "reasoning": {"effort": "high"},
        "stream": True,
        "store": False,
    }


def _event_payload(frame: str) -> dict[str, Any] | None:
    for line in frame.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return None


def test_convert_responses_request_preserves_tool_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PPT_MASTER_DEEPSEEK_MODEL", "DeepSeek-V4-Flash")
    payload = _responses_payload(
        tools=[
            {
                "type": "function",
                "name": "shell",
                "description": "Run a command.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            }
        ]
    )
    payload["input"].extend(
        [
            {
                "type": "function_call",
                "name": "shell",
                "call_id": "call_1",
                "arguments": '{"command":"pwd"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "Exit code: 0\n/mnt/user-data/workspace",
            },
        ]
    )

    converted = adapter.convert_responses_request(payload)

    assert converted.payload["stream"] is True
    assert converted.payload["max_tokens"] == 64000
    assert converted.payload["enable_thinking"] is True
    assert converted.payload["messages"] == [
        {"role": "system", "content": "Follow the project instructions."},
        {"role": "user", "content": "Inspect the project."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": '{"command":"pwd"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "Exit code: 0\n/mnt/user-data/workspace",
        },
    ]


def test_custom_tool_is_lowered_to_chat_function_and_restored() -> None:
    payload = _responses_payload(
        tools=[
            {
                "type": "custom",
                "name": "apply_patch",
                "description": "Apply a patch.",
                "format": {"type": "grammar", "syntax": "lark", "definition": "start: /.+/"},
            }
        ]
    )

    converted = adapter.convert_responses_request(payload)
    completion = adapter.DeepSeekCompletion(model="DeepSeek-V4-Flash")
    completion.consume(
        {
            "model": "DeepSeek-V4-Flash",
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_patch",
                                "function": {
                                    "name": "apply_patch",
                                    "arguments": '{"input":"*** Begin Patch\\n*** End Patch"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
    )

    output = adapter._response_output(completion, converted.tool_targets)

    assert converted.tool_kinds == {"apply_patch": "custom"}
    assert converted.payload["tools"][0]["function"]["parameters"]["required"] == ["input"]
    assert output[0]["type"] == "custom_tool_call"
    assert output[0]["name"] == "apply_patch"
    assert output[0]["input"] == "*** Begin Patch\n*** End Patch"


def test_namespace_tools_are_flattened_and_restored_without_losing_history() -> None:
    payload = _responses_payload(
        tools=[
            {
                "type": "namespace",
                "name": "multi_agent_v1",
                "description": "Tools for managing sub-agents.",
                "tools": [
                    {
                        "type": "function",
                        "name": "spawn_agent",
                        "description": "Spawn a sub-agent.",
                        "parameters": {
                            "type": "object",
                            "properties": {"message": {"type": "string"}},
                            "required": ["message"],
                        },
                    }
                ],
            }
        ]
    )
    payload["input"].extend(
        [
            {
                "type": "function_call",
                "namespace": "multi_agent_v1",
                "name": "spawn_agent",
                "call_id": "call_spawn_1",
                "arguments": '{"message":"inspect"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_spawn_1",
                "output": '{"agent_id":"agent-1"}',
            },
        ]
    )

    converted = adapter.convert_responses_request(payload)
    upstream_name = "multi_agent_v1__spawn_agent"

    assert converted.payload["tools"][0]["function"]["name"] == upstream_name
    assert converted.payload["messages"][2]["tool_calls"][0]["function"]["name"] == upstream_name
    assert converted.tool_targets[upstream_name] == adapter.ResponseToolTarget(
        kind="function",
        name="spawn_agent",
        namespace="multi_agent_v1",
    )

    completion = adapter.DeepSeekCompletion(model="DeepSeek-V4-Flash")
    completion.consume(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_spawn_2",
                                "function": {
                                    "name": upstream_name,
                                    "arguments": '{"message":"build"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )

    output = adapter._response_output(completion, converted.tool_targets)

    assert output[0]["type"] == "function_call"
    assert output[0]["name"] == "spawn_agent"
    assert output[0]["namespace"] == "multi_agent_v1"
    assert output[0]["arguments"] == '{"message":"build"}'


def test_namespace_custom_tool_is_flattened_and_restored() -> None:
    payload = _responses_payload(
        tools=[
            {
                "type": "namespace",
                "name": "editing",
                "description": "Editing tools.",
                "tools": [
                    {
                        "type": "custom",
                        "name": "apply_patch",
                        "description": "Apply a patch.",
                        "format": {"type": "grammar", "syntax": "lark", "definition": "start: /.+/"},
                    }
                ],
            }
        ]
    )
    converted = adapter.convert_responses_request(payload)
    upstream_name = "editing__apply_patch"
    completion = adapter.DeepSeekCompletion(model="DeepSeek-V4-Flash")
    completion.consume(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_patch",
                                "function": {
                                    "name": upstream_name,
                                    "arguments": '{"input":"*** Begin Patch"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )

    output = adapter._response_output(completion, converted.tool_targets)

    assert output[0]["type"] == "custom_tool_call"
    assert output[0]["name"] == "apply_patch"
    assert output[0]["namespace"] == "editing"
    assert output[0]["input"] == "*** Begin Patch"


def test_provider_hosted_web_search_is_not_forwarded_to_chat_completions() -> None:
    payload = _responses_payload(
        tools=[
            {
                "type": "web_search",
                "external_web_access": False,
            },
            {
                "type": "function",
                "name": "shell",
                "description": "Run a local command.",
                "parameters": {"type": "object", "properties": {}},
            },
        ]
    )

    converted = adapter.convert_responses_request(payload)

    assert [tool["function"]["name"] for tool in converted.payload["tools"]] == ["shell"]
    assert converted.ignored_hosted_tool_types == ("web_search",)
    assert converted.payload["tool_choice"] == "auto"


def test_legacy_web_search_preview_is_not_forwarded_to_chat_completions() -> None:
    payload = _responses_payload(tools=[{"type": "web_search_preview"}])

    converted = adapter.convert_responses_request(payload)

    assert "tools" not in converted.payload
    assert "tool_choice" not in converted.payload
    assert converted.ignored_hosted_tool_types == ("web_search_preview",)


def test_explicit_provider_hosted_tool_choice_is_rejected() -> None:
    payload = _responses_payload(tools=[{"type": "web_search"}])
    payload["tool_choice"] = {"type": "web_search"}

    with pytest.raises(adapter.AdapterRequestError) as exc_info:
        adapter.convert_responses_request(payload)

    assert exc_info.value.code == "unsupported_hosted_tool_choice"
    assert exc_info.value.param == "tool_choice.type"


def test_unknown_responses_tool_type_remains_fail_closed() -> None:
    payload = _responses_payload(tools=[{"type": "future_unknown_tool"}])

    with pytest.raises(adapter.AdapterRequestError) as exc_info:
        adapter.convert_responses_request(payload)

    assert exc_info.value.code == "unsupported_tool_type"
    assert exc_info.value.param == "tools[0].type"


def test_request_rejects_non_streaming_and_unknown_item_types() -> None:
    payload = _responses_payload()
    payload["stream"] = False
    with pytest.raises(adapter.AdapterRequestError, match="stream=true"):
        adapter.convert_responses_request(payload)

    payload = _responses_payload()
    payload["input"] = [{"type": "computer_call", "id": "computer_1"}]
    with pytest.raises(adapter.AdapterRequestError, match="Unsupported Responses input item type"):
        adapter.convert_responses_request(payload)


def test_business_error_envelope_is_never_treated_as_completion() -> None:
    completion = adapter.DeepSeekCompletion(model="DeepSeek-V4-Flash")

    with pytest.raises(adapter.DeepSeekUpstreamError) as exc_info:
        completion.consume(
            {
                "choices": None,
                "code": "MODEL_BUSY",
                "msg": "The inference service is temporarily overloaded.",
                "success": False,
            }
        )

    assert exc_info.value.code == "MODEL_BUSY"
    assert exc_info.value.retryable is True


class _FakeResponse:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    lines: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, *args, **kwargs) -> _FakeResponse:
        return _FakeResponse(self.lines)


def test_stream_bridge_emits_completed_custom_tool_item(monkeypatch: pytest.MonkeyPatch) -> None:
    converted = adapter.convert_responses_request(
        _responses_payload(
            tools=[
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "description": "Apply a patch.",
                }
            ]
        )
    )
    _FakeAsyncClient.lines = [
        'data: {"model":"DeepSeek-V4-Flash","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_patch","function":{"name":"apply_patch","arguments":"{\\"input\\":\\"abc"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"def\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(adapter.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(adapter, "_upstream_key", lambda: "test-upstream-key")

    async def collect_frames() -> list[str]:
        return [frame async for frame in adapter.stream_deepseek_responses(converted)]

    frames = asyncio.run(collect_frames())
    events = [event for frame in frames if (event := _event_payload(frame))]

    done = next(event for event in events if event["type"] == "response.output_item.done")
    completed = next(event for event in events if event["type"] == "response.completed")
    assert done["item"]["type"] == "custom_tool_call"
    assert done["item"]["input"] == "abcdef"
    assert completed["response"]["status"] == "completed"
    assert completed["response"]["usage"]["total_tokens"] == 14


def test_http_route_requires_scoped_relay_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PPT_MASTER_RESPONSES_RELAY_KEY", "relay-test-key")
    app = FastAPI()
    app.include_router(adapter.router)
    client = TestClient(app)

    response = client.post(
        "/api/codex/deepseek/v1/responses",
        json=_responses_payload(),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
