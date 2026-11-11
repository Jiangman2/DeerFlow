"""Versioned, non-executable calendar drafts and deterministic slot handling.

The model can propose patches. It cannot select an identity, provider endpoint,
token, confirmation or execution metadata. Omitted patch fields are preserved;
explicit null resets a slot. Finite daily occurrences never become one interval.
"""

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .calendar import CalendarIntent, Changes, Selector
from .capabilities import parse_time

TZ = ZoneInfo("Asia/Shanghai")
MAX_OCCURRENCES = 31
MAX_CREATE_OCCURRENCES = 10
CROSS_DAY_QUESTION = "这段时间超过24小时。您要创建一条连续跨日的日程，还是每天分别创建独立日程？如为每天独立，请说明每天开始时间和持续多久。"
TASK_RELATION_QUESTION = "这是继续修改当前未完成的安排，还是另建一项安排？"
_MISSING_CREATE_QUESTION = "请补充{fields}；已提供的其他条件会保留。"
TITLE_ONLY_QUESTION = _MISSING_CREATE_QUESTION.format(fields="标题")
Operation = Literal["query", "create", "update", "delete", "repair"]
Cardinality = Literal["single", "finite", "recurring"]


def _day(value: str) -> str:
    if value and (len(value) != 10 or date.fromisoformat(value).isoformat() != value):
        raise ValueError("calendar_date_must_be_iso_day")
    return value


class CalendarSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    dates: list[str] = Field(default_factory=list, max_length=MAX_OCCURRENCES)
    start_date: str = Field(default="", max_length=10)
    count: int | None = Field(default=None, ge=1, le=MAX_OCCURRENCES)
    start_clock: str = Field(default="", pattern=r"^(?:|(?:[01]\d|2[0-3]):[0-5]\d)$")
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)

    @field_validator("start_date")
    @classmethod
    def valid_day(cls, value):
        return _day(value)

    @field_validator("dates")
    @classmethod
    def valid_dates(cls, value):
        if any(not x for x in value) or len(set(value)) != len(value):
            raise ValueError("calendar_dates_must_be_unique")
        return sorted(_day(x) for x in value)

    @model_validator(mode="after")
    def consistent(self):
        if self.dates and self.count is not None and len(self.dates) != self.count:
            raise ValueError("occurrence_count_conflicts_with_dates")
        if self.dates and self.start_date and self.start_date != self.dates[0]:
            raise ValueError("occurrence_start_conflicts_with_dates")
        try:
            last_day = self.dates[-1] if self.dates else (date.fromisoformat(self.start_date) + timedelta(days=self.count - 1)).isoformat() if self.start_date and self.count is not None else ""
            if last_day and self.start_clock and self.duration_minutes is not None:
                datetime.fromisoformat(last_day + "T" + self.start_clock + ":00").replace(tzinfo=TZ) + timedelta(minutes=self.duration_minutes)
        except OverflowError:
            raise ValueError("calendar_occurrence_outside_supported_date_range") from None
        return self


class CalendarDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    operation: Operation | None = None
    cardinality: Cardinality = "single"
    selector: Selector = Field(default_factory=Selector)
    changes: Changes = Field(default_factory=Changes)
    schedule: CalendarSchedule = Field(default_factory=CalendarSchedule)
    reference: Literal["none", "recent_receipt"] = "none"
    # This is an audit explanation, never execution authority. A downgrade also
    # requires an exact quote from the current user turn at merge time.
    cardinality_change_reason: str = Field(default="", max_length=300)
    cardinality_change_user_delta: str = Field(default="", max_length=500)
    # Set only by the conditional pure confirmation tool after this exact
    # server question. It is intentionally absent from CalendarDraftPatch.
    continuous_span_confirmed: bool = False
    task_reset_reason: str = Field(default="", max_length=300)
    task_reset_user_delta: str = Field(default="", max_length=500)
    pending_start_clock: str = Field(default="", pattern=r"^(?:|(?:[01]\d|2[0-3]):[0-5]\d)$", description="服务端保留的已知钟点；缺日期时不形成可执行开始时间，模型补丁不可设置。")

    @model_validator(mode="before")
    @classmethod
    def normalize_single_count(cls, value):
        if isinstance(value, dict) and value.get("cardinality", "single") == "single":
            schedule = value.get("schedule")
            raw = schedule.model_dump() if isinstance(schedule, CalendarSchedule) else schedule
            if isinstance(raw, dict) and raw.get("start_date") and raw.get("count") is None:
                value = {**value, "schedule": {**raw, "count": 1}}
        return value

    @model_validator(mode="after")
    def consistent(self):
        if self.operation == "query" and self.changes.model_dump(exclude_none=True):
            raise ValueError("calendar_query_cannot_mutate")
        if self.cardinality == "finite" and self.operation in {"create", "repair"} and (self.changes.start or self.changes.end or self.changes.duration_minutes is not None):
            raise ValueError("finite_occurrences_require_schedule_not_one_interval")
        scheduled = self.schedule.dates or self.schedule.start_date or self.schedule.start_clock or self.schedule.count is not None or self.schedule.duration_minutes is not None
        if self.operation in {"create", "repair"} and scheduled and (self.changes.start or self.changes.end or self.changes.duration_minutes is not None):
            raise ValueError("calendar_schedule_cannot_mix_absolute_interval")
        if (self.operation == "create" or self.cardinality == "finite" and self.operation == "repair") and (self.changes.shift_minutes is not None or self.changes.keep_duration is not None):
            raise ValueError("creation_requires_explicit_times_not_relative_edit")
        if self.cardinality == "single" and (len(self.schedule.dates) > 1 or (self.schedule.count or 0) > 1):
            raise ValueError("single_event_cannot_drop_multiple_dates")
        if self.reference == "recent_receipt" and self.operation not in {"repair", "update", "delete", "query"}:
            raise ValueError("receipt_reference_requires_existing_event")
        return self


class SelectorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    starts_at: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=200, description="用户给出的原日程名称；会议/日程/安排等泛称不作标题。")
    event_id: str | None = Field(default=None, max_length=256, description="仅当前任务后端查询得到的目标，不猜测。")
    candidate: int | None = Field(default=None, ge=1, le=50, description="当前后端候选列表中用户明确选择的编号。")
    recent: bool | None = Field(default=None, description="仅明确指刚才那条且当前recent_id存在时可true；‘最近的安排’是日期不明确，绝不是recent=true。引用已执行回执请用reference=recent_receipt且不要填此字段。")


class ChangesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    title: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=4000)
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    duration_minutes: int | None = Field(default=None, ge=1, le=44640)
    shift_minutes: int | None = Field(default=None, ge=-44640, le=44640)
    keep_duration: bool | None = None


class SchedulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    dates: list[str] | None = Field(default=None, max_length=MAX_OCCURRENCES)
    start_date: str | None = Field(default=None, max_length=10, description="明确的YYYY-MM-DD。相对今天/明天/后天请用工具调用顶层start_day，不得把start_day放入schedule。")
    count: int | None = Field(default=None, ge=1, le=MAX_OCCURRENCES)
    start_clock: str | None = Field(default=None, max_length=5)
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)


