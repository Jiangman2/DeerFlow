"""Finite calendar batches are immutable proposals, never recurring jobs."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    operation: Literal["calendar_batch"] = "calendar_batch"
    items: list[dict] = Field(min_length=1, max_length=10)
    summary: str = Field(default="", max_length=500)


def item_fact(action, *, event_id="", plan_id="", batch_group=""):
    return {"event_id": event_id or action.event_id, "title": action.title, "start": action.start, "end": action.end, "plan_id": plan_id, "batch_group": batch_group}


def preview(actions, conflicts, snapshots=None):
    lines = [f"个人主日历批量修改，共 {len(actions)} 条（不邀请其他人，不创建周期规则）："]
    for index, action in enumerate(actions, 1):
        minutes = (datetime.fromisoformat(action.end) - datetime.fromisoformat(action.start)).total_seconds() / 60
        lines.append(f"{index}. {'新建' if action.operation == 'event_create' else '编辑'}：{action.title}\n   {action.start} 至 {action.end}；时长 {minutes:g} 分钟")
        if action.operation == "event_update" and snapshots:
            before = snapshots[index - 1]["event"]
            timezone = datetime.fromisoformat(action.start).tzinfo
            old_start = datetime.fromtimestamp(int(before["start_time"]["timestamp"]), timezone).isoformat()
            old_end = datetime.fromtimestamp(int(before["end_time"]["timestamp"]), timezone).isoformat()
            lines.append(f"   原日程：{before.get('summary') or ''}；{old_start} 至 {old_end}")
            lines.append("   修改字段：" + "、".join(action.update_fields or ("summary", "description", "start_time", "end_time")))
            if not action.update_fields or "description" in action.update_fields:
                lines.append(f"   描述：{before.get('description') or ''} → {action.text}")
        elif action.text:
            lines.append("   描述：" + action.text)
    lines.extend(conflicts)
    lines.append("确认后逐条处理；发生错误或结果不确定时停止后续条目。已经完成的条目不会自动撤销或重试。")
    return "\n".join(lines)


def outcome_text(items):
    names = {"pending": "未执行", "executing": "结果待核查", "completed": "成功并回读核对", "uncertain": "结果不确定，请到 Work 核查", "failed": "未确认成功，请核查", "unexecuted": "未执行"}
    lines = [f"批量处理结果：成功 {sum(item['status'] == 'completed' for item in items)} / {len(items)} 条。"]
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. {item['title']}：{names.get(item['status'], '未执行')}；{item['start']} 至 {item['end']}" + (f"；event_id：{item['receipt']}" if item.get("receipt") else ""))
        if item.get("reason"):
            reasons = {"task_superseded": "当前任务已切换或结束", "target_changed_request_new_preview": "目标已发生变化，需要重新生成预览", "binding_changed": "渠道绑定已变化", "authorization_required": "个人授权已失效"}
            lines.append("   停止原因：" + reasons.get(item["reason"], "执行前核验未通过，请重新查询后再预览"))
    lines.append("不会自动重试；如需修复，请明确选择具体日程，重新查询后生成新的确认预览。")
    return "\n".join(lines)
