"""Calendar intent/resolution regressions; no real Work traffic or writes."""

import json
import time
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from test_work_capabilities import fixture, message
from test_work_documents import calendar_message, inbound, service

from app.integrations.work import agent
from app.integrations.work.auth import Principal
from app.integrations.work.calendar import CalendarIntent, CalendarResolver
from app.integrations.work.capabilities import Action, parse_time
from app.integrations.work.client import WorkError

START = "2026-10-22T10:00:00+08:00"
END = "2026-10-22T10:30:00+08:00"
DAY = {"start": "2026-10-22T00:00:00+08:00", "end": "2026-10-23T00:00:00+08:00"}


def event(eid="event-123", title="技术交流"):
    return {
        "event_id": eid,
        "summary": title,
        "description": "保留原有说明",
        "status": "confirmed",
        "organizer_calendar_id": "primary",
        "start_time": {"timestamp": str(parse_time(START)), "timezone": "Asia/Shanghai"},
        "end_time": {"timestamp": str(parse_time(END)), "timezone": "Asia/Shanghai"},
    }


def setup(items=None):
    vault, api, tools = fixture()
    p = Principal.from_message("uat-app", message())
    events = {x["event_id"]: x for x in (items if items is not None else [event()])}

    async def call(op, **kw):
        if op == "calendar_primary":
            return {"data": {"calendars": [{"calendar": {"calendar_id": "primary", "type": "primary", "role": "owner"}}]}}
        if op == "events":
            return {"data": {"items": list(events.values()), "has_more": False}}
        if op == "event_get":
            return {"data": {"event": deepcopy(events[kw["event_id"]])}}
        if op == "event_attendees":
            return {"data": {"items": [], "has_more": False}}
        if op == "event_update":
            events[kw["event_id"]].update(kw["payload"])
            return {"data": {"event": deepcopy(events[kw["event_id"]])}}
        raise AssertionError(op)

    api.side_effect = call
    return p, api, tools, CalendarResolver(tools), events


def intent(**updates):
    data = {"operation": "calendar_update", "selector": {**DAY, "starts_at": START}, "changes": {"title": "项目会议"}}
    data.update(updates)
    return CalendarIntent.model_validate(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [None, {}, {"title": None, "text": None, "start": None, "end": None}])
async def test_day_query_accepts_explicit_absence_of_changes_and_keeps_all_day_results(monkeypatch, changes):
    raw = {"operation": "calendar_query", "selector": DAY, "changes": changes}
    model = AsyncMock(return_value=json.dumps(raw))
    monkeypatch.setattr(agent, "model_text", model)
    plan = await agent.propose_action("查询我2026年10月22日的日程。", "", [])
    assert isinstance(plan, CalendarIntent) and plan.operation == "calendar_query"
    assert plan.changes is not None and not plan.changes.model_dump(exclude_none=True)
    assert model.await_count == 1
    early, late = event("morning", "技术交流"), event("afternoon", "项目讨论")
    late.update(start_time={"timestamp": str(parse_time("2026-10-22T14:00:00+08:00"))}, end_time={"timestamp": str(parse_time("2026-10-22T15:00:00+08:00"))})
    p, api, tools, resolver, _ = setup([early, late])
    # An earlier selected event must not narrow an explicit whole-day query.
    resolver.remember(p, [early], recent_id="morning")
    result = await resolver.resolve(p, plan)
    assert "技术交流" in result.text and "项目讨论" in result.text
    assert not result.needs_reply and not tools.plans
    assert all(c.args[0] in {"calendar_primary", "events"} for c in api.call_args_list)


@pytest.mark.parametrize(
    "operation,changes",
    [
        ("calendar_create", None),
        ("calendar_update", None),
        ("calendar_query", {"title": "must not be silently ignored"}),
        ("calendar_query", {"shift_minutes": 30}),
    ],
)
def test_no_changes_normalization_is_read_only_and_never_discards_write_fields(operation, changes):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CalendarIntent.model_validate({"operation": operation, "selector": DAY, "changes": changes})


@pytest.mark.parametrize("extra", [{"token": "synthetic-secret"}, {"owner_user_id": "other-owner"}, {"url": "https://untrusted.invalid"}, {"update_fields": ["summary"]}])
def test_null_query_changes_do_not_allow_identity_transport_or_execution_fields(extra):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CalendarIntent.model_validate({"operation": "calendar_query", "selector": DAY, "changes": None, **extra})


