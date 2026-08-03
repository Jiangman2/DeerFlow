"""Secure AppForge adapters for CNOOC DataLake question answering.

The AppForge endpoint is an internal SSE API. Credentials and workflow identity
inputs are resolved only from server-side configuration; the model-visible tool
schema contains only the business question.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

import httpx
from langchain.tools import tool

from deerflow.config import get_app_config
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "http://10.77.124.142:9080"
_DEFAULT_TIMEOUT_S = 180.0
_OUTPUT_CHAR_LIMIT = 10_000
_ANSWER_CHAR_LIMIT = 5_000
_KNOWLEDGE_CHAR_LIMIT = 600
_MAX_KNOWLEDGE_ITEMS = 5
_MAX_PROGRESS_ITEMS = 10
_PROGRESS_CHAR_LIMIT = 1_200
_MAX_THINKING_ITEMS = 4
_THINKING_CHAR_LIMIT = 400
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


def _safe_json(value: dict[str, Any]) -> str:
    return json.dumps(_fit_output_budget(value), ensure_ascii=False, indent=2)


def _invoke_tool(tool_name: str, query: str, runtime: object | None) -> str:
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
    """Query narrative well-engineering documents in the enterprise DataLake.

    Use this for document, report, well-history, geology, completion, and other
    narrative questions. Use detail QA for table fields or record lists, and
    metrics QA for named KPIs, plans, actuals, rankings, rates, or totals.

    Args:
        query: The user's complete DataLake document question.
    """

    return _invoke_tool("datalake_smart_qa", query, runtime)


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
