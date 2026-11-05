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


def completed_agent_message(run_id: str, final_message: str) -> AIMessage:
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
            }
        },
    )


def _bounded_text(value: object, *, limit: int = 12_000) -> str:
    text = value if isinstance(value, str) else ""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... (truncated by host bridge)"


def codex_event_to_messages(event: dict[str, Any]) -> list[BaseMessage]:
    """Map public Codex JSONL events onto DeerFlow's durable task timeline."""

    if event.get("type") not in {"item.started", "item.completed"}:
        return []
    item = event.get("item")
    if not isinstance(item, dict):
        return []
    item_type = item.get("type")
    item_id = str(item.get("id") or "ppt-master-agent-step")

    if item_type == "agent_message":
        text = _bounded_text(item.get("text")).strip()
        return [AIMessage(id=f"{item_id}-message", content=text)] if text else []

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
) -> dict[str, Any]:
    after_seq = 0
    message_index = 0
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
                for message in codex_event_to_messages(envelope["payload"]):
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
            return payload
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


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
    sandbox_id = await provider.acquire_async(thread_id, user_id=resolve_runtime_user_id(runtime))
    sandbox = provider.get(sandbox_id)
    if sandbox is None:
        raise RuntimeError(f"The ppt-master Agent bridge cannot resolve Sandbox {sandbox_id}")
    base_url = getattr(sandbox, "base_url", None)
    if not isinstance(base_url, str):
        raise RuntimeError("The external ppt-master Agent host is available only in Kubernetes AIO Sandboxes")
    host_url = derive_agent_host_url(base_url)
    writer = get_stream_writer()
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
    try:
        acquire_execution_lease = getattr(provider, "acquire_execution_lease", None)
        if not callable(acquire_execution_lease):
            raise RuntimeError("The configured Sandbox provider does not support external execution leases")
        execution_lease = await asyncio.to_thread(acquire_execution_lease, sandbox_id)
        async with httpx.AsyncClient(
            headers=_auth_headers(app_config.sandbox.sandbox_api_key or ""),
            timeout=timeout,
        ) as client:
            await _wait_for_agent_host(client=client, host_url=host_url)
            response = await _start_agent_task_with_retry(
                client=client,
                host_url=host_url,
                payload={
                    "task_id": run_id,
                    "thread_id": thread_id,
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
            )
    except asyncio.CancelledError:
        try:
            async with httpx.AsyncClient(
                headers=_auth_headers(app_config.sandbox.sandbox_api_key or ""),
                timeout=httpx.Timeout(5.0),
            ) as client:
                await client.post(f"{host_url}/v1/tasks/{run_id}/cancel")
        except Exception:
            logger.warning("Failed to cancel external ppt-master Agent task %s", run_id, exc_info=True)
        writer({"type": "task_cancelled", "task_id": run_id, "thread_id": thread_id})
        raise
    except Exception as exc:
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
        if execution_lease is not None:
            release_execution_lease = getattr(provider, "release_execution_lease", None)
            if callable(release_execution_lease):
                await asyncio.to_thread(release_execution_lease, execution_lease, sandbox_id)
        await asyncio.to_thread(provider.release, sandbox_id)

    status = result.get("status")
    final_message = str(result.get("final_message") or "").strip()
    artifacts = [str(path) for path in result.get("artifacts", []) if isinstance(path, str)]
    usage = _report_agent_usage(config, runtime, result)
    if status == "completed":
        writer(
            {
                "type": "task_completed",
                "task_id": run_id,
                "thread_id": thread_id,
                "result": final_message,
                "model_name": "ppt-master-agent-host",
                **({"usage": usage} if usage else {}),
            }
        )
        state_update = {
            "messages": [completed_agent_message(run_id, final_message)],
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
