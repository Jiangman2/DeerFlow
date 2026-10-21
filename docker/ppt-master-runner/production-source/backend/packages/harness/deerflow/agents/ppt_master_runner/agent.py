"""Thin transport graph for the external ``ppt-master`` Agent host.

The presentation Agent itself runs in the thread's Kubernetes Sandbox Pod.  It
is a real Agent CLI host with the complete upstream Skill package, writable
workspace, dependencies, and confirmation server.  This module deliberately
does not construct a second LangChain Agent or reinterpret the Skill workflow;
its single graph node only provisions the Pod, relays the user turn, streams
the host's receipts, and returns the final response/artifacts to DeerFlow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from langchain_core.callbacks import BaseCallbackManager
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.title_middleware import fallback_title_from_text
from deerflow.agents.thread_state import ThreadState
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.config.title_config import TitleConfig
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.sandbox import get_sandbox_provider

logger = logging.getLogger(__name__)

PPT_MASTER_RUNNER_ASSISTANT_ID = "ppt-master-runner"
PPT_MASTER_AGENT_HOST_PORT = int(os.environ.get("PPT_MASTER_AGENT_HOST_PORT", "8788"))
PPT_MASTER_AGENT_HOST_READY_TIMEOUT_SECONDS = float(os.environ.get("PPT_MASTER_AGENT_HOST_READY_TIMEOUT_SECONDS", "120"))
PPT_MASTER_AGENT_HOST_REQUEST_RETRY_ATTEMPTS = int(os.environ.get("PPT_MASTER_AGENT_HOST_REQUEST_RETRY_ATTEMPTS", "5"))
_POLL_INTERVAL_SECONDS = 0.5
_TRANSIENT_RETRY_BASE_SECONDS = 0.5
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_PPT_MASTER_SLASH_PREFIX = re.compile(r"^\s*/ppt-master(?:\s+|$)", re.IGNORECASE)
_CHINESE_BOOK_TITLE = re.compile(r"《([^》\r\n]{2,100})》")

_PRESENTATION_REQUIREMENTS: dict[str, re.Pattern[str]] = {
    "speaker_notes": re.compile(r"演讲者?备注|备注|讲稿|speaker\s+notes?", re.IGNORECASE),
    "transitions": re.compile(r"页面?转场|切换效果|transitions?", re.IGNORECASE),
    "timed_advance": re.compile(r"自动(?:播放|翻页|切换)|auto[- ]?advance", re.IGNORECASE),
    "object_animations": re.compile(r"对象动画|元素动画|object\s+animations?", re.IGNORECASE),
    "native_charts": re.compile(r"原生图表|native\s+charts?", re.IGNORECASE),
    "ai_images": re.compile(r"(?:AI|ai)\s*(?:生图|图片|图像)|文生图|image\s+generation", re.IGNORECASE),
    "narration_audio": re.compile(r"配音|旁白|语音|音频|narration|audio", re.IGNORECASE),
    "template": re.compile(r"模板|template", re.IGNORECASE),
}

_NEGATED_REQUIREMENTS: dict[str, re.Pattern[str]] = {
    "ai_images": re.compile(r"不(?:使用|需要|要).{0,8}(?:AI\s*生图|文生图|图片生成)", re.IGNORECASE),
    "narration_audio": re.compile(r"不(?:使用|需要|要).{0,8}(?:配音|旁白|语音|音频)", re.IGNORECASE),
    "template": re.compile(r"不(?:使用|需要|要).{0,8}(?:模板|template)", re.IGNORECASE),
}


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part for part in parts if part)


def _latest_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            text = _content_text(message.content).strip()
            if text:
                return text
    raise ValueError("The external ppt-master host received no user message")


def _original_ppt_master_request(messages: list[BaseMessage], fallback: str) -> str:
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        text = _content_text(message.content).strip()
        if text.lstrip().lower().startswith("/ppt-master"):
            return text
    return fallback


def derive_ppt_master_thread_title(text: str, *, title_config: TitleConfig) -> str | None:
    """Create a deterministic title for the transport-only PPT graph.

    The isolated runner intentionally does not attach ``TitleMiddleware`` or a
    title model.  It still owns the graph's ``title`` state field, so derive the
    same bounded local fallback from the original Slash request.  A Chinese
    book-title topic is preferred when the request supplies one explicitly.
    """

    if not title_config.enabled:
        return None
    normalized = _PPT_MASTER_SLASH_PREFIX.sub("", text, count=1).strip()
    explicit_topic = _CHINESE_BOOK_TITLE.search(normalized)
    title_source = explicit_topic.group(1).strip() if explicit_topic else normalized
    return fallback_title_from_text(title_source, title_config)


def requested_delivery_contract(text: str) -> dict[str, Any]:
    """Project explicit user requirements onto the Agent-host acceptance edge."""

    capabilities = {name: True for name, pattern in _PRESENTATION_REQUIREMENTS.items() if pattern.search(text) and not (_NEGATED_REQUIREMENTS.get(name) and _NEGATED_REQUIREMENTS[name].search(text))}
    slide_match = re.search(r"(?<!\d)(\d{1,3})\s*页", text)
    return {
        "required_capabilities": capabilities,
        "expected_slides": int(slide_match.group(1)) if slide_match else None,
    }


def build_ppt_master_runner_system_prompt(
    user_text: str,
    *,
    original_request: str | None = None,
    resumed: bool = False,
) -> str:
    """Build the minimal host handoff sent to the independent Agent CLI."""

    return f"""Use the installed `$ppt-master` Skill for this task and follow its
authoritative routing, references, commands, confirmation surfaces, validators,
and recovery instructions exactly. Do not replace its workflow with an invented
shortcut.

Host paths:
- User attachments: `/mnt/user-data/uploads`
- Writable project workspace: `/mnt/user-data/workspace`
- Final artifact handoff: `/mnt/user-data/outputs`

