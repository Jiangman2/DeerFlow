"""Finite schedules and receipt-based repair use only synthetic provider reads."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from test_work_capabilities import fixture, message

from app.integrations.work.auth import Principal
from app.integrations.work.calendar import CalendarIntent, CalendarResolver
from app.integrations.work.calendar_workflow import resolve_draft
from app.integrations.work.capabilities import parse_time
from app.integrations.work.client import WorkError
from app.integrations.work.drafts import CalendarDraft
from app.integrations.work.turn_state import MemoryTurns

START = "2026-11-03T09:00:00+08:00"
END = "2026-11-03T12:00:00+08:00"
LAST_END = "2026-11-05T12:00:00+08:00"
SCHEDULE = {"start_date": "2026-11-03", "count": 3, "start_clock": "09:00", "duration_minutes": 180}


def event(eid="old-51h", *, title="技术培训", start=START, end=LAST_END):
    return {
        "event_id": eid,
        "summary": title,
        "description": "保留原说明",
        "organizer_calendar_id": "primary",
        "status": "confirmed",
        "start_time": {"timestamp": str(parse_time(start)), "timezone": "Asia/Shanghai"},
        "end_time": {"timestamp": str(parse_time(end)), "timezone": "Asia/Shanghai"},
    }


async def setup(items=(), receipts=(), *, complete=True):
    vault, api, tools = fixture()
    principal = Principal.from_message("uat-app", message())
    state = MemoryTurns()
    owner = vault.principal_key(principal)
    turn = await state.reserve_turn(owner, "workflow-request")
    ref = await state.finish_turn(owner, turn.turn_id, task={"domain": "calendar"})

    async def guard(p, current):
        await state.assert_task(vault.principal_key(p), current)

    tools.task_fencing = True
    tools.task_guard = guard
    tools.recent_calendar_receipts = AsyncMock(return_value=deepcopy(list(receipts)))
    events = {item["event_id"]: deepcopy(item) for item in items}

    async def call(op, **kwargs):
        if op == "calendar_primary":
            return {"data": {"calendars": [{"calendar": {"calendar_id": "primary", "type": "primary", "role": "owner"}}]}}
        if op == "events":
            return {"data": {"items": list(events.values()), "has_more": not complete}}
        if op == "event_get":
            return {"data": {"event": deepcopy(events[kwargs["event_id"]])}}
        if op == "event_attendees":
            return {"data": {"items": [], "has_more": False}}
        raise AssertionError(f"Unexpected provider write: {op}")

    api.side_effect = call
    return principal, api, tools, CalendarResolver(tools), state, ref, events


def create(**changes):
    value = {"operation": "create", "cardinality": "finite", "changes": {"title": "技术培训"}, "schedule": SCHEDULE}
    value.update(changes)
    return CalendarDraft.model_validate(value)


def repair(**changes):
    value = {"operation": "repair", "cardinality": "finite", "reference": "recent_receipt", "schedule": SCHEDULE}
    value.update(changes)
    return CalendarDraft.model_validate(value)


def receipt(eid="old-51h", **values):
    return {"event_id": eid, "title": "", "start": "", "end": "", "operation": "event_create", "status": "completed", **values}


def only_reads(api):
    assert {call.args[0] for call in api.call_args_list} <= {"calendar_primary", "events", "event_get", "event_attendees"}
    assert all(call.kwargs.get("token") == "token-A" for call in api.call_args_list)


@pytest.mark.asyncio
async def test_three_mornings_expand_to_three_separate_three_hour_actions_with_one_range_read():
    p, api, tools, resolver, _, ref, _ = await setup()
    draft = create()
    result = await resolve_draft(p, draft, resolver, tools, task_ref=ref)
    assert not result.needs_reply and len(result.actions) == 3
    assert [a.start[:10] for a in result.actions] == ["2026-11-03", "2026-11-04", "2026-11-05"]
    assert all(a.operation == "event_create" and parse_time(a.end) - parse_time(a.start) == 3 * 3600 for a in result.actions)
    assert not result.conflicts and not tools.plans
    queries = [call for call in api.call_args_list if call.args[0] == "events"]
    assert len(queries) == 1
    assert queries[0].kwargs["params"]["start_time"] == str(parse_time(START))
    assert queries[0].kwargs["params"]["end_time"] == str(parse_time(LAST_END))
    assert draft == create()
    only_reads(api)


@pytest.mark.asyncio
async def test_existing_overlap_is_visible_without_shifting_or_dropping_proposed_items():
    existing = event("busy", title="已有培训", end="2026-11-03T11:00:00+08:00")
    p, api, tools, resolver, _, ref, _ = await setup([existing])
    result = await resolve_draft(p, create(), resolver, tools, task_ref=ref)
    assert len(result.actions) == 3 and not result.needs_reply
    assert result.actions[0].start == START and result.actions[0].end == END
    assert len(result.conflicts) == 1 and result.conflicts[0]["action_index"] == 1
    assert result.conflicts[0]["event_id"] == "busy"
    assert "已有培训" in result.text and "未自动调整" in result.text
    only_reads(api)


@pytest.mark.asyncio
async def test_all_day_calendar_entry_is_also_reported_as_busy():
    existing = event("all-day")
    existing.update(start_time={"date": "2026-11-04"}, end_time={"date": "2026-11-05"})
    p, api, tools, resolver, _, ref, _ = await setup([existing])
    result = await resolve_draft(p, create(), resolver, tools, task_ref=ref)
    assert len(result.actions) == 3
    assert [row["action_index"] for row in result.conflicts] == [2]
    only_reads(api)


@pytest.mark.asyncio
@pytest.mark.parametrize("complete,bad_time", [(False, False), (True, True)])
async def test_incomplete_or_unreadable_conflict_data_never_returns_partial_actions(complete, bad_time):
    existing = event("bad")
    if bad_time:
        existing["start_time"] = {}
    p, api, tools, resolver, _, ref, _ = await setup([existing], complete=complete)
    result = await resolve_draft(p, create(), resolver, tools, task_ref=ref)
    assert result.needs_reply and not result.actions
    assert "核对" in result.text or "读取" in result.text
    only_reads(api)


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule", [{**SCHEDULE, "count": 11}, {"dates": ["2026-11-03", "2026-12-20"], "start_clock": "09:00", "duration_minutes": 180}])
async def test_count_and_query_window_limits_stop_before_any_provider_read(schedule):
    p, api, tools, resolver, _, ref, _ = await setup()
    result = await resolve_draft(p, create(schedule=schedule), resolver, tools, task_ref=ref)
    assert result.needs_reply and not result.actions
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_overlap_is_rejected_before_any_provider_read(monkeypatch):
    first = CalendarIntent(operation="calendar_create", changes={"title": "培训", "start": START, "end": END})
    second = CalendarIntent(operation="calendar_create", changes={"title": "培训", "start": "2026-11-03T11:00:00+08:00", "end": "2026-11-03T13:00:00+08:00"})
    monkeypatch.setattr("app.integrations.work.calendar_workflow.expand_create", lambda _: [first, second])
    p, api, tools, resolver, _, ref, _ = await setup()
    result = await resolve_draft(p, create(), resolver, tools, task_ref=ref)
    assert result.needs_reply and not result.actions and "本批" in result.text
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_business_fields_and_disabled_delete_do_not_access_personal_data():
    p, api, tools, resolver, _, ref, _ = await setup()
    for draft in (create(schedule={}), CalendarDraft(operation="delete"), CalendarDraft(operation="create", cardinality="recurring")):
        result = await resolve_draft(p, draft, resolver, tools, task_ref=ref)
        assert result.needs_reply and not result.actions
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_receipt_is_read_live_before_splitting_wrong_51_hour_event():
    original = event()
    p, api, tools, resolver, _, ref, _ = await setup([original], [receipt()])
    result = await resolve_draft(p, repair(), resolver, tools, task_ref=ref)
    assert not result.needs_reply and len(result.actions) == 3
    first, *others = result.actions
    assert first.operation == "event_update" and first.event_id == original["event_id"]
    assert first.start == START and first.end == END
    assert first.update_fields == ("end_time",)
    assert first.title == original["summary"] and first.text == original["description"]
    assert all(a.operation == "event_create" and a.title == original["summary"] and a.text == original["description"] for a in others)
    assert not result.conflicts  # The replaced 51-hour interval is not an external conflict.
    assert result.selected_facts[0]["end"] == LAST_END
    assert not tools.plans
    tools.recent_calendar_receipts.assert_awaited_once_with(p)
    only_reads(api)


@pytest.mark.asyncio
async def test_repair_preserves_actual_untouched_fields_and_only_explicitly_updates_title():
    original = event(end=END)
    p, api, tools, resolver, _, ref, _ = await setup([original], [receipt()])
    draft = CalendarDraft(operation="repair", reference="recent_receipt", changes={"title": "新标题"})
    result = await resolve_draft(p, draft, resolver, tools, task_ref=ref)
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.update_fields == ("summary",)
    assert (action.start, action.end, action.text) == (START, END, original["description"])
    assert "保留原说明" in action.expected_event
    only_reads(api)


@pytest.mark.asyncio
async def test_stale_receipt_title_and_dates_are_not_used_to_choose_the_target():
    original = event(title="真实标题")
    p, api, tools, resolver, _, ref, _ = await setup([original], [receipt(title="过时标题", start=START, end=END)])
    result = await resolve_draft(p, repair(selector={"title": "过时标题"}), resolver, tools, task_ref=ref)
    assert result.needs_reply and not result.actions and "原条件" in result.text
    assert any(call.args[0] == "event_get" for call in api.call_args_list)
    only_reads(api)


@pytest.mark.asyncio
async def test_multiple_live_receipts_ask_for_selection_instead_of_choosing_latest():
    first, second = event("one"), event("two")
    p, api, tools, resolver, _, ref, _ = await setup([first, second], [receipt("one"), receipt("two")])
    draft = repair()
    result = await resolve_draft(p, draft, resolver, tools, task_ref=ref)
    assert result.needs_reply and not result.actions and "第几条" in result.text
    assert [row["event_id"] for row in resolver.contexts[p].candidates] == ["one", "two"]
    selected = draft.model_copy(update={"selector": draft.selector.model_copy(update={"candidate": 2})})
    resolved = await resolve_draft(p, selected, resolver, tools, task_ref=ref)
    assert resolved.actions[0].event_id == "two"
    only_reads(api)


@pytest.mark.asyncio
async def test_empty_receipts_do_not_fall_back_to_new_event_creation():
    p, api, tools, resolver, _, ref, _ = await setup()
    draft = repair()
    result = await resolve_draft(p, draft, resolver, tools, task_ref=ref)
    assert result.needs_reply and not result.actions and "不会另建替代日程" in result.text
    assert draft.operation == "repair" and draft.schedule.count == 3
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_unqueried_event_id_is_not_promoted_to_known_target():
    p, api, tools, resolver, _, ref, _ = await setup([event()])
    draft = CalendarDraft(operation="repair", selector={"event_id": "old-51h"}, changes={"title": "新标题"})
    result = await resolve_draft(p, draft, resolver, tools, task_ref=ref)
    assert result.needs_reply and not result.actions and "尚未经本账号查询" in result.text
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_receipt_event_changed_to_shared_or_all_day_requires_another_target():
    for shared in (True, False):
        original = event()
        if shared:
            original["recurrence"] = "FREQ=DAILY"
        else:
            original.update(start_time={"date": "2026-11-03"}, end_time={"date": "2026-11-04"})
        p, api, tools, resolver, _, ref, _ = await setup([original], [receipt()])
        result = await resolve_draft(p, repair(), resolver, tools, task_ref=ref)
        assert result.needs_reply and not result.actions
        assert "本轮不修正" in result.text or "编辑条件" in result.text
        only_reads(api)


@pytest.mark.asyncio
async def test_normal_query_and_single_edit_keep_existing_resolver_guards():
    p, api, tools, resolver, _, ref, _ = await setup([event(end=END)])
    selector = {"start": "2026-11-03T00:00:00+08:00", "end": "2026-11-04T00:00:00+08:00"}
    query = await resolve_draft(p, CalendarDraft(operation="query", selector=selector), resolver, tools, task_ref=ref)
    assert "技术培训" in query.text and not query.actions
    edited = await resolve_draft(p, CalendarDraft(operation="update", selector=selector, changes={"title": "新标题"}), resolver, tools, task_ref=ref)
    assert edited.actions[0].update_fields == ("summary",)
    assert edited.actions[0].text == "保留原说明"
    only_reads(api)


@pytest.mark.asyncio
async def test_topic_exit_during_slow_conflict_read_discards_the_entire_proposal():
    p, api, tools, resolver, state, ref, _ = await setup()
    original = api.side_effect

    async def read_then_exit(op, **kwargs):
        data = await original(op, **kwargs)
        if op == "events":
            owner = tools.vault.principal_key(p)
            turn = await state.reserve_turn(owner, "photosynthesis-question")
            await state.finish_turn(owner, turn.turn_id, mode="exit")
        return data

    api.side_effect = read_then_exit
    with pytest.raises(WorkError, match="task_superseded"):
        await resolve_draft(p, create(), resolver, tools, task_ref=ref)
    assert not tools.plans
    only_reads(api)


@pytest.mark.asyncio
async def test_unrelated_old_receipt_that_became_recurring_does_not_block_named_repair():
    unrelated, wanted = event("other", title="其他会议"), event("wanted")
    unrelated["recurrence"] = "FREQ=DAILY"
    p, api, tools, resolver, _, ref, _ = await setup([unrelated, wanted], [receipt("other"), receipt("wanted")])
    result = await resolve_draft(p, repair(selector={"title": "技术培训"}), resolver, tools, task_ref=ref)
    assert len(result.actions) == 3 and result.actions[0].event_id == "wanted"
    assert {call.kwargs["event_id"] for call in api.call_args_list if call.args[0] == "event_attendees"} == {"wanted"}
    only_reads(api)


@pytest.mark.asyncio
async def test_duplicate_old_receipt_ids_are_read_as_one_target_not_ambiguity():
    p, api, tools, resolver, _, ref, _ = await setup([event()], [receipt(), receipt(title="陈旧事实")])
    result = await resolve_draft(p, repair(), resolver, tools, task_ref=ref)
    assert len(result.actions) == 3 and not result.needs_reply
    only_reads(api)


@pytest.mark.asyncio
async def test_unchanged_first_day_can_be_preserved_while_proposing_remaining_days():
    p, api, tools, resolver, _, ref, _ = await setup([event(end=END)], [receipt()])
    result = await resolve_draft(p, repair(changes={"title": "技术培训"}), resolver, tools, task_ref=ref)
    assert len(result.actions) == 2
    assert all(action.operation == "event_create" for action in result.actions)
    assert [action.start[:10] for action in result.actions] == ["2026-11-04", "2026-11-05"]
    only_reads(api)


@pytest.mark.asyncio
async def test_selected_event_changed_between_discovery_and_edit_guard_is_not_repaired():
    p, api, tools, resolver, _, ref, events = await setup([event()], [receipt()])
    original = api.side_effect
    gets = 0

    async def changed(op, **kwargs):
        nonlocal gets
        if op == "event_get":
            gets += 1
            if gets == 2:
                events["old-51h"]["summary"] = "已被其他客户端修改"
        return await original(op, **kwargs)

    api.side_effect = changed
    result = await resolve_draft(p, repair(selector={"title": "技术培训"}), resolver, tools, task_ref=ref)
    assert result.needs_reply and not result.actions and "最新信息" in result.text
    only_reads(api)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(404, None), (403, 99991679), (None, None)])
async def test_selected_missing_event_is_clarified_but_permission_and_transport_failures_propagate(status, code):
    p, api, tools, resolver, _, ref, _ = await setup([event()], [receipt()])
    original = api.side_effect

    async def failed(op, **kwargs):
        if op == "event_get":
            raise WorkError("event_get", status, code)
        return await original(op, **kwargs)

    api.side_effect = failed
    if status == 404:
        result = await resolve_draft(p, repair(), resolver, tools, task_ref=ref)
        assert result.needs_reply and not result.actions and "不存在或不可访问" in result.text
    else:
        with pytest.raises(WorkError) as caught:
            await resolve_draft(p, repair(), resolver, tools, task_ref=ref)
        assert caught.value.http_status == status and caught.value.business_code == code
    only_reads(api)


@pytest.mark.asyncio
async def test_another_principal_cannot_reuse_the_workflow_task_or_read_its_receipts():
    p, api, tools, resolver, _, ref, _ = await setup([event()], [receipt()])
    other = Principal.from_message("uat-app", message("B"))
    assert other != p
    with pytest.raises(WorkError, match="task_superseded"):
        await resolve_draft(other, repair(), resolver, tools, task_ref=ref)
    tools.recent_calendar_receipts.assert_not_awaited()
    api.assert_not_awaited()
