"""Non-executable Work turn decisions, separate from identity and OAuth.

Natural-language admission is semantic. There is deliberately no operation-word
allowlist here. The caller verifies the trusted channel and binding; provider
calls and task fencing remain server-side responsibilities.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_serializer, field_validator, model_validator

from .calendar import CalendarIntent
from .capabilities import Action, parse_time
from .drafts import CalendarDraft

DOCUMENT_OPERATIONS = ("document_read", "document_create", "document_rename", "document_append", "document_replace")
CALENDAR_OPERATIONS = ("calendar_query", "calendar_create", "calendar_update")


def validate_intent(value):
    """Accept proposals only, never model-selected execution/transport metadata."""
    if value is None:
        return None
    raw = value.model_dump(mode="json") if isinstance(value, (Action, CalendarIntent)) else value
    if not isinstance(raw, dict):
        raise ValueError("intent_object_required")
    operation = raw.get("operation")
    if operation in CALENDAR_OPERATIONS:
        return CalendarIntent.model_validate(raw)
    if operation not in DOCUMENT_OPERATIONS:
        raise ValueError("unsupported_intent_operation")
    if any(raw.get(key) for key in ("event_id", "start", "end", "followup")):
        raise ValueError("document_intent_cannot_carry_calendar_fields")
    if any(key in raw for key in ("update_fields", "expected_event")):
        # Typed Action instances include empty server-owned defaults. JSON
        # input must not accept even empty caller-supplied execution metadata.
        if not isinstance(value, Action) or value.update_fields or value.expected_event:
            raise ValueError("server_execution_metadata_forbidden")
        raw = {key: item for key, item in raw.items() if key not in {"update_fields", "expected_event"}}
    return Action.model_validate(raw)


class WorkTurnDecision(BaseModel):
    """A planner result cannot confer authority or report execution success."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    _calendar_task_reset: bool = PrivateAttr(default=False)

    @property
    def calendar_task_reset_validated(self) -> bool:
        """Runtime proof set by this turn's verified reset tool, never durable."""
        return self._calendar_task_reset

    route: Literal["general", "new_work", "continue_work", "clarify", "cancel_current", "unsupported"]
    domain: Literal["calendar", "document"] | None = None
    intent: CalendarIntent | Action | None = None
    draft: CalendarDraft | None = None
    question: str = Field(default="", max_length=1000)
    time_window: Literal["today", "tomorrow", "day_after_tomorrow", "this_week", "next_week", "next_7_days"] | None = Field(
        default=None,
        description="日期窗口由服务端计算起止；今天/明天/后天的原钟点可填target_clock组合定位，修改后的时间放changes。",
    )
    target_clock: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="原日程明确钟点HH:MM；与单天time_window合用由服务端计算starts_at，不是修改后的时间。未给原钟点则null。")

    @field_validator("intent", mode="before")
    @classmethod
    def proposal_only(cls, value):
        return validate_intent(value)

    @field_serializer("intent")
    def serialize_proposal(self, value):
        if value is None:
            return None
        if isinstance(value, Action):
            return value.model_dump(mode="json", exclude={"update_fields", "expected_event"})
        return value.model_dump(mode="json")

    @field_validator("question")
    @classmethod
    def bounded_question(cls, value):
        value = value.strip()
        if re.search(r"(?:https?|javascript|data|file|vbscript):|\]\s*\(|<\s*a\b", value, re.I):
            raise ValueError("planner_cannot_supply_links")
        if re.search(r"(?<![A-Za-z0-9_])(?:access_token|refresh_token|app_secret|client_secret|token|secret|user_id|event_id|document_id)(?![A-Za-z0-9_])|授权码|访问令牌|应用密钥|(?:日程|文档|用户)(?:的)?\s*ID", value, re.I):
            raise ValueError("planner_cannot_request_credentials_or_ids")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if self.route in {"general", "cancel_current"}:
            if self.domain is not None or self.intent is not None or self.draft is not None or self.question or self.time_window is not None or self.target_clock is not None:
                raise ValueError("ordinary_and_cancel_routes_cannot_carry_work")
            return self
        if self.intent is not None:
            expected = "calendar" if isinstance(self.intent, CalendarIntent) else "document"
            if self.domain != expected:
                raise ValueError("intent_domain_mismatch")
        if self.draft is not None:
            if self.intent is not None or self.domain != "calendar":
                raise ValueError("calendar_draft_requires_exclusive_calendar_domain")
            if self.time_window is not None or self.target_clock is not None:
                raise ValueError("draft_time_windows_must_be_normalized")
        if self.time_window is not None or self.target_clock is not None:
            if not isinstance(self.intent, CalendarIntent) or self.intent.operation == "calendar_create":
                raise ValueError("time_window_requires_calendar_query_or_update")
            if self.time_window is not None and self.intent.selector.starts_at:
                if self.target_clock is None:
                    raise ValueError("whole_time_window_cannot_also_select_a_specific_start")
                if datetime.fromisoformat(self.intent.selector.starts_at).strftime("%H:%M") != self.target_clock:
                    raise ValueError("target_clock_conflicts_with_specific_start")
        if self.route in {"new_work", "continue_work"}:
            if (self.intent is None and self.draft is None) or self.domain is None or self.question:
                raise ValueError("work_route_requires_only_a_typed_intent")
        elif self.route == "clarify":
            if not self.question:
                raise ValueError("clarification_requires_a_question")
        elif self.route == "unsupported":
            if self.domain is None or self.intent is not None or self.draft is not None or not self.question:
                raise ValueError("unsupported_requires_a_domain_and_explanation")
        return self


def preauthorization_question(intent: CalendarIntent | Action) -> str:
    """Ask for missing user requirements without reading a private resource."""
    if not isinstance(intent, CalendarIntent):
        return ""
    selector, changes = intent.selector, intent.changes
    if intent.operation == "calendar_create":
        if not changes.title or not changes.title.strip() or not changes.start:
            return "请补充新日程的标题、开始时间，以及结束时间或持续多久。"
        if not changes.end and changes.duration_minutes is None:
            return "新日程到几点结束，或需要持续多久？"
        return ""
    if intent.operation == "calendar_update":
        if not changes.model_dump(exclude_none=True, exclude={"keep_duration"}):
            return "您想修改日程的名称、描述还是时间？请说明具体变更。"
        if changes.title is not None and not changes.title.strip():
            return "日程名称不能为空，请提供新的名称。"
        if changes.start and changes.end is None and changes.duration_minutes is None and changes.keep_duration is None:
            return "调整开始时间后，是否保持原来的时长？也可以提供新的结束时间。"
    selected = selector.candidate is not None or selector.recent or bool(selector.event_id)
    if selected:
        return ""
    if selector.starts_at:
        try:
            parse_time(selector.starts_at)
        except ValueError:
            return "请确认原日程的具体日期和开始时间，时间按北京时间理解。"
        # The resolver derives the containing local day for starts_at.
        return ""
    if not selector.start or not selector.end:
        return "您想查看或定位哪一天、哪一周或哪段时间的日程？时间按北京时间理解。"
    try:
        begin, end = parse_time(selector.start), parse_time(selector.end)
    except ValueError:
        return "请确认完整的日期范围，时间按北京时间理解。"
    if end <= begin or end - begin > 31 * 86400:
        return "请提供起止明确、不超过31天的查询范围。"
    return ""