After the Skill's own export and validation succeed, copy the final PPTX into
`/mnt/user-data/outputs`. The host will create or persist the browser preview;
do not claim delivery before the real PPTX exists there. Match the user's
language and keep concrete command/file progress visible.

Execution continuity:
- This is {"a resumed persisted Agent session" if resumed else "a fresh Agent session"}.
- Before the first workspace mutation in this turn, reopen the installed
  `ppt-master` Skill entry, the already-selected top-level route, and every
  current-stage authority/reference that route explicitly triggers. Never
  author a schema or command from recalled content after a turn boundary.
- Use the upstream package's own validators and stage commands as blocking
  gates. When one fails, read its owning upstream recovery/reference, repair
  the same project, and rerun that exact gate before advancing.
- Execute an upstream validator/stage command exactly as documented: never add
  `head`, `tail`, `grep`, another pipeline, output truncation, `|| true`, or
  shell composition that can replace or hide the owning command's exit code.
- Resume the existing project and persisted stage. Do not create a replacement
  project merely because this is a new DeerFlow run.

Original user request (authoritative except for later explicit changes):
{(original_request or user_text).strip()}

Completion boundary:
- Before claiming final delivery, map every explicitly requested capability to
  the upstream reference and official command that owns it, then run the
  upstream `pptx_delivery_check.py` against the final PPTX.
- A visual imitation of a chart is not an editable native chart, and a
  transition is not an object animation. If the finished package does not
  contain the requested native capability, repair the same project and export
  again instead of reporting completion.

Current user turn:
{user_text.strip()}
"""


def derive_agent_host_url(sandbox_base_url: str) -> str:
    """Resolve the Agent sidecar URL from the thread Sandbox Service URL.

    Kubernetes containers in one Pod share a Service and network namespace. The
    AIO command API uses port 8080 and the Agent host uses a second named Service
    port. Only the port changes; the DNS identity remains thread-scoped.
    """

    parsed = urlsplit(sandbox_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Invalid Sandbox URL for ppt-master Agent host: {sandbox_base_url!r}")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = f"{host}:{PPT_MASTER_AGENT_HOST_PORT}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def observed_session_usage(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Only fixed, reported root/Worker metadata; never journal/billing input."""

    raw = payload.get("session_usage_observation")
    semantics = "session_cumulative_not_current_turn_or_invoice"
    if not isinstance(raw, dict) or raw.get("semantics") != semantics:
        return None

    def counter(value: object) -> dict[str, Any] | None:
        if not isinstance(value, dict) or value.get("source") != "codex_session_token_count" or value.get("cli_version") != "0.139.0":
            return None
        identifier = value.get("session_id_hash")
        counts = value.get("usage")
        names = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{16}", identifier) or not isinstance(counts, dict)
                or any(type(counts.get(name)) is not int or not 0 <= counts[name] < 2 ** 53 for name in names)):
            return None
        if (counts["total_tokens"] != counts["input_tokens"] + counts["output_tokens"]
                or counts["cached_input_tokens"] > counts["input_tokens"] or counts["reasoning_output_tokens"] > counts["output_tokens"]):
            return None
        return {"source": "codex_session_token_count", "cli_version": "0.139.0", "session_id_hash": identifier, "usage": {name: counts[name] for name in names}}

    parent = counter(raw.get("parent"))
    workers = []
    seen = {parent["session_id_hash"]} if parent else set()
    raw_workers = raw.get("workers")
    if isinstance(raw_workers, list):
        for value in raw_workers[:7]:
            normalized = counter(value)
            if normalized is not None and normalized["session_id_hash"] not in seen:
                workers.append(normalized)
                seen.add(normalized["session_id_hash"])
    count = raw.get("worker_count")
    count = count if type(count) is int and 0 <= count <= 64 else None
    return {"semantics": semantics, "parent": parent, "workers": workers, "worker_count": count,
            "partial": raw.get("partial") is True or parent is None or count is None or len(workers) != count}


def completed_agent_message(run_id: str, final_message: str, usage: dict[str, int] | None = None, observation: dict[str, Any] | None = None) -> AIMessage:
    """Build the durable result message that reconnects the real task timeline."""

    content = final_message or "ppt-master Agent 本轮执行已完成。"
    return AIMessage(
        content=content,
        additional_kwargs={
            "ppt_master_task": {
                "task_id": run_id,
                "status": "completed",
                "description": "ppt-master 官方工作流",
                "model_name": "ppt-master-agent-host",
                "result": content,
                **({"usage": usage} if usage else {}),
                **({"session_usage_observation": observation} if observation else {}),
            }
        },
    )


def _bounded_text(value: object, *, limit: int = 12_000) -> str:
    text = value if isinstance(value, str) else ""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... (truncated by host bridge)"


def _codex_transport_notice(event, receipt_seq):
    """Project a real public retry/error, never upstream text or reasoning.

    The Host envelope sequence is the durable identity. Replaying a receipt
    must not duplicate it; identical failures at different sequences are distinct.
    This is a host status notice, not an Agent answer or a task-state transition.
    """
    if event.get("type") != "error" or type(receipt_seq) is not int or receipt_seq < 1:
        return []
    text = event.get("message")
    if not isinstance(text, str) or len(text) > 8192:
        return []
    retry = re.fullmatch(r"Reconnecting\.\.\. (\d{1,2})/(\d{1,2}) \(([^\r\n]+)\)", text)
    if retry and not (1 <= int(retry[1]) <= int(retry[2]) <= 10):
        retry = None
    reason = retry[3] if retry else text
    prefix = "stream disconnected before completion: "
    if reason.startswith(prefix):
        reason = reason[len(prefix):]
    # Exact public error strings only. Unknown errors are still visible but
    # their private text, endpoints and arbitrary fields never leave this helper.
    if reason == "Enterprise DeepSeek exceeded the total turn deadline.":
        code, description = "model_turn_deadline", "模型请求达到总时限"
    elif reason == "Enterprise DeepSeek tool arguments failed validation (JSON syntax). No tools from this response were dispatched.":
        code, description = "tool_arguments_json", "模型返回的工具参数不是有效 JSON；本次响应的工具未执行"
    else:
        code, description = "codex_stream_error", "Agent 报告连接或响应异常"
    if retry:
        detail = "；正在重连（{}/{}）。这不是人工确认等待，也不代表任务已完成。".format(retry[1], retry[2])
    else:
        detail = "。正在等待 Agent 的后续状态；此通知本身不代表任务已终止。"
    return [AIMessage(
        id="ppt-host-notice-{}".format(receipt_seq),
        content="运行状态（系统通知）：" + description + detail,
        additional_kwargs={"ppt_host_notice": {"code": code, "receipt_seq": receipt_seq,
                            "retrying": retry is not None}},
    )]


