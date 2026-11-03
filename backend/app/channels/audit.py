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
* Never write secrets or whole message bodies: ``text_preview`` is truncated.
* The blocking-IO gate forbids sync DB calls on the event loop, so recording awaits
  the async repository (or runs as a task) — never ``asyncio.to_thread`` on a
  synchronous ORM call.
"""

from __future__ import annotations

import asyncio
import logging
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

#: Strong references to in-flight fire-and-forget audit tasks. asyncio only keeps
#: weak references to tasks, so without this the recorder could be garbage
#: collected mid-write.
_pending_tasks: set[asyncio.Task[None]] = set()


def text_preview(text: str | None, *, limit: int = TEXT_PREVIEW_CHARS) -> str:
    """Return a bounded, single-purpose preview of a message body."""
    if not text:
        return ""
    collapsed = " ".join(str(text).split())
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
    """
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
        task = asyncio.create_task(record_channel_audit(event))
    except RuntimeError:
        # No running loop (sync context / shutdown): drop the event rather than
        # breaking the caller.
        logger.warning("[ChannelAudit] no event loop available for %s", action)
        return
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


async def drain_channel_audit_tasks() -> None:
    """Await in-flight background audit writes (used by tests and shutdown)."""
    if not _pending_tasks:
        return
    await asyncio.gather(*list(_pending_tasks), return_exceptions=True)