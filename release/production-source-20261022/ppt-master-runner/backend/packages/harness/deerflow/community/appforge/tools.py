"""Secure CNOOC DataLake adapters for question answering.

``datalake_smart_qa`` uses the DataLake Retrieve API and returns only ranked
source chunks for the DeerFlow model to analyze. The metrics and detail tools
continue to use the internal AppForge SSE API. Credentials and workflow identity
inputs are resolved only from server-side configuration; the model-visible tool
schema contains only the business question.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from langchain.tools import tool

from deerflow.config import get_app_config
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "http://10.77.124.142:9080"
_DEFAULT_TIMEOUT_S = 180.0
_DEFAULT_RETRIEVE_API_URL = "http://10.77.124.142:15955/rag/internal/v1/Retrieve"
_DEFAULT_RETRIEVE_TIMEOUT_S = 30.0
_OUTPUT_CHAR_LIMIT = 10_000
_ANSWER_CHAR_LIMIT = 5_000
_KNOWLEDGE_CHAR_LIMIT = 600
_MAX_KNOWLEDGE_ITEMS = 5
_MAX_PROGRESS_ITEMS = 10
_PROGRESS_CHAR_LIMIT = 1_200
_MAX_THINKING_ITEMS = 4
_THINKING_CHAR_LIMIT = 400
_RETRIEVE_CHUNK_CHAR_LIMIT = 3_000
_MAX_RETRIEVE_CHUNKS = 10

# AppForge can return image-preview URLs whose paths contain Chinese document
# names and spaces. Normalize only the reviewed source URL, then point Markdown
# at the authenticated same-origin Gateway proxy. Browsers must not load the raw
# HTTP-only DataLake endpoint from an HTTPS DeerFlow page.
_IMAGE_PREVIEW_URL_RE = re.compile(
    r"https?://[^\s<>'\"`]+/img-preview/[^\r\n<>'\"`]+?"
    r"\.(?:avif|gif|jpe?g|png|webp)(?:[?#][^\s<>'\"`]*)?",
    re.IGNORECASE,
)
_IMAGE_PREVIEW_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_IMAGE_PREVIEW_QUERY_SAFE = "=&%:/?@!$'()*+,;,-._~"
_IMAGE_PREVIEW_SCHEME = "http"
_IMAGE_PREVIEW_HOST = "10.77.124.142"
_IMAGE_PREVIEW_PORT = 30900
_IMAGE_PREVIEW_PROXY_PATH = "/api/datalake/image-preview?src="
_missing_config_warned: set[str] = set()

_TOOL_DEFAULTS: dict[str, dict[str, str]] = {
    "datalake_smart_qa": {
        "app_id": "app-p2cpu4dn",
        "app_id_env": "APPFORGE_SMART_QA_APP_ID",
        "conversation_prefix": "ApiKeyAgent_",
        "conversation_prefix_env": "APPFORGE_SMART_QA_CONVERSATION_PREFIX",
        "timeout_env": "APPFORGE_SMART_QA_TIMEOUT_SECONDS",
        "hytoken_env": "APPFORGE_SMART_QA_HYTOKEN",
        "hyaccount_env": "APPFORGE_SMART_QA_HYACCOUNT",
    },
    "datalake_metrics_qa": {
        "app_id": "app-n9ahac7g",
        "app_id_env": "APPFORGE_METRICS_QA_APP_ID",
        "conversation_prefix": "WorkflowAgent_",
        "conversation_prefix_env": "APPFORGE_METRICS_QA_CONVERSATION_PREFIX",
        "timeout_env": "APPFORGE_METRICS_QA_TIMEOUT_SECONDS",
        "hytoken_env": "APPFORGE_METRICS_QA_HYTOKEN",
        "hyaccount_env": "APPFORGE_METRICS_QA_HYACCOUNT",
    },
    "datalake_detail_qa": {
        "app_id": "app-5md031ia",
        "app_id_env": "APPFORGE_DETAIL_QA_APP_ID",
        "conversation_prefix": "WorkflowAgent_",
        "conversation_prefix_env": "APPFORGE_DETAIL_QA_CONVERSATION_PREFIX",
        "timeout_env": "APPFORGE_DETAIL_QA_TIMEOUT_SECONDS",
        "hytoken_env": "APPFORGE_DETAIL_QA_HYTOKEN",
        "hyaccount_env": "APPFORGE_DETAIL_QA_HYACCOUNT",
    },
}


def _tool_defaults(tool_name: str) -> dict[str, str]:
    try:
        return _TOOL_DEFAULTS[tool_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported AppForge tool: {tool_name}") from exc


def _tool_extra(tool_name: str) -> dict[str, Any]:
    config = get_app_config().get_tool_config(tool_name)
    if config is None:
        return {}
    extra = config.model_extra or {}
    return extra if isinstance(extra, dict) else {}


def _get_string(
    tool_name: str,
    key: str,
    env_names: str | Iterable[str] | None = None,
    default: str | None = None,
) -> str | None:
    value = _tool_extra(tool_name).get(key) if key else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(env_names, str):
        env_names = (env_names,)
    for env_name in env_names or ():
        env_value = os.getenv(env_name)
        if env_value and env_value.strip():
            return env_value.strip()
    return default


def _get_bool(tool_name: str, key: str, env_name: str, default: bool) -> bool:
    value = _tool_extra(tool_name).get(key)
    if value is None:
        value = os.getenv(env_name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(tool_name: str, key: str, env_name: str, default: int, *, minimum: int = 1, maximum: int = 50) -> int:
    value = _tool_extra(tool_name).get(key)
    if value is None:
        value = os.getenv(env_name)
    try:
        return min(maximum, max(minimum, int(value))) if value is not None else default
    except (TypeError, ValueError):
        logger.warning("Invalid DataLake Retrieve %s for %s; using %s", key, tool_name, default)
        return default


def _get_float(tool_name: str, key: str, env_name: str, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    value = _tool_extra(tool_name).get(key)
    if value is None:
        value = os.getenv(env_name)
    try:
        return min(maximum, max(minimum, float(value))) if value is not None else default
    except (TypeError, ValueError):
        logger.warning("Invalid DataLake Retrieve %s for %s; using %s", key, tool_name, default)
        return default


def _get_string_list(tool_name: str, key: str, env_name: str) -> list[str]:
    value = _tool_extra(tool_name).get(key)
    if value is None:
        value = os.getenv(env_name)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _get_timeout(tool_name: str) -> float:
    defaults = _tool_defaults(tool_name)
    value = _tool_extra(tool_name).get("timeout_seconds")
    if value is None:
        value = _get_string(
            tool_name,
            "",
            (defaults["timeout_env"], "APPFORGE_TIMEOUT_SECONDS"),
        )
    try:
        return max(1.0, float(value) if value is not None else _DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid AppForge timeout for %s; using %.1f seconds",
            tool_name,
            _DEFAULT_TIMEOUT_S,
        )
        return _DEFAULT_TIMEOUT_S


def _retrieve_timeout(tool_name: str) -> float:
    value = _tool_extra(tool_name).get("retrieve_timeout_seconds")
    if value is None:
        value = os.getenv("DATALAKE_RETRIEVE_TIMEOUT_SECONDS")
    try:
        return max(1.0, float(value) if value is not None else _DEFAULT_RETRIEVE_TIMEOUT_S)
    except (TypeError, ValueError):
        logger.warning("Invalid DataLake Retrieve timeout for %s; using %.1f seconds", tool_name, _DEFAULT_RETRIEVE_TIMEOUT_S)
        return _DEFAULT_RETRIEVE_TIMEOUT_S


def _api_url(tool_name: str) -> str:
    value = _get_string(tool_name, "api_url", "APPFORGE_API_URL", _DEFAULT_API_URL)
    return (value or _DEFAULT_API_URL).rstrip("/")


def _app_id(tool_name: str) -> str:
    defaults = _tool_defaults(tool_name)
    return _get_string(tool_name, "app_id", defaults["app_id_env"], defaults["app_id"]) or defaults["app_id"]


def _api_key(tool_name: str) -> str | None:
    return _get_string(tool_name, "api_key", "APPFORGE_API_KEY")


def _conversation_prefix(tool_name: str) -> str:
    defaults = _tool_defaults(tool_name)
    return (
        _get_string(
            tool_name,
            "conversation_prefix",
            defaults["conversation_prefix_env"],
            defaults["conversation_prefix"],
        )
        or defaults["conversation_prefix"]
    )


def _runtime_thread_id(runtime: object | None) -> str:
    context = getattr(runtime, "context", None)
    if isinstance(context, dict) and context.get("thread_id"):
        return str(context["thread_id"])
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        configurable = config.get("configurable")
        if isinstance(configurable, dict) and configurable.get("thread_id"):
            return str(configurable["thread_id"])
    return ""


def _conversation_id(tool_name: str, runtime: object | None) -> str:
    """Derive an opaque, stable AppForge conversation id for one tool/thread."""

    user_id = resolve_runtime_user_id(runtime)
    thread_id = _runtime_thread_id(runtime)
    if thread_id:
        seed = f"{tool_name}:{user_id}:{thread_id}"
        identifier = uuid.uuid5(uuid.NAMESPACE_URL, seed)
    else:
        identifier = uuid.uuid4()
    return f"{_conversation_prefix(tool_name)}{identifier}"


def _get_inputs(tool_name: str) -> dict[str, str]:
    defaults = _tool_defaults(tool_name)
    extra = _tool_extra(tool_name)
    configured = extra.get("inputs")
    inputs = configured if isinstance(configured, dict) else {}
    hytoken = _get_string(
        tool_name,
        "hytoken",
        (defaults["hytoken_env"], "APPFORGE_HYTOKEN"),
        "",
    )
    hyaccount = _get_string(
        tool_name,
        "hyaccount",
        (defaults["hyaccount_env"], "APPFORGE_HYACCOUNT"),
        "",
    )
    return {
        **{str(key): str(value) for key, value in inputs.items() if value is not None},
        "hytoken": hytoken or "",
        "hyaccount": hyaccount or "",
    }


def _headers(api_key: str) -> dict[str, str]:
    authorization = api_key if api_key.lower().startswith("bearer ") else f"Bearer {api_key}"
    return {
        "Authorization": authorization,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


def _iter_sse_events(response: httpx.Response) -> Iterable[dict[str, Any]]:
    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        if not line or not line.startswith("data:"):
            continue
        raw = line.removeprefix("data:").strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Skipping malformed AppForge SSE event")
            continue
        if isinstance(event, dict):
            yield event


def _parse_knowledge(content: Any) -> list[dict[str, str]]:
    if isinstance(content, str):
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = [{"content": content}]
    else:
        data = content
    if not isinstance(data, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in data[:_MAX_KNOWLEDGE_ITEMS]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "chunk_id": str(item.get("chunk_id") or item.get("id") or ""),
                "document_name": str(item.get("document_name") or item.get("knowledge_filename") or item.get("knowledge_title") or ""),
                "content": str(item.get("content") or "")[:_KNOWLEDGE_CHAR_LIMIT],
            }
        )
    return normalized


def _workflow_text(event: dict[str, Any]) -> tuple[str, str]:
    data = event.get("data")
    if not isinstance(data, dict):
        return "", ""
    text = data.get("text")
    if text is None:
        text = data.get("answer") or data.get("content")
    return str(text or ""), str(data.get("node_type") or "")


def _stream_invoke(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    api_key = _api_key(tool_name)
    if not api_key:
        raise ValueError("missing_api_key")

    answer_parts: list[str] = []
    workflow_answer_parts: list[str] = []
    progress: list[str] = []
    thinking: list[str] = []
    knowledge: list[dict[str, str]] = []
    interrupt: dict[str, Any] | None = None
    usage: dict[str, Any] = {}
    status = ""

    endpoint = f"{_api_url(tool_name)}/appforge/openapi/v1/InvokeApp/{payload['id']}"
    with httpx.Client(
        timeout=_get_timeout(tool_name),
        trust_env=_get_bool(tool_name, "trust_env", "APPFORGE_TRUST_ENV", False),
    ) as client:
        with client.stream(
            "POST",
            endpoint,
            headers=_headers(api_key),
            json=payload,
        ) as response:
            if response.is_error:
                response.read()
            response.raise_for_status()
            for event in _iter_sse_events(response):
                extra = event.get("xx_ceai_extra_data")
                if isinstance(extra, dict):
                    usage = {key: extra.get(key) for key in ("inputToken", "outputToken", "totalToken") if extra.get(key) is not None}
                    status = str(extra.get("status") or status)

                event_type = event.get("type")
                workflow_event = event.get("event")
                if event_type == "thinking":
                    text = str(event.get("content") or "").strip()
                    if text:
                        thinking.append(text[:_THINKING_CHAR_LIMIT])
                elif event_type == "knowledge":
                    knowledge.extend(_parse_knowledge(event.get("content")))
                elif event_type == "answer":
                    answer_parts.append(str(event.get("content") or ""))
                elif event_type == "done":
                    status = status or "done"
                elif workflow_event == "answer":
                    text, node_type = _workflow_text(event)
                    if text.strip():
                        progress.append(text.strip())
                        if node_type == "end":
                            workflow_answer_parts.append(text)
                elif workflow_event == "workflow_interrupt":
                    data = event.get("data")
                    if isinstance(data, dict):
                        outputs = data.get("outputs")
                        interrupt = outputs if isinstance(outputs, dict) else data
                        status = str(data.get("status") or "interrupted")
                    else:
                        status = "interrupted"
                elif workflow_event == "workflow_finished":
                    data = event.get("data")
                    if isinstance(data, dict):
                        outputs = data.get("outputs")
                        if isinstance(outputs, dict) and outputs.get("output"):
                            answer_parts.append(str(outputs["output"]))
                        status = str(data.get("status") or status or "finished")

    answer = "".join(answer_parts).strip() or "".join(workflow_answer_parts).strip()
    truncated = len(answer) > _ANSWER_CHAR_LIMIT or len(knowledge) > _MAX_KNOWLEDGE_ITEMS
    answer = answer[:_ANSWER_CHAR_LIMIT]
    progress_text = "\n".join(progress[-_MAX_PROGRESS_ITEMS:])[-_PROGRESS_CHAR_LIMIT:]
    return {
        "answer": answer,
        "knowledge": knowledge[:_MAX_KNOWLEDGE_ITEMS],
        "thinking": thinking[-_MAX_THINKING_ITEMS:],
        "progress": progress_text,
        "interrupt": interrupt,
        "status": status,
        "usage": usage,
        "truncated": truncated,
    }


def _encoded_length(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, indent=2))


def _fit_output_budget(result: dict[str, Any]) -> dict[str, Any]:
    """Keep a valid JSON envelope inline and below the global tool budget."""

    fitted = deepcopy(result)
    if _encoded_length(fitted) <= _OUTPUT_CHAR_LIMIT:
        return fitted
    fitted["truncated"] = True
    fitted["thinking"] = []
    fitted["progress"] = str(fitted.get("progress") or "")[:400]
    fitted["usage"] = {}
    if _encoded_length(fitted) <= _OUTPUT_CHAR_LIMIT:
        return fitted
    knowledge = fitted.get("knowledge")
    if isinstance(knowledge, list):
        for item in knowledge:
            if isinstance(item, dict):
                item["content"] = str(item.get("content") or "")[:200]
    fitted["answer"] = str(fitted.get("answer") or "")[:3_000]
    fitted["interrupt"] = None
    while isinstance(fitted.get("knowledge"), list) and _encoded_length(fitted) > _OUTPUT_CHAR_LIMIT:
        fitted["knowledge"].pop()
    if _encoded_length(fitted) > _OUTPUT_CHAR_LIMIT:
        fitted["answer"] = str(fitted.get("answer") or "")[:1_000]
    return fitted


def _encode_image_preview_url(match: re.Match[str]) -> str:
    """Encode and proxy only the reviewed DataLake image-preview origin."""

    raw_url = match.group(0)
    try:
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme.lower() != _IMAGE_PREVIEW_SCHEME
            or parsed.hostname != _IMAGE_PREVIEW_HOST
            or parsed.port != _IMAGE_PREVIEW_PORT
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path.startswith("/img-preview/")
        ):
            return raw_url
        normalized = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                quote(parsed.path, safe=_IMAGE_PREVIEW_PATH_SAFE),
                quote(parsed.query, safe=_IMAGE_PREVIEW_QUERY_SAFE),
                quote(parsed.fragment, safe=_IMAGE_PREVIEW_QUERY_SAFE),
            )
        )
        return _IMAGE_PREVIEW_PROXY_PATH + quote(normalized, safe="")
    except ValueError:
        return raw_url


def _normalize_image_preview_urls(value: Any) -> Any:
    """Normalize image-preview URLs anywhere in a structured tool result."""

    if isinstance(value, str):
        return _IMAGE_PREVIEW_URL_RE.sub(_encode_image_preview_url, value)
    if isinstance(value, list):
        return [_normalize_image_preview_urls(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_image_preview_urls(item) for key, item in value.items()}
    return value


def _safe_json(value: dict[str, Any]) -> str:
    return json.dumps(_fit_output_budget(_normalize_image_preview_urls(value)), ensure_ascii=False, indent=2)


def _retrieve_config_error(tool_name: str) -> str | None:
    if not _get_string(tool_name, "retrieve_api_url", "DATALAKE_RETRIEVE_API_URL", _DEFAULT_RETRIEVE_API_URL):
        return "missing_retrieve_api_url"
    if not _get_string(tool_name, "project_id", "DATALAKE_RETRIEVE_PROJECT_ID"):
        return "missing_retrieve_project_id"
    if not _get_string_list(tool_name, "document_store_ids", "DATALAKE_RETRIEVE_DOCUMENT_STORE_IDS"):
        return "missing_retrieve_document_store_ids"
    return None


def _retrieve_payload(tool_name: str, query: str) -> dict[str, Any]:
    retrieval_type = (_get_string(tool_name, "retrieve_type", "DATALAKE_RETRIEVE_TYPE", "Hybrid") or "Hybrid").title()
    if retrieval_type not in {"Hybrid", "Fulltext", "Vector"}:
        raise ValueError("invalid_retrieve_type")
    retrieval_type = "FullText" if retrieval_type == "Fulltext" else retrieval_type
    score_threshold_enabled = _get_bool(tool_name, "score_threshold_enabled", "DATALAKE_RETRIEVE_SCORE_THRESHOLD_ENABLED", False)
    payload: dict[str, Any] = {
        "projectID": _get_string(tool_name, "project_id", "DATALAKE_RETRIEVE_PROJECT_ID"),
        "documentStoreIDs": _get_string_list(tool_name, "document_store_ids", "DATALAKE_RETRIEVE_DOCUMENT_STORE_IDS"),
        "documentIDs": _get_string_list(tool_name, "document_ids", "DATALAKE_RETRIEVE_DOCUMENT_IDS"),
        "query": query.strip(),
        "type": retrieval_type,
        "extra_data": _tool_extra(tool_name).get("extra_data") if isinstance(_tool_extra(tool_name).get("extra_data"), dict) else {},
        "rerankingEnabled": _get_bool(tool_name, "reranking_enabled", "DATALAKE_RETRIEVE_RERANKING_ENABLED", False),
        "topK": _get_int(tool_name, "top_k", "DATALAKE_RETRIEVE_TOP_K", _MAX_RETRIEVE_CHUNKS, maximum=_MAX_RETRIEVE_CHUNKS),
        "scoreThresholdEnabled": score_threshold_enabled,
    }
    if retrieval_type == "Hybrid":
        payload["weight"] = _get_float(tool_name, "weight", "DATALAKE_RETRIEVE_WEIGHT", 0.5)
    if score_threshold_enabled:
        payload["scoreThreshold"] = _get_float(tool_name, "score_threshold", "DATALAKE_RETRIEVE_SCORE_THRESHOLD", 0.0)
    return payload


def _normalize_retrieve_chunks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    chunks: list[dict[str, Any]] = []
    for item in value[:_MAX_RETRIEVE_CHUNKS]:
        if not isinstance(item, dict):
            continue
        chunk: dict[str, Any] = {
            "chunk_id": str(item.get("id") or ""),
            "document_id": str(item.get("documentID") or ""),
            "document_store_id": str(item.get("documentStoreID") or ""),
            "document_name": str(item.get("documentName") or ""),
            "document_store_name": str(item.get("documentStoreName") or ""),
            "file_uuid": str(item.get("fileUUID") or ""),
            "type": str(item.get("type") or "text"),
            "content": str(item.get("content") or "")[:_RETRIEVE_CHUNK_CHAR_LIMIT],
        }
        score = item.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            chunk["score"] = score
        chunks.append(chunk)
    return chunks


def _retrieve_invoke(tool_name: str, query: str) -> dict[str, Any]:
    endpoint = _get_string(tool_name, "retrieve_api_url", "DATALAKE_RETRIEVE_API_URL", _DEFAULT_RETRIEVE_API_URL)
    assert endpoint is not None
    with httpx.Client(
        timeout=_retrieve_timeout(tool_name),
        trust_env=_get_bool(tool_name, "trust_env", "DATALAKE_RETRIEVE_TRUST_ENV", False),
    ) as client:
        response = client.post(endpoint, headers={"Content-Type": "application/json", "Accept": "application/json"}, json=_retrieve_payload(tool_name, query))
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict):
        raise ValueError("invalid_retrieve_response")
    return {
        "query": str(result.get("query") or query.strip()),
        "chunks": _normalize_retrieve_chunks(result.get("retrieveChunks")),
        "retrieval": {
            "id": str(result.get("id") or ""),
            "type": str(result.get("type") or ""),
            "strategy": result.get("strategy") if isinstance(result.get("strategy"), dict) else {},
        },
        "truncated": len(result.get("retrieveChunks") or []) > _MAX_RETRIEVE_CHUNKS,
    }


def _safe_retrieve_json(value: dict[str, Any]) -> str:
    """Keep source-rich Retrieve results below the inline tool-output budget."""

    fitted = _normalize_image_preview_urls(deepcopy(value))
    if _encoded_length(fitted) <= _OUTPUT_CHAR_LIMIT:
        return json.dumps(fitted, ensure_ascii=False, indent=2)
    fitted["truncated"] = True
    chunks = fitted.get("chunks")
    if isinstance(chunks, list):
        for chunk in chunks:
            if isinstance(chunk, dict):
                chunk["content"] = str(chunk.get("content") or "")[:600]
    while isinstance(fitted.get("chunks"), list) and _encoded_length(fitted) > _OUTPUT_CHAR_LIMIT:
        fitted["chunks"].pop()
    return json.dumps(fitted, ensure_ascii=False, indent=2)


def _invoke_retrieve_tool(query: str, runtime: object | None) -> str:
    runtime_context = getattr(runtime, "context", None)
    if isinstance(runtime_context, Mapping) and runtime_context.get("datalake_enabled") is False:
        return json.dumps({"error": "Data Lake use is disabled for this run", "code": "datalake_disabled", "retryable": False})
    if not query.strip():
        return json.dumps({"error": "query is required", "code": "invalid_query"})

    config_error = _retrieve_config_error("datalake_smart_qa")
    if config_error:
        logger.warning("DataLake Retrieve configuration is incomplete: %s", config_error)
        return json.dumps({"error": "DataLake Retrieve is not configured", "code": config_error, "retryable": False})
    try:
        return _safe_retrieve_json(_retrieve_invoke("datalake_smart_qa", query))
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        logger.error("DataLake Retrieve was rejected with HTTP %s", status_code)
        return json.dumps({"error": "DataLake Retrieve request was rejected", "code": f"http_{status_code}", "retryable": status_code >= 500 or status_code == 429})
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("DataLake Retrieve request failed")
        return json.dumps({"error": "DataLake Retrieve request failed", "code": type(exc).__name__, "retryable": True})


def _invoke_tool(tool_name: str, query: str, runtime: object | None) -> str:
    runtime_context = getattr(runtime, "context", None)
    if isinstance(runtime_context, Mapping) and runtime_context.get("datalake_enabled") is False:
        return json.dumps(
            {
                "error": "Data Lake use is disabled for this run",
                "code": "datalake_disabled",
                "retryable": False,
            }
        )
    if not query.strip():
        return json.dumps({"error": "query is required", "code": "invalid_query"})
    if not _api_key(tool_name):
        if tool_name not in _missing_config_warned:
            _missing_config_warned.add(tool_name)
            logger.warning("AppForge authentication is not configured for %s", tool_name)
        return json.dumps(
            {
                "error": "AppForge authentication is not configured",
                "code": "missing_api_key",
            }
        )

    payload = {
        "id": _app_id(tool_name),
        "inputs": _get_inputs(tool_name),
        "query": query.strip(),
        "conversationId": _conversation_id(tool_name, runtime),
    }
    try:
        return _safe_json(_stream_invoke(tool_name, payload))
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        logger.error(
            "AppForge tool %s was rejected with HTTP %s",
            tool_name,
            status_code,
        )
        return json.dumps(
            {
                "error": "AppForge request was rejected",
                "code": f"http_{status_code}",
                "retryable": status_code >= 500 or status_code == 429,
            }
        )
    except Exception as exc:
        logger.exception("AppForge tool %s request failed", tool_name)
        return json.dumps(
            {
                "error": "AppForge request failed",
                "code": type(exc).__name__,
                "retryable": True,
            }
        )


@tool("datalake_smart_qa", parse_docstring=True)
def datalake_smart_qa_tool(runtime: Runtime, query: str) -> str:
    """Retrieve narrative well-engineering evidence from the enterprise DataLake.

    Use this for document, report, well-history, geology, completion, and other
    narrative questions. Use detail QA for table fields or record lists, and
    metrics QA for named KPIs, plans, actuals, rankings, rates, or totals.

    This tool returns ranked source chunks only. Analyze and summarize those
    chunks yourself, identify uncertainty when the evidence is incomplete, and
    cite the returned document names. Do not state that the retrieval service
    generated the final answer.

    When the result contains an ``/api/datalake/image-preview?src=...`` URL and
    the user requests an image or original illustration, render that same-origin
    proxy URL in the final answer with Markdown image syntax, for example
    ``![NB27-1-1 wellbore structure](/api/datalake/image-preview?src=...)``.
    Retain its percent encoding and do not substitute the raw DataLake URL. Do
    not use bash, curl, ``present_files``, ``--noproxy '*'``, or a generated
    replacement chart for that source image.

    Args:
        query: The user's complete DataLake document question.
    """

    return _invoke_retrieve_tool(query, runtime)


@tool("datalake_metrics_qa", parse_docstring=True)
def datalake_metrics_qa_tool(runtime: Runtime, query: str) -> str:
    """Query enterprise DataLake business indicators and metrics.

    Use this for named indicators, KPIs, plans, actual/completed values,
    rankings, comparisons, totals, progress, costs, rates, and time series. If
    an interruption returns choices, present those choices to the user.

    Args:
        query: The user's complete indicator or metrics question.
    """

    return _invoke_tool("datalake_metrics_qa", query, runtime)


@tool("datalake_detail_qa", parse_docstring=True)
def datalake_detail_qa_tool(runtime: Runtime, query: str) -> str:
    """Query record-level enterprise DataLake tables and fields.

    Use this for lists, counts of records, concrete table fields, basic
    oilfield/platform/well attributes, coordinates, design depths, and other
    SQL-backed detail data. Use metrics QA for named indicators and smart QA
    for narrative document interpretation.

    Args:
        query: The user's complete record-level or table-field question.
    """

    return _invoke_tool("datalake_detail_qa", query, runtime)
