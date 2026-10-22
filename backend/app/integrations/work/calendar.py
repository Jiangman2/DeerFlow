"""Calendar intents and principal-scoped resolution. No model-selected identity/HTTP.

An intent may be incomplete; only a resolved, fully validated Action can enter
the existing signed-preview/confirmation executor. Candidate data is never
authority and every selected target is fetched again under the current grant.
"""

import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from .auth import Principal
from .capabilities import Action, WorkCapabilities, encode, parse_time
from .client import WorkError
from .scopes import CALENDAR_READ

TZ = ZoneInfo("Asia/Shanghai")


class Selector(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    start: str = Field(default="", max_length=40, description="查询区间起点，ISO时间含时区；不是修改后的时间")
    end: str = Field(default="", max_length=40, description="查询区间终点，最多31天")
    starts_at: str = Field(default="", max_length=40, description="用户明确指定的原日程开始时间，用于筛选")
    title: str = Field(default="", max_length=200, description="原日程标题的关键词；不是新标题")
    event_id: str = Field(default="", max_length=256, description="只能使用本账号已查询的ID")
    candidate: int | None = Field(default=None, ge=1, le=50, description="用户明确选择上次候选列表的第几条")
    recent: bool = Field(default=False, description="仅用户明确指刚才那条日程时为true")


class Changes(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    title: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=4000, description="用户明确指定的新描述；null表示保留")
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    duration_minutes: int | None = Field(default=None, ge=1, le=44640)
    shift_minutes: int | None = Field(default=None, ge=-44640, le=44640, description="整体平移；正数推后，负数提前；起止同时移动")
    keep_duration: bool | None = Field(default=None, description="只改开始时间时，用户是否明确要求保持时长；不明确则null补问")

    @model_validator(mode="after")
    def consistent(self):
        if self.shift_minutes is not None and any(x is not None for x in (self.start, self.end, self.duration_minutes, self.keep_duration)):
            raise ValueError("Do not combine relative and absolute time changes")
        if self.end is not None and self.duration_minutes is not None:
            raise ValueError("Use an end time or a duration, not both")
        for value in (self.start, self.end):
            if value:
                parse_time(value)
        return self


class CalendarIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    operation: Literal["calendar_query", "calendar_create", "calendar_update"]
    selector: Selector = Field(default_factory=Selector)
    changes: Changes = Field(default_factory=Changes)

    @field_validator("changes", mode="before", json_schema_input_type=Changes | None)
    @classmethod
    def normalize_read_only_changes(cls, value, info: ValidationInfo):
        # null and an empty object are equivalent only for a read. Do not
        # discard write fields or weaken the create/update contract.
        if value is None:
            if info.data.get("operation") != "calendar_query":
                raise ValueError("Calendar writes require a changes object")
            return {}
        return value

    @model_validator(mode="after")
    def query_has_no_mutation(self):
        if self.operation == "calendar_query" and self.changes.model_dump(exclude_none=True):
            raise ValueError("A calendar query must not carry mutation fields")
        return self


@dataclass
class CalendarContext:
    expires_at: float
    candidates: list[dict] = field(default_factory=list, repr=False)
    recent_id: str = ""


@dataclass
class Resolution:
    action: Action | None = None
    text: str = ""
    needs_reply: bool = False


def iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, TZ).isoformat()


def event_times(event: dict) -> tuple[int, int]:
    try:
        start, end = event["start_time"], event["end_time"]
        # All-day/date-only events need a separate contract; never flatten them.
        if start.get("date") or end.get("date"):
            raise ValueError
        begin, finish = int(start["timestamp"]), int(end["timestamp"])
        if finish <= begin:
            raise ValueError
        iso(begin), iso(finish)
        return begin, finish
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        raise WorkError("timed_event_required") from None


def event_fact(event: dict) -> dict:
    begin, finish = event_times(event)
    return {"event_id": event["event_id"], "title": str(event.get("summary") or "")[:200], "start": iso(begin), "end": iso(finish)}


def query_times(event: dict) -> tuple[int, int]:
    if not event.get("start_time", {}).get("date"):
        return event_times(event)
    try:
        start = datetime.strptime(event["start_time"]["date"], "%Y-%m-%d").replace(tzinfo=TZ)
        end = datetime.strptime(event["end_time"]["date"], "%Y-%m-%d").replace(tzinfo=TZ)
        if end <= start:
            raise ValueError
        return int(start.timestamp()), int(end.timestamp())
    except (KeyError, TypeError, ValueError):
        raise WorkError("timed_event_required") from None


def describe(items: list[dict]) -> str:
    lines = []
    for i, item in enumerate(items, 1):
        try:
            fact = event_fact(item)
            interval = f"{fact['start']} 至 {fact['end']}"
        except WorkError:
            interval = "全天或暂不支持的时间格式（仅展示，不可编辑）"
        title = str(item.get("summary") or "（无标题）").replace("\n", " ")[:200]
        lines.append(f"{i}. {title}｜{interval}")
    return "\n".join(lines)