class CalendarDraftPatch(BaseModel):
    """Never dump with exclude_none: null is an explicit clearing operation."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    operation: Operation | None = None
    cardinality: Cardinality | None = None
    selector: SelectorPatch | None = None
    changes: ChangesPatch | None = None
    schedule: SchedulePatch | None = None
    reference: Literal["none", "recent_receipt"] | None = None
    cardinality_change_reason: str | None = Field(default=None, max_length=300)
    cardinality_change_user_delta: str | None = Field(default=None, max_length=500)


def _span_semantics(draft: CalendarDraft) -> tuple:
    """Confirmation binds the actual arrangement, not repeated JSON keys."""
    c, s = draft.changes, draft.schedule
    scheduled = bool(s.dates or s.start_date or s.start_clock or s.count is not None or s.duration_minutes is not None)
    if scheduled:
        dates = tuple(_dates(s))
        timing = ("schedule", dates, s.start_date if not dates else "", s.count if not dates else None, s.start_clock, s.duration_minutes)
    else:
        start = parse_time(c.start) if c.start else None
        end = parse_time(c.end) if c.end else start + c.duration_minutes * 60 if start is not None and c.duration_minutes is not None else None
        timing = ("interval", start, end, c.duration_minutes if end is None else None, c.shift_minutes, c.keep_duration)
    return draft.operation, draft.cardinality, timing


def merge_draft(previous: CalendarDraft | dict | None, patch: CalendarDraftPatch | dict, *, user_request: str = "") -> CalendarDraft:
    """Apply explicit slot deltas; validate the whole resulting business shape."""
    old = CalendarDraft.model_validate(previous) if previous is not None else CalendarDraft()
    delta = CalendarDraftPatch.model_validate(patch).model_dump(exclude_unset=True)
    quote = delta.get("cardinality_change_user_delta") or ""
    evidence = bool(delta.get("cardinality_change_reason") and quote.strip() and quote in user_request)
    if old.cardinality in {"finite", "recurring"} and delta.get("cardinality") == "single":
        if not evidence:
            raise ValueError("cardinality_downgrade_requires_current_user_evidence")
        schedule_delta = delta.get("schedule") or {}
        changes_delta = delta.get("changes") or {}
        if not schedule_delta.get("start_date") and not (isinstance(schedule_delta.get("dates"), list) and len(schedule_delta["dates"]) == 1) and not changes_delta.get("start"):
            raise ValueError("single_occurrence_requires_explicit_target_date")
    value = old.model_dump(mode="json")
    defaults = CalendarDraft().model_dump(mode="json")
    if old.cardinality in {"finite", "recurring"} and delta.get("cardinality") == "single":
        if changes_delta.get("start"):
            value["schedule"] = dict(defaults["schedule"])
            if "duration_minutes" not in changes_delta and not changes_delta.get("end") and old.schedule.duration_minutes is not None:
                value["changes"]["duration_minutes"] = old.schedule.duration_minutes
        else:
            # The current, verified single-day choice replaces only obsolete
            # cardinality/date slots. Never pick the old first day implicitly.
            value["schedule"].update(dates=[], start_date="", count=1)
    for key, item in delta.items():
        if key in {"selector", "changes", "schedule"} and isinstance(item, dict):
            for slot, changed in item.items():
                value[key][slot] = defaults[key][slot] if changed is None else changed
        else:
            value[key] = defaults[key] if item is None else item
    result = CalendarDraft.model_validate(value)
    if old.continuous_span_confirmed and _span_semantics(old) != _span_semantics(result):
        result = result.model_copy(update={"continuous_span_confirmed": False})
    if old.cardinality == "finite" and old.operation in {"create", "repair"} and result.operation in {"create", "repair"}:
        old_dates, new_dates = _dates(old.schedule), _dates(result.schedule)
        old_count = len(old_dates) or old.schedule.count
        new_count = len(new_dates) or result.schedule.count
        reduced_count = old_count is not None and (new_count is None or new_count < old_count)
        lost_dates = bool(old_dates) and not new_dates
        if (reduced_count or lost_dates) and not evidence:
            raise ValueError("finite_constraints_cannot_be_silently_reduced")
    return result


def _dates(schedule: CalendarSchedule) -> list[str]:
    if schedule.dates:
        return list(schedule.dates)
    if not schedule.start_date or schedule.count is None:
        return []
    start = date.fromisoformat(schedule.start_date)
    return [(start + timedelta(days=i)).isoformat() for i in range(schedule.count)]


def _selector_question(selector: Selector) -> str:
    if selector.event_id or selector.candidate is not None or selector.recent:
        return ""
    if selector.starts_at:
        try:
            parse_time(selector.starts_at)
            return ""
        except ValueError:
            return "请确认原日程的具体日期和开始时间，时间按北京时间理解。"
    if not selector.start or not selector.end:
        return "您想查看或定位哪一天、哪一周或哪段时间的日程？时间按北京时间理解。"
    try:
        start, end = parse_time(selector.start), parse_time(selector.end)
        if end <= start or end - start > 31 * 86400:
            raise ValueError("invalid_range")
    except ValueError:
        return "请提供起止明确、不超过31天的查询日期范围。"
    return ""


def missing_question(draft: CalendarDraft) -> str:
    """Ask only missing/conflicting business conditions, before OAuth or writes."""
    if draft.operation is None:
        return "您是想查看已有安排、新建日程，还是修改已有日程？"
    if draft.operation == "delete":
        return "删除或取消已创建日程尚未开放；可以先查询核对，或在 Work 日历中处理。"
    if draft.cardinality == "recurring":
        return "周期日程尚未开放。是否改为明确日期和条数的独立日程？请说明日期范围。"
    if draft.cardinality == "finite" and draft.operation in {"create", "repair"} and max(len(draft.schedule.dates), draft.schedule.count or 0) > MAX_CREATE_OCCURRENCES:
        return "一次最多处理10条独立日程。已保留您的日期和其他条件，请选择本次先处理的10条以内日期，其余可分批处理。"
    uses_schedule = bool(draft.schedule.dates or draft.schedule.start_date or draft.schedule.start_clock or draft.schedule.count is not None or draft.schedule.duration_minutes is not None)
    if draft.operation == "create" or draft.operation == "repair" and (draft.cardinality == "finite" or uses_schedule):
        missing = []
        if draft.operation == "create" and (not draft.changes.title or not draft.changes.title.strip()):
            missing.append("标题")
        s = draft.schedule
        if draft.cardinality == "finite" or uses_schedule:
            if not _dates(s):
                missing.append("具体日期（或开始日期和连续天数）")
            if not s.start_clock:
                missing.append("每天的开始时间")
            if s.duration_minutes is None:
                missing.append("每条日程持续多久")
        else:
            if not draft.changes.start:
                missing.append("开始日期和时间")
            if not draft.changes.end and draft.changes.duration_minutes is None:
                missing.append("结束时间或持续多久")
        if missing:
            return _MISSING_CREATE_QUESTION.format(fields="、".join(missing))
        if draft.cardinality == "single" and not uses_schedule:
            try:
                _single_create(draft)
                begin = parse_time(draft.changes.start)
                finish = parse_time(draft.changes.end) if draft.changes.end else begin + draft.changes.duration_minutes * 60
                if finish - begin > 86400 and not draft.continuous_span_confirmed:
                    return CROSS_DAY_QUESTION
            except ValueError:
                return "请确认日程结束晚于开始，起止含明确时区，且时长不超过31天。"
        if draft.operation == "repair" and draft.reference != "recent_receipt":
            return _selector_question(draft.selector)
        return ""
    if draft.operation in {"update", "repair"}:
        if not draft.changes.model_dump(exclude_none=True, exclude={"keep_duration"}):
            return "您想修改日程的名称、描述还是时间？请说明具体变更，原有条件会保留。"
        if draft.changes.title is not None and not draft.changes.title.strip():
            return "日程名称不能为空，请提供新的名称。"
        if draft.changes.start and draft.changes.end is None and draft.changes.duration_minutes is None and draft.changes.keep_duration is None:
            return "调整开始时间后，是否保持原来的时长？也可以提供新的结束时间。"
    if draft.reference == "recent_receipt":
        return ""
    return _selector_question(draft.selector)


def _single_create(draft: CalendarDraft) -> CalendarIntent:
    from .capabilities import Action

    c = draft.changes
    end = c.end
    if c.duration_minutes is not None and c.start:
        end = datetime.fromtimestamp(parse_time(c.start) + c.duration_minutes * 60, TZ).isoformat()
    Action(operation="event_create", title=c.title or "", text=c.text or "", start=c.start or "", end=end or "")
    return CalendarIntent(operation="calendar_create", changes=c)


def expand_create(draft: CalendarDraft) -> list[CalendarIntent]:
    """Produce proposals, never provider calls. Each finite occurrence is separate."""
    if draft.operation != "create" or missing_question(draft):
        raise ValueError("calendar_draft_not_ready_for_creation")
    if draft.cardinality == "single" and not (draft.schedule.dates or draft.schedule.start_date or draft.schedule.start_clock or draft.schedule.count is not None or draft.schedule.duration_minutes is not None):
        return [_single_create(draft)]
    s = draft.schedule
    days = _dates(s)
    if not days or not s.start_clock or s.duration_minutes is None or draft.cardinality == "single" and len(days) != 1:
        raise ValueError("calendar_occurrence_schedule_incomplete")
    result = []
    for day in days:
        begin = datetime.fromisoformat(day + "T" + s.start_clock + ":00").replace(tzinfo=TZ)
        end = begin + timedelta(minutes=s.duration_minutes)
        result.append(CalendarIntent(operation="calendar_create", changes=Changes(title=draft.changes.title, text=draft.changes.text, start=begin.isoformat(), end=end.isoformat())))
    return result


def draft_summary(draft: CalendarDraft) -> dict:
    """Bounded progress facts for a pure validation tool and repeated questions."""
    return {
        "operation": draft.operation,
        "cardinality": draft.cardinality,
        "title": draft.changes.title,
        "dates": _dates(draft.schedule),
        "start_clock": draft.schedule.start_clock,
        "duration_minutes": draft.schedule.duration_minutes,
        "reference": draft.reference,
        "missing_question": missing_question(draft),
    }
