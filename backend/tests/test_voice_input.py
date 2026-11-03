from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import voice_input
from app.gateway.routers.voice_input import (
    build_upstream_stream_url,
    is_allowed_websocket_origin,
    sanitize_upstream_event,
)


def test_build_upstream_stream_url_uses_attachment_protocol_and_language() -> None:
    assert build_upstream_stream_url(
        "ws://model.ai.cnooc/ws/member1/qwen3-asr-v3/stream-transcriptions",
        "zh",
    ) == ("ws://model.ai.cnooc/ws/member1/qwen3-asr-v3/stream-transcriptions?language=chinese")


@pytest.mark.parametrize("language", ["zh", "zh-CN", "zh_Hans", "chinese"])
def test_build_upstream_stream_url_normalizes_chinese_request_language(
    language: str,
) -> None:
    assert build_upstream_stream_url(
        "ws://model.ai.cnooc/ws/member1/qwen3-asr-v3/stream-transcriptions",
        language,
    ).endswith("?language=chinese")


@pytest.mark.parametrize("language", ["", "auto", "invalid-language"])
def test_build_upstream_stream_url_falls_back_to_auto_detection(
    language: str,
) -> None:
    assert (
        build_upstream_stream_url(
            "ws://model.ai.cnooc/ws/member1/qwen3-asr-v3/stream-transcriptions?language=english",
            language,
        )
        == "ws://model.ai.cnooc/ws/member1/qwen3-asr-v3/stream-transcriptions"
    )


@pytest.mark.parametrize(
    ("origin", "forwarded_proto", "forwarded_host", "allowed"),
    [
        ("http://10.77.79.131:32026", "http", "10.77.79.131:32026", True),
        ("https://deerflow.example", "https", "deerflow.example", True),
        ("https://evil.example", "https", "deerflow.example", False),
        ("null", "https", "deerflow.example", False),
        (None, "https", "deerflow.example", False),
    ],
)
def test_websocket_origin_must_match_the_public_gateway_origin(
    origin: str | None,
    forwarded_proto: str,
    forwarded_host: str,
    allowed: bool,
) -> None:
    assert (
        is_allowed_websocket_origin(
            origin=origin,
            forwarded_proto=forwarded_proto,
            forwarded_host=forwarded_host,
            configured_origins=set(),
        )
        is allowed
    )


def test_websocket_origin_accepts_explicit_configured_origin() -> None:
    assert is_allowed_websocket_origin(
        origin="https://desktop.example",
        forwarded_proto="https",
        forwarded_host="gateway.internal",
        configured_origins={"https://desktop.example"},
    )


def test_sanitize_partial_and_final_events_expose_only_browser_contract() -> None:
    partial = sanitize_upstream_event(
        json.dumps(
            {
                "type": "partial",
                "text": "测试",
                "full_text": "这是测试",
                "language": "zh",
                "is_final": False,
                "count": 2,
                "client": "10.0.0.1",
                "timestamp": "secret-ish-metadata",
            }
        )
    )
    final = sanitize_upstream_event(
        json.dumps(
            {
                "type": "final",
                "text": "完成",
                "full_text": "这是测试完成",
                "language": "zh",
                "is_final": True,
                "total_partial_count": 3,
                "total_received_bytes": 32000,
            }
        )
    )

    assert partial == {
        "type": "partial",
        "text": "测试",
        "full_text": "这是测试",
        "language": "zh",
        "is_final": False,
    }
    assert final == {
        "type": "final",
        "text": "完成",
        "full_text": "这是测试完成",
        "language": "zh",
        "is_final": True,
    }


def test_sanitize_error_event_hides_upstream_internals() -> None:
    assert sanitize_upstream_event(
        json.dumps(
            {
                "type": "error",
                "success": False,
                "message": "raw upstream exception and token=never-return-this",
            }
        )
    ) == {
        "type": "error",
        "code": "upstream_error",
        "message": "语音转写服务暂时不可用，请稍后重试。",
    }


@pytest.mark.parametrize("payload", ["not-json", "[]", '{"type":"unknown"}'])
def test_sanitize_upstream_event_rejects_malformed_messages(payload: str) -> None:
    with pytest.raises(ValueError):
        sanitize_upstream_event(payload)


def test_stream_route_relays_pcm_and_never_returns_the_server_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = _FakeUpstream()
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")
    monkeypatch.delenv("DEER_FLOW_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("QWEN_ASR_API_KEY", "server-secret-must-stay-private")
    monkeypatch.setattr(voice_input, "connect", lambda *args, **kwargs: upstream)

    app = FastAPI()
    app.include_router(voice_input.router)
    with TestClient(app) as client:
        with client.websocket_connect("/api/voice-input/stream?language=zh") as ws:
            ready = ws.receive_json()
            ws.send_bytes(b"\x00\x01\x02\x03")
            ws.send_bytes(b"")
            final = ws.receive_json()

    assert ready == {
        "type": "ready",
        "model": "Qwen3-ASR-1.7B",
        "sample_rate": 16000,
    }
    assert final == {
        "type": "final",
        "text": "测试完成",
        "full_text": "测试完成",
        "language": "zh",
        "is_final": True,
    }
    assert upstream.sent == [b"\x00\x01\x02\x03", b""]
    assert "server-secret-must-stay-private" not in json.dumps([ready, final], ensure_ascii=False)


class _FakeUpstream:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._stop = asyncio.Event()

    async def __aenter__(self) -> _FakeUpstream:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def send(self, payload: bytes) -> None:
        self.sent.append(payload)
        if not payload:
            self._stop.set()

    async def recv(self) -> str:
        await self._stop.wait()
        return json.dumps(
            {
                "type": "final",
                "text": "测试完成",
                "full_text": "测试完成",
                "language": "zh",
                "is_final": True,
            }
        )
