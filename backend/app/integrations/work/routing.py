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
_WORK_LOCATION = re.compile(r"海能\s*work|(?<![a-z0-9_])work\s*(?:里|中|内|上|的|云文档|云盘|云空间)|我(?:的)?\s*(?:云文档|云盘|云空间)|云文档(?:空间)?|云盘|云空间", re.I)
_FILENAME_MENTION = re.compile(r"[^\s《》“”\"']\.(?:docx|xlsx|pdf)(?![a-z0-9_-]|\.[a-z0-9])", re.I)
_NON_WORK_FILE_SOURCE = re.compile(r"[/\\]|本地(?:文件|电脑|目录|路径|磁盘|的)|本机[里中上]|电脑[里中上]|桌面[里中上]|附件(?:[里中的]|\s*[:：])")
_READABLE_SUFFIXES = frozenset({"docx", "xlsx", "pdf"})
_UNSAFE_FILE_TITLE = re.compile(r"[/\\<>:*?|\"\x00-\x1f\x7f]|%(?:2f|5c)|[\u202a-\u202e\u2066-\u2069]", re.I)
_PROVIDER_ID = re.compile(r"(?:ou_|oc_|om_|cli_|tenant_)[a-z0-9_-]{8,}|(?:dox|fld|sht|box)[a-z0-9]{16,}", re.I)
_QUOTED_TITLE = re.compile(r'《[^》\n]+》|“[^”\n]+”|「[^」\n]+」|『[^』\n]+』|"[^"\n]+"')


class FileLookupIntent(BaseModel):
    """A current user-supplied title, with no provider ID or action authority."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    operation: Literal["file_lookup"]
    title: str = Field(min_length=1, max_length=160)
    read: bool

    @field_validator("title")
    @classmethod
    def plain_title(cls, value):
        if value != value.strip() or value in {".", ".."} or _UNSAFE_FILE_TITLE.search(value) or _PROVIDER_ID.fullmatch(value):
            raise ValueError("invalid_file_lookup_title")
        return value


class FileLookupDraft(BaseModel):
    """Business slots only; a location is never a grant or provider resource ID."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    source: Literal["work", "local", "attachment", "external", "unknown"] = "unknown"
    title: str | None = Field(default=None, min_length=1, max_length=160)
    read: bool | None = None
    ambiguity: Literal["none", "multiple_targets", "conflicting_request"] = "none"
    uncertain_fields: list[Literal["source", "title", "read"]] = Field(default_factory=list, max_length=3)
    conflict_resolution: Literal["read_only", "find_only"] | None = None

    @field_validator("title")
    @classmethod
    def plain_title(cls, value):
        return FileLookupIntent.plain_title(value) if value is not None else None


def lookup_body(request: str) -> str:
    return re.sub(r"^/(?:work|doc_skill)(?:\s+|$)", "", request.strip(), count=1)


def multiple_lookup_titles(request: str) -> bool:
    return len({match[0][1:-1].strip() for match in _QUOTED_TITLE.finditer(request)}) > 1


def file_lookup_in_scope(request: str) -> bool:
    """Legacy evidence check, never a condition for exposing the semantic tool."""
    body = lookup_body(request)
    if _NON_WORK_FILE_SOURCE.search(body):
        return False
    return bool(_WORK_LOCATION.search(body) or ("上传的" not in body and _FILENAME_MENTION.search(body)))


