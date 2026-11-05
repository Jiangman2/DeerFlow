"""Remove OAuth query credentials before ASGI/uvicorn emits access logs."""

from app.integrations.work.config import BROWSER_AUTHORIZE_PATH, BROWSER_CALLBACK_PATH


class WorkOAuthQueryRedactionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").rstrip("/") not in {BROWSER_AUTHORIZE_PATH, BROWSER_CALLBACK_PATH}:
            return await self.app(scope, receive, send)

        async def redacted_send(message):
            if message["type"] == "http.response.start":
                scope["query_string"] = b""
            await send(message)

        try:
            await self.app(scope, receive, redacted_send)
        finally:
            scope["query_string"] = b""
