"""Channel activity audit — records IM user operations into the ``audit_logs`` table.

Every user action that reaches the channel layer produces exactly one audit row:

    channel.message.received   an inbound message passed the adapter and is being handled
    channel.message.rejected   an inbound message was refused before any thread/run existed
    channel.message.skipped    an adapter dropped the message before the bus (e.g. group
                               message without an @-mention)
    channel.command            a /command was executed
    channel.run.completed      the agent run behind a chat message finished
    channel.run.failed         the agent run behind a chat message failed

Rows land in the same table the admin observability page already reads, so they are
filterable by person / time / event alongside HTTP audit rows.

Design constraints (see backend/AGENTS.md):

* Auditing must never break message handling. ``record_channel_audit`` swallows and
  logs failures; ``schedule_channel_audit`` additionally never blocks the caller.
* Auditing must never apply unbounded back-pressure to the Gateway either: events
  funnel through one bounded buffer drained by a capped pool of writers, so a slow
  database sheds the oldest pending events (counted + logged) instead of piling up
  unbounded in-flight writes.
* Never write secrets or whole message bodies: ``text_preview`` redacts credentials
  and PII, is truncated, and can be disabled entirely via
  ``DEER_FLOW_CHANNEL_AUDIT_STORE_BODY=false``.
* The blocking-IO gate forbids sync DB calls on the event loop, so recording awaits
  the async repository (or runs as a task) — never ``asyncio.to_thread`` on a
  synchronous ORM call.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from app.channels.message_bus import InboundMessage, InboundMessageType

logger = logging.getLogger(__name__)

ACTION_MESSAGE_RECEIVED = "channel.message.received"
ACTION_MESSAGE_REJECTED = "channel.message.rejected"
ACTION_MESSAGE_SKIPPED = "channel.message.skipped"
ACTION_COMMAND = "channel.command"
ACTION_RUN_COMPLETED = "channel.run.completed"
ACTION_RUN_FAILED = "channel.run.failed"

#: Every action this module writes. The query layer uses it to keep the
#: "channel events" filter in sync with what is actually recorded.
CHANNEL_AUDIT_ACTIONS = (
    ACTION_MESSAGE_RECEIVED,
    ACTION_MESSAGE_REJECTED,
    ACTION_MESSAGE_SKIPPED,
    ACTION_COMMAND,
    ACTION_RUN_COMPLETED,
    ACTION_RUN_FAILED,
)

TEXT_PREVIEW_CHARS = 500

#: Environment knobs for the bounded write queue and the body-capture policy.
ENV_QUEUE_SIZE = "DEER_FLOW_CHANNEL_AUDIT_QUEUE_SIZE"
ENV_WORKERS = "DEER_FLOW_CHANNEL_AUDIT_WORKERS"
ENV_STORE_BODY = "DEER_FLOW_CHANNEL_AUDIT_STORE_BODY"
DEFAULT_QUEUE_SIZE = 1000
DEFAULT_WORKER_COUNT = 2


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _store_body_enabled() -> bool:
    """Whether message/error text may be persisted at all (default: yes)."""
    raw = (os.environ.get(ENV_STORE_BODY) or "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


# -- body desensitisation --------------------------------------------------
# Best-effort redaction applied to every preview *before* it is truncated and
# persisted. The audit table is operator-facing and must never become a secret
# store, so we mask the credential and PII shapes that most often leak into a
# chat body. Matching is deliberately conservative: well-known token/secret/PII
# patterns only, so ordinary prose (including Chinese text) is left untouched.
_SECRET_KV_RE = re.compile(
    r"(?i)\b(app_secret|client_secret|secret|password|passwd|pwd|access_token|"
    r"refresh_token|token|authorization|api[_-]?key)\b(\s*[:=]\s*)(\S+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{6,}\b")
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")
_CN_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_CN_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+\-]+)@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)\b")


def _mask_email(match: re.Match[str]) -> str:
    local, domain = match.group(1), match.group(2)
    if len(local) <= 2:
        masked = "*" * len(local)
    else:
        masked = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked}@{domain}"


def redact_preview(text: str | None) -> str:
    """Mask credentials and PII in a message/error body (best effort)."""
    if not text:
        return ""
    redacted = _SECRET_KV_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    redacted = _BEARER_RE.sub("Bearer [redacted]", redacted)
    redacted = _OPENAI_KEY_RE.sub("[redacted]", redacted)
    redacted = _SLACK_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _GITHUB_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _CN_MOBILE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], redacted)
    redacted = _CN_ID_RE.sub(lambda m: m.group(0)[:6] + "********" + m.group(0)[-4:], redacted)
    return _EMAIL_RE.sub(_mask_email, redacted)


# -- bounded background write queue ---------------------------------------
# A single process-wide buffer with a hard ``maxlen`` sheds the *oldest* pending
# event when full (a deque evicts from the left on append), so a slow database
# cannot make in-flight audit writes grow without bound. Writers are spawned on
# whichever event loop calls :func:`schedule_channel_audit` (the Gateway main
# loop, or a channel adapter's own loop) and capped at ``_MAX_INFLIGHT``, so the
# buffer — not the task count — is the only thing that grows, and it is bounded.
_BUFFER_LOCK = threading.Lock()
_MAX_INFLIGHT = _env_int(ENV_WORKERS, DEFAULT_WORKER_COUNT, minimum=1, maximum=64)
_buffer: deque[dict[str, Any]] = deque(maxlen=_env_int(ENV_QUEUE_SIZE, DEFAULT_QUEUE_SIZE, minimum=1, maximum=100_000))
_inflight = 0
_dropped = 0
_last_drop_warning = 0.0
_writer_tasks: set[asyncio.Task[None]] = set()


def text_preview(text: str | None, *, limit: int = TEXT_PREVIEW_CHARS) -> str:
    """Return a bounded, redacted, single-purpose preview of a message body.

    Returns an empty string when body capture is disabled via
    ``DEER_FLOW_CHANNEL_AUDIT_STORE_BODY=false``, so a deployment that must not
    persist any chat text keeps only metadata (action / user / chat / time).
    """
    if not text or not _store_body_enabled():
        return ""
    collapsed = redact_preview(" ".join(str(text).split()))
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + "…"


def _status_for(result: str) -> int:
    return 200 if result == "success" else 500


def build_channel_audit_event(
    *,
    action: str,
    channel_name: str,
    result: str = "success",
    status_code: int | None = None,
    msg: InboundMessage | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    error: str | None = None,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``record_audit`` payload for one channel event.

    ``user_id`` is the DeerFlow user that owns the channel binding, because every
    admin filter and digest groups by that id. The platform identity (Feishu
    open_id etc.) is preserved in ``details_json`` for unbound traffic, where no
    DeerFlow user exists yet.
    """
    detail: dict[str, Any] = dict(details or {})
    if msg is not None:
        detail.setdefault("channel", msg.channel_name)
        detail.setdefault("chat_id", msg.chat_id)
        detail.setdefault("platform_user_id", msg.user_id)
        if msg.connection_id:
            detail.setdefault("connection_id", msg.connection_id)
        if msg.topic_id:
            detail.setdefault("topic_id", msg.topic_id)
        if msg.workspace_id:
            detail.setdefault("workspace_id", msg.workspace_id)
        chat_type = (msg.metadata or {}).get("chat_type")
        if chat_type:
            detail.setdefault("chat_type", chat_type)
        if msg.files:
            detail.setdefault("file_count", len(msg.files))
        if msg.text:
            detail.setdefault("text_preview", text_preview(msg.text))
        if msg.msg_type is InboundMessageType.COMMAND:
            detail.setdefault("message_type", "command")
    else:
        detail.setdefault("channel", channel_name)
    if thread_id:
        detail.setdefault("thread_id", thread_id)
    if run_id:
        detail.setdefault("run_id", run_id)
    if error:
        detail["error_preview"] = text_preview(error, limit=1000)

    resolved_thread = thread_id or (detail.get("thread_id") if isinstance(detail.get("thread_id"), str) else None)
    return {
        "created_at": datetime.now(UTC),
        "user_id": (msg.owner_user_id if msg is not None else None) or None,
        "user_email": None,
        "user_role": None,
        "action": action,
        "resource_type": "channel",
        "resource_id": resolved_thread or (msg.chat_id if msg is not None else None),
        "method": "EVENT",
        "path": f"channel/{channel_name}/{(msg.msg_type.value if msg is not None else 'event')}",
        "status_code": status_code if status_code is not None else _status_for(result),
        "result": result,
        "duration_ms": max(0, int(duration_ms)),
        "client_ip": None,
        "user_agent": None,
        "error": text_preview(error, limit=4000) if error else None,
        "details_json": detail,
    }


