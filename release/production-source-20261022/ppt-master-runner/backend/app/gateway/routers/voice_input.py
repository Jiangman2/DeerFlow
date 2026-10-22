"""Authenticated browser microphone streaming to the enterprise ASR service.

The browser sends raw 16 kHz mono PCM16 frames to this same-origin WebSocket.
Gateway owns the upstream credential and relays only the small, documented
transcript contract back to the browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.gateway.auth_disabled import get_auth_disabled_user, is_auth_disabled
from app.gateway.csrf_middleware import get_configured_cors_origins
from app.gateway.deps import get_current_user_from_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice-input", tags=["voice-input"])

_DEFAULT_UPSTREAM_URL = "ws://model.ai.cnooc/ws/member1/qwen3-asr-v3/stream-transcriptions"
# The enterprise ASR request contract uses language names (for example,
# ``chinese``), while transcript events return ISO-like short codes (for
# example, ``zh``).  The browser intentionally uses locale short codes, so the
# Gateway owns this protocol translation instead of exposing the upstream
# vocabulary to the UI.
_UPSTREAM_LANGUAGE_ALIASES = {
    "zh": "chinese",
    "zh-cn": "chinese",
    "zh-hans": "chinese",
    "zh-sg": "chinese",
    "zh-tw": "chinese",
    "zh-hant": "chinese",
    "zh-hk": "chinese",
    "en": "english",
    "en-us": "english",
    "en-gb": "english",
    "ja": "japanese",
    "ja-jp": "japanese",
    "ko": "korean",
    "ko-kr": "korean",
    "yue": "cantonese",
}
_UPSTREAM_LANGUAGE_NAMES = frozenset(
    {
        "chinese",
        "english",
        "japanese",
        "korean",
        "cantonese",
    }
)
_MAX_EVENT_TEXT = 100_000
_GENERIC_UPSTREAM_ERROR = "语音转写服务暂时不可用，请稍后重试。"


def _first_header_value(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(",", 1)[0].strip() or None


def _normalize_origin(value: str | None) -> str | None:
    if not value or value == "null":
        return None
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return None
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port is not None and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def is_allowed_websocket_origin(
    *,
    origin: str | None,
    forwarded_proto: str | None,
    forwarded_host: str | None,
    configured_origins: set[str],
) -> bool:
    """Require same-origin WebSocket use or an explicit CORS allowlist entry."""

    normalized_origin = _normalize_origin(origin)
    if normalized_origin is None:
        return False
    scheme = _first_header_value(forwarded_proto) or "http"
    host = _first_header_value(forwarded_host)
    request_origin = _normalize_origin(f"{scheme}://{host}") if host else None
    return normalized_origin == request_origin or normalized_origin in configured_origins


def build_upstream_stream_url(base_url: str, language: str) -> str:
    """Validate the configured ASR WebSocket and add the requested language."""

    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError("QWEN_ASR_STREAM_URL must be an absolute ws:// or wss:// URL")
    requested_language = language.strip().lower().replace("_", "-")
    normalized_language = _UPSTREAM_LANGUAGE_ALIASES.get(
        requested_language,
        requested_language,
    )
    if normalized_language not in _UPSTREAM_LANGUAGE_NAMES:
        normalized_language = "auto"
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if normalized_language != "auto":
        query["language"] = normalized_language
    else:
        query.pop("language", None)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _bounded_string(value: Any) -> str:
    return value[:_MAX_EVENT_TEXT] if isinstance(value, str) else ""


def sanitize_upstream_event(raw: str | bytes) -> dict[str, Any]:
    """Reduce an upstream event to the browser-visible transcript contract."""

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="strict")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Malformed ASR event") from exc
    if not isinstance(payload, dict):
        raise ValueError("ASR event must be a JSON object")

    event_type = payload.get("type")
    if event_type in {"partial", "final"}:
        return {
            "type": event_type,
            "text": _bounded_string(payload.get("text")),
            "full_text": _bounded_string(payload.get("full_text")),
            "language": _bounded_string(payload.get("language"))[:32],
            "is_final": event_type == "final",
        }
    if event_type == "error":
        return {
            "type": "error",
            "code": "upstream_error",
            "message": _GENERIC_UPSTREAM_ERROR,
        }
    raise ValueError("Unsupported ASR event type")


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


_VOICE_SESSION_LIMIT = _positive_int_env("QWEN_ASR_MAX_CONCURRENT_SESSIONS", 20)
_VOICE_SESSION_SEMAPHORE = asyncio.Semaphore(_VOICE_SESSION_LIMIT)


async def _authenticate_websocket(websocket: WebSocket) -> Any:
    if is_auth_disabled():
        return get_auth_disabled_user()
    try:
        return await get_current_user_from_request(websocket)  # type: ignore[arg-type]
    except HTTPException as exc:
        await websocket.close(code=4401, reason="Authentication required")
        raise WebSocketDisconnect(code=4401) from exc


async def _send_error(
    websocket: WebSocket,
    *,
    code: str,
    message: str,
    close_code: int = status.WS_1011_INTERNAL_ERROR,
) -> None:
    with suppress(RuntimeError, WebSocketDisconnect):
        await websocket.send_json({"type": "error", "code": code, "message": message})
    with suppress(RuntimeError):
        await websocket.close(code=close_code)


@router.websocket("/stream")
async def stream_voice_input(websocket: WebSocket) -> None:
    """Relay one authenticated microphone transcription session."""

    if not is_auth_disabled() and not is_allowed_websocket_origin(
        origin=websocket.headers.get("origin"),
        forwarded_proto=websocket.headers.get("x-forwarded-proto") or websocket.url.scheme.replace("ws", "http", 1),
        forwarded_host=websocket.headers.get("x-forwarded-host") or websocket.headers.get("host"),
        configured_origins=get_configured_cors_origins(),
    ):
        await websocket.close(code=4403, reason="Origin not allowed")
        return

    try:
        user = await _authenticate_websocket(websocket)
    except WebSocketDisconnect:
        return

    api_key = os.environ.get("QWEN_ASR_API_KEY", "").strip()
    if not api_key:
        await websocket.accept()
        await _send_error(
            websocket,
            code="service_not_configured",
            message="语音转写服务尚未配置，请联系平台管理员。",
        )
        return

    try:
        upstream_url = build_upstream_stream_url(
            os.environ.get("QWEN_ASR_STREAM_URL", _DEFAULT_UPSTREAM_URL),
            websocket.query_params.get("language", "auto"),
        )
    except ValueError:
        logger.exception("Invalid enterprise ASR stream URL configuration")
        await websocket.accept()
        await _send_error(
            websocket,
            code="service_not_configured",
            message="语音转写服务配置无效，请联系平台管理员。",
        )
        return

    max_bytes = _positive_int_env("QWEN_ASR_MAX_AUDIO_BYTES", 20 * 1024 * 1024)
    max_frame_bytes = _positive_int_env("QWEN_ASR_MAX_FRAME_BYTES", 256 * 1024)
    max_duration = _positive_float_env("QWEN_ASR_MAX_DURATION_SECONDS", 600.0)
    connect_timeout = _positive_float_env("QWEN_ASR_CONNECT_TIMEOUT_SECONDS", 15.0)
    final_timeout = _positive_float_env("QWEN_ASR_FINAL_TIMEOUT_SECONDS", 30.0)

    await websocket.accept()
    started_at = asyncio.get_running_loop().time()
    total_bytes = 0

    try:
        await asyncio.wait_for(_VOICE_SESSION_SEMAPHORE.acquire(), timeout=2.0)
    except TimeoutError:
        await _send_error(
            websocket,
            code="service_busy",
            message="当前语音转写并发已满，请稍后重试。",
            close_code=status.WS_1013_TRY_AGAIN_LATER,
        )
        return

    try:
        async with connect(
            upstream_url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            open_timeout=connect_timeout,
            close_timeout=5,
            ping_interval=None,
            ping_timeout=None,
            proxy=None,
            max_size=2 * 1024 * 1024,
        ) as upstream:
            await websocket.send_json(
                {
                    "type": "ready",
                    "model": os.environ.get("QWEN_ASR_MODEL_NAME", "Qwen3-ASR-1.7B"),
                    "sample_rate": 16000,
                }
            )

            async def forward_audio() -> bool:
                nonlocal total_bytes
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return False
                    chunk = message.get("bytes")
                    if chunk is None:
                        await _send_error(
                            websocket,
                            code="invalid_audio_frame",
                            message="录音数据格式无效，请重新开始语音输入。",
                            close_code=status.WS_1003_UNSUPPORTED_DATA,
                        )
                        return False
                    if not chunk:
                        await upstream.send(b"")
                        return True
                    if len(chunk) > max_frame_bytes:
                        await _send_error(
                            websocket,
                            code="audio_frame_too_large",
                            message="单个录音数据帧过大，请重新开始语音输入。",
                            close_code=status.WS_1009_MESSAGE_TOO_BIG,
                        )
                        return False
                    total_bytes += len(chunk)
                    elapsed = asyncio.get_running_loop().time() - started_at
                    if total_bytes > max_bytes or elapsed > max_duration:
                        await _send_error(
                            websocket,
                            code="recording_limit_exceeded",
                            message="单次语音输入时间过长，请分段录入。",
                            close_code=status.WS_1009_MESSAGE_TOO_BIG,
                        )
                        return False
                    await upstream.send(chunk)

            async def forward_transcripts() -> None:
                while True:
                    event = sanitize_upstream_event(await upstream.recv())
                    await websocket.send_json(event)
                    if event["type"] in {"final", "error"}:
                        return

            audio_task = asyncio.create_task(forward_audio())
            transcript_task = asyncio.create_task(forward_transcripts())
            done, _ = await asyncio.wait(
                {audio_task, transcript_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if transcript_task in done:
                audio_task.cancel()
                with suppress(asyncio.CancelledError):
                    await audio_task
                await transcript_task
            else:
                stop_sent = await audio_task
                if not stop_sent:
                    transcript_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await transcript_task
                    return
                await asyncio.wait_for(transcript_task, timeout=final_timeout)

            logger.info(
                "Voice transcription completed user_id=%s audio_bytes=%d duration_seconds=%.2f",
                getattr(user, "id", "unknown"),
                total_bytes,
                asyncio.get_running_loop().time() - started_at,
            )
            with suppress(RuntimeError):
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
    except (WebSocketDisconnect, ConnectionClosed):
        logger.info(
            "Voice transcription disconnected user_id=%s audio_bytes=%d",
            getattr(user, "id", "unknown"),
            total_bytes,
        )
    except TimeoutError:
        logger.warning(
            "Voice transcription timed out user_id=%s audio_bytes=%d",
            getattr(user, "id", "unknown"),
            total_bytes,
        )
        await _send_error(
            websocket,
            code="upstream_timeout",
            message="语音转写服务响应超时，请稍后重试。",
        )
    except ValueError:
        logger.warning(
            "Voice transcription received a malformed upstream event user_id=%s",
            getattr(user, "id", "unknown"),
            exc_info=True,
        )
        await _send_error(
            websocket,
            code="malformed_upstream_response",
            message=_GENERIC_UPSTREAM_ERROR,
        )
    except Exception:
        logger.exception(
            "Voice transcription upstream failure user_id=%s audio_bytes=%d",
            getattr(user, "id", "unknown"),
            total_bytes,
        )
        await _send_error(
            websocket,
            code="upstream_unavailable",
            message=_GENERIC_UPSTREAM_ERROR,
        )
    finally:
        _VOICE_SESSION_SEMAPHORE.release()
