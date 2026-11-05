"""Read-only calendar draft resolution; proposals never execute provider writes.

Finite occurrences expand deterministically. Repair locators come from the
current principal's receipts or queried candidates, and every target is read
again before an immutable confirmation preview can be prepared elsewhere.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .calendar import TZ, CalendarIntent, CalendarResolver, Changes, describe, event_fact, event_times, iso, query_times
from .capabilities import Action, WorkCapabilities, parse_time
from .client import WorkError
from .drafts import CalendarDraft, expand_create, missing_question

MAX_ACTIONS = 10
MAX_CONFLICTS = 50


@dataclass
class WorkflowResolution:
    actions: list[Action] = field(default_factory=list)
    text: str = ""
    needs_reply: bool = False
    conflicts: list[dict] = field(default_factory=list)
    selected_facts: list[dict] = field(default_factory=list)


def _ask(text):
    return WorkflowResolution(text=text, needs_reply=True)


async def _guard(tools, p, task_ref):
    await tools._assert_task(p, task_ref)


def _expanded_actions(draft):
    try:
        intents = expand_create(draft)
    except ValueError:
        return _ask("请核对每条日程的日期、开始时间和时长；本次不会把多条日程合并为一条跨日日程。")
    if not 1 <= len(intents) <= MAX_ACTIONS:
        return _ask(f"一次最多处理 {MAX_ACTIONS} 条独立日程，请缩小本次日期范围；其他条件会保留。")
    actions = []
    for intent in intents:
        result = CalendarResolver._create(intent.changes)
        if result.action is None:
            return _ask(result.text)
        actions.append(result.action)
    return WorkflowResolution(actions=actions)


async def _with_conflicts(p, actions, tools, task_ref, *, selected_facts=None):
    """Read one aggregate window; model/provider descriptions are only facts."""
    if not actions:
        return WorkflowResolution(selected_facts=selected_facts or [])
    if len(actions) > MAX_ACTIONS:
        return _ask(f"一次最多处理 {MAX_ACTIONS} 条独立日程，请缩小本次范围。")
    intervals = [(parse_time(a.start), parse_time(a.end)) for a in actions]
    for index, (start, end) in enumerate(intervals):
        if any(start < other_end and other_start < end for other_start, other_end in intervals[:index]):
            return _ask("本批拟处理的日程之间存在时间重叠，请确认各条日期、开始时间和时长；尚未生成修改预览。")
    start, end = min(x[0] for x in intervals), max(x[1] for x in intervals)
    if end - start > 31 * 86400:
        return _ask("本次日期跨度超过31天，请缩小范围；每天的时间和其他已提供条件会保留。")
    await _guard(tools, p, task_ref)
    items, complete = await tools.query_events(p, iso(start), iso(end))
    await _guard(tools, p, task_ref)
    if not complete:
        return _ask("现有日程未能完整读取，无法可靠核对时间冲突。请缩小本次日期范围后继续；尚未生成修改预览。")
    # An edited event's old, possibly multi-day interval is replaced by its
    # proposed interval. Internal-overlap checks above cover the resulting set.
    replaced = {a.event_id for a in actions if a.operation == "event_update"}
    conflicts = []
    for item in items:
        if item.get("status") == "cancelled" or item.get("event_id") in replaced:
            continue
        try:
            begin, finish = query_times(item)
        except WorkError:
            return _ask("现有日程中有无法可靠识别的时间信息，请先查询核对或缩小范围；尚未生成修改预览。")
        for index, (proposed_start, proposed_end) in enumerate(intervals, 1):
            if proposed_start < finish and begin < proposed_end:
                conflicts.append({"action_index": index, "event_id": item["event_id"], "title": str(item.get("summary") or "（无标题）")[:200], "start": iso(begin), "end": iso(finish)})
                if len(conflicts) > MAX_CONFLICTS:
                    return _ask("本次涉及的重叠日程较多，请缩小日期范围后核对；尚未生成修改预览。")
    conflicts.sort(key=lambda item: (item["action_index"], item["start"], item["event_id"]))
    text = ""
    if conflicts:
        rows = [f"第{item['action_index']}条与已有“{item['title'].replace(chr(10), ' ')}”（{item['start']} 至 {item['end']}）重叠" for item in conflicts]
        text = "发现现有日程时间重叠，未自动调整时间；请在修改预览中核对，再决定是否确认：\n" + "\n".join(rows)
    return WorkflowResolution(actions=actions, text=text, conflicts=conflicts, selected_facts=selected_facts or [])


async def _live_targets(p, draft, resolver, tools, task_ref):
    selector, ids = draft.selector, []
    context = resolver.contexts.get(p)
    if context and context.expires_at <= time.monotonic():
        context = None
    selected = ""
    if selector.candidate is not None:
        if not context or selector.candidate > len(context.candidates):
            return [], _ask("上次修正候选已过期或序号无效，请说明要修正的原日程日期、时间或名称；已提供的修正条件会保留。")
        selected = context.candidates[selector.candidate - 1].get("event_id", "")
        if selector.event_id and selector.event_id != selected:
            return [], _ask("修正候选序号与指定目标不一致，请重新选择要修正的日程；其他条件会保留。")
    elif selector.event_id:
        selected = selector.event_id
    elif selector.recent and context:
        selected = context.recent_id

    if draft.reference == "recent_receipt":
        await _guard(tools, p, task_ref)
        receipts = await tools.recent_calendar_receipts(p)
        await _guard(tools, p, task_ref)
        if not isinstance(receipts, list) or len(receipts) > MAX_ACTIONS:
            raise WorkError("calendar_receipts_invalid")
        ids = list(dict.fromkeys(row["event_id"] for row in receipts if isinstance(row, dict) and row.get("status") == "completed" and isinstance(row.get("event_id"), str) and 0 < len(row["event_id"]) <= 256))
        if selected:
            ids = [selected] if selected in ids else []
        if not ids:
            return [], _ask("没有找到可核对的本账号成功操作回执。请说明要修正的原日程日期、时间和名称；已提供的修正条件会保留，不会另建替代日程。")
        # Receipt metadata can be incomplete, especially for legacy plans. Its
        # owner-bound event ID is discovery evidence, never current event data.
        tools.remember_events(p, ids)
    elif selected:
        known = tools.known_events.get(p)
        context_ids = {item.get("event_id") for item in context.candidates} if context else set()
        if selected not in context_ids and (not known or known[0] <= time.monotonic() or selected not in known[1]):
            return [], _ask("该修正目标尚未经本账号查询核对，请提供原日期、时间和名称；不会改为创建新的日程。")
        ids = [selected]
        tools.remember_events(p, ids)
    else:
        start, end = selector.start, selector.end
        if selector.starts_at and not start and not end:
            day = datetime.fromtimestamp(parse_time(selector.starts_at), TZ).replace(hour=0, minute=0, second=0, microsecond=0)
            start, end = day.isoformat(), (day + timedelta(days=1)).isoformat()
        if not start or not end:
            return [], _ask("请说明要修正的原日程日期、时间或名称；已提供的修正条件会保留，不会另建替代日程。")
        await _guard(tools, p, task_ref)
        items, complete = await tools.query_events(p, start, end)
        await _guard(tools, p, task_ref)
        if not complete:
            return [], _ask("原日程未能完整查询，请缩小原日期范围；不会从不完整结果中选择修正目标。")
        items = [item for item in items if resolver._matches(item, selector)]
        if len(items) > MAX_ACTIONS:
            return [], _ask("符合原条件的日程较多，请补充原名称或具体开始时间；已提供的修正条件会保留。")
        ids = [item["event_id"] for item in items]
        tools.remember_events(p, ids)

    if not ids:
        return [], _ask("没有找到仍符合原条件的日程。请核对原日期、时间或名称；不会另建替代日程。")
    await _guard(tools, p, task_ref)
    calendar = await tools.primary(p)
    await _guard(tools, p, task_ref)
    live = []
    for event_id in ids:
        await _guard(tools, p, task_ref)
        try:
            event = (await tools.call(p, "event_get", calendar_id=calendar, event_id=event_id)).get("event", {})
        except WorkError as exc:
            if exc.stage != "event_get" or exc.http_status != 404:
                raise
            await _guard(tools, p, task_ref)
            return [], _ask("原日程已不存在或不可访问，请重新查询并选择要修正的原日程；已提供的修正条件会保留，不会另建替代日程。")
        await _guard(tools, p, task_ref)
        if event.get("event_id") != event_id:
            return [], _ask("原日程回读结果无法核对，请重新查询原日期和名称；尚未生成修改预览。")
        if resolver._matches(event, selector):
            live.append(event)
    if not live:
        return [], _ask("没有找到仍符合原条件、可安全修正的日程。请核对原日期、时间或名称；不会另建替代日程。")
    resolver.remember(p, live, recent_id=live[0]["event_id"] if len(live) == 1 else "")
    if len(live) != 1:
        return [], _ask("找到多条实际日程，请选择要修正的第几条；其他修正条件会保留，尚未修改：\n" + describe(live))
    if draft.operation == "query":
        return live, None
    # Discovery is a read, not edit authority. Re-read the unique selected
    # event under every existing organizer/attendee/recurrence edit guard.
    await _guard(tools, p, task_ref)
    try:
        snapshot = await tools.editable_event(p, live[0]["event_id"], calendar=calendar)
    except WorkError as exc:
        if exc.stage not in {"event_unavailable", "shared_or_recurring_event_not_supported", "event_with_attendees_not_supported", "timed_event_required"} and not (exc.stage == "event_get" and exc.http_status == 404):
            raise
        await _guard(tools, p, task_ref)
        return [], _ask("原日程已不可访问，或不符合本人无其他参会人的非周期日程编辑条件。请查询并明确选择其他修正目标；不会删除或另建替代日程。")
    await _guard(tools, p, task_ref)
    current = snapshot["event"]
    try:
        event_times(current)
    except WorkError:
        return [], _ask("原日程已变为全天或无法可靠识别时间的日程，本轮不修正。请核对并选择有明确起止时间的个人日程。")
    if not resolver._matches(current, selector):
        return [], _ask("原日程最新信息已与选择条件不同，请重新查询核对后修正；原有修正条件会保留。")
    resolver.remember(p, [current], recent_id=current["event_id"])
    return [current], None


async def resolve_draft(p, draft: CalendarDraft, resolver: CalendarResolver, tools: WorkCapabilities, *, task_ref) -> WorkflowResolution:
    """Resolve current-authority reads into all-or-zero validated proposals."""
    await _guard(tools, p, task_ref)
    question = missing_question(draft)
    if question:
        return _ask(question)
    if draft.operation == "create":
        expanded = _expanded_actions(draft)
        if expanded.needs_reply:
            return expanded
        return await _with_conflicts(p, expanded.actions, tools, task_ref)
    if draft.operation in {"query", "update"} and draft.reference != "recent_receipt":
        if draft.operation == "update" and draft.cardinality != "single":
            return _ask("本轮编辑需要明确选择一条原日程，尚不自动批量修改。请先查询选择目标；现有修正条件会保留。")
        intent = CalendarIntent(operation="calendar_query" if draft.operation == "query" else "calendar_update", selector=draft.selector, changes=draft.changes)
        result = await resolver.resolve(p, intent)
        await _guard(tools, p, task_ref)
        if result.action is None:
            return WorkflowResolution(text=result.text, needs_reply=result.needs_reply)
        return await _with_conflicts(p, [result.action], tools, task_ref)
    if draft.operation not in {"repair", "update", "query"}:
        return _ask("本次操作尚未开放，可以查询、新建或编辑个人日程；不会执行删除。")
    live, blocked = await _live_targets(p, draft, resolver, tools, task_ref)
    if blocked:
        return blocked
    original = live[0]
    if draft.operation == "query":
        try:
            fact = event_fact(original)
        except WorkError:
            fact = {"event_id": original["event_id"], "title": str(original.get("summary") or "")[:200], "editable": False}
        facts = [fact]
        return WorkflowResolution(text="已回读核对个人日程（北京时间）：\n" + describe(live), selected_facts=facts)
    facts = [event_fact(original)]
    begin, end = event_times(original)
    actions = []
    if draft.operation == "repair" and (draft.cardinality == "finite" or draft.schedule.dates or draft.schedule.start_date or draft.schedule.start_clock):
        try:
            changes = Changes(title=draft.changes.title if draft.changes.title is not None else original.get("summary"), text=draft.changes.text if draft.changes.text is not None else original.get("description") or "")
        except ValueError:
            return _ask("原日程标题或说明超出本次支持范围，请明确新的标题或说明后继续修正；原日程未变更。")
        create = draft.model_copy(update={"operation": "create", "changes": changes, "reference": "none"})
        expanded = _expanded_actions(create)
        if expanded.needs_reply:
            return expanded
        first, *remaining = expanded.actions
        update = resolver._update(original, begin, end, Changes(title=draft.changes.title, text=draft.changes.text, start=first.start, end=first.end))
        if update.action is not None:
            actions.append(update.action)
        elif (
            first.start != iso(begin)
            or first.end != iso(end)
            or draft.changes.title is not None
            and draft.changes.title != original.get("summary")
            or draft.changes.text is not None
            and draft.changes.text != (original.get("description") or "")
        ):
            return _ask(update.text)
        actions.extend(remaining)
    else:
        result = resolver._update(original, begin, end, draft.changes)
        if result.action is None:
            return WorkflowResolution(text=result.text, needs_reply=result.needs_reply, selected_facts=facts)
        actions.append(result.action)
    if not actions:
        return WorkflowResolution(text="已回读核对，原日程已符合本次修正条件，无需修改或新增。", selected_facts=facts)
    return await _with_conflicts(p, actions, tools, task_ref, selected_facts=facts)