def _repository():
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.observability.sql import ObservabilityRepository

    return ObservabilityRepository(get_session_factory())


async def record_channel_audit(event: dict[str, Any]) -> None:
    """Persist one channel audit row. Never raises."""
    try:
        await _repository().record_audit(event)
    except Exception:
        logger.exception("[ChannelAudit] failed to record %s", event.get("action"))


def _spawn_writer() -> None:
    """Start one bounded-concurrency writer on the running loop, if under budget.

    Never raises: when there is no running loop (a sync/shutdown caller) or the
    concurrency budget is exhausted, the event simply stays buffered for a later
    writer or for :func:`drain_channel_audit_tasks`.
    """
    global _inflight
    with _BUFFER_LOCK:
        if _inflight >= _MAX_INFLIGHT or not _buffer:
            return
        _inflight += 1
    try:
        task = asyncio.get_running_loop().create_task(_write_buffered())
    except RuntimeError:
        with _BUFFER_LOCK:
            _inflight -= 1
        return
    _writer_tasks.add(task)
    task.add_done_callback(_writer_tasks.discard)


async def _write_buffered() -> None:
    """Drain buffered events sequentially until the buffer is empty."""
    global _inflight
    try:
        while True:
            try:
                event = _buffer.popleft()
            except IndexError:
                return
            await record_channel_audit(event)
    finally:
        with _BUFFER_LOCK:
            _inflight -= 1
        # The buffer may have refilled while we were writing; keep draining it.
        if _buffer:
            _spawn_writer()


