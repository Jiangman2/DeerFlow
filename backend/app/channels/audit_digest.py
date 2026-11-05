"""Periodic "push the bot activity digest into a chat" loop.

Deliberately *not* a ``ScheduledTaskService`` task. That service exists to run
user-authored scheduled runs: it owns run admission, leases, and the global
concurrency budget, all of which are meaningless for "render a table and send a
card". Reusing it would drag audit reporting into the run lifecycle and let a
failed digest consume run budget. A small self-contained loop is the honest
shape.

Behaviour:

* Disabled by default. Reporting volume into a real chat is a policy decision,
  not a default.
* First dispatch happens one full interval after startup, so restarting the
  Gateway never spams the operator with a digest of the last few minutes.
* Nothing is sent for an empty window — an empty digest trains people to ignore
  the channel.
* Every failure is logged and swallowed; a broken digest must never take down
  the Gateway or stop the loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

from app.channels.audit_report import render_digest, resolve_notify_chat_id, send_digest
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.observability.sql import ObservabilityRepository

logger = logging.getLogger(__name__)

ENV_ENABLED = "DEER_FLOW_CHANNEL_AUDIT_DIGEST_ENABLED"
ENV_INTERVAL_HOURS = "DEER_FLOW_CHANNEL_AUDIT_DIGEST_INTERVAL_HOURS"
ENV_WINDOW_HOURS = "DEER_FLOW_CHANNEL_AUDIT_DIGEST_WINDOW_HOURS"
ENV_TITLE = "DEER_FLOW_CHANNEL_AUDIT_DIGEST_TITLE"
ENV_CHANNEL = "DEER_FLOW_CHANNEL_AUDIT_DIGEST_CHANNEL"

DEFAULT_INTERVAL_HOURS = 24
DEFAULT_TITLE = "机器人使用日报"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_hours(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(float(raw))
    except ValueError:
        return default
    return max(1, min(value, 24 * 30))


def digest_settings() -> dict[str, Any]:
    """Resolve the digest loop settings from the environment."""
    interval = _env_hours(ENV_INTERVAL_HOURS, DEFAULT_INTERVAL_HOURS)
    return {
        "enabled": _env_flag(ENV_ENABLED, False),
        "interval_hours": interval,
        # Default window equals the interval: a daily digest covers a day.
        "window_hours": _env_hours(ENV_WINDOW_HOURS, interval),
        "title": (os.environ.get(ENV_TITLE) or "").strip() or DEFAULT_TITLE,
        "channel_name": (os.environ.get(ENV_CHANNEL) or "").strip() or "feishu",
    }


class ChannelAuditDigestService:
    """Background loop that periodically pushes an audit digest into a chat."""

    def __init__(
        self,
        *,
        interval_hours: int = DEFAULT_INTERVAL_HOURS,
        window_hours: int | None = None,
        title: str = DEFAULT_TITLE,
        channel_name: str = "feishu",
        chat_id: str | None = None,
    ) -> None:
        self.interval_hours = max(1, int(interval_hours))
        self.window_hours = max(1, int(window_hours or interval_hours))
        self.title = title
        self.channel_name = channel_name
        self.chat_id = chat_id
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run_loop(), name="channel-audit-digest")
        logger.info(
            "[Audit] digest loop started: every %dh, window=%dh, channel=%s, chat=%s",
            self.interval_hours,
            self.window_hours,
            self.channel_name,
            self.chat_id or resolve_notify_chat_id(self.channel_name) or "<unconfigured>",
        )

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: B014 - shutdown must not raise
            pass
        logger.info("[Audit] digest loop stopped")

    # -- work --------------------------------------------------------------

    async def _run_loop(self) -> None:
        interval = self.interval_hours * 3600
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
                return  # stop() fired
            except TimeoutError:
                pass
            try:
                await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[Audit] periodic digest failed; will retry next interval")

    def _current_window_bucket(self) -> str:
        """Absolute-time bucket id shared by every replica.

        Flooring ``now`` to the interval means all Pods — regardless of when each
        started its loop — compute the same bucket for the same wall-clock window,
        so the dedup claim below collapses their duplicate dispatches into one.
        """
        slot = int(datetime.now(UTC).timestamp() // (self.interval_hours * 3600))
        return f"{self.interval_hours}h:{slot}"

    async def dispatch_once(self) -> dict[str, Any]:
        """Aggregate the window and push one digest. Safe to call from tests.

        Cross-replica safe: the window bucket is claimed in SQL first, so with the
        digest enabled across many Gateway Pods only one of them queries and sends
        for a given window; the rest short-circuit before doing any work.
        """
        repo = ObservabilityRepository(get_session_factory())
        window_bucket = self._current_window_bucket()
        claimed = await repo.claim_digest_window(channel_name=self.channel_name, window_bucket=window_bucket)
        if not claimed:
            logger.info("[Audit] digest skipped: window %s already claimed by another replica", window_bucket)
            return {"sent": False, "reason": "claimed by another replica", "events": 0, "window_bucket": window_bucket}

        try:
            summary = await repo.audit_summary(
                hours=self.window_hours,
                resource_type="channel",
                error_samples=20,
                bucket_minutes=60,
            )
            events = int((summary.get("totals") or {}).get("events") or 0)
            if not events:
                logger.info("[Audit] digest skipped: no channel events in the last %dh", self.window_hours)
                return {"sent": False, "reason": "no events", "events": 0, "window_bucket": window_bucket}

            digest = render_digest(summary, title=self.title)
            delivery = await send_digest(digest, chat_id=self.chat_id, channel_name=self.channel_name)
            delivery["events"] = events
            delivery["window_bucket"] = window_bucket
            if delivery.get("sent"):
                await repo.finalize_digest_window(
                    channel_name=self.channel_name,
                    window_bucket=window_bucket,
                    chat_id=delivery.get("chat_id"),
                    events=events,
                )
                logger.info("[Audit] digest delivered: events=%d chat_id=%s", events, delivery.get("chat_id"))
            else:
                logger.warning("[Audit] digest not delivered: %s", delivery.get("reason"))
            return delivery
        except Exception:
            # Release the claim so a later tick (or another replica) can retry this
            # window instead of it being silently skipped until the next bucket.
            await repo.release_digest_window(channel_name=self.channel_name, window_bucket=window_bucket)
            raise


_service: ChannelAuditDigestService | None = None


async def start_channel_audit_digest(window_hours: int | None = None) -> ChannelAuditDigestService | None:
    """Start the digest loop if enabled. Returns ``None`` when disabled."""
    global _service
    if _service is not None:
        return _service
    settings = digest_settings()
    if not settings["enabled"]:
        logger.debug("[Audit] digest loop disabled (%s not set)", ENV_ENABLED)
        return None
    _service = ChannelAuditDigestService(
        interval_hours=settings["interval_hours"],
        window_hours=window_hours or settings["window_hours"],
        title=settings["title"],
        channel_name=settings["channel_name"],
    )
    await _service.start()
    return _service


async def stop_channel_audit_digest() -> None:
    global _service
    service = _service
    _service = None
    if service is not None:
        await service.stop()