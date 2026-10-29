"""Bounded fixed-origin Work transport. No arbitrary URL or automatic retries."""

import json
import re
from urllib.parse import quote, urlsplit

import httpx

UAT_APP_ID = "cli_aaec76b56d38d3c7"
WORK_API = "https://open.w.cnooc.com.cn"
AUTHORIZE_API = "https://accounts.w.cnooc.com.cn/open-apis/authen/v1/authorize"
CALLBACK = "http://localhost:10086/auth/callback"
RELAY_PATH = "/api/webhooks/work-documents/oauth-relay"
DOCUMENT_HOST = "haiyoutest.w.cnooc.com.cn"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
DOC_ID_RE = re.compile(r"^[A-Za-z0-9]{5,128}$")


class WorkError(Exception):
    """Content-free failure, safe for a user-visible stage/code only."""

    def __init__(self, stage: str, http_status: int | None = None, business_code: int | None = None):
        self.stage, self.http_status = stage, http_status
        self.business_code = business_code if isinstance(business_code, int) else None
        super().__init__(f"Work operation failed at {stage}")


def document_links(text: str, host: str = DOCUMENT_HOST) -> list[tuple[str, str]]:
    result = []
    for value in re.findall(r"https?://[^\s<>\"'，。；）)\]]+", text):
        try:
            parsed = urlsplit(value)
        except ValueError:
            continue
        if parsed.scheme != "https" or parsed.netloc != host or parsed.username or parsed.password:
            continue
        match = re.fullmatch(r"/docx/([A-Za-z0-9]{5,128})/?", parsed.path)
        if match:
            item = match[1], f"https://{host}/docx/{match[1]}"
            if item not in result:
                result.append(item)
    return result


def segment(value: str | None) -> str:
    if not value or not re.fullmatch(r"[A-Za-z0-9_@.\-]{1,256}", value) or value in {".", ".."}:
        raise WorkError("invalid_resource_id")
    return quote(value, safe="")


class WorkAPI:
    def __init__(self, client: httpx.AsyncClient | None = None, *, api_url: str = WORK_API):
        self.api_url = api_url
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=20)

    async def __call__(
        self, operation: str, *, payload: dict | None = None, token: str | None = None, document_id: str | None = None, block_id: str | None = None, calendar_id: str | None = None, event_id: str | None = None, params: dict | None = None
    ) -> dict:
        if operation == "token":
            path, method = "/open-apis/authen/v2/oauth/token", "POST"
        elif operation == "identity":
            path, method = "/open-apis/authen/v1/user_info", "GET"
        elif operation == "document_create":
            path, method = "/open-apis/docx/v1/documents", "POST"
        elif operation in {"document_info", "document_content", "blocks", "block_get", "block_update", "block_append"} and DOC_ID_RE.fullmatch(document_id or ""):
            path, method = f"/open-apis/docx/v1/documents/{document_id}", "GET"
            if operation == "document_content":
                path += "/raw_content"
            elif operation == "blocks":
                path += "/blocks"
            elif operation.startswith("block_"):
                path += "/blocks/" + segment(block_id)
                if operation == "block_update":
                    method = "PATCH"
                elif operation == "block_append":
                    path, method = path + "/children", "POST"
        elif operation == "calendar_primary":
            path, method = "/open-apis/calendar/v4/calendars/primary", "POST"
        elif operation in {"events", "event_get", "event_attendees", "event_create", "event_update"}:
            path, method = f"/open-apis/calendar/v4/calendars/{segment(calendar_id)}/events", "GET"
            if operation == "event_create":
                method = "POST"
            elif operation in {"event_get", "event_attendees", "event_update"}:
                path += "/" + segment(event_id)
                method = "PATCH" if operation == "event_update" else "GET"
                if operation == "event_attendees":
                    path += "/attendees"
        else:
            raise WorkError("unsupported_operation")
        if operation != "token" and not token:
            raise WorkError("missing_user_grant")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with self._client.stream(method, self.api_url + path, headers=headers, params=params, json=payload if method in {"POST", "PATCH"} else None, follow_redirects=False) as response:
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 1024 * 1024:
                        raise WorkError(operation, response.status_code)
                data = json.loads(chunks)
                if not isinstance(data, dict) or not 200 <= response.status_code < 300 or data.get("code", 0) != 0 or data.get("error"):
                    raise WorkError(operation, response.status_code, data.get("code") if isinstance(data, dict) else None)
                return data
        except WorkError:
            raise
        except Exception:
            raise WorkError(operation) from None

    async def close(self):
        if self._owns_client:
            await self._client.aclose()