def codex_event_to_messages(event: dict[str, Any], *, receipt_seq: int | None = None) -> list[BaseMessage]:
    """Map public Codex JSONL events onto DeerFlow's durable task timeline."""

    if event.get("type") == "error":
        return _codex_transport_notice(event, receipt_seq)

    if event.get("type") not in {"item.started", "item.completed"}:
        return []
    item = event.get("item")
    if not isinstance(item, dict):
        return []
    item_type = item.get("type")
    item_id = str(item.get("id") or "ppt-master-agent-step")

    if item_type == "agent_message" and event.get("type") == "item.completed":
        text = _bounded_text(item.get("text")).strip()
        return [AIMessage(id=f"{item_id}-message", content=text)] if text else []

    if item_type == "command_execution" and event.get("type") == "item.started":
        command = _bounded_text(item.get("command"), limit=4_000).strip()
        if not command:
            return []
        # Publish the actual command before it returns, including long upstream
        # confirmation waits. Do not manufacture a successful owning receipt.
        return [AIMessage(id=f"{item_id}-request", content="", tool_calls=[{"id": f"{item_id}-bash", "name": "bash", "args": {"command": command}}])]

    if item_type == "collab_tool_call":
        tool = item.get("tool")
        if not isinstance(tool, str) or tool not in {"spawn_agent", "wait", "send_input", "close_agent", "resume_agent"}:
            return []
        # Project lifecycle facts, never worker prompts or hidden result text.
        statuses: dict[str, int] = {}
        raw_states = item.get("agents_states")
        if isinstance(raw_states, dict):
            for state in list(raw_states.values())[:32]:
                status = state.get("status") if isinstance(state, dict) else state
                if isinstance(status, str) and status in {"pending_init", "running", "completed", "errored", "shutdown"}:
                    statuses[status] = statuses.get(status, 0) + 1
        public_state = item.get("status")
        if not isinstance(public_state, str) or public_state not in {"in_progress", "completed", "failed"}:
            public_state = "unknown"
        if event.get("type") == "item.started":
            return [AIMessage(id=f"{item_id}-worker-started", content="", tool_calls=[{"id": f"{item_id}-worker", "name": f"codex_{tool}", "args": {"status": public_state}}])]
        return [
            ToolMessage(
                id=f"{item_id}-worker-result",
                tool_call_id=f"{item_id}-worker",
                name=f"codex_{tool}",
                content=json.dumps({"tool": tool, "status": public_state, "workers": statuses}, ensure_ascii=False),
                status="error" if public_state == "failed" or statuses.get("errored") else "success",
            )
        ]

    if item_type == "command_execution" and event.get("type") == "item.completed":
        command = _bounded_text(item.get("command"), limit=4_000).strip()
        if not command:
            return []
        tool_call_id = f"{item_id}-bash"
        request = AIMessage(
            id=f"{item_id}-request",
            content="",
            tool_calls=[{"id": tool_call_id, "name": "bash", "args": {"command": command}}],
        )
        output = _bounded_text(item.get("aggregated_output")).strip() or "(no output)"
        exit_code = item.get("exit_code")
        result = ToolMessage(
            id=f"{item_id}-result",
            content=output,
            tool_call_id=tool_call_id,
            name="bash",
            status="success" if exit_code in (None, 0) else "error",
        )
        return [request, result]

    if item_type == "file_change" and event.get("type") == "item.completed":
        changes = item.get("changes")
        summary = _bounded_text(item.get("text") or item.get("summary")).strip()
        if not summary and isinstance(changes, list):
            paths = [str(change.get("path")) for change in changes if isinstance(change, dict) and change.get("path")]
            summary = "已更新文件：" + "、".join(paths[:20]) if paths else "文件修改已完成"
        return [AIMessage(id=f"{item_id}-file-change", content=summary)] if summary else []

    if item_type in {"mcp_tool_call", "web_search"} and event.get("type") == "item.completed":
        name = str(item.get("tool") or item.get("name") or item_type)
        tool_call_id = f"{item_id}-{name}"
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        request = AIMessage(
            id=f"{item_id}-request",
            content="",
            tool_calls=[{"id": tool_call_id, "name": name, "args": args}],
        )
        output = _bounded_text(item.get("result") or item.get("text")).strip() or "(completed)"
        result = ToolMessage(
            id=f"{item_id}-result",
            content=output,
            tool_call_id=tool_call_id,
            name=name,
        )
        return [request, result]

    # Do not project raw reasoning events. Public command/file/tool receipts and
    # the Agent's own visible messages are sufficient to audit execution.
    return []


def _auth_headers(api_key: str) -> dict[str, str]:
    if not api_key:
        raise RuntimeError("PPT Agent host authentication is not configured (sandbox.sandbox_api_key is empty)")
    return {"Authorization": f"Bearer {api_key}"}


