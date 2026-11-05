"""Render channel audit digests and deliver them back into an IM channel.

The aggregation itself lives in the harness
(:meth:`deerflow.persistence.observability.sql.ObservabilityRepository.audit_summary`)
because counting audit rows is not a channel-specific concern. This module is the
product-facing half: turn that summary into something a human can read in a chat
window, and hand it to whichever channel can deliver it.

Design notes:

* The digest is rendered as Markdown **bullets**, never tables. Feishu interactive
  cards carry a single ``markdown`` element built by ``FeishuChannel._build_card_content``,
  and card markdown (``lark_md``) does not render Markdown tables — they collapse
  into a wall of pipe characters. Bullets survive every renderer we have.
* Delivery reuses the normal outbound bus instead of reaching into the Feishu API
  client, so a digest goes through exactly the same send path (retries, logging,
  connection credentials) as an agent reply. ``thread_ts`` is deliberately left
  empty: a digest is a fresh card, not a reply threaded under some user's message,
  and only the empty ``thread_ts`` branch reaches ``_create_card``.
* The target chat is *not* inferred. We read an explicit ``audit_notify_chat_id``
  from the channel config, with an environment override for deployments that do not
  want to edit config.yaml. When neither is set we report that back instead of
  guessing a chat and spraying operational data into the wrong conversation.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.channels.message_bus import OutboundMessage
from app.channels.service import get_channel_service

logger = logging.getLogger(__name__)

#: Environment override for the digest target chat (useful in containers).
AUDIT_NOTIFY_CHAT_ENV = "DEER_FLOW_CHANNEL_AUDIT_CHAT_ID"

#: Feishu rejects oversized cards; keep well under the limit and say so.
MAX_CARD_CHARS = 12000

#: How many rows to show per bucket before collapsing into "... 还有 N 项".
DEFAULT_TOP_N = 8


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _short_time(value: Any) -> str:
    """Trim an ISO timestamp down to ``MM-DD HH:MM`` for chat display."""
    text = str(value or "")
    if not text:
        return "-"
    # Audit timestamps are timezone-aware ISO strings in business time; the date
    # and the clock are the only parts a reader needs in a chat card.
    try:
        return f"{text[5:10]} {text[11:16]}" if len(text) >= 16 else text
    except Exception:  # pragma: no cover - defensive, str slicing cannot raise
        return text


def resolve_notify_chat_id(channel_name: str = "feishu") -> str | None:
    """Resolve the chat that receives digests, or ``None`` when unconfigured."""
    env_value = (os.environ.get(AUDIT_NOTIFY_CHAT_ENV) or "").strip()
    if env_value:
        return env_value

    service = get_channel_service()
    if service is None:
        return None
    config = service.get_channel_config(channel_name) or {}
    value = config.get("audit_notify_chat_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def render_digest(
    summary: dict[str, Any],
    *,
    title: str | None = None,
    top_n: int = DEFAULT_TOP_N,
    error_limit: int = 5,
) -> str:
    """Render an ``audit_summary`` payload as a chat-friendly Markdown digest."""
    window = summary.get("window") or {}
    totals = summary.get("totals") or {}
    events = int(totals.get("events") or 0)
    success = int(totals.get("success") or 0)
    failure = int(totals.get("failure") or 0)

    heading = title or "机器人使用日志摘要"
    lines: list[str] = [f"**{heading}**"]

    start_at = _short_time(window.get("start_at"))
    end_at = _short_time(window.get("end_at"))
    hours = window.get("hours")
    span = f"{start_at} → {end_at}"
    if hours:
        span += f"（近 {hours} 小时）"
    lines.append(f"统计窗口：{span}")

    if not events:
        lines.append("")
        lines.append("这个窗口内没有任何机器人使用记录。")
        return "\n".join(lines)

    lines.append("")
    lines.append("**总览**")
    lines.append(f"- 事件总数：{_fmt_int(events)}（成功 {_fmt_int(success)} / 失败 {_fmt_int(failure)}）")
    lines.append(f"- 涉及用户：{_fmt_int(len(summary.get('by_user') or []))} 人")
    if events:
        lines.append(f"- 失败率：{failure / events * 100:.1f}%")
    if totals.get("truncated"):
        lines.append("- 注意：窗口内记录过多，统计基于最近的一部分记录")

    by_action = summary.get("by_action") or []
    if by_action:
        lines.append("")
        lines.append("**按事件类型**")
        for item in by_action[:top_n]:
            count = _fmt_int(item.get("count"))
            fails = int(item.get("failure") or 0)
            suffix = f"（失败 {_fmt_int(fails)}）" if fails else ""
            lines.append(f"- {item.get('action')}：{count} 次{suffix}")
        if len(by_action) > top_n:
            lines.append(f"- … 另有 {len(by_action) - top_n} 类事件")

    by_user = summary.get("by_user") or []
    if by_user:
        lines.append("")
        lines.append("**最活跃用户**")
        for item in by_user[:top_n]:
            who = item.get("user_email") or item.get("user_id") or "未知用户"
            count = _fmt_int(item.get("count"))
            fails = int(item.get("failure") or 0)
            suffix = f"，失败 {_fmt_int(fails)}" if fails else ""
            lines.append(f"- {who}：{count} 次{suffix}（最近 {_short_time(item.get('last_at'))}）")
        if len(by_user) > top_n:
            lines.append(f"- … 另有 {len(by_user) - top_n} 位用户")

    by_channel = summary.get("by_channel") or []
    if len(by_channel) > 1:
        lines.append("")
        lines.append("**按渠道**")
        for item in by_channel[:top_n]:
            lines.append(f"- {item.get('channel')}：{_fmt_int(item.get('count'))} 次")

    timeline = summary.get("timeline") or []
    if len(timeline) > 1:
        lines.append("")
        lines.append("**时间分布**")
        peak = max(timeline, key=lambda item: int(item.get("count") or 0))
        lines.append(f"- 共 {len(timeline)} 个统计区间，峰值 {_short_time(peak.get('bucket'))}：{_fmt_int(peak.get('count'))} 次")

    errors = summary.get("errors") or []
    if errors:
        lines.append("")
        lines.append(f"**失败样本**（最多 {min(len(errors), error_limit)} 条）")
        for index, item in enumerate(errors[:error_limit], start=1):
            who = item.get("user_email") or item.get("user_id") or "未知用户"
            parts = [f"{index}. {_short_time(item.get('created_at'))} {item.get('action')} · {who}"]
            reason = item.get("reason")
            if reason:
                parts.append(f"原因标记：{reason}")
            error = str(item.get("error") or "").strip()
            if error:
                parts.append(error[:300])
            lines.append("　".join(parts))
        if len(errors) > error_limit:
            lines.append(f"　… 另有 {len(errors) - error_limit} 条失败记录")

    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…（内容过长已截断）"


async def send_digest(
    text: str,
    *,
    chat_id: str | None = None,
    channel_name: str = "feishu",
) -> dict[str, Any]:
    """Publish a digest card into an IM chat through the normal outbound bus.

    Returns a small dict describing what happened so a route can report it back
    to the operator instead of silently dropping the message.
    """
    service = get_channel_service()
    if service is None:
        return {"sent": False, "channel": channel_name, "chat_id": chat_id, "reason": "channel service is not running"}

    target = (chat_id or "").strip() or resolve_notify_chat_id(channel_name)
    if not target:
        return {
            "sent": False,
            "channel": channel_name,
            "chat_id": None,
            "reason": f"no target chat configured (set channels.{channel_name}.audit_notify_chat_id or {AUDIT_NOTIFY_CHAT_ENV})",
        }

    channel = service.get_channel(channel_name)
    if channel is None or not getattr(channel, "is_running", False):
        return {"sent": False, "channel": channel_name, "chat_id": target, "reason": f"{channel_name} channel is not running"}

    body = _truncate(text.strip(), MAX_CARD_CHARS)
    if not body:
        return {"sent": False, "channel": channel_name, "chat_id": target, "reason": "digest is empty"}

    message = OutboundMessage(
        channel_name=channel_name,
        chat_id=target,
        # Empty thread_id and thread_ts are load-bearing: they select the
        # "fresh card" branch instead of replying under a user's message.
        thread_id="",
        text=body,
        is_final=True,
    )
    await service.bus.publish_outbound(message)
    logger.info("[Audit] digest dispatched: channel=%s chat_id=%s chars=%d", channel_name, target, len(body))
    return {"sent": True, "channel": channel_name, "chat_id": target, "reason": None, "chars": len(body)}