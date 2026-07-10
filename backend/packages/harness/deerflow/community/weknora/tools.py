"""WeKnora knowledge search and RAG tools.

WeKnora is treated as an external knowledge service. DeerFlow keeps agent
orchestration in-process and calls WeKnora for document retrieval/RAG so the
knowledge storage, parsing, permissions, and indexing remain owned by WeKnora.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from typing import Any

import httpx
from langchain.tools import tool

from deerflow.config import get_app_config
from deerflow.runtime.weknora_context import get_current_weknora_access_token

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "http://localhost:8080"
_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_MAX_RESULTS = 8
_MAX_RESULTS_CAP = 30
_ANSWER_CHAR_LIMIT = 24_000
_missing_config_warned: set[str] = set()


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


def _format_search_results(query: str, data: dict[str, Any], max_results: int) -> str:
    results = data.get("data") or []
    if not isinstance(results, list):
        return json.dumps({"query": query, "error": "Unexpected WeKnora response shape", "raw": data}, ensure_ascii=False)

    normalized = []
    for item in results[:max_results]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "content": item.get("content", ""),
                "score": item.get("score"),
                "knowledge_id": item.get("knowledge_id", ""),
                "knowledge_title": item.get("knowledge_title", ""),
                "knowledge_filename": item.get("knowledge_filename", ""),
                "chunk_id": item.get("id", ""),
                "chunk_index": item.get("chunk_index"),
                "chunk_type": item.get("chunk_type", ""),
                "source": item.get("knowledge_source", ""),
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

    if not any(key in payload for key in ("knowledge_base_id", "knowledge_base_ids", "knowledge_ids")):
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

    return _format_search_results(query, data, result_limit)


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


def _stream_knowledge_chat(tool_name: str, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
    api_key = _get_api_key(tool_name)
    if not api_key and not get_current_weknora_access_token():
        raise ValueError("WEKNORA_API_KEY is not configured")

    url = _endpoint(_get_api_url(tool_name), f"/knowledge-chat/{session_id}")
    answer_parts: list[str] = []
    references: list[dict[str, Any]] = []

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
                            references.append(
                                {
                                    "content": ref.get("content", ""),
                                    "score": ref.get("score"),
                                    "knowledge_id": ref.get("knowledge_id", ""),
                                    "knowledge_title": ref.get("knowledge_title", ""),
                                    "knowledge_filename": ref.get("knowledge_filename", ""),
                                    "chunk_id": ref.get("id", ""),
                                    "chunk_index": ref.get("chunk_index"),
                                    "chunk_type": ref.get("chunk_type", ""),
                                }
                            )
                elif response_type == "answer":
                    answer_parts.append(str(event.get("content") or ""))

    return {
        "answer": "".join(answer_parts)[:_ANSWER_CHAR_LIMIT],
        "references": references,
    }


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

    payload = {
        "query": query,
        **_build_scope(tool_name, knowledge_base_id=knowledge_base_id, knowledge_base_ids=knowledge_base_ids, knowledge_ids=knowledge_ids),
    }
    resolved_agent_id = agent_id or _get_string(tool_name, "agent_id", "WEKNORA_AGENT_ID")
    if resolved_agent_id:
        payload["agent_id"] = resolved_agent_id

    if not any(key in payload for key in ("knowledge_base_id", "knowledge_base_ids", "knowledge_ids")):
        return _missing_config(
            tool_name,
            "WeKnora knowledge scope is not configured. Provide knowledge_base_id, knowledge_base_ids, knowledge_ids, or set WEKNORA_KNOWLEDGE_BASE_IDS.",
            query=query,
        )

    try:
        resolved_session_id = session_id or _create_session(tool_name, query)
        result = _stream_knowledge_chat(tool_name, payload, resolved_session_id)
    except ValueError as e:
        return _missing_config(tool_name, str(e), query=query)
    except httpx.HTTPStatusError as e:
        logger.error("WeKnora chat returned HTTP %s: %s", e.response.status_code, _response_text(e.response))
        return json.dumps({"error": f"WeKnora API error: HTTP {e.response.status_code}", "query": query}, ensure_ascii=False)
    except Exception as e:
        logger.error("WeKnora chat failed: %s: %s", type(e).__name__, e)
        return json.dumps({"error": str(e), "query": query}, ensure_ascii=False)

    return json.dumps({"query": query, "session_id": resolved_session_id, **result}, indent=2, ensure_ascii=False)
