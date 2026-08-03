from __future__ import annotations

import pytest

from deerflow.models.robust_openai import (
    OpenAICompatibleResponseError,
    RobustChatOpenAI,
)


def _model() -> RobustChatOpenAI:
    return RobustChatOpenAI(model="test-model", api_key="test-key")


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
