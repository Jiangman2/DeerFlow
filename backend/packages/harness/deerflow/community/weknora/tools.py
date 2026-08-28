"""Permission-scoped WeKnora knowledge, document, and agent tools.

WeKnora is treated as an external knowledge service. DeerFlow keeps agent
orchestration in-process and calls WeKnora for document retrieval/RAG so the
knowledge storage, parsing, permissions, and indexing remain owned by WeKnora.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

import httpx
from langchain.tools import tool

from deerflow.community.weknora.citations import normalize_weknora_citations
from deerflow.config import get_app_config
from deerflow.runtime.weknora_context import (
    get_current_weknora_access_token,
    get_current_weknora_scope,
)

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "http://localhost:8080"
_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_MAX_RESULTS = 8
_MAX_RESULTS_CAP = 30
_DEFAULT_PAGE_SIZE = 20
_PAGE_SIZE_CAP = 100
_DEFAULT_READ_CHUNKS = 100
_READ_CHUNKS_CAP = 300
_ANSWER_CHAR_LIMIT = 24_000
_DOCUMENT_CHAR_LIMIT = 48_000
_TOOL_EVENT_CONTENT_LIMIT = 2_000
_missing_config_warned: set[str] = set()
_RESOURCE_URI_RE = re.compile(r"resource://[A-Za-z0-9_-]{1,256}")
_MARKDOWN_RESOURCE_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\((?P<uri>resource://[A-Za-z0-9_-]{1,256})\)")
_MARKDOWN_PROXY_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]\n]*)\]\((?P<url>/api/weknora/knowledge-bases/"
    r"[^)\s/]+/files\?file_path=[^)\s]+)\)"
)


def _tool_extra(tool_name: str) -> dict[str, Any]:
    config = get_app_config().get_tool_config(tool_name)
    if config is None:
        return {}
    extra = config.model_extra or {}
    return extra if isinstance(extra, dict) else {}


def _get_string(tool_name: str, key: str, env_name: str | None = None, default: str | None = None) -> str | None:
    value = _tool_extra(tool_name).get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if env_name:
        env_value = os.getenv(env_name)
        if env_value and env_value.strip():
            return env_value.strip()
    return default


def _get_api_url(tool_name: str) -> str:
    return (_get_string(tool_name, "api_url", "WEKNORA_API_URL", _DEFAULT_API_URL) or _DEFAULT_API_URL).rstrip("/")


def _get_api_key(tool_name: str) -> str | None:
    return _get_string(tool_name, "api_key", "WEKNORA_API_KEY")


def _get_timeout(tool_name: str) -> float:
    value = _tool_extra(tool_name).get("timeout_seconds", os.getenv("WEKNORA_TIMEOUT_SECONDS"))
    try:
        timeout = float(value) if value is not None else _DEFAULT_TIMEOUT_S
    except (TypeError, ValueError):
        logger.warning("Invalid WeKnora timeout_seconds=%r; using default %s", value, _DEFAULT_TIMEOUT_S)
        timeout = _DEFAULT_TIMEOUT_S
    return max(1.0, timeout)


def _coerce_max_results(value: object) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid WeKnora max_results=%r; using default %s", value, _DEFAULT_MAX_RESULTS)
        count = _DEFAULT_MAX_RESULTS
    return max(1, min(count, _MAX_RESULTS_CAP))


def _coerce_positive_int(value: object, *, default: int, maximum: int) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = default
    return max(1, min(count, maximum))


def _split_csv(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _configured_ids(tool_name: str, key: str, env_name: str) -> list[str]:
    values = _split_csv(_tool_extra(tool_name).get(key))
    if values:
        return values
    return _split_csv(os.getenv(env_name))


def _endpoint(api_url: str, path: str) -> str:
    base = api_url.rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}{path}"
    return f"{base}/api/v1{path}"


def _headers(api_key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    access_token = get_current_weknora_access_token()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    elif api_key:
        headers["X-API-Key"] = api_key
    return headers


def _missing_config(tool_name: str, message: str, **context: Any) -> str:
    if tool_name not in _missing_config_warned:
        _missing_config_warned.add(tool_name)
        logger.warning(message)
    return json.dumps({"error": message, **context}, ensure_ascii=False)


def _build_scope(
    tool_name: str,
    *,
    knowledge_base_id: str | None,
    knowledge_base_ids: list[str] | None,
    knowledge_ids: list[str] | None,
) -> dict[str, Any]:
    run_scope = get_current_weknora_scope()
    if run_scope:
        payload: dict[str, Any] = {}
        for key in ("knowledge_base_ids", "knowledge_ids", "mentioned_items"):
            value = run_scope.get(key)
            if isinstance(value, list) and value:
                payload[key] = value
        tag_scopes = run_scope.get("tag_scopes")
        if isinstance(tag_scopes, list) and tag_scopes:
            payload["tag_ids"] = list(
                dict.fromkeys(
                    tag_id
                    for tag_scope in tag_scopes
                    if isinstance(tag_scope, dict)
                    for tag_id in _split_csv(tag_scope.get("tag_ids"))
                )
            )
        return payload

    payload: dict[str, Any] = {}

    extra = _tool_extra(tool_name)
    config_kb_ids = _split_csv(extra.get("knowledge_base_ids"))
    config_knowledge_ids = _split_csv(extra.get("knowledge_ids"))
    config_kb_id = _get_string(tool_name, "knowledge_base_id")
    env_kb_ids = _split_csv(os.getenv("WEKNORA_KNOWLEDGE_BASE_IDS"))
    env_knowledge_ids = _split_csv(os.getenv("WEKNORA_KNOWLEDGE_IDS"))
    env_kb_id = _get_string(tool_name, "knowledge_base_id", "WEKNORA_KNOWLEDGE_BASE_ID")

    explicit_kb_ids = [item for item in (knowledge_base_ids or []) if item]
    explicit_knowledge_ids = [item for item in (knowledge_ids or []) if item]

    if explicit_kb_ids:
        payload["knowledge_base_ids"] = explicit_kb_ids
    elif knowledge_base_id:
        payload["knowledge_base_id"] = knowledge_base_id
    elif config_kb_ids:
        payload["knowledge_base_ids"] = config_kb_ids
    elif config_kb_id:
        payload["knowledge_base_id"] = config_kb_id
    elif env_kb_ids:
        payload["knowledge_base_ids"] = env_kb_ids
    elif env_kb_id:
        payload["knowledge_base_id"] = env_kb_id

    if explicit_knowledge_ids:
        payload["knowledge_ids"] = explicit_knowledge_ids
    elif config_knowledge_ids:
        payload["knowledge_ids"] = config_knowledge_ids
    elif env_knowledge_ids:
        payload["knowledge_ids"] = env_knowledge_ids

    return payload


def _scope_has_target(scope: dict[str, Any]) -> bool:
    return any(
        bool(scope.get(key))
        for key in (
            "knowledge_base_id",
            "knowledge_base_ids",
            "knowledge_ids",
            "mentioned_items",
            "tag_ids",
        )
    )


def _run_scope_effective_kb_ids() -> set[str]:
    scope = get_current_weknora_scope() or {}
    result = set(_split_csv(scope.get("knowledge_base_ids")))
    for item in scope.get("mentioned_items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "kb" and isinstance(item.get("id"), str):
            result.add(str(item["id"]).strip())
        elif isinstance(item.get("kb_id"), str):
            result.add(str(item["kb_id"]).strip())
    return {item for item in result if item}


def _document_row_matches_run_scope(item: dict[str, Any]) -> bool:
    scope = get_current_weknora_scope()
    if not scope:
        return True
    knowledge_id = str(item.get("id") or item.get("knowledge_id") or "").strip()
    knowledge_base_id = str(item.get("knowledge_base_id") or "").strip()
    if knowledge_id and knowledge_id in set(_split_csv(scope.get("knowledge_ids"))):
        return True
    if knowledge_base_id and knowledge_base_id in set(_split_csv(scope.get("knowledge_base_ids"))):
        return True

    tag_ids = {
        str(item.get("tag_id") or "").strip(),
        *(
            str(tag.get("id") or "").strip()
            for tag in item.get("tags") or []
            if isinstance(tag, dict)
        ),
    }
    for tag_scope in scope.get("tag_scopes") or []:
        if not isinstance(tag_scope, dict):
            continue
        if str(tag_scope.get("knowledge_base_id") or "").strip() != knowledge_base_id:
            continue
        if tag_ids.intersection(_split_csv(tag_scope.get("tag_ids"))):
            return True
    return False


def _scope_error(message: str, **context: Any) -> str:
    return json.dumps({"error": message, **context}, ensure_ascii=False)


def _scope_knowledge_base_ids(scope: dict[str, Any]) -> list[str]:
    ids = _split_csv(scope.get("knowledge_base_ids"))
    single = scope.get("knowledge_base_id")
    if not ids and isinstance(single, str) and single.strip():
        ids = [single.strip()]
    return ids


def _resource_uris(value: Any) -> list[str]:
    """Return unique stable WeKnora resource handles from a response value."""

    found: list[str] = []
    seen: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            for match in _RESOURCE_URI_RE.finditer(item):
                uri = match.group(0)
                if uri not in seen:
                    seen.add(uri)
                    found.append(uri)
            return
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return found


def _resource_proxy_url(resource_uri: str, knowledge_base_id: str) -> str:
    return f"/api/weknora/knowledge-bases/{quote(knowledge_base_id, safe='')}/files?file_path={quote(resource_uri, safe='')}"


def _resource_image_alts(value: Any) -> dict[str, str]:
    """Collect the best available label for each stable image handle."""

    labels: dict[str, str] = {}

    def visit(item: Any) -> None:
        if isinstance(item, str):
            for match in _MARKDOWN_RESOURCE_IMAGE_RE.finditer(item):
                alt = match.group("alt").strip()
                if alt:
                    labels.setdefault(match.group("uri"), alt)
            stripped = item.strip()
            if stripped.startswith(("[", "{")):
                try:
                    structured = json.loads(stripped)
                except (TypeError, ValueError, json.JSONDecodeError):
                    structured = None
                if isinstance(structured, (dict, list)):
                    visit(structured)
            return
        if isinstance(item, dict):
            uri = next(
                (
                    candidate
                    for key in ("url", "uri", "resource_uri", "file_path")
                    if isinstance((candidate := item.get(key)), str)
                    and _RESOURCE_URI_RE.fullmatch(candidate)
                ),
                None,
            )
            if uri:
                for key in ("caption", "alt", "title", "name"):
                    label = item.get(key)
                    if isinstance(label, str) and label.strip():
                        labels.setdefault(uri, label.strip())
                        break
            for nested in item.values():
                visit(nested)
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return labels


def _resource_images(
    item: dict[str, Any],
    scope: dict[str, Any],
) -> list[dict[str, str]]:
    knowledge_base_id = item.get("knowledge_base_id")
    if not isinstance(knowledge_base_id, str) or not knowledge_base_id.strip():
        scoped_ids = _scope_knowledge_base_ids(scope)
        knowledge_base_id = scoped_ids[0] if len(scoped_ids) == 1 else ""
    knowledge_base_id = knowledge_base_id.strip()
    if not knowledge_base_id:
        return []

    labels = _resource_image_alts(item)
    images: list[dict[str, str]] = []
    for resource_uri in _resource_uris(item):
        url = _resource_proxy_url(resource_uri, knowledge_base_id)
        alt = labels.get(resource_uri) or "知识库图片"
        images.append(
            {
                "resource_uri": resource_uri,
                "url": url,
                "alt": alt,
                "markdown": f"![{alt}]({url})",
            }
        )
    return images


def _replace_resource_uris(
    text: str,
    resource_images: Iterable[dict[str, str]],
) -> str:
    replacements = {image["resource_uri"]: image["url"] for image in resource_images if image.get("resource_uri") and image.get("url")}
    if not replacements:
        return text
    return _RESOURCE_URI_RE.sub(
        lambda match: replacements.get(match.group(0), match.group(0)),
        text,
    )


def _answer_resource_images(
    raw_answer: str,
    normalized_answer: str,
    references: Iterable[dict[str, Any]],
) -> list[dict[str, str]]:
    available: dict[str, str] = {}
    for reference in references:
        for image in reference.get("resource_images") or []:
            if not isinstance(image, dict):
                continue
            resource_uri = image.get("resource_uri")
            url = image.get("url")
            if isinstance(resource_uri, str) and isinstance(url, str):
                available[resource_uri] = url

    images: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for match in _MARKDOWN_RESOURCE_IMAGE_RE.finditer(raw_answer):
        resource_uri = match.group("uri")
        url = available.get(resource_uri)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        alt = match.group("alt").strip() or "知识库图片"
        images.append(
            {
                "resource_uri": resource_uri,
                "url": url,
                "markdown": f"![{alt}]({url})",
            }
        )

    # Keep already-normalized image Markdown discoverable if an upstream answer
    # contains a same-origin proxy URL directly.
    for match in _MARKDOWN_PROXY_IMAGE_RE.finditer(normalized_answer):
        url = match.group("url")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        alt = match.group("alt").strip() or "知识库图片"
        resource_uri = next(
            (uri for uri, candidate in available.items() if candidate == url),
            "",
        )
        images.append(
            {
                "resource_uri": resource_uri,
                "url": url,
                "markdown": f"![{alt}]({url})",
            }
        )
    return images


def _citation_fields(item: dict[str, Any], scope: dict[str, Any]) -> dict[str, str]:
    knowledge_id = item.get("knowledge_id")
    if not isinstance(knowledge_id, str) or not knowledge_id.strip():
        return {"citation_url": "", "citation_markdown": ""}

    knowledge_base_id = item.get("knowledge_base_id")
    if not isinstance(knowledge_base_id, str) or not knowledge_base_id.strip():
        scoped_ids = _scope_knowledge_base_ids(scope)
        knowledge_base_id = scoped_ids[0] if len(scoped_ids) == 1 else ""
    if not knowledge_base_id:
        return {"citation_url": "", "citation_markdown": ""}

    citation_url = f"/platform/knowledge-bases/{quote(knowledge_base_id.strip(), safe='')}?knowledge_id={quote(knowledge_id.strip(), safe='')}"
    title = item.get("knowledge_title") or item.get("knowledge_filename") or "WeKnora knowledge"
    safe_title = str(title).replace("]", "）").strip() or "WeKnora knowledge"
    return {
        "citation_url": citation_url,
        "citation_markdown": f"[citation:{safe_title}]({citation_url})",
    }


def _format_search_results(query: str, data: dict[str, Any], max_results: int, scope: dict[str, Any]) -> str:
    results = data.get("data") or []
    if not isinstance(results, list):
        return json.dumps({"query": query, "error": "Unexpected WeKnora response shape", "raw": data}, ensure_ascii=False)

    normalized = []
    for item in results[:max_results]:
        if not isinstance(item, dict):
            continue
        citations = _citation_fields(item, scope)
        resource_images = _resource_images(item, scope)
        normalized.append(
            {
                "content": _replace_resource_uris(
                    str(item.get("content") or ""),
                    resource_images,
                ),
                "score": item.get("score"),
                "knowledge_id": item.get("knowledge_id", ""),
                "knowledge_base_id": item.get("knowledge_base_id", ""),
                "knowledge_title": item.get("knowledge_title", ""),
                "knowledge_filename": item.get("knowledge_filename", ""),
                "chunk_id": item.get("id", ""),
                "chunk_index": item.get("chunk_index"),
                "chunk_type": item.get("chunk_type", ""),
                "source": citations["citation_url"],
                "source_type": item.get("knowledge_source", ""),
                "resource_images": resource_images,
                **citations,
            }
        )

    return json.dumps(
        {
            "query": query,
            "total_results": len(normalized),
            "results": normalized,
        },
        indent=2,
        ensure_ascii=False,
    )


def _post_json(tool_name: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    api_key = _get_api_key(tool_name)
    if not api_key and not get_current_weknora_access_token():
        raise ValueError("WEKNORA_API_KEY is not configured")

    url = _endpoint(_get_api_url(tool_name), path)
    with httpx.Client(timeout=_get_timeout(tool_name)) as client:
        response = client.post(url, headers=_headers(api_key), json=payload)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {"data": data}


def _get_json(
    tool_name: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    api_key = _get_api_key(tool_name)
    if not api_key and not get_current_weknora_access_token():
        raise ValueError("WEKNORA_API_KEY is not configured")

    url = _endpoint(_get_api_url(tool_name), path)
    with httpx.Client(timeout=_get_timeout(tool_name)) as client:
        response = client.get(url, headers=_headers(api_key), params=params)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {"data": data}


def _public_knowledge_base(item: dict[str, Any]) -> dict[str, Any]:
    """Return the useful read-only KB fields without credentials/config secrets."""

    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or ""),
        "description": str(item.get("description") or ""),
        "type": str(item.get("type") or ""),
        "is_pinned": bool(item.get("is_pinned", False)),
        "knowledge_count": int(item.get("knowledge_count") or 0),
        "chunk_count": int(item.get("chunk_count") or 0),
        "is_processing": bool(item.get("is_processing", False)),
        "processing_count": int(item.get("processing_count") or 0),
    }


def _public_document(item: dict[str, Any]) -> dict[str, Any]:
    scope = {"knowledge_base_id": item.get("knowledge_base_id")}
    citations = _citation_fields(
        {
            "knowledge_id": item.get("id"),
            "knowledge_base_id": item.get("knowledge_base_id"),
            "knowledge_title": item.get("title") or item.get("file_name"),
        },
        scope,
    )
    return {
        "id": str(item.get("id") or ""),
        "knowledge_base_id": str(item.get("knowledge_base_id") or ""),
        "type": str(item.get("type") or ""),
        "title": str(item.get("title") or ""),
        "description": str(item.get("description") or ""),
        "source": str(item.get("source") or ""),
        "channel": str(item.get("channel") or ""),
        "parse_status": str(item.get("parse_status") or ""),
        "summary_status": str(item.get("summary_status") or ""),
        "enable_status": str(item.get("enable_status") or ""),
        "file_name": str(item.get("file_name") or ""),
        "file_type": str(item.get("file_type") or ""),
        "file_size": int(item.get("file_size") or 0),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "processed_at": item.get("processed_at"),
        "error_message": str(item.get("error_message") or ""),
        **citations,
    }


def _public_chunk(item: dict[str, Any]) -> dict[str, Any]:
    scope = {"knowledge_base_id": item.get("knowledge_base_id")}
    citations = _citation_fields(
        {
            "knowledge_id": item.get("knowledge_id"),
            "knowledge_base_id": item.get("knowledge_base_id"),
            "knowledge_title": item.get("knowledge_title") or item.get("knowledge_filename"),
        },
        scope,
    )
    resource_images = _resource_images(item, scope)
    return {
        "id": str(item.get("id") or ""),
        "knowledge_id": str(item.get("knowledge_id") or ""),
        "knowledge_base_id": str(item.get("knowledge_base_id") or ""),
        "content": _replace_resource_uris(str(item.get("content") or ""), resource_images),
        "chunk_index": item.get("chunk_index"),
        "chunk_type": str(item.get("chunk_type") or ""),
        "is_enabled": bool(item.get("is_enabled", True)),
        "resource_images": resource_images,
        **citations,
    }


def _public_agent(item: dict[str, Any], disabled_ids: set[str]) -> dict[str, Any]:
    config = item.get("config") if isinstance(item.get("config"), dict) else {}
    allowed_tools = [str(value) for value in config.get("allowed_tools") or [] if str(value)]
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or ""),
        "description": str(item.get("description") or ""),
        "avatar": str(item.get("avatar") or ""),
        "is_builtin": bool(item.get("is_builtin", False)),
        "enabled": str(item.get("id") or "") not in disabled_ids,
        "agent_mode": str(config.get("agent_mode") or ""),
        "agent_type": str(config.get("agent_type") or ""),
        "allowed_tools": allowed_tools,
        "knowledge_base_selection": str(config.get("kb_selection_mode") or ""),
        "capabilities": {
            "data_analysis": bool(config.get("data_analysis_enabled", False)),
            "web_search": bool(config.get("web_search_enabled", False)),
            "web_fetch": bool(config.get("web_fetch_enabled", False)),
            "image_upload": bool(config.get("image_upload_enabled", False)),
            "audio_upload": bool(config.get("audio_upload_enabled", False)),
            "multi_turn": bool(config.get("multi_turn_enabled", False)),
        },
    }


def _tool_error(
    tool_name: str,
    operation: str,
    exc: Exception,
    **context: Any,
) -> str:
    if isinstance(exc, ValueError):
        return _missing_config(tool_name, str(exc), **context)
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        logger.error("WeKnora %s returned HTTP %s: %s", operation, status_code, _response_text(exc.response))
        return json.dumps(
            {"error": f"WeKnora API error: HTTP {status_code}", **context},
            ensure_ascii=False,
        )
    logger.error("WeKnora %s failed: %s: %s", operation, type(exc).__name__, exc)
    return json.dumps({"error": str(exc), **context}, ensure_ascii=False)


def _create_session(tool_name: str, query: str) -> str:
    title = query.strip().replace("\n", " ")[:80] or "WeKnora RAG"
    data = _post_json(tool_name, "/sessions", {"title": title, "description": "Created by DeerFlow WeKnora tool"})
    session = data.get("data") if isinstance(data.get("data"), dict) else data
    session_id = session.get("id") if isinstance(session, dict) else None
    if not session_id:
        raise ValueError("WeKnora session creation did not return a session id")
    return str(session_id)


def _response_text(response: httpx.Response) -> str:
    try:
        return response.text
    except httpx.ResponseNotRead:
        try:
            return response.read().decode("utf-8", errors="replace")
        except Exception:
            return ""


@tool("weknora_list_knowledge_bases", parse_docstring=True)
def weknora_list_knowledge_bases_tool(
    knowledge_base_ids: list[str] | None = None,
) -> str:
    """List WeKnora knowledge bases visible to the current user.

    Args:
        knowledge_base_ids: Optional exact knowledge-base IDs to retain. Pass the IDs selected in the current DeerFlow run.
    """
    tool_name = "weknora_list_knowledge_bases"
    try:
        data = _get_json(tool_name, "/knowledge-bases")
    except Exception as exc:
        return _tool_error(tool_name, "knowledge-base list", exc)

    rows = data.get("data") if isinstance(data.get("data"), list) else []
    requested_ids = set(_split_csv(knowledge_base_ids))
    run_scope_ids = _run_scope_effective_kb_ids()
    if get_current_weknora_scope():
        requested_ids = requested_ids.intersection(run_scope_ids) if requested_ids else run_scope_ids
    knowledge_bases = [
        _public_knowledge_base(item)
        for item in rows
        if isinstance(item, dict)
        and (not requested_ids or str(item.get("id") or "") in requested_ids)
    ]
    return json.dumps(
        {"total": len(knowledge_bases), "knowledge_bases": knowledge_bases},
        indent=2,
        ensure_ascii=False,
    )


@tool("weknora_get_knowledge_base", parse_docstring=True)
def weknora_get_knowledge_base_tool(knowledge_base_id: str) -> str:
    """Get read-only metadata and processing counts for one WeKnora knowledge base.

    Args:
        knowledge_base_id: WeKnora knowledge-base ID selected for the current run.
    """
    tool_name = "weknora_get_knowledge_base"
    if not knowledge_base_id.strip():
        return json.dumps({"error": "knowledge_base_id is required"}, ensure_ascii=False)
    if get_current_weknora_scope() and knowledge_base_id.strip() not in _run_scope_effective_kb_ids():
        return _scope_error(
            "knowledge_base_id is outside the knowledge range selected for this run",
            knowledge_base_id=knowledge_base_id,
        )
    try:
        data = _get_json(tool_name, f"/knowledge-bases/{quote(knowledge_base_id.strip(), safe='')}")
    except Exception as exc:
        return _tool_error(tool_name, "knowledge-base detail", exc, knowledge_base_id=knowledge_base_id)

    row = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(row, dict):
        return json.dumps({"error": "Unexpected WeKnora response shape"}, ensure_ascii=False)
    return json.dumps({"knowledge_base": _public_knowledge_base(row)}, indent=2, ensure_ascii=False)


@tool("weknora_list_documents", parse_docstring=True)
def weknora_list_documents_tool(
    knowledge_base_id: str,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
    keyword: str | None = None,
    file_type: str | None = None,
    parse_status: str | None = None,
    source: str | None = None,
) -> str:
    """List documents in one selected WeKnora knowledge base.

    Args:
        knowledge_base_id: WeKnora knowledge-base ID selected for the current run.
        page: One-based result page.
        page_size: Documents per page, capped at 100.
        keyword: Optional title or filename keyword.
        file_type: Optional file type such as pdf, docx, or xlsx.
        parse_status: Optional WeKnora parse status filter.
        source: Optional source filter such as file, url, or manual.
    """
    tool_name = "weknora_list_documents"
    if not knowledge_base_id.strip():
        return json.dumps({"error": "knowledge_base_id is required"}, ensure_ascii=False)
    if get_current_weknora_scope() and knowledge_base_id.strip() not in _run_scope_effective_kb_ids():
        return _scope_error(
            "knowledge_base_id is outside the knowledge range selected for this run",
            knowledge_base_id=knowledge_base_id,
        )
    resolved_page = _coerce_positive_int(page, default=1, maximum=1_000_000)
    resolved_page_size = _coerce_positive_int(page_size, default=_DEFAULT_PAGE_SIZE, maximum=_PAGE_SIZE_CAP)
    params: dict[str, Any] = {"page": resolved_page, "page_size": resolved_page_size}
    for key, value in (
        ("keyword", keyword),
        ("file_type", file_type),
        ("parse_status", parse_status),
        ("source", source),
    ):
        if isinstance(value, str) and value.strip():
            params[key] = value.strip()
    try:
        data = _get_json(
            tool_name,
            f"/knowledge-bases/{quote(knowledge_base_id.strip(), safe='')}/knowledge",
            params=params,
        )
    except Exception as exc:
        return _tool_error(tool_name, "document list", exc, knowledge_base_id=knowledge_base_id)

    rows = data.get("data") if isinstance(data.get("data"), list) else []
    documents = [
        _public_document(item)
        for item in rows
        if isinstance(item, dict) and _document_row_matches_run_scope(item)
    ]
    return json.dumps(
        {
            "knowledge_base_id": knowledge_base_id.strip(),
            "page": int(data.get("page") or resolved_page),
            "page_size": int(data.get("page_size") or resolved_page_size),
            "total": int(data.get("total") or len(documents)),
            "documents": documents,
        },
        indent=2,
        ensure_ascii=False,
    )


@tool("weknora_get_document", parse_docstring=True)
def weknora_get_document_tool(knowledge_id: str) -> str:
    """Get safe read-only metadata for one WeKnora document.

    Args:
        knowledge_id: WeKnora document/knowledge ID from a selected knowledge base.
    """
    tool_name = "weknora_get_document"
    if not knowledge_id.strip():
        return json.dumps({"error": "knowledge_id is required"}, ensure_ascii=False)
    try:
        data = _get_json(tool_name, f"/knowledge/{quote(knowledge_id.strip(), safe='')}")
    except Exception as exc:
        return _tool_error(tool_name, "document detail", exc, knowledge_id=knowledge_id)

    row = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(row, dict):
        return json.dumps({"error": "Unexpected WeKnora response shape"}, ensure_ascii=False)
    if not _document_row_matches_run_scope(row):
        return _scope_error(
            "knowledge_id is outside the knowledge range selected for this run",
            knowledge_id=knowledge_id,
        )
    return json.dumps({"document": _public_document(row)}, indent=2, ensure_ascii=False)


@tool("weknora_list_document_chunks", parse_docstring=True)
def weknora_list_document_chunks_tool(
    knowledge_id: str,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
    chunk_types: list[str] | None = None,
) -> str:
    """List parsed chunks for one WeKnora document with pagination.

    Args:
        knowledge_id: WeKnora document/knowledge ID from a selected knowledge base.
        page: One-based result page.
        page_size: Chunks per page, capped at 100.
        chunk_types: Optional chunk types; defaults to text, image_caption, and image_ocr.
    """
    tool_name = "weknora_list_document_chunks"
    if not knowledge_id.strip():
        return json.dumps({"error": "knowledge_id is required"}, ensure_ascii=False)
    resolved_page = _coerce_positive_int(page, default=1, maximum=1_000_000)
    resolved_page_size = _coerce_positive_int(page_size, default=_DEFAULT_PAGE_SIZE, maximum=_PAGE_SIZE_CAP)
    resolved_types = _split_csv(chunk_types) or ["text", "image_caption", "image_ocr"]
    params: dict[str, Any] = {
        "page": resolved_page,
        "page_size": resolved_page_size,
        "chunk_type": resolved_types,
    }
    try:
        if get_current_weknora_scope():
            document_data = _get_json(tool_name, f"/knowledge/{quote(knowledge_id.strip(), safe='')}")
            document_row = document_data.get("data") if isinstance(document_data.get("data"), dict) else document_data
            if not isinstance(document_row, dict) or not _document_row_matches_run_scope(document_row):
                return _scope_error(
                    "knowledge_id is outside the knowledge range selected for this run",
                    knowledge_id=knowledge_id,
                )
        data = _get_json(
            tool_name,
            f"/chunks/{quote(knowledge_id.strip(), safe='')}",
            params=params,
        )
    except Exception as exc:
        return _tool_error(tool_name, "document chunks", exc, knowledge_id=knowledge_id)

    rows = data.get("data") if isinstance(data.get("data"), list) else []
    chunks = [_public_chunk(item) for item in rows if isinstance(item, dict)]
    chunks.sort(key=lambda item: (item.get("chunk_index") is None, item.get("chunk_index") or 0))
    return json.dumps(
        {
            "knowledge_id": knowledge_id.strip(),
            "page": int(data.get("page") or resolved_page),
            "page_size": int(data.get("page_size") or resolved_page_size),
            "total": int(data.get("total") or len(chunks)),
            "chunks": chunks,
        },
        indent=2,
        ensure_ascii=False,
    )


@tool("weknora_read_document", parse_docstring=True)
def weknora_read_document_tool(
    knowledge_id: str,
    max_chunks: int = _DEFAULT_READ_CHUNKS,
) -> str:
    """Read ordered parsed text/OCR chunks from one WeKnora document.

    Args:
        knowledge_id: WeKnora document/knowledge ID from a selected knowledge base.
        max_chunks: Maximum chunks to read, capped at 300. Use paged chunk listing for larger documents.
    """
    tool_name = "weknora_read_document"
    if not knowledge_id.strip():
        return json.dumps({"error": "knowledge_id is required"}, ensure_ascii=False)
    resolved_max_chunks = _coerce_positive_int(
        max_chunks,
        default=_DEFAULT_READ_CHUNKS,
        maximum=_READ_CHUNKS_CAP,
    )
    try:
        document_data = _get_json(tool_name, f"/knowledge/{quote(knowledge_id.strip(), safe='')}")
        document_row = document_data.get("data") if isinstance(document_data.get("data"), dict) else document_data
        if not isinstance(document_row, dict):
            raise ValueError("Unexpected WeKnora document response shape")
        if not _document_row_matches_run_scope(document_row):
            return _scope_error(
                "knowledge_id is outside the knowledge range selected for this run",
                knowledge_id=knowledge_id,
            )

        chunks: list[dict[str, Any]] = []
        page = 1
        while len(chunks) < resolved_max_chunks:
            page_size = min(_PAGE_SIZE_CAP, resolved_max_chunks - len(chunks))
            chunk_data = _get_json(
                tool_name,
                f"/chunks/{quote(knowledge_id.strip(), safe='')}",
                params={
                    "page": page,
                    "page_size": page_size,
                    "chunk_type": ["text", "image_caption", "image_ocr"],
                },
            )
            rows = chunk_data.get("data") if isinstance(chunk_data.get("data"), list) else []
            chunks.extend(_public_chunk(item) for item in rows if isinstance(item, dict))
            total = int(chunk_data.get("total") or len(chunks))
            if not rows or len(chunks) >= total:
                break
            page += 1
    except Exception as exc:
        return _tool_error(tool_name, "document read", exc, knowledge_id=knowledge_id)

    chunks = chunks[:resolved_max_chunks]
    chunks.sort(key=lambda item: (item.get("chunk_index") is None, item.get("chunk_index") or 0))
    joined = "\n\n".join(str(item.get("content") or "") for item in chunks if item.get("content"))
    truncated = len(joined) > _DOCUMENT_CHAR_LIMIT
    content = joined[:_DOCUMENT_CHAR_LIMIT]
    return json.dumps(
        {
            "document": _public_document(document_row),
            "chunk_count": len(chunks),
            "content": content,
            "content_truncated": truncated,
            "chunks": chunks,
        },
        indent=2,
        ensure_ascii=False,
    )


@tool("weknora_search", parse_docstring=True)
def weknora_search_tool(
    query: str,
    knowledge_base_id: str | None = None,
    knowledge_base_ids: list[str] | None = None,
    knowledge_ids: list[str] | None = None,
    max_results: int | None = None,
) -> str:
    """Search WeKnora knowledge bases and return source chunks without LLM summarization.

    Args:
        query: Search query text. Use the user's question or a focused keyword query.
        knowledge_base_id: Optional single WeKnora knowledge base ID to search.
        knowledge_base_ids: Optional list of WeKnora knowledge base IDs for cross-knowledge-base search.
        knowledge_ids: Optional list of WeKnora knowledge/file IDs to restrict the search.
        max_results: Maximum number of chunks to return to DeerFlow. Defaults to the tool config or 8.
    """
    tool_name = "weknora_search"
    if not query.strip():
        return json.dumps({"error": "query is required"}, ensure_ascii=False)

    configured_max_results = _tool_extra(tool_name).get("max_results")
    result_limit = _coerce_max_results(max_results if max_results is not None else configured_max_results)
    payload = {"query": query, **_build_scope(tool_name, knowledge_base_id=knowledge_base_id, knowledge_base_ids=knowledge_base_ids, knowledge_ids=knowledge_ids)}

    if not _scope_has_target(payload):
        return _missing_config(
            tool_name,
            "WeKnora knowledge scope is not configured. Provide knowledge_base_id, knowledge_base_ids, knowledge_ids, or set WEKNORA_KNOWLEDGE_BASE_IDS.",
            query=query,
        )

    try:
        data = _post_json(tool_name, "/knowledge-search", payload)
    except ValueError as e:
        return _missing_config(tool_name, str(e), query=query)
    except httpx.HTTPStatusError as e:
        logger.error("WeKnora search returned HTTP %s: %s", e.response.status_code, e.response.text)
        return json.dumps({"error": f"WeKnora API error: HTTP {e.response.status_code}", "query": query}, ensure_ascii=False)
    except Exception as e:
        logger.error("WeKnora search failed: %s: %s", type(e).__name__, e)
        return json.dumps({"error": str(e), "query": query}, ensure_ascii=False)

    return _format_search_results(query, data, result_limit, payload)


def _iter_sse_events(response: httpx.Response) -> Iterable[dict[str, Any]]:
    for line in response.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        if not line.startswith("data:"):
            continue
        raw = line.removeprefix("data:").strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Skipping non-JSON WeKnora SSE payload: %s", raw[:200])
            continue
        if isinstance(event, dict):
            yield event


def _normalize_reference(ref: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
    citations = _citation_fields(ref, scope)
    resource_images = _resource_images(ref, scope)
    return {
        "content": _replace_resource_uris(
            str(ref.get("content") or ""),
            resource_images,
        ),
        "score": ref.get("score"),
        "knowledge_id": ref.get("knowledge_id", ""),
        "knowledge_base_id": ref.get("knowledge_base_id", ""),
        "knowledge_title": ref.get("knowledge_title", ""),
        "knowledge_filename": ref.get("knowledge_filename", ""),
        "chunk_id": ref.get("id", ""),
        "chunk_index": ref.get("chunk_index"),
        "chunk_type": ref.get("chunk_type", ""),
        "resource_images": resource_images,
        **citations,
    }


def _stream_chat(
    tool_name: str,
    payload: dict[str, Any],
    session_id: str,
    *,
    agent_mode: bool,
) -> dict[str, Any]:
    api_key = _get_api_key(tool_name)
    if not api_key and not get_current_weknora_access_token():
        raise ValueError("WEKNORA_API_KEY is not configured")

    chat_path = "agent-chat" if agent_mode else "knowledge-chat"
    url = _endpoint(_get_api_url(tool_name), f"/{chat_path}/{quote(session_id, safe='')}")
    answer_parts: list[str] = []
    references: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    pending_actions: list[dict[str, Any]] = []
    errors: list[str] = []

    with httpx.Client(timeout=_get_timeout(tool_name)) as client:
        with client.stream("POST", url, headers=_headers(api_key), json=payload) as response:
            if response.is_error:
                response.read()
            response.raise_for_status()
            for event in _iter_sse_events(response):
                response_type = event.get("response_type")
                if response_type == "references":
                    for ref in event.get("knowledge_references") or []:
                        if isinstance(ref, dict):
                            references.append(_normalize_reference(ref, payload))
                elif response_type == "answer":
                    answer_parts.append(str(event.get("content") or ""))
                elif response_type == "tool_call":
                    event_data = event.get("data") if isinstance(event.get("data"), dict) else {}
                    tool_name_value = event_data.get("tool_name") or event_data.get("name")
                    tool_call: dict[str, Any] = {
                        "tool_name": str(tool_name_value or "unknown"),
                    }
                    arguments = event_data.get("arguments")
                    if isinstance(arguments, dict):
                        tool_call["arguments"] = arguments
                    tool_calls.append(tool_call)
                elif response_type == "tool_result":
                    event_data = event.get("data") if isinstance(event.get("data"), dict) else {}
                    tool_name_value = event_data.get("tool_name") or event_data.get("name")
                    tool_results.append(
                        {
                            "tool_name": str(tool_name_value or "unknown"),
                            "content": str(event.get("content") or "")[:_TOOL_EVENT_CONTENT_LIMIT],
                        }
                    )
                elif response_type in {
                    "tool_approval_required",
                    "mcp_oauth_required",
                }:
                    pending_actions.append(
                        {
                            "type": str(response_type),
                            "content": str(event.get("content") or ""),
                            "data": event.get("data") if isinstance(event.get("data"), dict) else {},
                        }
                    )
                elif response_type == "error":
                    error_text = str(event.get("content") or "WeKnora agent stream failed")
                    errors.append(error_text[:_TOOL_EVENT_CONTENT_LIMIT])

    raw_answer = "".join(answer_parts)[:_ANSWER_CHAR_LIMIT]
    all_resource_images = [image for reference in references for image in reference.get("resource_images") or [] if isinstance(image, dict)]
    answer = _replace_resource_uris(raw_answer, all_resource_images)
    result: dict[str, Any] = {
        "answer": normalize_weknora_citations(answer, references),
        "references": references,
        "answer_images": _answer_resource_images(
            raw_answer,
            answer,
            references,
        ),
    }
    if agent_mode:
        result.update(
            {
                "tool_calls": tool_calls,
                "tool_results": tool_results,
                "pending_actions": pending_actions,
            }
        )
    if errors:
        result["errors"] = errors
    return result


@tool("weknora_list_agents", parse_docstring=True)
def weknora_list_agents_tool(include_disabled: bool = True) -> str:
    """List WeKnora built-in and custom agents available to the current user.

    The result exposes capability flags and tool names but deliberately omits
    private system prompts and credentials.

    Args:
        include_disabled: Include agents disabled in the current user's conversation picker.
    """
    tool_name = "weknora_list_agents"
    try:
        data = _get_json(tool_name, "/agents")
    except Exception as exc:
        return _tool_error(tool_name, "agent list", exc)

    rows = data.get("data") if isinstance(data.get("data"), list) else []
    disabled_ids = set(_split_csv(data.get("disabled_own_agent_ids")))
    agents = [
        _public_agent(item, disabled_ids)
        for item in rows
        if isinstance(item, dict)
        and (include_disabled or str(item.get("id") or "") not in disabled_ids)
    ]
    return json.dumps({"total": len(agents), "agents": agents}, indent=2, ensure_ascii=False)


@tool("weknora_invoke_agent", parse_docstring=True)
def weknora_invoke_agent_tool(
    agent_id: str,
    query: str,
    knowledge_base_ids: list[str] | None = None,
    knowledge_ids: list[str] | None = None,
    session_id: str | None = None,
    web_search_enabled: bool = False,
) -> str:
    """Run a selected WeKnora agent and return its answer, citations, and tool receipts.

    Built-in agents can invoke WeKnora's retrieval, graph, document inspection,
    data-analysis, and Wiki tools according to their own configuration. Invoke a
    mutation-capable agent only when the user explicitly requests that action.
    Human approval or MCP authorization events are returned as pending actions;
    this tool never auto-approves them.

    Args:
        agent_id: Exact enabled WeKnora agent ID returned by weknora_list_agents.
        query: User request for the selected WeKnora agent.
        knowledge_base_ids: Exact WeKnora knowledge-base IDs selected for this DeerFlow run.
        knowledge_ids: Optional document IDs within the selected knowledge bases.
        session_id: Optional WeKnora session ID; a new session is created when omitted.
        web_search_enabled: Allow the WeKnora agent to use its configured web-search provider.
    """
    tool_name = "weknora_invoke_agent"
    if not agent_id.strip():
        return json.dumps({"error": "agent_id is required"}, ensure_ascii=False)
    if not query.strip():
        return json.dumps({"error": "query is required"}, ensure_ascii=False)

    scope = _build_scope(
        tool_name,
        knowledge_base_id=None,
        knowledge_base_ids=knowledge_base_ids,
        knowledge_ids=knowledge_ids,
    )
    kb_ids = _scope_knowledge_base_ids(scope)
    payload: dict[str, Any] = {
        "query": query,
        **({"knowledge_base_ids": kb_ids} if kb_ids else {}),
        **({"knowledge_ids": scope["knowledge_ids"]} if scope.get("knowledge_ids") else {}),
        **({"mentioned_items": scope["mentioned_items"]} if scope.get("mentioned_items") else {}),
        **({"tag_ids": scope["tag_ids"]} if scope.get("tag_ids") else {}),
        "agent_id": agent_id.strip(),
        "agent_enabled": True,
        "web_search_enabled": bool(web_search_enabled),
    }
    if not _scope_has_target(scope):
        return _missing_config(
            tool_name,
            "WeKnora knowledge scope is not configured. Pass the knowledge_base_ids selected in the current DeerFlow run.",
            query=query,
            agent_id=agent_id,
        )

    try:
        resolved_session_id = session_id or _create_session(tool_name, query)
        result = _stream_chat(
            tool_name,
            payload,
            resolved_session_id,
            agent_mode=True,
        )
    except Exception as exc:
        return _tool_error(tool_name, "agent chat", exc, query=query, agent_id=agent_id)

    return json.dumps(
        {
            "query": query,
            "agent_id": agent_id.strip(),
            "session_id": resolved_session_id,
            **result,
        },
        indent=2,
        ensure_ascii=False,
    )


@tool("weknora_ask", parse_docstring=True)
def weknora_ask_tool(
    query: str,
    knowledge_base_id: str | None = None,
    knowledge_base_ids: list[str] | None = None,
    knowledge_ids: list[str] | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Ask WeKnora to answer with RAG over selected knowledge bases and return citations.

    Args:
        query: User question to answer using WeKnora knowledge.
        knowledge_base_id: Optional single WeKnora knowledge base ID to search.
        knowledge_base_ids: Optional list of WeKnora knowledge base IDs for cross-knowledge-base RAG.
        knowledge_ids: Optional list of WeKnora knowledge/file IDs to restrict the answer.
        agent_id: Optional WeKnora agent ID. Defaults to the tool config or WEKNORA_AGENT_ID.
        session_id: Optional WeKnora chat session ID. A new WeKnora session is created when omitted.
    """
    tool_name = "weknora_ask"
    if not query.strip():
        return json.dumps({"error": "query is required"}, ensure_ascii=False)

    scope = _build_scope(
        tool_name,
        knowledge_base_id=knowledge_base_id,
        knowledge_base_ids=knowledge_base_ids,
        knowledge_ids=knowledge_ids,
    )
    payload: dict[str, Any] = {
        "query": query,
        **scope,
    }
    resolved_agent_id = agent_id or _get_string(tool_name, "agent_id", "WEKNORA_AGENT_ID")
    if resolved_agent_id:
        resolved_kb_ids = _scope_knowledge_base_ids(scope)
        payload.pop("knowledge_base_id", None)
        if resolved_kb_ids:
            payload["knowledge_base_ids"] = resolved_kb_ids
        payload.update(
            {
                "agent_id": resolved_agent_id,
                "agent_enabled": True,
                "web_search_enabled": False,
            }
        )

    if not _scope_has_target(payload):
        return _missing_config(
            tool_name,
            "WeKnora knowledge scope is not configured. Provide knowledge_base_id, knowledge_base_ids, knowledge_ids, or set WEKNORA_KNOWLEDGE_BASE_IDS.",
            query=query,
        )

    try:
        resolved_session_id = session_id or _create_session(tool_name, query)
        result = _stream_chat(
            tool_name,
            payload,
            resolved_session_id,
            agent_mode=bool(resolved_agent_id),
        )
    except ValueError as e:
        return _missing_config(tool_name, str(e), query=query)
    except httpx.HTTPStatusError as e:
        logger.error("WeKnora chat returned HTTP %s: %s", e.response.status_code, _response_text(e.response))
        return json.dumps({"error": f"WeKnora API error: HTTP {e.response.status_code}", "query": query}, ensure_ascii=False)
    except Exception as e:
        logger.error("WeKnora chat failed: %s: %s", type(e).__name__, e)
        return json.dumps({"error": str(e), "query": query}, ensure_ascii=False)

    return json.dumps({"query": query, "session_id": resolved_session_id, **result}, indent=2, ensure_ascii=False)
