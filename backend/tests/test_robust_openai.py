from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessageChunk

from deerflow.models.robust_openai import (
    OpenAICompatibleResponseError,
    RobustChatOpenAI,
    _StreamFrameGuard,
)


def _model(*, cumulative_stream_usage: bool = False) -> RobustChatOpenAI:
    return RobustChatOpenAI(
        model="test-model",
        api_key="test-key",
        cumulative_stream_usage=cumulative_stream_usage,
    )


def _stream_chunk(
    *,
    completion_id: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    content: str = "",
    finish_reason: str | None = None,
) -> dict:
    chunk = {
        "model": "DeepSeek-V4-Flash",
        "choices": [
            {
                "delta": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens},
        },
    }
    if completion_id is not None:
        chunk["id"] = completion_id
    return chunk


def _convert_stream_chunk(model: RobustChatOpenAI, chunk: dict):
    return model._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {})


def _assert_usage(message, *, input_tokens: int, output_tokens: int, total_tokens: int, cache_read: int | None = None) -> None:
    assert message.usage_metadata is not None
    assert message.usage_metadata["input_tokens"] == input_tokens
    assert message.usage_metadata["output_tokens"] == output_tokens
    assert message.usage_metadata["total_tokens"] == total_tokens
    if cache_read is not None:
        assert message.usage_metadata["input_token_details"]["cache_read"] == cache_read


def test_streaming_remains_enabled_for_enterprise_tool_calls() -> None:
    assert _model().disable_streaming is False


def test_cumulative_stream_usage_prevents_1024x_cache_inflation() -> None:
    model = _model(cumulative_stream_usage=True)
    chunks = [
        _convert_stream_chunk(
            model,
            _stream_chunk(
                completion_id="chatcmpl-production-regression",
                prompt_tokens=36_144,
                completion_tokens=0,
                cached_tokens=36_144,
            ),
        )
        for _ in range(1024)
    ]
    terminal = _convert_stream_chunk(
        model,
        {
            "id": "chatcmpl-production-regression",
            "model": "DeepSeek-V4-Flash",
            "choices": [],
            "usage": {
                "prompt_tokens": 36_144,
                "completion_tokens": 0,
                "total_tokens": 36_144,
                "prompt_tokens_details": {"cached_tokens": 36_144},
            },
        },
    )

    assert all(chunk is not None for chunk in chunks)
    assert terminal is not None
    combined = chunks[0]
    for chunk in chunks[1:]:
        combined += chunk
    combined += terminal

    _assert_usage(
        combined.message,
        input_tokens=36_144,
        output_tokens=0,
        total_tokens=36_144,
        cache_read=36_144,
    )
    assert not model._cumulative_usage_by_completion


def test_cumulative_stream_usage_converts_snapshots_to_deltas() -> None:
    model = _model(cumulative_stream_usage=True)

    first = _convert_stream_chunk(
        model,
        _stream_chunk(
            completion_id="chatcmpl-1",
            prompt_tokens=100,
            completion_tokens=1,
            cached_tokens=80,
            content="A",
        ),
    )
    second = _convert_stream_chunk(
        model,
        _stream_chunk(
            completion_id="chatcmpl-1",
            prompt_tokens=100,
            completion_tokens=3,
            cached_tokens=80,
            content="B",
            finish_reason="stop",
        ),
    )
    terminal = _convert_stream_chunk(
        model,
        {
            "id": "chatcmpl-1",
            "model": "DeepSeek-V4-Flash",
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 3,
                "total_tokens": 103,
                "prompt_tokens_details": {"cached_tokens": 80},
            },
        },
    )

    assert first is not None
    _assert_usage(first.message, input_tokens=100, output_tokens=1, total_tokens=101, cache_read=80)
    assert second is not None
    _assert_usage(second.message, input_tokens=0, output_tokens=2, total_tokens=2, cache_read=0)
    assert terminal is not None
    _assert_usage(terminal.message, input_tokens=0, output_tokens=0, total_tokens=0, cache_read=0)
    combined = first + second + terminal
    _assert_usage(combined.message, input_tokens=100, output_tokens=3, total_tokens=103, cache_read=80)
    assert combined.message.content == "AB"
    assert not model._cumulative_usage_by_completion


def test_cumulative_stream_usage_leaves_terminal_only_usage_unchanged() -> None:
    model = _model(cumulative_stream_usage=True)

    terminal = _convert_stream_chunk(
        model,
        {
            "id": "chatcmpl-terminal",
            "model": "DeepSeek-V4-Flash",
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 4,
                "total_tokens": 104,
                "prompt_tokens_details": {"cached_tokens": 80},
            },
        },
    )

    assert terminal is not None
    _assert_usage(terminal.message, input_tokens=100, output_tokens=4, total_tokens=104, cache_read=80)
    assert not model._cumulative_usage_by_completion


def test_cumulative_stream_usage_clears_snapshot_on_terminal_without_usage() -> None:
    model = _model(cumulative_stream_usage=True)
    _convert_stream_chunk(
        model,
        _stream_chunk(
            completion_id="chatcmpl-no-terminal-usage",
            prompt_tokens=100,
            completion_tokens=1,
        ),
    )

    terminal = _convert_stream_chunk(
        model,
        {
            "id": "chatcmpl-no-terminal-usage",
            "model": "DeepSeek-V4-Flash",
            "choices": [],
        },
    )

    assert terminal is not None
    assert terminal.message.usage_metadata is None
    assert not model._cumulative_usage_by_completion


