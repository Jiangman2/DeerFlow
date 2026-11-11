"""Bounded fixed-origin Work transport. No arbitrary URL or automatic retries."""

import json
import re
from email.message import Message
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
WORK_FILE_ID_RE = re.compile(r"[A-Za-z0-9_-]{5,128}")
WORK_SHEET_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
WORK_BITABLE_TABLE_RE = re.compile(r"tbl[A-Za-z0-9_-]{1,61}")
WORK_BITABLE_VIEW_RE = re.compile(r"vew[A-Za-z0-9_-]{1,61}")
WORK_SEARCH_TYPES = ("DOCX", "SHEET", "BITABLE", "FILE")
WORK_SEARCH_QUERY_LIMIT = 30
WORK_LEGACY_SEARCH_TITLE_LIMIT = 240
WORK_SEARCH_PAGE_SIZE = 20
WORK_LEGACY_SEARCH_WINDOW = 200
_SHEET_RANGE_RE = re.compile(r"([A-Z]{1,3})([1-9][0-9]{0,6}):([A-Z]{1,3})([1-9][0-9]{0,6})")


def sheet_bounds(value: str) -> tuple[int, int, int, int]:
    match = _SHEET_RANGE_RE.fullmatch(value)
    if not match:
        raise WorkError("invalid_sheet_range")

    def column(letters):
        number = 0
        for letter in letters:
            number = number * 26 + ord(letter) - ord("A") + 1
        return number

    left, top, right, bottom = column(match[1]), int(match[2]), column(match[3]), int(match[4])
    if not 0 <= right - left < 16 or not 0 <= bottom - top < 100 or right > 16384 or bottom > 1048576:
        raise WorkError("invalid_sheet_range")
    return left, top, right, bottom


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


def valid_page_token(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 2048 and value.isascii() and value.isprintable() and " " not in value


class WorkAPI:
    def __init__(self, client: httpx.AsyncClient | None = None, *, api_url: str = WORK_API):
        self.api_url = api_url
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=20)

    async def __call__(
        self,
        operation: str,
        *,
        payload: dict | None = None,
        token: str | None = None,
        document_id: str | None = None,
        block_id: str | None = None,
        calendar_id: str | None = None,
        event_id: str | None = None,
        params: dict | None = None,
        sheet_range: str | None = None,
        search_query: str | None = None,
        search_offset: int = 0,
        page_token: str | None = None,
        page_size: int = 100,
        view_id: str | None = None,
    ) -> dict:
        if operation == "token":
            path, method = "/open-apis/authen/v2/oauth/token", "POST"
        elif operation == "identity":
            path, method = "/open-apis/authen/v1/user_info", "GET"
        elif operation == "file_search":
            if not search_query or not search_query.strip() or len(search_query) > WORK_SEARCH_QUERY_LIMIT or not search_query.isprintable():
                raise WorkError("invalid_search_title")
            path, method = "/open-apis/search/v2/doc_wiki/search", "POST"
            payload = {"query": search_query, "page_size": WORK_SEARCH_PAGE_SIZE, "doc_filter": {"doc_types": list(WORK_SEARCH_TYPES), "only_title": True}}
            if page_token is not None:
                if not valid_page_token(page_token):
                    raise WorkError("invalid_page_token")
                payload["page_token"] = page_token
            params = None
        elif operation == "file_search_legacy":
            if not search_query or not search_query.strip() or len(search_query) > WORK_LEGACY_SEARCH_TITLE_LIMIT or not search_query.isprintable():
                raise WorkError("invalid_search_title")
            if type(search_offset) is not int or not 0 <= search_offset < WORK_LEGACY_SEARCH_WINDOW - WORK_SEARCH_PAGE_SIZE:
                raise WorkError("invalid_search_offset")
            path, method = "/open-apis/suite/docs-api/search/object", "POST"
            payload = {"search_key": search_query, "count": WORK_SEARCH_PAGE_SIZE, "offset": search_offset, "docs_types": ["doc", "sheet", "bitable", "file"]}
            params = None
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
        elif operation in {"sheet_tabs", "sheet_values", "drive_file_download"} and WORK_FILE_ID_RE.fullmatch(document_id or ""):
            method = "GET"
            params = None
            if operation == "sheet_tabs":
                path = f"/open-apis/sheets/v3/spreadsheets/{document_id}/sheets/query"
            elif operation == "sheet_values":
                if not WORK_SHEET_ID_RE.fullmatch(block_id or ""):
                    raise WorkError("invalid_sheet_range")
                sheet_bounds(sheet_range or "")
                path = f"/open-apis/sheets/v2/spreadsheets/{document_id}/values/" + quote(f"{block_id}!{sheet_range}", safe="")
                params = {"valueRenderOption": "ToString"}
            else:
                path = f"/open-apis/drive/v1/files/{document_id}/download"
        elif operation in {"bitable_tables", "bitable_records"} and WORK_FILE_ID_RE.fullmatch(document_id or ""):
            path, method = f"/open-apis/bitable/v1/apps/{document_id}/tables", "GET"
            if type(page_size) is not int or not 1 <= page_size <= 100:
                raise WorkError("invalid_page_size")
            params = {"page_size": page_size}
            if page_token is not None:
                if not valid_page_token(page_token):
                    raise WorkError("invalid_page_token")
                params["page_token"] = page_token
            if operation == "bitable_records":
                if not WORK_BITABLE_TABLE_RE.fullmatch(block_id or "") or (view_id and not WORK_BITABLE_VIEW_RE.fullmatch(view_id)):
                    raise WorkError("invalid_file_reference")
                path, method = path + f"/{block_id}/records/search", "POST"
                payload = {"automatic_fields": False}
                if view_id:
                    payload["view_id"] = view_id
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
        if operation == "drive_file_download":
            headers["Accept-Encoding"] = "identity"
        try:
            async with self._client.stream(method, self.api_url + path, headers=headers, params=params, json=payload if method in {"POST", "PATCH"} else None, follow_redirects=False) as response:
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                download = operation == "drive_file_download" and response.status_code == 200 and not (content_type.endswith("/json") or content_type.endswith("+json"))
                limit = 1024 * 1024
                if download:
                    from .file_content import MAX_FILE_BYTES

                    if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                        raise WorkError("file_invalid", response.status_code)
                    limit = MAX_FILE_BYTES
                    length = response.headers.get("content-length", "")
                    if length.isdecimal() and len(length) <= 20 and int(length) > limit:
                        raise WorkError("file_too_large", response.status_code)
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > limit:
                        raise WorkError("file_too_large" if download else operation, response.status_code)
                    chunks.extend(chunk)
                if download:
                    disposition = Message()
                    disposition["Content-Disposition"] = response.headers.get("content-disposition", "")
                    return {"content": bytes(chunks), "filename": disposition.get_filename() or "", "content_type": content_type}
                try:
                    data = json.loads(chunks)
                except (ValueError, RecursionError):
                    raise WorkError(operation, response.status_code) from None
                if operation == "drive_file_download" or not isinstance(data, dict) or not 200 <= response.status_code < 300 or data.get("code", 0) != 0 or data.get("error"):
                    raise WorkError(operation, response.status_code, data.get("code") if isinstance(data, dict) else None)
                return data
        except WorkError:
            raise
        except Exception:
            raise WorkError(operation) from None

    async def close(self):
        if self._owns_client:
            await self._client.aclose()