def validate_file_lookup(intent: FileLookupIntent, request: str, *, semantic_scope: bool = False, allow_title_only: bool = False) -> FileLookupIntent:
    request = lookup_body(request)
    if not isinstance(intent, FileLookupIntent) or _NON_WORK_FILE_SOURCE.search(request) or not semantic_scope and not file_lookup_in_scope(request):
        raise ValueError("file_lookup_title_not_in_current_request")
    if multiple_lookup_titles(request):
        raise ValueError("file_lookup_multiple_targets")
    # A substring that drops a suffix or copies the scope words themselves
    # is not evidence of an explicit filename.
    title = re.compile(r"(?<![A-Za-z0-9_.-])" + re.escape(intent.title) + r"(?![A-Za-z0-9_-]|[.．][A-Za-z0-9])")
    quoted = tuple(_QUOTED_TITLE.finditer(request))

    def disjoint(left, right):
        return left.end() <= right.start() or right.end() <= left.start()

    matches = tuple(match for match in title.finditer(request) if all(disjoint(match, quote) or quote[0][1:-1].strip() == intent.title for quote in quoted))
    locations = tuple(location for location in _WORK_LOCATION.finditer(request) if all(disjoint(location, quote) for quote in quoted))
    if semantic_scope:
        # Cloud words inside an actual name are data. Only an unquoted source
        # label by itself is insufficient evidence of a named target.
        source_only = re.fullmatch(r"海能\s*work|work|云盘|云空间|云文档(?:空间)?", intent.title, re.I)
        grounded = bool(matches) and (not source_only or any(not all(disjoint(match, quote) for quote in quoted) for match in matches))
    elif locations:
        grounded = any(all(disjoint(match, location) for location in locations) for match in matches)
    else:
        stem, _, suffix = intent.title.rpartition(".")
        grounded = bool(matches and stem and suffix.lower() in _READABLE_SUFFIXES)
    if not grounded:
        raise ValueError("file_lookup_title_not_in_current_request")
    if not allow_title_only and request.strip().strip("《》“”「」『』()（）[]\"'。！？!?,.，").strip() == intent.title:
        raise ValueError("file_lookup_requires_read_or_find_request")
    return intent


def explicit_lookup_facts(request: str) -> FileLookupDraft | None:
    """Preserve unambiguous command slots; this is not natural-language admission.

    Other wording still reaches the same semantic schema. Deliberately do not
    infer names from pronouns, multiple targets, examples or compound writes.
    """
    body = lookup_body(request)
    match = re.fullmatch(r"(?:请|麻烦|帮我|请帮我)?\s*(读取|阅读|打开|总结|查找|搜索)\s*(?:我(?:的)?\s*)?(?:海能\s*Work\s*(?:里|中|的)?\s*)?(?:云空间|云文档(?:空间)?|云盘)\s*(?:里|中|内|下)?\s*(?:的)?\s*(.+?)\s*[。.!！]?", body, re.I)
    if not match:
        return None
    title = match[2].strip()
    quote = _QUOTED_TITLE.fullmatch(title)
    if quote:
        title = title[1:-1].strip()
    elif re.search(r"[，,；;。！？!?、《》“”「」『』\"']|以及|和|与|并|然后|再|^(?:这个|那个|哪份|哪些|所有|全部|一些|几个|文档|文件)$", title):
        return None
    try:
        return FileLookupDraft(source="work", title=title, read=match[1] not in {"查找", "搜索"})
    except ValueError:
        return None


