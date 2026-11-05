"""Safe provider error vocabulary, shared by DeepSeek Search and Fetch."""

import json

import httpx


class DeepSeekError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, scope: str = "provider_response", http_status: int | None = None):
        super().__init__(message)
        self.code = f"DEEPSEEK_{code}"
        self.message = message
        self.retryable = retryable
        self.scope = scope
        self.http_status = http_status


def http_error(status: int) -> DeepSeekError:
    code, message, retryable = {
        400: ("INVALID_REQUEST", "request rejected", False),
        401: ("AUTHENTICATION", "authentication failed", False),
        402: ("BILLING", "account balance insufficient; service disabled until funded", False),
        403: ("ACCESS_DENIED", "access denied by provider policy", False),
        404: ("PROVIDER_NOT_FOUND", "provider could not supply this operation or usable content; target webpage status is not known", False),
        408: ("TIMEOUT", "request timed out", True),
        413: ("REQUEST_TOO_LARGE", "request too large", False),
        429: ("RATE_LIMITED", "rate limited", True),
    }.get(status, ("UNAVAILABLE", "temporarily unavailable", True) if 500 <= status <= 599 else ("HTTP_ERROR", "request failed", False))
    if 300 <= status <= 399:
        code, message, retryable = "REDIRECT_BLOCKED", "API redirect rejected", False
    return DeepSeekError(code, f"DeepSeek API HTTP {status}: {message}", retryable=retryable, scope="provider_api", http_status=status)


def render_error(exc: Exception, *, fetch: bool = False) -> str:
    if isinstance(exc, DeepSeekError):
        error = exc
    elif isinstance(exc, httpx.TimeoutException):
        error = DeepSeekError("TIMEOUT", "DeepSeek network request timed out", retryable=True, scope="transport")
    elif isinstance(exc, httpx.TransportError):
        error = DeepSeekError("NETWORK", "DeepSeek network connection failed", retryable=True, scope="transport")
    else:
        # Do not serialize upstream bodies, URL-bearing exception text or credentials.
        error = DeepSeekError("INTERNAL", "DeepSeek adapter internal error", scope="adapter")
    payload = {"error": error.message, "error_code": error.code, "retryable": error.retryable, "error_scope": error.scope}
    if error.http_status is not None:
        payload["http_status"] = error.http_status
    encoded = json.dumps(payload, ensure_ascii=False)
    # Retain the established Markdown fetch error prefix; both tools share the payload.
    return f"Error: {encoded}" if fetch else encoded