def _transport_failure_kind(exc: Exception) -> str:
    """Return a bounded, credential-free failure category for the task UI."""

    if isinstance(exc, httpx.HTTPStatusError):
        return f"agent host HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.ConnectError):
        return "agent host DNS/connect failure"
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return "agent host timeout"
    if isinstance(exc, (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)):
        return "agent host connection interrupted"
    if "lease" in str(exc).casefold() or "sandbox" in str(exc).casefold():
        return "Sandbox lifecycle failure"
    return type(exc).__name__


def _find_usage_recorder(config: RunnableConfig, runtime: Runtime) -> Any | None:
    """Find the run journal callback without coupling the runner to its class."""

    callbacks = config.get("callbacks")
    runtime_config = getattr(runtime, "config", None)
    if not callbacks and isinstance(runtime_config, dict):
        callbacks = runtime_config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        callbacks = callbacks.handlers
    if not isinstance(callbacks, list):
        return None
    for callback in callbacks:
        if hasattr(callback, "record_external_llm_usage_records"):
            return callback
    return None


def _report_agent_usage(
    config: RunnableConfig,
    runtime: Runtime,
    result: dict[str, Any],
) -> dict[str, int] | None:
    """Persist external Codex usage through DeerFlow's existing run journal."""

    raw_records = result.get("usage_records")
    if not isinstance(raw_records, list):
        return None
    records = [record for record in raw_records if isinstance(record, dict)]
    if not records:
        return None
    recorder = _find_usage_recorder(config, runtime)
    if recorder is None:
        raise RuntimeError("PPT Agent token usage recorder is unavailable")
    recorder.record_external_llm_usage_records(records)
    return {
        "input_tokens": sum(int(record.get("input_tokens") or 0) for record in records),
        "cached_input_tokens": sum(int(record.get("cache_read_tokens") or 0) for record in records),
        "output_tokens": sum(int(record.get("output_tokens") or 0) for record in records),
        "total_tokens": sum(int(record.get("total_tokens") or 0) for record in records),
        "llm_call_count": sum(max(int(record.get("llm_call_count") or 1), 1) for record in records),
    }


async def _wait_for_agent_host(
    *,
    client: httpx.AsyncClient,
    host_url: str,
    timeout_seconds: float = PPT_MASTER_AGENT_HOST_READY_TIMEOUT_SECONDS,
) -> None:
    """Wait until the thread-scoped Agent sidecar and its Service DNS are ready."""

    deadline = asyncio.get_running_loop().time() + max(1.0, timeout_seconds)
    last_error: Exception | None = None
    while True:
        try:
            response = await client.get(f"{host_url}/health")
            if response.status_code == 200:
                return
            last_error = RuntimeError(f"PPT Agent host health endpoint returned HTTP {response.status_code}")
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            last_error = exc

        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("PPT Agent host did not become ready before the startup deadline") from last_error
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _get_agent_task_with_retry(
    *,
    client: httpx.AsyncClient,
    host_url: str,
    task_id: str,
    after_seq: int,
) -> httpx.Response:
    """Read an Agent task while tolerating bounded transient Pod/DNS interruptions."""

    attempts = max(1, PPT_MASTER_AGENT_HOST_REQUEST_RETRY_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        try:
            return await client.get(
                f"{host_url}/v1/tasks/{task_id}",
                params={"after_seq": after_seq},
            )
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
        ):
            if attempt >= attempts:
                raise
            await asyncio.sleep(min(_TRANSIENT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), 4.0))
    raise AssertionError("unreachable")


async def _start_agent_task_with_retry(
    *,
    client: httpx.AsyncClient,
    host_url: str,
    payload: dict[str, Any],
) -> httpx.Response:
    """Start a task after health passed without losing a turn to a DNS flap."""

    attempts = max(1, PPT_MASTER_AGENT_HOST_REQUEST_RETRY_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        try:
            return await client.post(f"{host_url}/v1/tasks", json=payload)
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
        ):
            if attempt >= attempts:
                raise
            await asyncio.sleep(min(_TRANSIENT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), 4.0))
    raise AssertionError("unreachable")


