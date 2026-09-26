"""Gateway request trace middleware."""

from __future__ import annotations

from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from deerflow.config.app_config import is_trace_correlation_enabled
from deerflow.trace_context import TRACE_ID_HEADER, request_trace_context


class TraceMiddleware:
    """Bind a request-level trace id and write it to HTTP response headers.

    HTTP correlation is unconditional, following upstream #5119. Enhanced
    logging controls only the log formatter. The legacy ``enabled`` argument
    is accepted for enterprise callers but cannot disable correlation.

    Write at response start, without buffering SSE. An unhandled pre-response
    exception gets a plain 500 here because Starlette's outer error middleware
    would bypass our send wrapper. Re-raise for normal server error logging.
    That fallback remains CORS-opaque, as in upstream: do not duplicate the
    CORS origin authorization policy outside CORSMiddleware.
    """

    def __init__(self, app: ASGIApp, *, enabled: bool | None = None):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming_trace_id = headers.get(TRACE_ID_HEADER)

        with request_trace_context(incoming_trace_id) as trace_id:
            response_started = False

            async def send_with_trace(message: Message) -> None:
                nonlocal response_started
                if message["type"] == "http.response.start":
                    response_started = True
                    response_headers = MutableHeaders(scope=message)
                    response_headers[TRACE_ID_HEADER] = trace_id
                await send(message)

            try:
                await self.app(scope, receive, send_with_trace)
            except Exception:
                if not response_started:
                    body = b"Internal Server Error"
                    await send_with_trace(
                        {
                            "type": "http.response.start",
                            "status": 500,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode("ascii"))],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                raise


def resolve_trace_enabled(config: Any) -> bool:
    """Read ``logging.enhance.enabled`` from an ``AppConfig``-like object.

    Thin backwards-compatible alias around
    :func:`deerflow.config.app_config.is_trace_correlation_enabled`, kept so
    existing callers do not have to switch imports. This legacy helper no
    longer controls Gateway HTTP or embedded-turn correlation.
    """
    return is_trace_correlation_enabled(config)
