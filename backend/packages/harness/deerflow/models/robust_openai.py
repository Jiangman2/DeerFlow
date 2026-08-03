"""OpenAI-compatible chat adapter with business-envelope diagnostics.

Some enterprise OpenAI-compatible gateways report application failures as
``200 OK`` responses shaped like this::

    {"choices": null, "success": false, "code": "...", "msg": "..."}

``langchain-openai`` correctly rejects the null ``choices`` value, but its
generic ``TypeError`` only lists the response keys. That discards the fields
needed to distinguish a transient provider failure from quota, authentication,
or context-limit errors. This adapter preserves the bounded business error
fields in a typed exception before delegating valid responses to the standard
``ChatOpenAI`` implementation.
"""

from __future__ import annotations

from typing import Any

from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI

_MAX_PROVIDER_MESSAGE_CHARS = 1000


def _bounded_field(value: Any, *, limit: int = _MAX_PROVIDER_MESSAGE_CHARS) -> str:
    """Return a single-line bounded representation suitable for logs."""

    if value in (None, ""):
        return ""
    normalized = " ".join(str(value).split())
    return normalized[:limit]


class OpenAICompatibleResponseError(RuntimeError):
    """Business error returned inside an HTTP-success response."""

    def __init__(
        self,
        *,
        code: Any = None,
        message: Any = None,
        success: Any = None,
    ) -> None:
        self.code = _bounded_field(code, limit=200)
        self.error_code = self.code
        self.provider_message = _bounded_field(message)
        self.success = success

        parts = ["OpenAI-compatible provider returned choices=null"]
        if self.code:
            parts.append(f"code={self.code}")
        if self.provider_message:
            parts.append(f"msg={self.provider_message}")
        if success is not None:
            parts.append(f"success={success!r}")
        super().__init__("; ".join(parts))


class RobustChatOpenAI(ChatOpenAI):
    """``ChatOpenAI`` that preserves null-choices business error details."""

    def _create_chat_result(
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        response_dict = response if isinstance(response, dict) else response.model_dump()
        if response_dict.get("choices") is None and any(key in response_dict for key in ("code", "msg", "success")):
            raise OpenAICompatibleResponseError(
                code=response_dict.get("code"),
                message=response_dict.get("msg"),
                success=response_dict.get("success"),
            )
        return super()._create_chat_result(response, generation_info)
