"""Wire the scheduler's private outbox to the current Work application."""

import os
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from app.integrations.work.config import PRODUCTION_CALLBACK, WorkSettings
from app.integrations.work.notifications import WorkNotificationError, send_work_notification, work_notifications_enabled
from deerflow.persistence.engine import get_session_factory
from deerflow.utils.thread_id import validate_thread_id

from .work_notifications import DeliveryResult, WorkNotification, WorkNotificationDispatcher


def _platform_origin(profile: str) -> str:
    default = urlsplit(PRODUCTION_CALLBACK).netloc
    raw = os.getenv("DEER_FLOW_WORK_SCHEDULED_NOTIFICATION_WEB_URL", f"https://{default}" if profile == "production" else "").strip()
    parsed = urlsplit(raw)
    local = profile == "uat" and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return ""
    return raw.rstrip("/") if parsed.scheme == "https" or local else ""


def _message(notification: WorkNotification, origin: str) -> str:
    # Final replies are user content. Keep external/signed URLs out of the
    # private notification and attach only the server-generated result link.
    title = " ".join(notification.title.split())
    summary = re.sub(r"https?://[^\s<>]+", "[链接见平台]", notification.result_text, flags=re.IGNORECASE)
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
    parts.append(f"查看完整结果：{origin}/workspace/chats/{thread}" if origin and thread else "请在智能体平台的定时任务运行记录中查看完整结果。")
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
            )
        except WorkNotificationError as exc:
            if exc.uncertain:
                return "uncertain"
            return "retry" if exc.stage == "sender_unavailable" else "failed"
        return "sent"

    return WorkNotificationDispatcher(sessions, key=os.getenv("DEER_FLOW_WORK_STATE_KEY", ""), send=deliver, profile=work.profile)