async def _poll_agent_turn(
    *,
    client: httpx.AsyncClient,
    host_url: str,
    task_id: str,
    writer,
    thread_id: str | None = None,
    keep_sandbox_alive: Callable[[], bool] | None = None,
    report_usage: Callable[[dict[str, Any]], dict[str, int] | None] | None = None,
    provider_receipts=None,
) -> dict[str, Any]:
    after_seq = 0
    message_index = 0
    last_usage: dict[str, int] | None = None
    last_observation: dict[str, Any] | None = None
    seen_message_ids: set[str] = set()
    cleanup_started_at: float | None = None
    last_unknown_calls = 0
    last_sync_delayed = False
    while True:
        # The external Agent can legitimately spend much longer than the AIO
        # sandbox idle timeout building and validating a deck.  Host polling
        # bypasses the normal Sandbox client, so explicitly renew the provider
        # lease before every poll.  Without this heartbeat the idle reaper can
        # delete a still-busy Pod and its Service after ~10 minutes, turning a
        # healthy long-running task into a late DNS/transport failure.
        if keep_sandbox_alive is not None and not keep_sandbox_alive():
            raise RuntimeError("The active ppt-master Sandbox lease disappeared during Agent execution")
        response = await _get_agent_task_with_retry(
            client=client,
            host_url=host_url,
            task_id=task_id,
            after_seq=after_seq,
        )
        response.raise_for_status()
        payload = response.json()
        if provider_receipts is not None:
            payload = await provider_receipts.merge(
                payload, force=payload.get("status") in _TERMINAL_STATUSES)
        if not isinstance(payload, dict) or payload.get("task_id") != task_id or (thread_id is not None and payload.get("thread_id") != thread_id):
            raise RuntimeError("PPT task receipt identity mismatch")
        observation = observed_session_usage(payload)
        if observation is not None and observation != last_observation:
            writer({"type": "task_running", "task_id": task_id, **({"thread_id": thread_id} if thread_id else {}),
                    "model_name": "ppt-master-agent-host", "session_usage_observation": observation})
            last_observation = observation
        # Usage is cumulative and sourced only from official turn.completed
        # receipts. RunJournal already deduplicates records by source_run_id.
        if report_usage is not None:
            usage = report_usage(payload)
            if usage and usage != last_usage:
                writer({"type": "task_running", "task_id": task_id, **({"thread_id": thread_id} if thread_id else {}), "model_name": "ppt-master-agent-host", "usage": usage})
                last_usage = usage
        unknown_calls = payload.get("provider_usage_unknown_calls", 0)
        if type(unknown_calls) is int and unknown_calls > last_unknown_calls:
            message_index += 1
            notice = AIMessage(id=f"ppt-usage-unknown:{task_id}:{unknown_calls}", content=f"用量说明：本轮有 {unknown_calls} 次模型请求未取得服务商 Token 回执；当前已统计用量不完整，不代表这些请求消耗为零。")
            writer({"type": "task_running", "task_id": task_id, **({"thread_id": thread_id} if thread_id else {}), "model_name": "ppt-master-agent-host", "message": notice.model_dump(), "message_index": message_index})
            last_unknown_calls = unknown_calls
        sync_delayed = payload.get("provider_usage_sync_state") == "delayed"
        if sync_delayed != last_sync_delayed:
            message_index += 1
            notice = AIMessage(
                id=f"ppt-usage-sync:{task_id}:{'delayed' if sync_delayed else 'recovered'}",
                content=("Token 回执查询暂时超时；PPT 任务仍在继续，当前用量为已核实的部分值。"
                         if sync_delayed else "Token 回执查询已恢复，继续汇总当前任务用量。"),
            )
            writer({"type": "task_running", "task_id": task_id,
                    **({"thread_id": thread_id} if thread_id else {}),
                    "model_name": "ppt-master-agent-host",
                    "message": notice.model_dump(), "message_index": message_index})
            last_sync_delayed = sync_delayed
        events = payload.get("events")
        if isinstance(events, list):
            for envelope in events:
                if not isinstance(envelope, dict):
                    continue
                seq = envelope.get("seq")
                if isinstance(seq, int):
                    after_seq = max(after_seq, seq)
                if envelope.get("kind") != "codex" or not isinstance(envelope.get("payload"), dict):
                    continue
                for message in codex_event_to_messages(envelope["payload"], receipt_seq=seq):
                    # A completed receipt carries the request for replay-only
                    # clients. If item.started already projected that request,
                    # keep one timeline entry; this is not another execution.
                    if message.id and message.id in seen_message_ids:
                        continue
                    if message.id:
                        seen_message_ids.add(message.id)
                    message_index += 1
                    writer(
                        {
                            "type": "task_running",
                            "task_id": task_id,
                            **({"thread_id": thread_id} if thread_id else {}),
                            # ``task_running`` is a public/durable transport
                            # contract. The shared step-event projector expects
                            # the same plain message dictionaries emitted by the
                            # ordinary subagent collector, not live LangChain
                            # message objects.
                            "message": message.model_dump(),
                            "message_index": message_index,
                            "model_name": "ppt-master-agent-host",
                        }
                    )

        status = payload.get("status")
        if status in _TERMINAL_STATUSES:
            if "process_cleanup_complete" not in payload or payload["process_cleanup_complete"] is True:
                return payload
            now = asyncio.get_running_loop().time()
            if cleanup_started_at is None:
                cleanup_started_at = now
            elif now - cleanup_started_at >= 20.0:
                raise RuntimeError("PPT terminal process cleanup could not be confirmed")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