@pytest.mark.asyncio
async def test_validation_diagnostics_exclude_raw_values_and_unknown_field_names(monkeypatch, caplog):
    import logging

    model = AsyncMock(return_value=json.dumps({"operation": "calendar_query", "selector": DAY, "changes": None, "private-field-name": "private-field-value"}))
    monkeypatch.setattr(agent, "model_text", model)
    with caplog.at_level(logging.INFO, logger=agent.__name__):
        result = await agent.propose_action("查询日程", "", [])
    assert result.operation == "clarify" and model.await_count == 2
    assert "work_plan_validation" in caplog.text
    assert "private-field-name" not in caplog.text and "private-field-value" not in caplog.text
    repair_prompt = model.call_args.args[1]["格式修复要求"]
    assert "private-field-name" not in repair_prompt and "private-field-value" not in repair_prompt


@pytest.mark.asyncio
async def test_title_only_edit_resolves_live_target_preserves_other_fields_and_patches_only_title():
    p, api, tools, resolver, events = setup()
    result = await resolver.resolve(p, intent())
    a = result.action
    assert a.event_id == "event-123" and a.title == "项目会议"
    assert a.start == START and a.end == END and a.text == "保留原有说明"
    assert a.update_fields == ("summary",)
    plan = await tools.prepare(p, a)
    assert "技术交流" in plan.preview and "项目会议" in plan.preview
    assert not any(c.args[0] == "event_update" for c in api.call_args_list)
    with pytest.raises(WorkError):
        await tools.confirm(Principal.from_message("uat-app", message("B")), plan.id)
    await tools.confirm(p, plan.id)
    write = next(c for c in api.call_args_list if c.args[0] == "event_update")
    assert write.kwargs["payload"] == {"summary": "项目会议"}
    assert events["event-123"]["description"] == "保留原有说明"
    with pytest.raises(WorkError):
        await tools.confirm(p, plan.id)


@pytest.mark.asyncio
async def test_multiple_candidates_require_selection_and_are_principal_scoped():
    p, api, tools, resolver, _ = setup([event(), event("event-456", "项目讨论")])
    result = await resolver.resolve(p, intent())
    assert result.action is None and "1." in result.text and "2." in result.text
    assert not tools.plans
    selected = intent(selector={"candidate": 2})
    other = await resolver.resolve(Principal.from_message("uat-app", message("B")), selected)
    assert other.action is None
    result = await resolver.resolve(p, selected)
    assert result.action.event_id == "event-456"
    assert not any(c.args[0] == "event_update" for c in api.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,expected_start,expected_end",
    [
        ({"shift_minutes": 30}, "2026-10-22T10:30:00+08:00", "2026-10-22T11:00:00+08:00"),
        ({"start": "2026-10-23T14:00:00+08:00", "keep_duration": True}, "2026-10-23T14:00:00+08:00", "2026-10-23T14:30:00+08:00"),
        ({"end": "2026-10-22T11:00:00+08:00"}, START, "2026-10-22T11:00:00+08:00"),
    ],
)
async def test_relative_shift_and_explicit_duration_policy(changes, expected_start, expected_end):
    p, _, _, resolver, _ = setup()
    result = await resolver.resolve(p, intent(changes=changes))
    assert result.action.start == expected_start and result.action.end == expected_end
    assert result.action.title == "技术交流" and result.action.text == "保留原有说明"


@pytest.mark.asyncio
async def test_missing_time_policy_or_create_duration_asks_without_writes():
    p, api, _, resolver, _ = setup()
    result = await resolver.resolve(p, intent(changes={"start": "2026-10-22T11:00:00+08:00"}))
    assert result.action is None and "时长" in result.text
    result = await resolver.resolve(p, CalendarIntent(operation="calendar_create", changes={"title": "讨论", "start": START}))
    assert result.action is None and "结束" in result.text
    assert not any(c.args[0] in {"event_update", "event_create"} for c in api.call_args_list)


@pytest.mark.asyncio
async def test_empty_query_is_a_normal_result_not_a_pending_edit():
    p, _, _, resolver, _ = setup([])
    result = await resolver.resolve(p, CalendarIntent(operation="calendar_query", selector=DAY))
    assert result.action is None and not result.needs_reply and "没有" in result.text


@pytest.mark.asyncio
async def test_changed_event_after_partial_preview_cannot_be_confirmed():
    p, api, tools, resolver, events = setup()
    result = await resolver.resolve(p, intent())
    plan = await tools.prepare(p, result.action)
    events["event-123"]["description"] = "changed after preview"
    with pytest.raises(WorkError, match="target_changed"):
        await tools.confirm(p, plan.id)
    assert not any(c.args[0] == "event_update" for c in api.call_args_list)


