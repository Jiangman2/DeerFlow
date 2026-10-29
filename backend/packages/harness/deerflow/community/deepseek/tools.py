"""Bounded adapters for DeepSeek's official standalone Search and Fetch APIs."""

import json
import math
import os
import time

import httpx
from langchain.tools import tool
from langchain_core.messages import HumanMessage

from deerflow.community.deepseek.errors import DeepSeekError, http_error, render_error
from deerflow.community.url_safety import validate_cloud_fetch_url
from deerflow.community.web_fetch_output import content_limit, format_page
from deerflow.config import get_app_config
from deerflow.runtime.context_keys import CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY
from deerflow.tools.types import Runtime

_BASE_URL = "https://api.deepseek.com/v1/tools/"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _extras(tool_name: str) -> dict:
    config = get_app_config().get_tool_config(tool_name)
    return (config.model_extra or {}) if config else {}


def _post(tool_name: str, path: str, payload: dict) -> dict:
    extras = _extras(tool_name)
    key = extras.get("api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
    if not isinstance(key, str) or not key.strip():
        raise DeepSeekError("CREDENTIAL_MISSING", "DEEPSEEK_API_KEY is not configured", scope="configuration")
    try:
        timeout = float(extras.get("timeout", 30))
        if not math.isfinite(timeout):
            raise ValueError
        timeout = max(1.0, min(timeout, 60.0))
    except (ValueError, TypeError, OverflowError):
        raise DeepSeekError("CONFIG_ERROR", "DeepSeek timeout configuration is invalid", scope="configuration") from None
    started = time.monotonic()
    # Deliberately fixed official host: a model cannot redirect credentials to another API.
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, 10)), trust_env=False, follow_redirects=False) as client:
        with client.stream("POST", _BASE_URL + path, headers={"Authorization": f"Bearer {key.strip()}"}, json=payload) as response:
            if response.status_code != 200:
                raise http_error(response.status_code)
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise DeepSeekError("RESPONSE_TOO_LARGE", "DeepSeek response size limit exceeded")
                body.extend(chunk)
                if time.monotonic() - started > timeout:
                    raise DeepSeekError("TIMEOUT", "DeepSeek response deadline exceeded", retryable=True, scope="transport")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        raise DeepSeekError("INVALID_RESPONSE", "DeepSeek response is not valid JSON") from None
    if not isinstance(data, dict):
        raise DeepSeekError("INVALID_RESPONSE", "DeepSeek response must be an object")
    return data


def _original_question(runtime: Runtime | None) -> str | None:
    if runtime is None:
        return None
    state = runtime.state if isinstance(runtime.state, dict) else {}
    context = runtime.context if isinstance(runtime.context, dict) else {}
    old_ids = context.get(CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY) or ()
    old_ids = set(old_ids) if isinstance(old_ids, (list, tuple, set, frozenset)) else set()
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        if (old_ids and (message.id is None or message.id in old_ids)) or message.name not in (None, "user-input") or (message.additional_kwargs or {}).get("hide_from_ui"):
            continue
        content = message.content
        if isinstance(content, list):
            content = "\n".join(block if isinstance(block, str) else block["text"] for block in content if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)))
        # Do not reuse an older question when the current user only supplied an image.
        if not isinstance(content, str):
            return None
        return " ".join(content.split())[:128] or None
    return None


def _search_settings(extras: dict) -> tuple[int, str, int, bool]:
    try:
        count = max(1, min(int(extras.get("max_results", 5)), 50))
        limit = max(1, min(int(extras.get("snippet_max_characters", 1000)), 10000))
        source = extras.get("snippet_source", "summary")
        send_question = extras.get("send_original_question", True)
        if source not in {"summary", "snippet"} or not isinstance(send_question, bool):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise DeepSeekError("CONFIG_ERROR", "DeepSeek search configuration is invalid", scope="configuration") from None
    return count, source, limit, send_question


def _search_result(row: dict, preferred: str, limit: int) -> dict:
    if not isinstance(row.get("url"), str) or not row["url"] or not isinstance(row.get("title", ""), str):
        raise DeepSeekError("INVALID_RESPONSE", "DeepSeek returned invalid source fields")
    for key in ("summary", "snippet", "date"):
        if row.get(key) is not None and not isinstance(row[key], str):
            raise DeepSeekError("INVALID_RESPONSE", "DeepSeek returned invalid source metadata")
    alternative = "snippet" if preferred == "summary" else "summary"
    selected = preferred if row.get(preferred) else alternative
    text = row.get(selected) or ""
    result = {"title": row.get("title") or "", "url": row["url"], "snippet": text[:limit]}
    if text:
        result["snippet_type"] = selected
    if len(text) > limit:
        result["snippet_truncated"] = True
    if row.get("date"):
        result["published_at"] = row["date"]
    return result


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str, runtime: Runtime = None) -> str:
    """Search the web for relevant pages and source summaries using DeepSeek.

    Args:
        query: Search keywords, between 1 and 128 Unicode characters.
    """
    query = query.strip()
    if not query or len(query) > 128:
        return render_error(DeepSeekError("INVALID_REQUEST", "query must contain 1 to 128 nonblank characters", scope="client_validation"))
    try:
        count, source, limit, send_question = _search_settings(_extras("web_search"))
        payload = {"query": query, "count": count, "need_content": False}
        original = _original_question(runtime) if send_question else None
        if original:
            payload["ori_question"] = original
        data = _post("web_search", "search", payload)
        rows = data.get("results")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DeepSeekError("INVALID_RESPONSE", "DeepSeek returned invalid search results")
        return json.dumps(
            [_search_result(row, source, limit) for row in rows[:count]],
            ensure_ascii=False,
        )
    except Exception as exc:
        return render_error(exc)


@tool("web_fetch", parse_docstring=True)
def web_fetch_tool(url: str) -> str:
    """Fetch a public webpage as Markdown using DeepSeek.
    Only fetch exact URLs supplied by the user or returned by web_search/web_fetch.
    This tool cannot read authenticated or private pages.

    Args:
        url: Complete public HTTP or HTTPS URL without a username or password.
    """
    try:
        error = validate_cloud_fetch_url(url)
        if error:
            return render_error(DeepSeekError("INVALID_REQUEST", error.removeprefix("Error: "), scope="client_validation"), fetch=True)
        data = _post("web_fetch", "fetch", {"url": url})
        if not isinstance(data.get("content"), str):
            raise DeepSeekError("INVALID_RESPONSE", "DeepSeek returned invalid page content")
        if not data["content"].strip():
            raise DeepSeekError("EMPTY_CONTENT", "DeepSeek returned no content found")
        return format_page(data.get("title"), data.get("content"), content_limit(_extras("web_fetch")))
    except Exception as exc:
        return render_error(exc, fetch=True)