class PptProviderReceipts:
    """Reconcile run-scoped Gateway receipts without making usage a task liveness gate."""

    _POLL_SECONDS = 5.0

    def __init__(self, capability, run_id, thread_id):
        self.capability, self.run_id, self.thread_id = capability, run_id, thread_id
        self.cursor, self.records, self.unknown = 0, {}, set()
        self.next_poll_at = 0.0
        self.sync_delayed = False

    def _snapshot(self, payload):
        return {**payload, "usage_records": list(self.records.values()),
                "provider_usage_unknown_calls": len(self.unknown),
                "provider_usage_source": "gateway_per_attempt_all_codex_agents",
                "provider_usage_sync_state": "delayed" if self.sync_delayed else "recent"}

    async def merge(self, payload, *, force=False):
        now = asyncio.get_running_loop().time()
        if not force and now < self.next_poll_at:
            return self._snapshot(payload)
        # A receipt lookup is idempotent. Transport/server failures defer only
        # accounting, never Host event delivery or the active Agent process.
        self.next_poll_at = now + self._POLL_SECONDS
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=httpx.Timeout(10.0),
                headers={"X-Ppt-Capability": self.capability,
                         "X-Ppt-Runtime-Operation": "receipts"},
            ) as client:
                for _ in range(32):
                    response = await client.post(
                        "http://gateway:8001/api/codex/deepseek/v1/responses",
                        json={"after_seq": self.cursor},
                    )
                    if response.status_code == 429 or 500 <= response.status_code <= 599:
                        self.sync_delayed = True
                        logger.warning("PPT provider receipt lookup deferred: HTTP %s", response.status_code)
                        return self._snapshot(payload)
                    response.raise_for_status()  # 4xx is an authorization/contract fault.
                    data = response.json()
                    if (not isinstance(data, dict) or data.get("run_id") != self.run_id
                            or data.get("thread_id") != self.thread_id
                            or not isinstance(data.get("rows"), list)
                            or len(data["rows"]) > 500):
                        raise RuntimeError("PPT provider receipt scope mismatch")
                    for row in data["rows"]:
                        if not isinstance(row, dict):
                            raise RuntimeError("PPT provider receipt row invalid")
                        record, seq = row.get("content"), row.get("seq")
                        if (type(seq) is not int or seq <= self.cursor
                                or not isinstance(record, dict)
                                or record.get("task_id") != self.run_id
                                or record.get("thread_id") != self.thread_id):
                            raise RuntimeError("PPT provider receipt identity mismatch")
                        source = record.get("source_run_id")
                        if not isinstance(source, str) or not source.startswith("ppt-relay:" + self.run_id + ":"):
                            raise RuntimeError("PPT provider receipt source mismatch")
                        if record.get("usage_state") == "reported":
                            counts = [record.get(k) for k in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens")]
                            if (any(type(v) is not int or not 0 <= v <= 10**9 for v in counts)
                                    or counts[2] != counts[0] + counts[1] or counts[3] > counts[0]):
                                raise RuntimeError("PPT provider numeric receipt invalid")
                            previous = self.records.get(source)
                            if previous is not None and any(previous.get(k) != record.get(k) for k in (
                                    "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "model_name")):
                                raise RuntimeError("PPT provider receipt changed after settlement")
                            self.records[source] = record
                            self.unknown.discard(source)
                        elif record.get("usage_state") == "unknown":
                            if source not in self.records:
                                self.unknown.add(source)
                        else:
                            raise RuntimeError("PPT provider receipt state invalid")
                        self.cursor = seq
                    if len(data["rows"]) < 500:
                        self.sync_delayed = False
                        return self._snapshot(payload)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
                httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.PoolTimeout,
                httpx.WriteError, httpx.WriteTimeout) as exc:
            self.sync_delayed = True
            logger.warning("PPT provider receipt lookup deferred: %s", type(exc).__name__)
            return self._snapshot(payload)
        raise RuntimeError("PPT provider receipt pagination limit reached")


async def _mint_ppt_capability(run_id, thread_id, api_key):
    async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(10.0), headers={**_auth_headers(api_key), "X-Ppt-Runtime-Operation": "capability"}) as client:
        response = await client.post("http://gateway:8001/api/codex/deepseek/v1/responses", json={"run_id": run_id, "thread_id": thread_id})
        response.raise_for_status()
        value = response.json().get("capability")
        if not isinstance(value, str) or len(value) > 1024 or "." not in value:
            raise RuntimeError("PPT runtime capability unavailable")
        return value


async def _drain_ppt_operation(operation):
    """Keep ownership until an already-started operation actually finishes.

    Cancelling to_thread's awaiter does not stop its OS thread. Shield the
    operation and remember repeated cancellation instead of losing its result.
    Return that cancellation separately so an acquisition can release its exact
    returned resource before propagating it. An operation error stays an error.
    """
    cancelled = None
    while not operation.done():
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError as exc:
            if cancelled is None:
                cancelled = exc
    return operation.result(), cancelled


async def _acquire_ppt_execution_lease(provider, sandbox_id):
    acquire = getattr(provider, "acquire_execution_lease", None)
    release = getattr(provider, "release_execution_lease", None)
    if not callable(acquire) or not callable(release):
        raise RuntimeError("The configured Sandbox provider does not support external execution leases")
    operation = asyncio.create_task(asyncio.to_thread(acquire, sandbox_id))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError as cancelled:
        try:
            lease, _ = await _drain_ppt_operation(operation)
        except Exception:
            # A late acquire failure must not replace the original Stop. No
            # lease was returned; its provider owns partial-acquisition cleanup.
            logger.warning("PPT execution lease acquisition failed while stopping")
            raise cancelled
        # Even after repeated Stop, the exact acquired descriptor must be
        # released once. Never start the Agent or release a different lease.
        try:
            await _drain_ppt_operation(asyncio.create_task(asyncio.to_thread(release, lease, sandbox_id)))
        except Exception as exc:
            raise RuntimeError("PPT execution lease cleanup could not be confirmed") from exc
        raise cancelled


async def _acquire_ppt_sandbox(provider, thread_id, user_id):
    operation = asyncio.create_task(provider.acquire_async(thread_id, user_id=user_id))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError as cancelled:
        try:
            sandbox_id, _ = await _drain_ppt_operation(operation)
        except Exception:
            raise cancelled
        await _drain_ppt_operation(asyncio.create_task(asyncio.to_thread(provider.release, sandbox_id)))
        raise cancelled


_PPT_CLEANUP_GUARDS = set()


def _retain_ppt_cleanup_guard(provider, execution_lease, sandbox_id, host_url, task_id, thread_id, api_key):
    """Unknown exit cannot authorize idle destruction. Hold the exact lease.

    No restart, task lookup by another ID, or time-based assumption of exit.
    Operator intervention is needed if the exact Host never answers. A process
    crash is still an infrastructure recovery boundary, not a verified Stop.
    """
    async def guard():
        while True:
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(5.0), headers=_auth_headers(api_key)) as client:
                    response = await client.get(f"{host_url}/v1/tasks/{task_id}", params={"after_seq": 2147483647})
                    response.raise_for_status()
                    payload = response.json()
                    if isinstance(payload, dict) and payload.get("task_id") == task_id and payload.get("thread_id") == thread_id and payload.get("status") in _TERMINAL_STATUSES and payload.get("process_cleanup_complete") is True:
                        await _release_ppt_resources(provider, execution_lease, sandbox_id)
                        return
            except asyncio.CancelledError:
                # Do not transform shutdown cancellation into a destruction
                # permission. OS/process shutdown releases its own descriptor.
                raise
            except Exception:
                pass
            await asyncio.sleep(5)
    operation = asyncio.create_task(guard())
    _PPT_CLEANUP_GUARDS.add(operation)
    operation.add_done_callback(_PPT_CLEANUP_GUARDS.discard)


async def _release_ppt_resources(provider, execution_lease, sandbox_id):
    async def cleanup():
        if execution_lease is not None:
            await asyncio.to_thread(provider.release_execution_lease, execution_lease, sandbox_id)
        # Do not park/release the Sandbox until the real descriptor operation
        # finishes. A failed lease release deliberately leaves tracking intact.
        await asyncio.to_thread(provider.release, sandbox_id)
    _, cancelled = await _drain_ppt_operation(asyncio.create_task(cleanup()))
    if cancelled is not None:
        raise cancelled