def test_cumulative_stream_usage_tracks_interleaved_completion_ids() -> None:
    model = _model(cumulative_stream_usage=True)

    stream_a_first = _convert_stream_chunk(
        model,
        _stream_chunk(completion_id="chatcmpl-a", prompt_tokens=10, completion_tokens=1),
    )
    stream_b_first = _convert_stream_chunk(
        model,
        _stream_chunk(completion_id="chatcmpl-b", prompt_tokens=20, completion_tokens=2),
    )
    stream_a_second = _convert_stream_chunk(
        model,
        _stream_chunk(completion_id="chatcmpl-a", prompt_tokens=10, completion_tokens=5),
    )

    assert stream_a_first is not None
    _assert_usage(stream_a_first.message, input_tokens=10, output_tokens=1, total_tokens=11)
    assert stream_b_first is not None
    _assert_usage(stream_b_first.message, input_tokens=20, output_tokens=2, total_tokens=22)
    assert stream_a_second is not None
    _assert_usage(stream_a_second.message, input_tokens=0, output_tokens=4, total_tokens=4)


def test_cumulative_stream_usage_does_not_advance_for_discarded_null_delta() -> None:
    model = _model(cumulative_stream_usage=True)
    first = _convert_stream_chunk(
        model,
        _stream_chunk(completion_id="chatcmpl-null-delta", prompt_tokens=10, completion_tokens=1),
    )
    discarded = _convert_stream_chunk(
        model,
        {
            "id": "chatcmpl-null-delta",
            "model": "DeepSeek-V4-Flash",
            "choices": [{"delta": None, "finish_reason": None}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        },
    )
    next_chunk = _convert_stream_chunk(
        model,
        _stream_chunk(completion_id="chatcmpl-null-delta", prompt_tokens=10, completion_tokens=3),
    )

    assert first is not None
    assert discarded is None
    assert next_chunk is not None
    _assert_usage(next_chunk.message, input_tokens=0, output_tokens=2, total_tokens=2)


def test_cumulative_stream_usage_does_not_evict_active_streams_at_soft_capacity(monkeypatch) -> None:
    monkeypatch.setattr("deerflow.models.robust_openai._CUMULATIVE_USAGE_TRACKER_CAPACITY", 2)
    monkeypatch.setattr("deerflow.models.robust_openai.time.monotonic", lambda: 0.0)
    model = _model(cumulative_stream_usage=True)

    for index in range(3):
        _convert_stream_chunk(
            model,
            _stream_chunk(completion_id=f"chatcmpl-{index}", prompt_tokens=10, completion_tokens=1),
        )

    assert list(model._cumulative_usage_by_completion) == ["chatcmpl-0", "chatcmpl-1", "chatcmpl-2"]


def test_cumulative_stream_usage_evicts_only_idle_streams_above_soft_capacity(monkeypatch) -> None:
    monkeypatch.setattr("deerflow.models.robust_openai._CUMULATIVE_USAGE_TRACKER_CAPACITY", 2)
    monkeypatch.setattr("deerflow.models.robust_openai._CUMULATIVE_USAGE_TRACKER_IDLE_SECONDS", 10)
    now = [0.0]
    monkeypatch.setattr("deerflow.models.robust_openai.time.monotonic", lambda: now[0])
    model = _model(cumulative_stream_usage=True)

    for index in range(3):
        _convert_stream_chunk(
            model,
            _stream_chunk(completion_id=f"chatcmpl-{index}", prompt_tokens=10, completion_tokens=1),
        )

    now[0] = 11.0
    _convert_stream_chunk(
        model,
        _stream_chunk(completion_id="chatcmpl-3", prompt_tokens=10, completion_tokens=1),
    )

    assert list(model._cumulative_usage_by_completion) == ["chatcmpl-2", "chatcmpl-3"]


def test_cumulative_stream_usage_is_opt_in_and_requires_completion_id() -> None:
    default_model = _model()
    opted_in_model = _model(cumulative_stream_usage=True)

    _convert_stream_chunk(
        default_model,
        _stream_chunk(completion_id="chatcmpl-default", prompt_tokens=10, completion_tokens=1),
    )
    default_second = _convert_stream_chunk(
        default_model,
        _stream_chunk(completion_id="chatcmpl-default", prompt_tokens=10, completion_tokens=3),
    )
    _convert_stream_chunk(
        opted_in_model,
        _stream_chunk(completion_id=None, prompt_tokens=10, completion_tokens=1),
    )
    no_id_second = _convert_stream_chunk(
        opted_in_model,
        _stream_chunk(completion_id=None, prompt_tokens=10, completion_tokens=3),
    )

    assert default_second is not None
    _assert_usage(default_second.message, input_tokens=10, output_tokens=3, total_tokens=13)
    assert no_id_second is not None
    _assert_usage(no_id_second.message, input_tokens=10, output_tokens=3, total_tokens=13)
    assert not default_model._cumulative_usage_by_completion
    assert not opted_in_model._cumulative_usage_by_completion


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