@pytest.mark.asyncio
async def test_paginated_search_never_assumes_first_page_unique():
    p, api, _, resolver, _ = setup()
    original = api.side_effect

    async def paginated(op, **kw):
        if op == "events":
            if "page_token" not in kw["params"]:
                return {"data": {"items": [event()], "has_more": True, "page_token": "page2"}}
            return {"data": {"items": [event("event-456")], "has_more": False}}
        return await original(op, **kw)

    api.side_effect = paginated
    result = await resolver.resolve(p, intent())
    assert result.action is None and "多条" in result.text
    api.side_effect = lambda op, **kw: {"data": {"items": [event()], "has_more": True, "page_token": "repeated"}} if op == "events" else None

    # Restore primary separately; repeated pagination must fail closed.
    async def incomplete(op, **kw):
        if op == "events":
            return {"data": {"items": [event()], "has_more": True, "page_token": "repeated"}}
        return await original(op, **kw)

    api.side_effect = incomplete
    result = await resolver.resolve(p, intent())
    assert result.action is None and "缩小" in result.text


@pytest.mark.asyncio
async def test_expired_candidates_and_unqueried_ids_cannot_edit():
    p, _, _, resolver, _ = setup()
    result = await resolver.resolve(p, intent(selector={"event_id": "invented"}))
    assert result.action is None
    await resolver.resolve(p, CalendarIntent(operation="calendar_query", selector=DAY))
    resolver.contexts[p].expires_at = time.monotonic() - 1
    result = await resolver.resolve(p, intent(selector={"candidate": 1}))
    assert result.action is None


@pytest.mark.asyncio
async def test_resolved_target_changed_before_preview_requires_new_resolution():
    p, _, tools, resolver, events = setup()
    result = await resolver.resolve(p, intent())
    events["event-123"]["description"] = "另外修改过"
    with pytest.raises(WorkError, match="target_changed"):
        await tools.prepare(p, result.action)


@pytest.mark.asyncio
async def test_exact_original_start_can_supply_its_day_without_model_filling_query_bounds():
    p, _, _, resolver, _ = setup()
    result = await resolver.resolve(p, intent(selector={"starts_at": START}))
    assert result.action and result.action.event_id == "event-123"


@pytest.mark.asyncio
async def test_partial_edit_cannot_silently_disagree_with_preserved_fields():
    p, api, tools, resolver, _ = setup()
    result = await resolver.resolve(p, intent())
    altered = result.action.model_copy(update={"text": "would be inconsistent"})
    with pytest.raises(WorkError):
        await tools.prepare(p, altered)
    assert not any(c.args[0] == "event_update" for c in api.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"recurrence": "FREQ=DAILY"}, {"organizer_calendar_id": "someone-else"}, {"status": "cancelled"}])
async def test_unsafe_or_cancelled_event_is_not_editable(bad):
    item = event()
    item.update(bad)
    p, _, _, resolver, _ = setup([item])
    result = await resolver.resolve(p, intent())
    assert result.action is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        '{"operation":"event_delete","event_id":"event-123"}',
        '{"operation":"calendar_delete"}',
        '{"operation":"calendar_update","selector":{"event_id":"event-123"}}',
    ],
)
async def test_unsupported_delete_is_a_clear_decline_not_an_internal_validation_error(monkeypatch, raw):
    model = AsyncMock(return_value=raw)
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.propose_action("删除明天的日程", "", [])
    assert isinstance(result, Action) and result.operation == "clarify" and "不支持" in result.text


@pytest.mark.asyncio
async def test_model_json_repair_is_bounded_and_cannot_choose_credentials(monkeypatch):
    good = {"operation": "calendar_update", "selector": {**DAY, "title": "技术交流"}, "changes": {"title": "项目会议"}}
    model = AsyncMock(side_effect=['{"operation":"calendar_update","token":"stolen"}', "```json\n" + json.dumps(good) + "\n```"])
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.propose_action("把明天技术交流改名为项目会议", "", [])
    assert isinstance(result, CalendarIntent) and result.changes.title == "项目会议"
    assert model.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ['{"operation":{}}', '{"operation":[]}', "[" * 1200 + "0" + "]" * 1200])
async def test_malformed_nested_model_output_still_falls_back_safely(monkeypatch, raw):
    model = AsyncMock(return_value=raw)
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.propose_action("帮我改日程", "", [])
    assert result.operation == "clarify" and model.await_count == 2