def validate_intent(value):
    """Accept proposals only, never model-selected execution/transport metadata."""
    if value is None:
        return None
    raw = value.model_dump(mode="json") if isinstance(value, (Action, CalendarIntent, FileLookupIntent)) else value
    if not isinstance(raw, dict):
        raise ValueError("intent_object_required")
    operation = raw.get("operation")
    if operation == "file_lookup":
        return FileLookupIntent.model_validate(raw)
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
    _lookup_evidence: str = PrivateAttr(default="")
    _lookup_continuation: bool = PrivateAttr(default=False)

    @property
    def lookup_evidence(self) -> str:
        return self._lookup_evidence

    @property
    def lookup_continuation(self) -> bool:
        return self._lookup_continuation

    @property
    def calendar_task_reset_validated(self) -> bool:
        """Runtime proof set by this turn's verified reset tool, never durable."""
        return self._calendar_task_reset

    route: Literal["general", "new_work", "continue_work", "clarify", "cancel_current", "unsupported"]
    domain: Literal["calendar", "document", "file_lookup"] | None = None
    intent: CalendarIntent | Action | FileLookupIntent | None = None
    draft: CalendarDraft | None = None
    lookup: FileLookupDraft | None = None
    question: str = Field(default="", max_length=1000)
    time_window: Literal["today", "tomorrow", "day_after_tomorrow", "this_week", "next_week", "next_7_days"] | None = Field(
        default=None,
        description="日期窗口由服务端计算起止；今天/明天/后天的原钟点可填target_clock组合定位，修改后的时间放changes。",
    )
    target_clock: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="原日程明确钟点HH:MM；与单天time_window合用由服务端计算starts_at，不是修改后的时间。未给原钟点则null。")

    @model_validator(mode="before")
    @classmethod
    def redundant_incomplete_lookup_intent(cls, value):
        """Prefer a matching business draft over its null-action duplicate.

        This narrow provider-shape normalization never drops unknown keys or a
        conflicting target/action. A complete legacy intent stays unchanged.
        """
        if not isinstance(value, dict) or value.get("domain") != "file_lookup":
            return value
        raw = value.get("intent")
        lookup = value.get("lookup")
        if not isinstance(raw, dict) or set(raw) != {"operation", "title", "read"} or raw.get("operation") != "file_lookup" or raw.get("read") is not None or not isinstance(raw.get("title"), str):
            return value
        if not isinstance(lookup, (dict, FileLookupDraft)):
            return value
        draft = FileLookupDraft.model_validate(lookup)
        if draft.title == raw["title"] and draft.read is None:
            return {**value, "intent": None}
        return value

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

    @field_validator("question", mode="before")
    @classmethod
    def null_question_is_absent(cls, value):
        # Some providers emit optional JSON strings as null. Normalize only
        # absence; route consistency still rejects a missing required question.
        return "" if value is None else value

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
            if self.domain is not None or self.intent is not None or self.draft is not None or self.lookup is not None or self.question or self.time_window is not None or self.target_clock is not None:
                raise ValueError("ordinary_and_cancel_routes_cannot_carry_work")
            return self
        if self.intent is not None:
            expected = "calendar" if isinstance(self.intent, CalendarIntent) else "file_lookup" if isinstance(self.intent, FileLookupIntent) else "document"
            if self.domain != expected:
                raise ValueError("intent_domain_mismatch")
            if expected == "file_lookup" and self.route != "new_work" and not (self.lookup is not None and self.route in {"continue_work", "clarify"}):
                raise ValueError("file_lookup_requires_new_current_request")
        if self.draft is not None:
            if self.intent is not None or self.domain != "calendar":
                raise ValueError("calendar_draft_requires_exclusive_calendar_domain")
            if self.time_window is not None or self.target_clock is not None:
                raise ValueError("draft_time_windows_must_be_normalized")
        if self.lookup is not None:
            if self.domain != "file_lookup" or self.draft is not None or self.intent is not None and not isinstance(self.intent, FileLookupIntent):
                raise ValueError("lookup_requires_exclusive_file_domain")
            if self.intent is not None and (self.lookup.title != self.intent.title or self.lookup.read != self.intent.read):
                raise ValueError("lookup_and_intent_conflict")
        if self.time_window is not None or self.target_clock is not None:
            if not isinstance(self.intent, CalendarIntent) or self.intent.operation == "calendar_create":
                raise ValueError("time_window_requires_calendar_query_or_update")
            if self.time_window is not None and self.intent.selector.starts_at:
                if self.target_clock is None:
                    raise ValueError("whole_time_window_cannot_also_select_a_specific_start")
                if datetime.fromisoformat(self.intent.selector.starts_at).strftime("%H:%M") != self.target_clock:
                    raise ValueError("target_clock_conflicts_with_specific_start")
        if self.route in {"new_work", "continue_work"}:
            if (self.intent is None and self.draft is None and self.lookup is None) or self.domain is None or self.question and self.lookup is None:
                raise ValueError("work_route_requires_only_a_typed_intent")
        elif self.route == "clarify":
            if not self.question and self.lookup is None:
                raise ValueError("clarification_requires_a_question")
        elif self.route == "unsupported":
            if self.domain is None or self.intent is not None or self.draft is not None or self.lookup is not None or not self.question:
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