class CalendarResolver:
    def __init__(self, tools: WorkCapabilities):
        self.tools = tools
        self.contexts: dict[Principal, CalendarContext] = {}

    def prune(self):
        self.contexts = {p: c for p, c in self.contexts.items() if c.expires_at > time.monotonic()}

    def remember(self, p: Principal, items: list[dict], *, recent_id=""):
        self.prune()
        if len(self.contexts) >= 64 and p not in self.contexts:
            return
        # Cache only bounded locator facts, never attendee lists or descriptions.
        facts = [{"event_id": x.get("event_id", ""), "summary": str(x.get("summary") or "")[:200], "start_time": deepcopy(x.get("start_time", {})), "end_time": deepcopy(x.get("end_time", {}))} for x in items[:50]]
        self.contexts[p] = CalendarContext(time.monotonic() + 600, facts, recent_id)
        self.tools.remember_events(p, [x.get("event_id", "") for x in items])

    def facts(self, p: Principal) -> dict:
        self.prune()
        context = self.contexts.get(p)
        facts = []
        for item in context.candidates if context else []:
            try:
                facts.append(event_fact(item))
            except WorkError:
                facts.append({"event_id": item.get("event_id", ""), "title": str(item.get("summary") or "")[:200], "editable": False})
        return {"candidates": facts, "recent_id": context.recent_id if context else ""}

    async def resolve(self, p: Principal, intent: CalendarIntent) -> Resolution:
        await self.tools.vault.require(p, CALENDAR_READ)
        self.prune()
        if intent.operation == "calendar_create":
            return self._create(intent.changes)
        if intent.operation == "calendar_update" and not intent.changes.model_dump(exclude_none=True, exclude={"keep_duration"}):
            return Resolution(text="请说明新名称、描述或时间。本轮不支持删除或取消已有日程。", needs_reply=True)
        s = intent.selector
        if s.starts_at and not s.start and not s.end:
            try:
                day = datetime.fromtimestamp(parse_time(s.starts_at), TZ).replace(hour=0, minute=0, second=0, microsecond=0)
                s = s.model_copy(update={"start": day.isoformat(), "end": (day + timedelta(days=1)).isoformat()})
            except (ValueError, OverflowError, OSError):
                return Resolution(text="原日程的开始时间无效，请确认具体日期、时间及其时区。", needs_reply=True)
        context = self.contexts.get(p)
        selected_id = ""
        if s.candidate is not None:
            if not context or s.candidate > len(context.candidates):
                return Resolution(text="候选列表已过期或序号无效，请重新提供日程日期和原名称。", needs_reply=True)
            selected_id = context.candidates[s.candidate - 1].get("event_id", "")
        elif s.recent:
            selected_id = context.recent_id if context else ""
            if not selected_id:
                return Resolution(text="无法唯一确定刚才那条日程，请说明原日程的日期、时间或名称。", needs_reply=True)
        elif s.event_id:
            known = self.tools.known_events.get(p)
            if not known or known[0] <= time.monotonic() or s.event_id not in known[1]:
                return Resolution(text="请先查询该日程，或提供原日程的日期和名称，不能直接使用未经本账号查询的日程 ID。", needs_reply=True)
            selected_id = s.event_id
        if selected_id:
            # A selected candidate must still meet every extra user constraint.
            if s.event_id and s.event_id != selected_id:
                return Resolution(text="候选序号与日程 ID 不一致，请重新选择目标。", needs_reply=True)
            calendar = await self.tools.primary(p)
            item = (await self.tools.call(p, "event_get", calendar_id=calendar, event_id=selected_id)).get("event", {})
            items = [item] if self._matches(item, s) else []
        else:
            if not s.start or not s.end:
                return Resolution(text="请说明要查询或编辑哪一天（或哪段日期）的日程，以及原名称或开始时间。", needs_reply=True)
            try:
                Action(operation="events_list", start=s.start, end=s.end)
                if s.starts_at:
                    parse_time(s.starts_at)
            except ValueError:
                return Resolution(text="请提供明确的日期范围（最多 31 天）和含时区的时间。", needs_reply=True)
            items, complete = await self.tools.query_events(p, s.start, s.end)
            if not complete:
                return Resolution(text="日程较多，未能完整查询；请缩小日期范围后重试，不会从不完整结果中选择修改目标。", needs_reply=True)
            items = [x for x in items if self._matches(x, s)]
            if len(items) > 50:
                return Resolution(text="符合条件的日程超过 50 条，请缩小日期范围或补充原名称。", needs_reply=True)
            self.remember(p, items, recent_id=items[0]["event_id"] if len(items) == 1 else "")
        if not items:
            if intent.operation == "calendar_query":
                return Resolution(text="个人主日历在该时间范围和筛选条件下没有有效日程。")
            return Resolution(text="没有找到符合条件的有效日程，请核对原日程日期、时间或名称。", needs_reply=True)
        if intent.operation == "calendar_query":
            if selected_id:
                self.remember(p, items, recent_id=selected_id)
            return Resolution(text="个人主日历日程（北京时间）：\n" + describe(items))
        if len(items) != 1:
            return Resolution(text="找到多条符合条件的日程，请回复选择第几条（尚未修改）：\n" + describe(items), needs_reply=True)
        eid = items[0]["event_id"]
        try:
            snapshot = await self.tools.editable_event(p, eid)
            event = snapshot["event"]
            begin, finish = event_times(event)
            if not self._matches(event, s):
                return Resolution(text="日程最新信息已与原条件不同，请重新查询后再修改。", needs_reply=True)
            self.remember(p, [event], recent_id=eid)
            return self._update(event, begin, finish, intent.changes)
        except WorkError as exc:
            hints = {
                "shared_or_recurring_event_not_supported": "本轮仅支持本人组织、无其他参会人的非周期个人日程，不编辑周期或共享日程。",
                "event_with_attendees_not_supported": "该日程有其他参会人或会议室，本轮不编辑，避免通知或影响他人。",
                "event_unavailable": "该日程已取消、不可访问或并非本人组织，请重新选择。",
                "timed_event_required": "本轮编辑仅支持有明确起止时间的日程，暂不编辑全天日程。",
            }
            if exc.stage not in hints:
                raise
            return Resolution(text=hints[exc.stage])

    @staticmethod
    def _matches(event: dict, selector: Selector) -> bool:
        if event.get("status") == "cancelled" or not event.get("event_id"):
            return False
        if selector.title and selector.title.casefold() not in str(event.get("summary") or "").casefold():
            return False
        try:
            if selector.start or selector.end or selector.starts_at:
                begin, finish = query_times(event)
                if selector.start and finish <= parse_time(selector.start) or selector.end and begin >= parse_time(selector.end):
                    return False
                if selector.starts_at and (event.get("start_time", {}).get("date") or begin != parse_time(selector.starts_at)):
                    return False
        except (ValueError, WorkError):
            return False
        return True

    @staticmethod
    def _create(c: Changes) -> Resolution:
        if not c.title or not c.title.strip() or not c.start:
            return Resolution(text="请补充新日程的标题、开始时间和结束时间（或持续多久）。", needs_reply=True)
        end = c.end
        if c.duration_minutes is not None:
            end = iso(parse_time(c.start) + c.duration_minutes * 60)
        if not end or c.shift_minutes is not None:
            return Resolution(text="请补充新日程的结束时间或明确时长，不会默认创建一小时日程。", needs_reply=True)
        try:
            return Resolution(action=Action(operation="event_create", title=c.title, text=c.text or "", start=c.start, end=end))
        except ValueError:
            return Resolution(text="日程起止时间无效，请确认结束晚于开始、时区明确且时长不超过 31 天。", needs_reply=True)

    @staticmethod
    def _update(event: dict, begin: int, finish: int, c: Changes) -> Resolution:
        fields = []
        title, text = event.get("summary") or "", event.get("description") or ""
        if c.title is not None:
            if not c.title.strip():
                return Resolution(text="日程名称不能为空，请提供新的名称。", needs_reply=True)
            title = c.title
            fields.append("summary")
        if c.text is not None:
            text = c.text
            fields.append("description")
        old_begin, old_finish = begin, finish
        if c.shift_minutes is not None:
            begin += c.shift_minutes * 60
            finish += c.shift_minutes * 60
        else:
            if c.start:
                begin = parse_time(c.start)
                if c.end is None and c.duration_minutes is None:
                    if c.keep_duration is None:
                        return Resolution(text="开始时间变更后，是否保持原时长？请说明保持时长或提供新的结束时间。", needs_reply=True)
                    finish = begin + old_finish - old_begin if c.keep_duration else old_finish
            if c.end:
                finish = parse_time(c.end)
            if c.duration_minutes is not None:
                finish = begin + c.duration_minutes * 60
        if begin != old_begin:
            fields.append("start_time")
        if finish != old_finish:
            fields.append("end_time")
        fields = [k for k in fields if {"summary": title, "description": text}.get(k, object()) != event.get(k, "")]
        if not fields:
            return Resolution(text="没有需要修改的字段。请说明新名称、描述或时间；原日程未变更。", needs_reply=True)
        try:
            return Resolution(action=Action(operation="event_update", event_id=event["event_id"], title=title, text=text, start=iso(begin), end=iso(finish), update_fields=tuple(fields), expected_event=encode(event)))
        except ValueError:
            return Resolution(text="修改后的起止时间或字段超出当前支持范围，请确认结束晚于开始、时长不超过 31 天、标题最多 200 字。", needs_reply=True)
