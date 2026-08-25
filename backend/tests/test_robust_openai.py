from __future__ import annotations

import json

import pytest

from deerflow.models.robust_openai import (
    OpenAICompatibleResponseError,
    RobustChatOpenAI,
    _StreamFrameGuard,
)


def _model() -> RobustChatOpenAI:
    return RobustChatOpenAI(model="test-model", api_key="test-key")


def test_streaming_remains_enabled_for_enterprise_tool_calls() -> None:
    assert _model().disable_streaming is False


def test_json_encoded_stream_chunk_is_normalized() -> None:
    chunk = """{
        "id": "chunk-1",
        "choices": [{
            "index": 0,
            "delta": {
                "role": "assistant",
                "content": "OK"
            },
            "finish_reason": null
        }],
        "created": 1,
        "model": "DeepSeek-V4-Flash",
        "object": "chat.completion.chunk"
    }"""

    normalized = _model()._normalize_stream_chunk(chunk)

    assert normalized["choices"][0]["delta"]["content"] == "OK"


def test_nested_json_encoded_stream_chunk_is_normalized() -> None:
    chunk = {
        "id": "chunk-nested",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "nested"},
                "finish_reason": None,
            }
        ],
    }

    normalized = _model()._normalize_stream_chunk(json.dumps(json.dumps(chunk)))

    assert normalized["choices"][0]["delta"]["content"] == "nested"


def test_data_prefixed_json_stream_chunk_is_normalized() -> None:
    chunk = {
        "id": "chunk-data",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "data"},
                "finish_reason": None,
            }
        ],
    }

    normalized = _model()._normalize_stream_chunk(f"data: {json.dumps(chunk)}")

    assert normalized["choices"][0]["delta"]["content"] == "data"


def test_stream_business_error_becomes_typed_error() -> None:
    chunk = """{
        "choices": null,
        "code": 20009,
        "msg": "safety extraction failed",
        "success": false
    }"""

    with pytest.raises(OpenAICompatibleResponseError) as exc_info:
        _model()._normalize_stream_chunk(chunk)

    assert exc_info.value.code == "20009"
    assert exc_info.value.provider_message == "safety extraction failed"


def test_malformed_stream_scalar_becomes_typed_error() -> None:
    with pytest.raises(OpenAICompatibleResponseError) as exc_info:
        _model()._normalize_stream_chunk("not-json")

    assert exc_info.value.code == "MALFORMED_RESPONSE"
    assert "type=str" in exc_info.value.diagnostic
    assert "sha256=" in exc_info.value.diagnostic
    assert "not-json" not in str(exc_info.value)


def test_stream_frame_guard_accepts_quarantined_noise_after_terminal_chunk() -> None:
    guard = _StreamFrameGuard(_model()._normalize_stream_chunk)

    assert guard.normalize("not-json") is None
    terminal = guard.normalize(
        {
            "id": "chunk-terminal",
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": []},
                    "finish_reason": "tool_calls",
                }
            ],
        }
    )
    guard.raise_if_incomplete()

    assert terminal is not None
    assert terminal["choices"][0]["finish_reason"] == "tool_calls"


def test_stream_frame_guard_rejects_scalar_only_truncated_stream() -> None:
    guard = _StreamFrameGuard(_model()._normalize_stream_chunk)

    assert guard.normalize("not-json") is None

    with pytest.raises(OpenAICompatibleResponseError) as exc_info:
        guard.raise_if_incomplete()

    assert exc_info.value.code == "MALFORMED_RESPONSE"


def test_stream_frame_guard_never_quarantines_business_error() -> None:
    guard = _StreamFrameGuard(_model()._normalize_stream_chunk)

    with pytest.raises(OpenAICompatibleResponseError) as exc_info:
        guard.normalize(
            {
                "choices": None,
                "code": 20009,
                "msg": "safety extraction failed",
                "success": False,
            }
        )

    assert exc_info.value.code == "20009"


def test_null_choices_business_envelope_preserves_code_and_message() -> None:
    response = {
        "id": "request-1",
        "choices": None,
        "created": 1,
        "model": "DeepSeek-V4-Flash",
        "object": "chat.completion",
        "usage": None,
        "code": "MODEL_BUSY",
        "msg": "The inference service is temporarily overloaded.",
        "success": False,
    }

    with pytest.raises(OpenAICompatibleResponseError) as exc_info:
        _model()._create_chat_result(response)

    error = exc_info.value
    assert error.code == "MODEL_BUSY"
    assert error.provider_message == "The inference service is temporarily overloaded."
    assert error.success is False
    assert "MODEL_BUSY" in str(error)
    assert "temporarily overloaded" in str(error)


def test_null_choices_business_envelope_bounds_provider_message() -> None:
    response = {
        "choices": None,
        "code": "UPSTREAM_ERROR",
        "msg": "x" * 5000,
        "success": False,
    }

    with pytest.raises(OpenAICompatibleResponseError) as exc_info:
        _model()._create_chat_result(response)

    assert len(exc_info.value.provider_message) <= 1000
    assert len(str(exc_info.value)) < 1200


def test_valid_openai_response_still_uses_standard_chat_result() -> None:
    response = {
        "id": "request-2",
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "logprobs": None,
                "message": {
                    "content": "OK",
                    "role": "assistant",
                },
            }
        ],
        "created": 1,
        "model": "DeepSeek-V4-Flash",
        "object": "chat.completion",
        "usage": {
            "completion_tokens": 1,
            "prompt_tokens": 1,
            "total_tokens": 2,
        },
    }

    result = _model()._create_chat_result(response)

    assert result.generations[0].message.content == "OK"
    assert result.llm_output is not None
    assert result.llm_output["model_name"] == "DeepSeek-V4-Flash"


def test_json_encoded_valid_response_is_accepted() -> None:
    response = """{
        "id": "request-3",
        "choices": [{
            "finish_reason": "stop",
            "index": 0,
            "logprobs": null,
            "message": {"content": "JSON OK", "role": "assistant"}
        }],
        "created": 1,
        "model": "DeepSeek-V4-Flash",
        "object": "chat.completion",
        "usage": null
    }"""

    result = _model()._create_chat_result(response)

    assert result.generations[0].message.content == "JSON OK"


@pytest.mark.parametrize(
    "response",
    [
        "provider temporarily unavailable",
        '"provider temporarily unavailable"',
        "[]",
        b"not-json",
    ],
)
def test_malformed_scalar_response_becomes_typed_retriable_error(response: object) -> None:
    with pytest.raises(OpenAICompatibleResponseError) as exc_info:
        _model()._create_chat_result(response)

    error = exc_info.value
    assert error.code == "MALFORMED_RESPONSE"
    assert error.success is False
    assert "malformed response" in str(error)
    assert "provider temporarily unavailable" not in str(error)
