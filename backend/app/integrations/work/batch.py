"""Finite calendar batches are immutable proposals, never recurring jobs."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


class BatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    operation: Literal["calendar_batch"] = "calendar_batch"
    items: list[dict] = Field(min_length=1, max_length=10)
    summary: str = Field(default="", max_length=500)


def item_fact(action, *, event_id="", plan_id="", batch_group=""):
    return {"event_id": event_id or action.event_id, "title": action.title, "start": action.start, "end": action.end, "plan_id": plan_id, "batch_group": batch_group}


def calendar_slot(start: str, end: str) -> str:
    """Show Beijing time, keeping both dates for overnight events."""
    try:
        begin, finish = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if begin.tzinfo is None or finish.tzinfo is None:
            return ""
        timezone = ZoneInfo("Asia/Shanghai")
        begin, finish = begin.astimezone(timezone), finish.astimezone(timezone)
    except (TypeError, ValueError, OverflowError):
        return ""
    clock = "%H:%M:%S" if begin.second or finish.second else "%H:%M"
    finish_format = clock if begin.date() == finish.date() else f"%Y-%m-%d {clock}"
    return f"{begin.strftime(f'%Y-%m-%d {clock}')}–{finish.strftime(finish_format)}"


def calendar_success(operation: str, title: str, start: str, end: str) -> str:
    verb = {"event_create": "已创建", "event_update": "已更新"}.get(operation, "已完成")
    name = " ".join(title.split()) or "日程"
    slot = calendar_slot(start, end)
    return f"{verb}「{name}」" + (f" · {slot}" if slot else "")


def preview(actions, conflicts, snapshots=None):
    lines = [f"个人主日历修改，共 {len(actions)} 条（北京时间）"] if len(actions) > 1 else []
    for index, action in enumerate(actions, 1):
        minutes = (datetime.fromisoformat(action.end) - datetime.fromisoformat(action.start)).total_seconds() / 60
        prefix = f"{index}. " if len(actions) > 1 else ""
        verb = "创建日程" if action.operation == "event_create" else "编辑日程"
        lines.append(f"{prefix}{verb} · {action.title}\n{calendar_slot(action.start, action.end)} · {minutes:g} 分钟")
        if action.operation == "event_update" and snapshots:
            before = snapshots[index - 1]["event"]
            timezone = datetime.fromisoformat(action.start).tzinfo
            old_start = datetime.fromtimestamp(int(before["start_time"]["timestamp"]), timezone).isoformat()
            old_end = datetime.fromtimestamp(int(before["end_time"]["timestamp"]), timezone).isoformat()
            lines.append(f"   原日程：{before.get('summary') or ''} · {calendar_slot(old_start, old_end)}")
            lines.append("   修改字段：" + "、".join(action.update_fields or ("summary", "description", "start_time", "end_time")))
            if not action.update_fields or "description" in action.update_fields:
                lines.append(f"   描述：{before.get('description') or ''} → {action.text}")
        elif action.text:
            lines.append("   描述：" + action.text)
    lines.extend(conflicts)
    if len(actions) > 1:
        lines.append("确认后逐条处理。遇到异常会停止后续条目。")
    return "\n".join(lines)


def outcome_text(items):
    unfinished = [item for item in items if item["status"] != "completed"]
    if items and not unfinished:
        if len(items) == 1:
            item = items[0]
            return calendar_success(item.get("operation", ""), item["title"], item["start"], item["end"])
        operations = {item.get("operation") for item in items}
        verbs = {frozenset({"event_create"}): "已创建", frozenset({"event_update"}): "已更新"}
        verb = verbs.get(frozenset(operations), "已处理")
        return f"{verb} {len(items)} 条日程。"

    names = {"executing": "正在处理或待核查", "uncertain": "结果不确定，请在 Work 日历核查", "failed": "未确认成功，请核查"}
    lines = [f"已完成 {len(items) - len(unfinished)}/{len(items)} 条日程。"]
    for item in unfinished:
        title = " ".join(item["title"].split())
        slot = calendar_slot(item["start"], item["end"])
        status = names.get(item["status"], "未执行")
        lines.append(f"- {title} · {status}" + (f" · {slot}" if slot else ""))
        if item.get("reason"):
            reasons = {"task_superseded": "当前任务已切换或结束", "target_changed_request_new_preview": "目标已发生变化，需要重新生成预览", "binding_changed": "渠道绑定已变化", "authorization_required": "个人授权已失效"}
            lines.append("  " + reasons.get(item["reason"], "执行前核验未通过，请重新查询后再预览"))
    if any(item["status"] in names for item in unfinished):
        lines.append("不会自动重试。")
    return "\n".join(lines)