async def _cancel_agent_turn(*, client, host_url: str, task_id: str, thread_id: str, timeout_seconds: float = 20.0):
    """A 200 POST accepts intent; only a matching cleanup receipt proves exit.

    No retries of task start, no other task lookup, no Sandbox destruction.
    Older hosts without the additive receipt remain explicitly unconfirmed.
    """
    async def cancel_and_wait():
        response = await client.post(f"{host_url}/v1/tasks/{task_id}/cancel")
        response.raise_for_status()
        while True:
            response = await client.get(f"{host_url}/v1/tasks/{task_id}", params={"after_seq": 2147483647})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("task_id") != task_id or payload.get("thread_id") != thread_id:
                raise RuntimeError("PPT cancellation receipt identity mismatch")
            if payload.get("status") in _TERMINAL_STATUSES and payload.get("process_cleanup_complete") is True:
                return payload
            await asyncio.sleep(0.25)
    operation = asyncio.create_task(asyncio.wait_for(cancel_and_wait(), timeout=timeout_seconds))
    result, _ = await _drain_ppt_operation(operation)
    return result


async def _run_external_ppt_master(
    state: ThreadState,
    config: RunnableConfig,
    runtime: Runtime,
    *,
    app_config: AppConfig,
) -> dict[str, Any]:
    configurable = config.get("configurable", {}) or {}
    context = runtime.context or {}
    thread_id = str(context.get("thread_id") or configurable.get("thread_id") or "").strip()
    run_id = str(context.get("run_id") or configurable.get("run_id") or "").strip()
    if not thread_id or not run_id:
        raise RuntimeError("The ppt-master Agent bridge requires thread_id and run_id")

    messages = list(state.get("messages") or [])
    user_text = _latest_user_text(messages)
    resume = not user_text.lstrip().lower().startswith("/ppt-master")
    original_request = _original_ppt_master_request(messages, user_text)
    prompt = build_ppt_master_runner_system_prompt(
        user_text,
        original_request=original_request,
        resumed=resume,
    )
    provider = get_sandbox_provider()
    capability = await _mint_ppt_capability(run_id, thread_id, app_config.sandbox.sandbox_api_key or "")
    provider_receipts = PptProviderReceipts(capability, run_id, thread_id)
    sandbox_id = await _acquire_ppt_sandbox(provider, thread_id, resolve_runtime_user_id(runtime))
    try:
        sandbox = provider.get(sandbox_id)
        if sandbox is None:
            raise RuntimeError(f"The ppt-master Agent bridge cannot resolve Sandbox {sandbox_id}")
        base_url = getattr(sandbox, "base_url", None)
        if not isinstance(base_url, str):
            raise RuntimeError("The external ppt-master Agent host is available only in Kubernetes AIO Sandboxes")
        host_url = derive_agent_host_url(base_url)
    except BaseException:
        await _drain_ppt_operation(asyncio.create_task(asyncio.to_thread(provider.release, sandbox_id)))
        raise
    raw_writer = get_stream_writer()
    last_reported: dict[str, Any] = {}

    def writer(event: dict[str, Any]) -> None:
        # Retain only normalized receipts already observed during this turn.
        # A lost transport/cancel is not a final usage measurement.
        observation = observed_session_usage(event)
        if observation is not None:
            last_reported["session_usage_observation"] = observation
        if isinstance(event.get("usage"), dict):
            last_reported["usage"] = event["usage"]
        if event.get("type") in {"task_completed", "task_failed", "task_cancelled"}:
            event = {"model_name": "ppt-master-agent-host", **last_reported, **event}
        terminal_receipt = context.get("__ppt_terminal_receipt")
        if terminal_receipt is not None:
            terminal_receipt.capture(event)
        raw_writer(event)

    writer(
        {
            "type": "task_started",
            "task_id": run_id,
            "thread_id": thread_id,
            "description": "ppt-master 官方工作流",
            "model_name": "ppt-master-agent-host",
        }
    )

    timeout = httpx.Timeout(connect=10.0, read=35.0, write=30.0, pool=10.0)
    execution_lease = None
    task_start_attempted = False
    cleanup_guard_retained = False
    try:
        execution_lease = await _acquire_ppt_execution_lease(provider, sandbox_id)
        async with httpx.AsyncClient(
            headers=_auth_headers(app_config.sandbox.sandbox_api_key or ""),
            timeout=timeout,
        ) as client:
            await _wait_for_agent_host(client=client, host_url=host_url)
            task_start_attempted = True
            response = await _start_agent_task_with_retry(
                client=client,
                host_url=host_url,
                payload={
                    "task_id": run_id,
                    "thread_id": thread_id,
                    "runtime_capability": capability,
                    "prompt": prompt,
                    "resume": resume,
                    "delivery_contract": requested_delivery_contract(original_request),
                },
            )
            response.raise_for_status()
            result = await _poll_agent_turn(
                client=client,
                host_url=host_url,
                task_id=run_id,
                thread_id=thread_id,
                writer=writer,
                keep_sandbox_alive=lambda: provider.get(sandbox_id) is not None,
                report_usage=lambda payload: _report_agent_usage(config, runtime, payload),
                provider_receipts=provider_receipts,
            )
    except asyncio.CancelledError:
        try:
            if task_start_attempted:
                async with httpx.AsyncClient(
                    headers=_auth_headers(app_config.sandbox.sandbox_api_key or ""),
                    timeout=httpx.Timeout(5.0), trust_env=False,
                ) as client:
                    stopped = await _cancel_agent_turn(client=client, host_url=host_url, task_id=run_id, thread_id=thread_id)
                stopped = await provider_receipts.merge(stopped, force=True)
                # Preserve only real final/partial turn receipts; never settle
                # cumulative session observations as this run's billed usage.
                usage = _report_agent_usage(config, runtime, stopped)
                observation = observed_session_usage(stopped)
                if usage:
                    last_reported["usage"] = usage
                if observation is not None:
                    last_reported["session_usage_observation"] = observation
            writer({"type": "task_cancelled", "task_id": run_id, "thread_id": thread_id})
        except Exception:
            logger.warning("PPT Agent stop could not be confirmed for run %s", run_id)
            if task_start_attempted and execution_lease is not None:
                _retain_ppt_cleanup_guard(provider, execution_lease, sandbox_id, host_url, run_id, thread_id, app_config.sandbox.sandbox_api_key or "")
                cleanup_guard_retained = True
            writer({"type": "task_failed", "task_id": run_id, "thread_id": thread_id,
                    "error": "当前轮次收到停止请求，但无法核实外部 Agent 已退出；请先检查任务状态，不要反复启动。"})
        raise
    except Exception as exc:
        if task_start_attempted:
            try:
                async with httpx.AsyncClient(headers=_auth_headers(app_config.sandbox.sandbox_api_key or ""), timeout=httpx.Timeout(5.0), trust_env=False) as stop_client:
                    stopped = await _cancel_agent_turn(client=stop_client, host_url=host_url, task_id=run_id, thread_id=thread_id)
                _report_agent_usage(config, runtime, await provider_receipts.merge(stopped, force=True))
            except Exception:
                if execution_lease is not None:
                    _retain_ppt_cleanup_guard(provider, execution_lease, sandbox_id, host_url, run_id, thread_id, app_config.sandbox.sandbox_api_key or "")
                    cleanup_guard_retained = True
        failure_kind = _transport_failure_kind(exc)
        logger.exception(
            "External ppt-master transport failed (thread=%s run=%s sandbox=%s kind=%s)",
            thread_id,
            run_id,
            sandbox_id,
            failure_kind,
        )
        writer(
            {
                "type": "task_failed",
                "task_id": run_id,
                "thread_id": thread_id,
                "error": f"PPT Agent host transport failed before task completion ({failure_kind})",
            }
        )
        raise RuntimeError(f"PPT Agent host transport failed before task completion ({failure_kind})") from exc
    finally:
        if not cleanup_guard_retained:
            await _release_ppt_resources(provider, execution_lease, sandbox_id)

    status = result.get("status")
    final_message = str(result.get("final_message") or "").strip()
    artifacts = [str(path) for path in result.get("artifacts", []) if isinstance(path, str)]
    usage = _report_agent_usage(config, runtime, result)
    observation = observed_session_usage(result)
    if status == "completed":
        writer(
            {
                "type": "task_completed",
                "task_id": run_id,
                "thread_id": thread_id,
                "result": final_message,
                "model_name": "ppt-master-agent-host",
                **({"usage": usage} if usage else {}),
                **({"session_usage_observation": observation} if observation else {}),
            }
        )
        state_update = {
            "messages": [completed_agent_message(run_id, final_message, usage, observation)],
            "artifacts": artifacts,
            "sandbox": {"sandbox_id": sandbox_id},
        }
        return state_update

    error = str(result.get("error") or final_message or f"external Agent ended with status={status}")
    event_type = "task_cancelled" if status == "cancelled" else "task_failed"
    writer(
        {
            "type": event_type,
            "task_id": run_id,
            "thread_id": thread_id,
            "error": error,
            **({"usage": usage} if usage else {}),
            **({"session_usage_observation": observation} if observation else {}),
        }
    )
    raise RuntimeError(f"PPT Agent host failed: {error}")


