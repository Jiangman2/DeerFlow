"""Run-scoped WeKnora authentication context."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Final

_current_weknora_access_token: Final[ContextVar[str | None]] = ContextVar("deerflow_weknora_access_token", default=None)


def set_current_weknora_access_token(token: str) -> Token[str | None]:
    return _current_weknora_access_token.set(token)


def reset_current_weknora_access_token(token: Token[str | None]) -> None:
    _current_weknora_access_token.reset(token)


def get_current_weknora_access_token() -> str | None:
    token = _current_weknora_access_token.get()
    return token.strip() if isinstance(token, str) and token.strip() else None
