"""Wire the scheduler's private outbox to the current Work application."""

import os
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from app.integrations.work.config import PRODUCTION_CALLBACK, WorkSettings
from app.integrations.work.notifications import WorkNotificationError, send_work_notification, work_notifications_enabled
from deerflow.persistence.engine import get_session_factory
from deerflow.utils.conversation_url import validated_web_origin
from deerflow.utils.thread_id import validate_thread_id

from .work_notifications import DeliveryResult, WorkNotification, WorkNotificationDispatcher


def _platform_origin(profile: str) -> str:
    default = urlsplit(PRODUCTION_CALLBACK).netloc
    raw = os.getenv("DEER_FLOW_WORK_SCHEDULED_NOTIFICATION_WEB_URL", f"https://{default}" if profile == "production" else "").strip()
    return validated_web_origin(raw, profile=profile)


def _message(notification: WorkNotification, origin: str) -> str:
    # Final replies are user content. Keep external/signed URLs out of the
    # private notification and attach only the server-generated result link.
    title = " ".join(notification.title.split())
    daily = notification.result_text.lstrip().startswith("油气与金融市场日报")
    def source_url(match: re.Match) -> str:
        if daily:
            # Preserve only the original public news hosts, never signed file
            # URLs, arbitrary provider links, credentials or query strings.
            raw = match.group(0)
            suffix = ")" if raw.endswith(")") else ""
            target = raw[:-1] if suffix else raw
            try:
                parsed = urlsplit(target)
                if parsed.scheme == "https" and parsed.hostname in {"oilprice.com", "www.offshore-energy.biz", "www.rigzone.com"} and not parsed.query and not parsed.fragment and parsed.username is None and parsed.password is None and parsed.port in (None, 443):
                    return target + suffix
            except ValueError:
                pass
        return "[链接见平台]"
    summary = re.sub(r"https?://[^\s<>]+", source_url, notification.result_text, flags=re.IGNORECASE)
    parts = [f"【定时任务】{title}已完成"]
    if notification.finished_at:
        try:
            finished = datetime.fromisoformat(notification.finished_at)
            finished = (finished if finished.tzinfo else finished.replace(tzinfo=UTC)).astimezone(ZoneInfo("Asia/Shanghai"))
            parts.append(f"完成时间：{finished:%Y-%m-%d %H:%M}（北京时间）")
        except ValueError:
            pass
    if summary.strip():
        parts.append(summary.strip())
    try:
        thread = validate_thread_id(notification.thread_id)
    except ValueError:
        thread = ""
    label = "点击查看完整日报" if daily else "查看完整结果"
    parts.append((f"[{label}]({origin}/workspace/chats/{thread})" if daily else f"{label}：{origin}/workspace/chats/{thread}") if origin and thread else "请在智能体平台的定时任务运行记录中查看完整结果。")
    return "\n\n".join(parts)


def build_work_notification_dispatcher() -> WorkNotificationDispatcher | None:
    if not work_notifications_enabled():
        return None
    sessions = get_session_factory()
    if sessions is None:
        return None
    work = WorkSettings.from_env()
    origin = _platform_origin(work.profile)

    async def deliver(notification: WorkNotification) -> DeliveryResult:
        try:
            await send_work_notification(
                notification.owner_user_id,
                notification.connection_id,
                _message(notification, origin),
                notification.delivery_id,
                binding_key=notification.binding_key,
                **({"markdown": True} if notification.result_text.lstrip().startswith("油气与金融市场日报") else {}),
            )
        except WorkNotificationError as exc:
            if exc.uncertain:
                return "uncertain"
            return "retry" if exc.stage == "sender_unavailable" else "failed"
        return "sent"

    return WorkNotificationDispatcher(sessions, key=os.getenv("DEER_FLOW_WORK_STATE_KEY", ""), send=deliver, profile=work.profile)