@pytest.mark.asyncio
async def test_duplicate_model_json_keys_are_not_accepted(monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr(agent, "model_text", model)
    model.reset_mock(side_effect=True)
    model.return_value = '{"operation":"calendar_update","operation":"calendar_create"}'
    result = await agent.propose_action("改个会议", "", [])
    assert result.operation == "clarify" and "invalid_model_plan" not in result.text
    assert model.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attendees,allowed",
    [
        ([{"type": "user", "user_id": "ou-A", "is_organizer": True}], True),
        ([{"type": "user", "user_id": "ou-B", "is_organizer": True}], False),
        ([{"type": "chat", "chat_id": "group", "is_organizer": True}], False),
        ([{"type": "resource", "room_id": "room"}], False),
        ([{"type": "user", "user_id": "ou-A", "is_external": True}], False),
    ],
)
async def test_attendee_policy_allows_only_verified_self_organizer(attendees, allowed):
    p, api, _, resolver, _ = setup()
    original = api.side_effect

    async def call(op, **kw):
        if op == "event_attendees":
            assert kw["params"]["user_id_type"] == "open_id"
            return {"data": {"items": attendees, "has_more": False}}
        return await original(op, **kw)

    api.side_effect = call
    result = await resolver.resolve(p, intent())
    assert (result.action is not None) == allowed


@pytest.mark.asyncio
async def test_query_displays_all_day_events_but_cannot_edit_them():
    item = event()
    item.update(start_time={"date": "2026-10-22"}, end_time={"date": "2026-10-23"})
    p, _, _, resolver, _ = setup([item])
    result = await resolver.resolve(p, CalendarIntent(operation="calendar_query", selector=DAY))
    assert "技术交流" in result.text and "全天" in result.text
    result = await resolver.resolve(p, intent(selector=DAY))
    assert result.action is None and "全天" in result.text


@pytest.mark.asyncio
async def test_calendar_pending_followup_and_revoke_are_principal_scoped():
    instance, _, api, send, _ = service()
    p = instance._identity(inbound())
    from app.integrations.work.auth import Grant

    instance.vault.put(p, Grant("synthetic-calendar-token", time.monotonic() + 600, frozenset({"calendar:calendar:readonly", "calendar:calendar"})))
    instance._planner.side_effect = [
        CalendarIntent(operation="calendar_create", changes={"title": "技术交流", "start": START}),
        CalendarIntent(operation="calendar_create", changes={"title": "技术交流", "start": START, "duration_minutes": 30}),
    ]
    api.side_effect = None
    api.return_value = {"data": {"calendars": [{"calendar": {"calendar_id": "primary", "type": "primary", "role": "owner"}}]}}
    try:
        assert await instance.handle(calendar_message())
        await instance.drain()
        assert p in instance._calendar_dialogues
        assert "结束" in send.call_args.args[0].text
        assert await instance.handle(inbound(text="半个小时就够了", thread_ts="followup"))
        await instance.drain()
        passed = instance._planner.call_args.kwargs["calendar_context"]
        assert passed["pending_dialogue"][0]["user"].startswith("在我的个人日历")
        assert "待确认" in send.call_args.args[0].text
        assert p not in instance._calendar_dialogues
        assert not any(c.args[0] == "event_create" for c in api.call_args_list)
        await instance.handle(inbound(text="/work 撤销授权", thread_ts="revoke"))
        assert p not in instance.calendar.contexts and p not in instance._calendar_dialogues
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_context_for_other_principal_is_never_passed_to_planner():
    instance, _, _, _, _ = service()
    try:
        a = instance._identity(inbound())
        instance._remember_question(a, "private-A-request", "private-A-question")
        instance.calendar.remember(a, [event()], recent_id="event-123")
        # A different connection is a distinct principal even for the same sender.
        b = instance._identity(inbound(connection_id="other-connection"))
        assert b not in instance._calendar_dialogues
        assert instance.calendar.facts(b) == {"candidates": [], "recent_id": ""}
        assert "description" not in instance.calendar.contexts[a].candidates[0]
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_declined_operation_does_not_trap_later_chat_in_calendar_dialogue():
    from app.integrations.work.auth import Grant

    instance, _, _, _, _ = service()
    try:
        p = instance._identity(inbound())
        instance.vault.put(p, Grant("synthetic-token", time.monotonic() + 600, frozenset({"calendar:calendar:readonly", "calendar:calendar"})))
        instance._remember_question(p, "prior request", "prior question")
        instance._planner.return_value = Action(operation="clarify", text="本轮不支持删除。")
        await instance.handle(calendar_message())
        await instance.drain()
        assert p not in instance._calendar_dialogues
        assert await instance.handle(inbound(text="你是谁", thread_ts="ordinary-chat")) is False
    finally:
        await instance.close()
