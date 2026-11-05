import base64
import io

import httpx
import pytest
from fastapi import HTTPException, UploadFile
from starlette.requests import Request

from app.gateway.routers import weknora


def _request(api_key: str | None = None) -> Request:
    headers = []
    if api_key is not None:
        headers.append((b"authorization", f"Bearer {api_key}".encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/weknora/bailian/v1/audio/transcriptions",
            "headers": headers,
        }
    )


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, response: _Response, calls: list):
        self.response = response
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_bailian_asr_adapter_translates_openai_transcription_request(monkeypatch):
    calls = []
    monkeypatch.setenv("LLM_API_KEY", "shared-secret")
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(
            _Response(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": "欢迎使用语音识别。",
                                "annotations": [
                                    {
                                        "type": "audio_info",
                                        "language": "zh",
                                        "emotion": "neutral",
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"seconds": 1},
                },
            ),
            calls,
        ),
    )

    result = await weknora.bailian_audio_transcription(
        request=_request("shared-secret"),
        file=UploadFile(
            filename="sample.wav",
            file=io.BytesIO(b"RIFF-test-audio"),
            headers={"content-type": "audio/wav"},
        ),
        model="qwen3-asr-flash",
        language="zh",
        response_format="verbose_json",
    )

    assert result == {
        "text": "欢迎使用语音识别。",
        "language": "zh",
        "duration": 1,
        "segments": [],
    }
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer shared-secret"
    payload = kwargs["json"]
    assert payload["model"] == "qwen3-asr-flash"
    assert payload["stream"] is False
    assert payload["asr_options"] == {"enable_itn": True, "language": "zh"}
    data_uri = payload["messages"][0]["content"][0]["input_audio"]["data"]
    prefix, encoded = data_uri.split(",", 1)
    assert prefix == "data:audio/wav;base64"
    assert base64.b64decode(encoded) == b"RIFF-test-audio"


@pytest.mark.asyncio
async def test_bailian_asr_adapter_rejects_missing_or_wrong_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "shared-secret")
    upload = UploadFile(filename="sample.wav", file=io.BytesIO(b"audio"))

    with pytest.raises(HTTPException) as missing:
        await weknora.bailian_audio_transcription(
            request=_request(),
            file=upload,
            model="qwen3-asr-flash",
        )
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as wrong:
        await weknora.bailian_audio_transcription(
            request=_request("wrong-secret"),
            file=UploadFile(filename="sample.wav", file=io.BytesIO(b"audio")),
            model="qwen3-asr-flash",
        )
    assert wrong.value.status_code == 401


@pytest.mark.asyncio
async def test_bailian_asr_adapter_rejects_oversized_audio(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "shared-secret")
    monkeypatch.setenv("WEKNORA_BAILIAN_ASR_MAX_BYTES", "4")

    with pytest.raises(HTTPException) as oversized:
        await weknora.bailian_audio_transcription(
            request=_request("shared-secret"),
            file=UploadFile(filename="sample.wav", file=io.BytesIO(b"12345")),
            model="qwen3-asr-flash",
        )

    assert oversized.value.status_code == 413


@pytest.mark.asyncio
async def test_bailian_asr_adapter_maps_upstream_failures_to_bad_gateway(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "shared-secret")
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(
            _Response(429, {"error": {"message": "rate limit"}}),
            [],
        ),
    )

    with pytest.raises(HTTPException) as upstream_error:
        await weknora.bailian_audio_transcription(
            request=_request("shared-secret"),
            file=UploadFile(filename="sample.wav", file=io.BytesIO(b"audio")),
            model="qwen3-asr-flash",
        )

    assert upstream_error.value.status_code == 502
    assert "429" in str(upstream_error.value.detail)


@pytest.mark.asyncio
async def test_bailian_asr_adapter_maps_network_failures_to_bad_gateway(monkeypatch):
    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            raise httpx.ConnectError("offline")

    monkeypatch.setenv("LLM_API_KEY", "shared-secret")
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _FailingClient(),
    )

    with pytest.raises(HTTPException) as upstream_error:
        await weknora.bailian_audio_transcription(
            request=_request("shared-secret"),
            file=UploadFile(filename="sample.wav", file=io.BytesIO(b"audio")),
            model="qwen3-asr-flash",
        )

    assert upstream_error.value.status_code == 502
