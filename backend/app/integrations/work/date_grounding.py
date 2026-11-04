"""Pure day-source checks for calendar write proposals, never intent admission.

Only original user inputs and server-owned facts can ground a write day. Query
selectors are unchanged. An unsupported/ambiguous date expression asks for a
concrete day instead of silently selecting today or an old task's first day.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .capabilities import parse_time
from .drafts import CalendarDraft

TZ = ZoneInfo("Asia/Shanghai")
FULL_DAY = re.compile(r"(?<!\d)(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日|号)?(?!\d)")
MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})(?:日|号|(?=\s|[,，。;；]|$))")
SHORT_RANGE = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})(?:日|号)?\s*(?:至|到|[-~～–—])\s*(?:(\d{1,2})月)?(\d{1,2})(?:日|号)")
RELATIVE_DAY = re.compile(r"大后天|大前天|后天|前天|明天|明日|今天|今日|昨天|昨日|\btomorrow\b|\btoday\b|\byesterday\b", re.I)
WEEK_DAY = re.compile(r"(下下|下|本|这)(?:个)?(?:周|星期|礼拜)([一二三四五六日天])")
OFFSET_DAY = re.compile(r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})天(?:之)?([前后])")


@dataclass(frozen=True)
class DateGrounding:
    draft: CalendarDraft
    question: str
    verified_days: list[str]


def _iso_day(year, month, day):
    try:
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def _not_negated(text, start):
    # Parameter-level exclusion only. This does not classify a Work operation
    # and is not a general natural-language negation/intent interpreter.
    return not re.search(r"(?:不是|不要|不选|排除|别选|别安排在)\s*$", text[max(0, start - 10) : start])


def _number(value):
    if value.isdecimal():
        return int(value)
    digits = {x: i for i, x in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if value in digits:
        return digits[value]
    if value.count("十") == 1:
        left, right = value.split("十")
        if (not left or left in digits) and (not right or right in digits):
            return (digits[left] if left else 1) * 10 + (digits[right] if right else 0)
    return None


def _expand_range(first, last):
    if first is None or last is None or not 0 <= (last - first).days <= 31:
        return set()
    return {first + timedelta(days=i) for i in range((last - first).days + 1)}


def _user_days(text, received_at):
    text = unicodedata.normalize("NFKC", text[:12000])
    base = received_at.astimezone(TZ).date() if isinstance(received_at, datetime) and received_at.tzinfo is not None else None
    found = []
    full_spans = [match.span() for match in FULL_DAY.finditer(text)]
    for match in FULL_DAY.finditer(text):
        day = _iso_day(*match.groups())
        if day is not None and _not_negated(text, match.start()):
            found.append((match.start(), match.end(), day))
    years = {day.year for _, _, day in found}
    year = base.year if base is not None else next(iter(years)) if len(years) == 1 else None
    for match in MONTH_DAY.finditer(text):
        if any(start <= match.start() < end for start, end in full_spans):
            continue
        day = _iso_day(year, *match.groups())
        if day is not None and base is not None and day < base and "今年" not in text and not years:
            continue  # Do not choose a past or next year for a bare month/day.
        if day is not None and _not_negated(text, match.start()):
            found.append((match.start(), match.end(), day))
    days = {day for _, _, day in found}
    found.sort()
    for first, last in zip(found, found[1:]):
        if re.fullmatch(r"\s*(?:至|到|[-~～–—])\s*", text[first[1] : last[0]]):
            days.update(_expand_range(first[2], last[2]))
    for match in SHORT_RANGE.finditer(text):
        start_year, month, begin, end_month, finish = match.groups()
        chosen_year = int(start_year) if start_year else year
        first, last = _iso_day(chosen_year, month, begin), _iso_day(chosen_year, end_month or month, finish)
        if not start_year and first is not None and base is not None and first < base and "今年" not in text and not years:
            continue
        if _not_negated(text, match.start()):
            days.update(_expand_range(first, last))
    if base is not None:
        offsets = {"今天": 0, "今日": 0, "today": 0, "明天": 1, "明日": 1, "tomorrow": 1, "后天": 2, "大后天": 3, "昨天": -1, "昨日": -1, "yesterday": -1, "前天": -2, "大前天": -3}
        for match in RELATIVE_DAY.finditer(text):
            if _not_negated(text, match.start()):
                days.add(base + timedelta(days=offsets[match.group().lower()]))
        for match in WEEK_DAY.finditer(text):
            if _not_negated(text, match.start()):
                week, weekday = match.groups()
                index = "一二三四五六日".index("日" if weekday == "天" else weekday)
                days.add(base - timedelta(days=base.weekday()) + timedelta(days={"本": 0, "这": 0, "下": 7, "下下": 14}[week] + index))
        for match in OFFSET_DAY.finditer(text):
            count = _number(match.group(1))
            if count is not None and 0 <= count <= 31 and _not_negated(text, match.start()):
                days.add(base + timedelta(days=count if match.group(2) == "后" else -count))
    return {day.isoformat() for day in days}


def _date_part(value):
    try:
        return datetime.fromtimestamp(parse_time(value), TZ).date().isoformat() if value else ""
    except (ValueError, OverflowError, OSError):
        return ""


def _valid_days(values):
    if not isinstance(values, list):
        return set()
    result = set()
    for value in values[:128]:
        try:
            if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
                result.add(value)
        except ValueError:
            pass
    return result


def _target_days(draft, context):
    if draft.operation not in {"update", "repair"} or draft.cardinality != "single":
        return set()
    candidates = [x for x in context.get("candidates", [])[:50] if isinstance(x, dict)]
    receipts = [x for x in context.get("receipt_events", [])[:10] if isinstance(x, dict)]
    s = draft.selector
    selected = []
    if s.event_id:
        selected = [x for x in candidates + receipts if x.get("event_id") == s.event_id]
    elif s.candidate is not None and s.candidate <= len(candidates):
        selected = [candidates[s.candidate - 1]]
    elif draft.reference == "recent_receipt" or s.recent:
        unique = {x.get("event_id"): x for x in receipts if x.get("event_id")}
        if len(unique) == 1:
            selected = list(unique.values())
    result = set()
    for item in selected:
        if day := _date_part(item.get("start")):
            result.add(day)
    return result


def _retain_clock_without_day(draft):
    """Preserve business conditions while removing an ungrounded absolute day."""
    raw = draft.model_dump(mode="json")
    c = draft.changes
    duration = c.duration_minutes
    clock = ""
    try:
        if c.start:
            clock = datetime.fromtimestamp(parse_time(c.start), TZ).strftime("%H:%M")
        if duration is None and c.start and c.end:
            seconds = parse_time(c.end) - parse_time(c.start)
            if seconds > 0 and seconds % 60 == 0:
                duration = seconds // 60
    except (ValueError, OverflowError, OSError):
        pass
    raw["changes"]["start"] = None
    raw["changes"]["end"] = None
    raw["continuous_span_confirmed"] = False
    raw["pending_start_clock"] = ""
    if duration is not None and duration <= 1440 and clock:
        raw["changes"]["duration_minutes"] = None
        raw["schedule"] = {"start_clock": clock, "duration_minutes": duration, "count": 1}
    else:
        raw["changes"]["duration_minutes"] = duration
        raw["pending_start_clock"] = clock
    cleaned = CalendarDraft.model_validate(raw)
    detail = f"已保留{clock}开始" if clock else "已保留其他条件"
    if duration is not None:
        detail += f"、持续{duration}分钟"
    return cleaned, "请确认这条日程的具体日期；" + detail + "，不会默认安排在今天。"


def ground_write_dates(old, new, user_sources, now, trusted_context=None):
    """Return a grounded proposal or a precise non-executable date question.

    ``same_task`` and ``verified_days`` are supplied by the service, never by a
    model. Fresh tasks accept only this turn's timestamped input. Historical
    inputs without a timestamp can contribute fully specified absolute dates.
    """
    new = CalendarDraft.model_validate(new)
    context = trusted_context if isinstance(trusted_context, dict) else {}
    same_task = context.get("same_task") is True
    verified = _valid_days(context.get("verified_days", [])) if same_task else set()
    for row in (user_sources or [])[-8:]:
        if not isinstance(row, dict) or row.get("role", "user") != "user":
            continue
        text = row.get("text") if isinstance(row.get("text"), str) else row.get("user")
        stamp = row.get("received_at")
        if not isinstance(text, str) or not same_task and stamp != now:
            continue
        verified.update(_user_days(text, stamp))
    if new.operation not in {"create", "repair", "update"}:
        return DateGrounding(new.model_copy(update={"pending_start_clock": ""}), "", sorted(verified)[:128])
    allowed = verified | _target_days(new, context)
    raw = new.model_dump(mode="json")
    schedule = new.schedule
    invalid_schedule = bool(schedule.start_date and schedule.start_date not in allowed or schedule.dates and any(day not in allowed for day in schedule.dates))
    if invalid_schedule:
        raw["schedule"].update(start_date="", dates=[], count=schedule.count or len(schedule.dates) or (1 if new.cardinality == "single" else None))
        raw["continuous_span_confirmed"] = False
        return DateGrounding(CalendarDraft.model_validate(raw), "请说明从哪一天开始，或明确每条日程的日期；其他已提供的条件会保留，不会默认从今天开始。", sorted(verified)[:128])
    starts, ends = _date_part(new.changes.start), _date_part(new.changes.end)
    natural_overnight = False
    if starts in allowed and ends and new.changes.start and new.changes.end:
        seconds = parse_time(new.changes.end) - parse_time(new.changes.start)
        natural_overnight = 0 < seconds <= 86400 and (date.fromisoformat(ends) - date.fromisoformat(starts)).days in {0, 1}
    if starts and starts not in allowed or ends and ends not in allowed and ends != starts and not natural_overnight:
        cleaned, question = _retain_clock_without_day(new)
        return DateGrounding(cleaned, question, sorted(verified)[:128])
    # A verified start and a bounded count generate the following days in code;
    # they do not need individual model-supplied date arithmetic.
    if schedule.start_date and schedule.start_date in allowed and schedule.count is not None:
        start = date.fromisoformat(schedule.start_date)
        verified.update((start + timedelta(days=i)).isoformat() for i in range(schedule.count))
    verified.update(day for day in (starts, ends) if day and (day in allowed or day == starts or natural_overnight))
    if new.pending_start_clock and (new.changes.start or new.schedule.start_clock and (new.schedule.start_date or new.schedule.dates)):
        new = new.model_copy(update={"pending_start_clock": ""})
    return DateGrounding(new, "", sorted(verified)[:128])
