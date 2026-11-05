"""Security and write lifecycle tests: all Work traffic is synthetic."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.channels.message_bus import InboundMessage
from app.integrations.work.auth import Grant, Principal, TokenVault
from app.integrations.work.capabilities import Action, WorkCapabilities, parse_time
from app.integrations.work.client import WorkError

CALENDAR_SCOPES = frozenset({"calendar:calendar:readonly", "calendar:calendar"})


def message(user="A", **kwargs):
    data = dict(
        channel_name="feishu", user_id="ou-" + user, owner_user_id="owner-" + user, connection_id="conn-" + user, workspace_id="chat-" + user, chat_id="chat-" + user, text="测试", thread_ts="msg-" + user, metadata={"chat_type": "p2p"}
    )
    data.update(kwargs)
    return InboundMessage(**data)


def fixture():
    async def find(**kw):
        name = kw["external_account_id"].removeprefix("ou-")
        return {"id": "conn-" + name, "owner_user_id": "owner-" + name, "status": "connected"}

    repo = SimpleNamespace(find_connection_by_external_identity=AsyncMock(side_effect=find))
    vault = TokenVault("uat-app", repo, {"owner-A", "owner-B"})
    for name in ("A", "B"):
        vault.put(Principal.from_message("uat-app", message(name)), Grant("token-" + name, time.monotonic() + 7000, CALENDAR_SCOPES | {"docx:document"}))
    api = AsyncMock()
    return vault, api, WorkCapabilities(vault, api)


@pytest.mark.asyncio
async def test_tokens_are_separated_by_every_trusted_identity_dimension():
    vault, _, _ = fixture()
    a = Principal.from_message("uat-app", message())
    assert (await vault.require(a, "docx:document")).token == "token-A"
    assert (await vault.require(Principal.from_message("uat-app", message("B")), "docx:document")).token == "token-B"
    for change in ({"owner_user_id": "owner-B"}, {"connection_id": "conn-B"}, {"workspace_id": "chat-B"}, {"user_id": "ou-B"}):
        with pytest.raises(WorkError):
            await vault.require(Principal.from_message("uat-app", message(**change)), "docx:document")
    with pytest.raises(WorkError):
        await vault.require(Principal.from_message("other-app", message()), "docx:document")
    assert "token-A" not in repr(vault)
    assert "token-A" not in repr(vault.grants)


@pytest.mark.asyncio
async def test_revocation_and_expiry_fail_closed_before_api_calls():
    vault, api, tools = fixture()
    p = Principal.from_message("uat-app", message())
    vault.repo.find_connection_by_external_identity = AsyncMock(return_value=None)
    with pytest.raises(WorkError):
        await tools.read_document(p, "doxTest123")
    api.assert_not_awaited()
    assert p not in vault.grants


@pytest.mark.parametrize("extra", [{"token": "stolen"}, {"owner_user_id": "owner-A"}, {"url": "http://evil"}, {"attendees": ["other-person"]}])
def test_model_cannot_choose_identity_token_arbitrary_http_or_invite_people(extra):
    with pytest.raises(ValidationError):
        Action.model_validate({"operation": "document_create", "title": "测试", **extra})


@pytest.mark.asyncio
async def test_write_preview_no_write_and_wrong_user_cannot_confirm():
    vault, api, tools = fixture()
    a = Principal.from_message("uat-app", message())
    b = Principal.from_message("uat-app", message("B"))
    api.return_value = {"data": {"document": {"document_id": "doxTest123", "title": "旧名", "revision_id": 7}}}
    plan = await tools.prepare(a, Action(operation="document_rename", document_id="doxTest123", title="新名"))
    assert all(call.args[0] == "document_info" for call in api.call_args_list)
    assert "旧名" in plan.preview and "新名" in plan.preview
    with pytest.raises(WorkError):
        await tools.confirm(b, plan.id)
    assert plan.stage == "pending"
    assert all(call.args[0] == "document_info" for call in api.call_args_list)


@pytest.mark.asyncio
async def test_confirm_executes_once_and_reads_back_result():
    vault, api, tools = fixture()
    a = Principal.from_message("uat-app", message())
    api.side_effect = [
        {"data": {"document": {"title": "旧名", "revision_id": 7}}},
        {"data": {"document": {"title": "旧名", "revision_id": 7}}},
        {"data": {"block": {"block_id": "doxTest123"}}},
        {"data": {"document": {"title": "新名", "revision_id": 8}}},
    ]
    plan = await tools.prepare(a, Action(operation="document_rename", document_id="doxTest123", title="新名"))
    replies = await asyncio.gather(tools.confirm(a, plan.id), tools.confirm(a, plan.id), return_exceptions=True)
    assert sum(isinstance(x, WorkError) for x in replies) == 1
    assert plan.stage == "completed"
    assert sum(call.args[0] == "block_update" for call in api.call_args_list) == 1
    assert api.call_args_list[2].kwargs["params"]["document_revision_id"] == 7
    assert "新名" in next(x for x in replies if isinstance(x, str))


@pytest.mark.asyncio
async def test_changed_document_or_plan_and_expired_plan_never_write():
    for mode in ("revision", "expiry", "tamper"):
        _, api, tools = fixture()
        a = Principal.from_message("uat-app", message())
        api.return_value = {"data": {"document": {"title": "旧名", "revision_id": 7}}}
        plan = await tools.prepare(a, Action(operation="document_rename", document_id="doxTest123", title="新名"))
        if mode == "revision":
            api.return_value = {"data": {"document": {"title": "别人的改动", "revision_id": 8}}}
        elif mode == "expiry":
            plan.expires_at = 0
        else:
            plan.payload_json = '{"operation":"document_rename","title":"恶意篡改"}'
        with pytest.raises(WorkError):
            await tools.confirm(a, plan.id)
        assert all(call.args[0] == "document_info" for call in api.call_args_list)


@pytest.mark.asyncio
async def test_timeout_after_write_is_uncertain_not_retried():
    _, api, tools = fixture()
    a = Principal.from_message("uat-app", message())
    api.side_effect = WorkError("document_create")
    plan = await tools.prepare(a, Action(operation="document_create", title="测试"))
    with pytest.raises(WorkError):
        await tools.confirm(a, plan.id)
    assert plan.stage == "uncertain"
    with pytest.raises(WorkError):
        await tools.confirm(a, plan.id)
    assert api.await_count == 1


@pytest.mark.asyncio
async def test_calendar_creation_uses_user_primary_calendar_and_no_invitees():
    _, api, tools = fixture()
    a = Principal.from_message("uat-app", message())
    primary = {"data": {"calendars": [{"calendar": {"calendar_id": "userA@calendar", "type": "primary", "role": "owner"}}]}}
    event = {
        "event_id": "event-123",
        "summary": "测试",
        "description": "",
        "start_time": {"timestamp": str(parse_time("2027-01-15T08:00:00+08:00")), "timezone": "Asia/Shanghai"},
        "end_time": {"timestamp": str(parse_time("2027-01-15T09:00:00+08:00")), "timezone": "Asia/Shanghai"},
    }
    api.side_effect = [primary, primary, {"data": {"event": event}}, {"data": {"event": event}}]
    plan = await tools.prepare(a, Action(operation="event_create", title="测试", start="2027-01-15T08:00:00+08:00", end="2027-01-15T09:00:00+08:00"))
    result = await tools.confirm(a, plan.id)
    assert result is not None
    write = next(c for c in api.call_args_list if c.args[0] == "event_create")
    assert write.kwargs["calendar_id"] == "userA@calendar"
    assert "attendees" not in write.kwargs["payload"]
    assert len(write.kwargs["params"]["idempotency_key"]) >= 32


@pytest.mark.asyncio
async def test_append_replace_and_create_have_preview_confirm_readback_lifecycle():
    a = Principal.from_message("uat-app", message())
    for operation in ("document_append", "document_replace", "document_create"):
        _, api, tools = fixture()
        info = {"data": {"document": {"title": "新文档", "revision_id": 3}}}
        block = {"block_id": "block123", "block_type": 2, "text": {"elements": [{"text_run": {"content": "旧段落"}}]}}
        readback = {"data": {"block": {"text": {"elements": [{"text_run": {"content": "新段落"}}]}}}}
        if operation == "document_create":
            api.side_effect = [{"data": {"document": {"document_id": "doxNew123"}}}, info]
            action = Action(operation=operation, title="新文档")
        elif operation == "document_append":
            api.side_effect = [info, info, {"data": {"children": [{"block_id": "block123"}]}}, readback]
            action = Action(operation=operation, document_id="doxTest123", text="新段落")
        else:
            api.side_effect = [info, {"data": {"items": [block]}}, info, {"data": {"items": [block]}}, {"data": {}}, readback]
            action = Action(operation=operation, document_id="doxTest123", text="新段落", old_text="旧段落")
        plan = await tools.prepare(a, action)
        assert not any(c.args[0] in {"document_create", "block_update", "block_append"} for c in api.call_args_list)
        await tools.confirm(a, plan.id)
        assert plan.stage == "completed"


@pytest.mark.asyncio
async def test_formatted_ambiguous_or_large_paragraph_replacement_is_rejected():
    a = Principal.from_message("uat-app", message())
    for blocks in (
        {"items": [], "has_more": True},
        {"items": [{"block_id": "b1", "block_type": 2, "text": {"elements": [{"text_run": {"content": "old", "text_element_style": {"bold": True}}}]}}]},
        {"items": [{"block_id": "b1", "block_type": 2, "text": {"elements": [{"text_run": {"content": "old"}}]}}] * 2},
    ):
        _, api, tools = fixture()
        api.side_effect = [{"data": {"document": {"title": "old", "revision_id": 1}}}, {"data": blocks}]
        with pytest.raises(WorkError):
            await tools.prepare(a, Action(operation="document_replace", document_id="doxTest123", old_text="old", text="new"))
        assert not tools.plans


@pytest.mark.asyncio
async def test_permission_failure_does_not_clear_another_users_grant():
    vault, api, tools = fixture()
    a = Principal.from_message("uat-app", message())
    b = Principal.from_message("uat-app", message("B"))
    vault.grants[a].scopes = frozenset({"docx:document"})
    with pytest.raises(WorkError):
        await tools.prepare(a, Action(operation="event_create", title="x", start="2026-10-17T08:00:00+08:00", end="2026-10-17T09:00:00+08:00"))
    api.assert_not_awaited()
    assert (await vault.require(b, "calendar:calendar:readonly")).token == "token-B"


@pytest.mark.asyncio
async def test_event_edit_requires_owned_queried_event_and_no_attendees():
    p = Principal.from_message("uat-app", message())
    for has_attendees in (False, True):
        _, api, tools = fixture()
        primary = {"data": {"calendars": [{"calendar": {"calendar_id": "userA@calendar", "type": "primary", "role": "owner"}}]}}
        event = {"event_id": "event-123", "summary": "old", "description": "old", "organizer_calendar_id": "userA@calendar", "status": "confirmed"}
        action = Action(operation="event_update", event_id="event-123", title="new", text="new", start="2026-10-17T08:00:00+08:00", end="2026-10-17T09:00:00+08:00")
        api.return_value = primary
        with pytest.raises(WorkError):
            await tools.prepare(p, action)
        tools.remember_events(p, ["event-123"])
        attendees = {"data": {"items": [{"attendee_id": "someone"}] if has_attendees else []}}
        updated = {**event, "summary": "new", "description": "new", "start_time": {"timestamp": str(parse_time(action.start))}, "end_time": {"timestamp": str(parse_time(action.end))}}
        api.side_effect = [primary, {"data": {"event": event}}, attendees, primary, {"data": {"event": event}}, attendees, {"data": {"event": updated}}, {"data": {"event": updated}}]
        if has_attendees:
            with pytest.raises(WorkError):
                await tools.prepare(p, action)
        else:
            plan = await tools.prepare(p, action)
            await tools.confirm(p, plan.id)
            assert plan.stage == "completed"


@pytest.mark.asyncio
async def test_plan_cancellation_and_expired_grant_cannot_write():
    vault, api, tools = fixture()
    p = Principal.from_message("uat-app", message())
    for cancel in (False, True):
        plan = await tools.prepare(p, Action(operation="document_create", title="new"))
        if cancel:
            tools.cancel(p, plan.id)
        else:
            vault.grants[p].expires_at = 0
        with pytest.raises(WorkError):
            await tools.confirm(p, plan.id)
        if not cancel:
            vault.put(p, Grant("new-token", time.monotonic() + 1000, frozenset({"docx:document"})))
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_app_switch_or_feature_disable_invalidates_cached_grants():
    vault, api, tools = fixture()
    p = Principal.from_message("uat-app", message())
    vault.runtime_guard = lambda: False
    with pytest.raises(WorkError):
        await tools.read_document(p, "doxTest123")
    assert not vault.grants
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_calendar_scope_bundle_requires_every_permission_on_the_same_user_token():
    vault, _, _ = fixture()
    a = Principal.from_message("uat-app", message())
    b = Principal.from_message("uat-app", message("B"))
    scope = " ".join(sorted(CALENDAR_SCOPES))
    assert (await vault.require(a, scope)).token == "token-A"
    vault.grants[a].scopes = frozenset({"calendar:calendar:readonly"})
    with pytest.raises(WorkError) as error:
        await vault.require(a, scope)
    assert error.value.stage == "scope_not_granted"
    assert (await vault.require(b, scope)).token == "token-B"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,required",
    [
        ("calendar_primary", "calendar:calendar:readonly"),
        ("events", "calendar:calendar:readonly"),
        ("event_get", "calendar:calendar:readonly"),
        ("event_attendees", "calendar:calendar:readonly"),
        ("event_create", "calendar:calendar"),
        ("event_update", "calendar:calendar"),
    ],
)
async def test_calendar_api_calls_enforce_their_specific_scope(operation, required):
    vault, api, tools = fixture()
    p = Principal.from_message("uat-app", message())
    api.return_value = {"data": {}}
    vault.grants[p].scopes = frozenset({required})
    await tools.call(p, operation)
    assert api.await_args.kwargs["token"] == "token-A"
    api.reset_mock()
    vault.grants[p].scopes = CALENDAR_SCOPES - {required}
    with pytest.raises(WorkError) as error:
        await tools.call(p, operation)
    assert error.value.stage == "scope_not_granted"
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_write_scope_is_not_treated_as_permission_to_read_primary_calendar():
    vault, api, tools = fixture()
    p = Principal.from_message("uat-app", message())
    vault.grants[p].scopes = frozenset({"calendar:calendar"})
    with pytest.raises(WorkError) as error:
        await tools.primary(p)
    assert error.value.stage == "scope_not_granted"
    api.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_grant_during_request", [False, True])
async def test_user_permission_rejection_clears_only_failed_token_not_other_user_or_newer_grant(replace_grant_during_request):
    vault, api, tools = fixture()
    a = Principal.from_message("uat-app", message())
    b = Principal.from_message("uat-app", message("B"))
    replacement = Grant("new-token-A", time.monotonic() + 3600, CALENDAR_SCOPES)

    async def rejected(*args, **kwargs):
        if replace_grant_during_request:
            vault.put(a, replacement)
        raise WorkError("calendar_primary", 400, 99991679)

    api.side_effect = rejected
    with pytest.raises(WorkError):
        await tools.primary(a)
    if replace_grant_during_request:
        assert vault.grants[a] is replacement
    else:
        assert a not in vault.grants
    assert (await vault.require(b, "calendar:calendar:readonly")).token == "token-B"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["event_create", "event_update"])
async def test_calendar_write_admission_requires_readback_permission_before_any_api_call(operation):
    vault, api, tools = fixture()
    p = Principal.from_message("uat-app", message())
    action = Action(operation=operation, event_id="known-event" if operation == "event_update" else "", title="测试", start="2026-10-22T10:00:00+08:00", end="2026-10-22T10:30:00+08:00")
    vault.grants[p].scopes = CALENDAR_SCOPES - {"calendar:calendar:readonly"}
    with pytest.raises(WorkError) as error:
        await tools.prepare(p, action)
    assert error.value.stage == "scope_not_granted"
    assert not tools.plans
    api.assert_not_awaited()


@pytest.mark.asyncio
async def test_calendar_readonly_token_can_query_but_cannot_prepare_writes():
    vault, api, tools = fixture()
    p = Principal.from_message("uat-app", message())
    vault.grants[p].scopes = frozenset({"calendar:calendar:readonly"})
    api.side_effect = [
        {"data": {"calendars": [{"calendar": {"calendar_id": "userA@calendar", "type": "primary", "role": "owner"}}]}},
        {"data": {"items": []}},
    ]
    start, end = "2026-10-22T10:00:00+08:00", "2026-10-22T10:30:00+08:00"
    await tools.events(p, Action(operation="events_list", start=start, end=end))
    assert [c.args[0] for c in api.call_args_list] == ["calendar_primary", "events"]
    api.reset_mock()
    with pytest.raises(WorkError) as error:
        await tools.prepare(p, Action(operation="event_create", title="测试", start=start, end=end))
    assert error.value.stage == "scope_not_granted"
    api.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["event_delete", "attendee_create", "attendee_delete", "room_reserve", "calendar_acl_create"])
async def test_broad_calendar_scope_does_not_enable_unsupported_operations(operation):
    _, api, tools = fixture()
    with pytest.raises(WorkError) as error:
        await tools.call(Principal.from_message("uat-app", message()), operation)
    assert error.value.stage == "unsupported_operation"
    api.assert_not_awaited()
