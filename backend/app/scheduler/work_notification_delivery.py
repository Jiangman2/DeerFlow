"""Wire the scheduler's private outbox to the current Work application."""

import asyncio
import os
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select

from app.integrations.work.config import PRODUCTION_CALLBACK, WorkSettings
from app.integrations.work.notifications import WorkNotificationError, send_work_notification, work_notifications_enabled
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.scheduled_task_runs.model import ScheduledTaskRunRow
from deerflow.persistence.scheduled_tasks.model import ScheduledTaskRow
from deerflow.utils.conversation_url import validated_web_origin
from deerflow.utils.thread_id import validate_thread_id

from .work_notifications import DeliveryResult, WorkNotification, WorkNotificationDispatcher, _notification_summary


def _platform_origin(profile: str) -> str:
    default = urlsplit(PRODUCTION_CALLBACK).netloc
    raw = os.getenv("DEER_FLOW_WORK_SCHEDULED_NOTIFICATION_WEB_URL", f"https://{default}" if profile == "production" else "").strip()
    return validated_web_origin(raw, profile=profile)


def _daily_reader_url(value: str, origin: str, thread: str) -> str | None:
    """Accept only an unsigned reader URL for this exact task and trusted origin."""
    try:
        target, trusted = urlsplit(value), urlsplit(origin)
        if not thread or target.scheme != "https" or trusted.scheme != "https":
            return None
        approved_aliases = {"cnoocclaw.cnooc.com.cn", "heai.cnooc.com.cn"}
        same_platform = target.hostname == trusted.hostname or (target.hostname in approved_aliases and trusted.hostname in approved_aliases)
        if not trusted.hostname or not same_platform or target.port not in (None, 443) or trusted.port not in (None, 443) or target.username is not None or target.password is not None or target.query or target.fragment:
            return None
        match = re.fullmatch(r"/reports/energy-daily/" + re.escape(thread) + r"/([0-9]{4}-[0-9]{2}-[0-9]{2})\.html", target.path)
        if not match or date.fromisoformat(match.group(1)).isoformat() != match.group(1):
            return None
        return origin.rstrip("/") + target.path
    except (TypeError, ValueError):
        return None


def _message(notification: WorkNotification, origin: str) -> str:
    try:
        thread = validate_thread_id(notification.thread_id)
    except ValueError:
        thread = ""
    footer_pattern = r"\[点击查看日报网页\]\((https?://[^\s()<>]+)\)"
    footer_lines = [re.fullmatch(footer_pattern, line.strip()) for line in notification.result_text.splitlines()]
    daily = notification.result_text.lstrip().startswith("油气与金融市场日报") or any(footer_lines)
    if daily:
        readers = []
        for footer in footer_lines:
            reader = _daily_reader_url(footer.group(1), origin, thread) if footer else None
            if reader and reader not in readers:
                readers.append(reader)
        # This task's authenticated HTML link is the entire Work message.
        # No title, timestamp, body, attachment claim or conversation fallback.
        return "[点击查看日报网页](" + readers[0] + ")" if len(readers) == 1 else ""
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
    parts.append(f"查看完整结果：{origin}/workspace/chats/{thread}" if origin and thread else "请在智能体平台的定时任务运行记录中查看完整结果。")
    return "\n\n".join(parts)


def _is_reader_message(text: str) -> bool:
    return bool(re.fullmatch(r"\[点击查看日报网页\]\(https://[^\s()<>]+\)", text))


async def _ready_daily_message(notification: WorkNotification, origin: str, sessions) -> tuple[str, str | None]:
    """Refresh an early completion snapshot without exposing another run's text.

    A peer scheduler can observe success before journal completion fields have
    been written. The queued notification then has no result, even though the
    final answer is available by send time. Resolve this exact occurrence and
    owner; a missing daily-reader link waits in the existing bounded outbox
    before any external send, instead of becoming a generic chat-link notice.
    """
    run_identity = and_(
        RunRow.run_id == ScheduledTaskRunRow.run_id,
        RunRow.thread_id == notification.thread_id,
        RunRow.user_id == notification.owner_user_id,
        RunRow.status == "success",
        RunRow.metadata_json["scheduled_task_id"].as_string() == notification.task_id,
        RunRow.metadata_json["scheduled_task_run_id"].as_string() == notification.task_run_id,
    )
    statement = (
        select(
            func.substr(ScheduledTaskRow.prompt, 1, 2048),
            RunRow.run_id,
            func.substr(RunRow.last_ai_message, 1, 2000),
            func.substr(RunRow.kwargs_json["input"]["messages"][0]["content"].as_string(), 1, 2048),
        )
        .select_from(ScheduledTaskRunRow)
        .join(ScheduledTaskRow, ScheduledTaskRow.id == ScheduledTaskRunRow.task_id)
        .outerjoin(RunRow, run_identity)
        .where(
            ScheduledTaskRunRow.id == notification.task_run_id,
            ScheduledTaskRunRow.task_id == notification.task_id,
            ScheduledTaskRunRow.thread_id == notification.thread_id,
            ScheduledTaskRunRow.status == "success",
            ScheduledTaskRow.user_id == notification.owner_user_id,
        )
        .limit(1)
    )
    async with sessions() as session:
        row = (await session.execute(statement)).first()
    if row is None:
        return "failed", None
    prompt, run_id, latest, original_input = row
    task_input = original_input if isinstance(original_input, str) else prompt
    explicit_daily = isinstance(task_input, str) and re.match(r"^\s*/energy-market-daily(?:\s|$)", task_input) is not None
    if not run_id:
        return ("failed", None) if explicit_daily else ("unchanged", None)
    summary = _notification_summary(latest)
    refreshed = _message(replace(notification, result_text=summary), origin)
    if _is_reader_message(refreshed):
        return "ready", refreshed
    if explicit_daily:
        # "retry" is a local pre-send wait. The existing dispatcher caps it;
        # unknown outcomes after contacting Work retain its no-resend policy.
        return "retry", None
    return "unchanged", None


def build_work_notification_dispatcher() -> WorkNotificationDispatcher | None:
    if not work_notifications_enabled():
        return None
    sessions = get_session_factory()
    if sessions is None:
        return None
    work = WorkSettings.from_env()
    origin = _platform_origin(work.profile)

    async def deliver(notification: WorkNotification) -> DeliveryResult:
        message = _message(notification, origin)
        if not _is_reader_message(message):
            try:
                state, ready = await asyncio.wait_for(_ready_daily_message(notification, origin, sessions), timeout=3)
            except Exception:
                # No Work request has been made at this point.
                return "retry"
            if state in {"failed", "retry"}:
                return state
            if state == "ready":
                message = ready
        if not message:
            return "failed"
        try:
            await send_work_notification(
                notification.owner_user_id,
                notification.connection_id,
                message,
                notification.delivery_id,
                binding_key=notification.binding_key,
                **({"markdown": True} if message.startswith("[点击查看日报网页](") else {}),
            )
        except WorkNotificationError as exc:
            if exc.uncertain:
                return "uncertain"
            return "retry" if exc.stage == "sender_unavailable" else "failed"
        return "sent"

    return WorkNotificationDispatcher(sessions, key=os.getenv("DEER_FLOW_WORK_STATE_KEY", ""), send=deliver, profile=work.profile)
