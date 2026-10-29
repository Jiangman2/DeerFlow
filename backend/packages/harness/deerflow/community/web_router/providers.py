"""Fixed-host JSON clients for the router; no SDK retries or proxy inheritance."""

import json
import time

import httpx

from deerflow.community.deepseek.tools import _search_result

ENDPOINTS = {
    "deepseek": ("https://api.deepseek.com/v1/tools/search", "https://api.deepseek.com/v1/tools/fetch"),
    "tavily": ("https://api.tavily.com/search", "https://api.tavily.com/extract"),
    "exa": ("https://api.exa.ai/search", "https://api.exa.ai/contents"),
}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderFailure(Exception):
    """Safe diagnostic vocabulary; never carry upstream text or credentials."""

    def __init__(self, code, *, status=None, stop=False):
        super().__init__(code)
        self.code = code
        self.status = status
        self.stop = stop


async def _request(provider, kind, key, payload, timeout):
    headers = {"x-api-key" if provider == "exa" else "Authorization": key if provider == "exa" else f"Bearer {key}"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 8)), trust_env=False, follow_redirects=False) as client:
            async with client.stream("POST", ENDPOINTS[provider][1 if kind == "fetch" else 0], headers=headers, json=payload) as response:
                status = response.status_code
                if status != 200:
                    code = {400: "INVALID_REQUEST", 401: "AUTHENTICATION", 402: "BILLING", 403: "ACCESS_DENIED", 404: "CONTENT_UNAVAILABLE", 408: "TIMEOUT", 413: "REQUEST_TOO_LARGE", 429: "RATE_LIMITED"}.get(
                        status, "UNAVAILABLE" if status >= 500 else "HTTP_ERROR"
                    )
                    # Policy denial and invalid requests must not be bypassed with another provider.
                    raise ProviderFailure(code, status=status, stop=status in {400, 403, 413} or 300 <= status < 400)
                body = bytearray()
                async for chunk in response.aiter_bytes(65536):
                    if time.monotonic() - started >= timeout:
                        raise ProviderFailure("TIMEOUT")
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ProviderFailure("RESPONSE_TOO_LARGE")
                    body.extend(chunk)
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ProviderFailure("INVALID_RESPONSE")
        return data
    except httpx.TimeoutException:
        raise ProviderFailure("TIMEOUT") from None
    except httpx.TransportError:
        raise ProviderFailure("NETWORK") from None
    except (ValueError, UnicodeError):
        raise ProviderFailure("INVALID_RESPONSE") from None


async def search(provider, key, query, count, snippet_limit, original_question, timeout):
    if provider == "deepseek":
        payload = {"query": query, "count": count, "need_content": False}
        if original_question:
            payload["ori_question"] = original_question
    elif provider == "tavily":
        payload = {"query": query, "max_results": count, "search_depth": "basic", "include_answer": False, "include_raw_content": False}
    else:
        payload = {"query": query, "numResults": count, "type": "auto", "contents": {"highlights": {"maxCharacters": snippet_limit}}}
    data = await _request(provider, "search", key, payload, timeout)
    rows = data.get("results")
    if not isinstance(rows, list):
        raise ProviderFailure("INVALID_RESPONSE")
    normalized = []
    for row in rows[:count]:
        if not isinstance(row, dict):
            raise ProviderFailure("INVALID_RESPONSE")
        if provider == "deepseek":
            try:
                item = _search_result(row, "summary", snippet_limit)
            except Exception:
                raise ProviderFailure("INVALID_RESPONSE") from None
        else:
            snippet = row.get("content", "") if provider == "tavily" else row.get("highlights", [])
            if provider == "exa":
                if not isinstance(snippet, list) or any(not isinstance(x, str) for x in snippet):
                    raise ProviderFailure("INVALID_RESPONSE")
                snippet = "\n".join(snippet)
            if not isinstance(row.get("url"), str) or not row["url"] or not isinstance(row.get("title", ""), str) or not isinstance(snippet, str):
                raise ProviderFailure("INVALID_RESPONSE")
            item = {"title": row.get("title") or "", "url": row["url"], "snippet": snippet[:snippet_limit], "snippet_type": "content" if provider == "tavily" else "highlights"}
            if len(snippet) > snippet_limit:
                item["snippet_truncated"] = True
        item["provider"] = provider
        normalized.append(item)
    return normalized


async def fetch(provider, key, url, limit, timeout):
    payload = {"url": url} if provider == "deepseek" else {"urls": [url]}
    if provider == "tavily":
        payload.update(extract_depth="basic", format="markdown")
    elif provider == "exa":
        payload["text"] = {"maxCharacters": limit}
    data = await _request(provider, "fetch", key, payload, timeout)
    if provider == "deepseek":
        page, field = data, "content"
    else:
        rows = data.get("results")
        if not isinstance(rows, list):
            raise ProviderFailure("INVALID_RESPONSE")
        if not rows:
            raise ProviderFailure("EMPTY_CONTENT")
        page, field = rows[0], "raw_content" if provider == "tavily" else "text"
    if not isinstance(page, dict) or not isinstance(page.get(field), str) or not isinstance(page.get("title", ""), (str, type(None))):
        raise ProviderFailure("INVALID_RESPONSE")
    if not page[field].strip():
        raise ProviderFailure("EMPTY_CONTENT")
    return page.get("title"), page[field]