def make_ppt_master_runner_agent(
    config: RunnableConfig,
    app_config: AppConfig | None = None,
):
    """Compile the one-node protocol bridge used by the runner API process."""

    del config
    effective_config = app_config or get_app_config()

    async def initialize_thread_node(
        state: ThreadState,
    ) -> dict[str, Any]:
        """Publish the deterministic title before the long external turn.

        The sidebar consumes update events while the Agent host is still
        working.  Returning the title from a short first node gives the UI and
        checkpointer an authoritative title immediately instead of leaving a
        long-running presentation thread as ``Untitled`` until completion.
        """

        current_title = str(state.get("title") or "").strip()
        if current_title and current_title.casefold() not in {
            "untitled",
            "new chat",
            "新对话",
        }:
            return {}

        messages = list(state.get("messages") or [])
        user_text = _latest_user_text(messages)
        original_request = _original_ppt_master_request(messages, user_text)
        title = derive_ppt_master_thread_title(
            original_request,
            title_config=effective_config.title,
        )
        return {"title": title} if title else {}

    async def external_agent_node(
        state: ThreadState,
        config: RunnableConfig,
        runtime: Runtime,
    ) -> dict[str, Any]:
        return await _run_external_ppt_master(
            state,
            config,
            runtime,
            app_config=effective_config,
        )

    builder = StateGraph(ThreadState)
    builder.add_node("initialize_ppt_master_thread", initialize_thread_node)
    builder.add_node("external_ppt_master_agent", external_agent_node)
    builder.add_edge(START, "initialize_ppt_master_thread")
    builder.add_edge("initialize_ppt_master_thread", "external_ppt_master_agent")
    builder.add_edge("external_ppt_master_agent", END)
    graph = builder.compile()
    graph.metadata = {"model_name": "ppt-master-agent-host"}
    return graph


__all__ = [
    "PPT_MASTER_AGENT_HOST_PORT",
    "PPT_MASTER_AGENT_HOST_READY_TIMEOUT_SECONDS",
    "PPT_MASTER_AGENT_HOST_REQUEST_RETRY_ATTEMPTS",
    "PPT_MASTER_RUNNER_ASSISTANT_ID",
    "build_ppt_master_runner_system_prompt",
    "codex_event_to_messages",
    "derive_ppt_master_thread_title",
    "derive_agent_host_url",
    "make_ppt_master_runner_agent",
    "requested_delivery_contract",
]
