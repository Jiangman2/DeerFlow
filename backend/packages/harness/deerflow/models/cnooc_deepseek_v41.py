"""The audited 1017 endpoint is a complete URL, not an OpenAI base directory."""

from __future__ import annotations

import os
import ssl
from typing import Any

import httpx

from deerflow.models.robust_openai import RobustChatOpenAI

ENDPOINT = "https://aiapi.cnooc.com.cn/DeepSeek-V4.1-Flash-1017/FNC-01"


def _route(request: httpx.Request) -> None:
    # The SDK appends this suffix. Rewrite only that one expected operation;
    # never turn an unrelated SDK request into a model submission.
    if str(request.url) != ENDPOINT + "/chat/completions":
        raise ValueError("Unexpected DeepSeek V4.1 SDK operation")
    request.url = httpx.URL(ENDPOINT)


async def _aroute(request: httpx.Request) -> None:
    _route(request)


class CnoocDeepSeekV41Chat(RobustChatOpenAI):
    def __init__(self, *, transport: Any = None, async_transport: Any = None, **kwargs: Any) -> None:
        if any(key in kwargs for key in ("http_client", "http_async_client", "tls_verify", "trust_env")):
            raise ValueError("DeepSeek V4.1 requires its managed verified transports")
        configured = str(kwargs.pop("base_url", ENDPOINT)).rstrip("/")
        if configured != ENDPOINT:
            raise ValueError("Unexpected DeepSeek V4.1 endpoint")
        # Explicit CA selection must fail closed if missing or malformed.
        ca_file = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
        verify = ssl.create_default_context(cafile=ca_file)
        timeout = kwargs.get("timeout", 180)
        kwargs["http_client"] = httpx.Client(verify=verify, trust_env=False, follow_redirects=False, timeout=timeout, transport=transport, event_hooks={"request": [_route]})
        kwargs["http_async_client"] = httpx.AsyncClient(verify=verify, trust_env=False, follow_redirects=False, timeout=timeout, transport=async_transport, event_hooks={"request": [_aroute]})
        kwargs.setdefault("max_retries", 0)
        kwargs.setdefault("cumulative_stream_usage", True)
        super().__init__(base_url=ENDPOINT, **kwargs)

    def _get_request_payload(self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # The upstream defaults to thinking ON. Both UI modes are explicit.
        extra = payload.setdefault("extra_body", {})
        thinking = extra.setdefault("thinking", {"type": "disabled"})
        if thinking.get("type") not in {"enabled", "disabled"}:
            raise ValueError("Invalid thinking mode")
        if "reasoning" in extra or "enable_thinking" in extra:
            raise ValueError("Unsupported DeepSeek V4.1 thinking format")
        if thinking["type"] == "disabled":
            payload.pop("reasoning_effort", None)
        elif not payload.get("reasoning_effort"):
            payload["reasoning_effort"] = "medium"
        return payload