def schedule_channel_audit(
    *,
    action: str,
    channel_name: str,
    result: str = "success",
    status_code: int | None = None,
    msg: InboundMessage | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    error: str | None = None,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> None:
    """Record an audit row in the background so the message path never waits.

    Use this from latency-sensitive call sites (the inbound dispatcher). Call
    sites that already sit in an error path can ``await record_channel_audit``.

    The event is appended to a bounded buffer drained by a capped pool of writers,
    so a slow database sheds the oldest pending events (counted and logged)
    instead of accumulating unbounded in-flight writes. Never blocks, never raises.
    """
    global _dropped, _last_drop_warning
    try:
        event = build_channel_audit_event(
            action=action,
            channel_name=channel_name,
            result=result,
            status_code=status_code,
            msg=msg,
            thread_id=thread_id,
            run_id=run_id,
            error=error,
            duration_ms=duration_ms,
            details=details,
        )
    except Exception:
        logger.exception("[ChannelAudit] failed to build event %s", action)
        return

    with _BUFFER_LOCK:
        maxlen = _buffer.maxlen
        overflow = maxlen is not None and len(_buffer) >= maxlen
        if overflow:
            _dropped += 1
        _buffer.append(event)
        dropped_total = _dropped
        now = time.monotonic()
        warn = overflow and now - _last_drop_warning >= 5.0
        if warn:
            _last_drop_warning = now
    if warn:
        logger.warning(
            "[ChannelAudit] audit buffer full (maxlen=%d); dropped %d event(s) so far",
            maxlen,
            dropped_total,
        )
    _spawn_writer()


async def drain_channel_audit_tasks() -> None:
    """Flush buffered audit writes and await in-flight writers (tests + shutdown)."""
    while True:
        _spawn_writer()
        with _BUFFER_LOCK:
            pending = len(_buffer)
            inflight = _inflight
        if pending == 0 and inflight == 0:
            return
        if pending and inflight == 0:
            # No writer could be scheduled (loop/budget edge): drain one event
            # directly so shutdown and tests always make forward progress.
            try:
                event = _buffer.popleft()
            except IndexError:
                event = None
            if event is not None:
                await record_channel_audit(event)
                continue
        await asyncio.sleep(0.005)
