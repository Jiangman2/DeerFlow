"""UAT document OAuth: no live Work calls or messages in this suite."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.channels.message_bus import InboundMessage
from app.channels.work_documents import UAT_APP_ID, WorkDocumentService, WorkError, document_links
from app.integrations.work.auth import Grant
from app.integrations.work.capabilities import Action

DOC_URL = "https://haiyoutest.w.cnooc.com.cn/docx/doxTest123"
CALENDAR_SCOPES = (
    "calendar:calendar:readonly",
    "calendar:calendar",
)


def calendar_message(**overrides):
    return inbound(text="在我的个人日历新增日程，2026年10月22日10:00至10:30，标题：技术交流，不邀请其他人", **overrides)


def inbound(**overrides):
    values = dict(
        channel_name="feishu",
        chat_id="chat-test",
        user_id="ou-sender",
        text="请总结 " + DOC_URL,
        connection_id="connection-test",
        owner_user_id="owner-test",
        workspace_id="chat-test",
        thread_ts="message-test",
        metadata={"chat_type": "p2p", "message_id": "message-test"},
    )
    values.update(overrides)
    return InboundMessage(**values)


def service():
    repo = SimpleNamespace(find_connection_by_external_identity=AsyncMock(return_value={"id": "connection-test", "owner_user_id": "owner-test", "status": "connected"}))
    api = AsyncMock(
        side_effect=[
            {"access_token": "synthetic-user-token", "expires_in": 7200, "scope": "docx:document"},
            {"data": {"open_id": "ou-sender"}},
            {"data": {"document": {"document_id": "doxTest123", "title": "测试会议"}}},
            {"data": {"content": "会议决定周五上线。"}},
        ]
    )
    send = AsyncMock()
    summarize = AsyncMock(return_value="会议决定：周五上线。")
    planner = AsyncMock(return_value=Action(operation="document_read", document_id="doxTest123"))
    instance = WorkDocumentService(app_id=UAT_APP_ID, app_secret="synthetic-app-secret", allowed_owners={"owner-test"}, repo=repo, send=send, api=api, summarize=summarize, planner=planner)
    return instance, repo, api, send, summarize


async def begin(instance, msg=None):
    assert await instance.handle(msg or inbound())
    ticket = next(iter(instance.jobs))
    started = await instance.authorize(ticket, "")
    state = parse_qs(urlsplit(started["authorize_url"]).query)["state"][0]
    return ticket, state, started["browser_session"]


def test_links_accept_only_exact_uat_docx_origin_and_ids():
    assert document_links("总结 " + DOC_URL) == [("doxTest123", DOC_URL)]
    for link in (
        "http://haiyoutest.w.cnooc.com.cn/docx/abc",
        "https://haiyoutest.w.cnooc.com.cn.evil.test/docx/abc",
        "https://evil.test/docx/abc",
        "https://haiyoutest.w.cnooc.com.cn@evil.test/docx/abc",
        "https://haiyoutest.w.cnooc.com.cn:443/docx/abc",
        "https://haiyoutest.w.cnooc.com.cn/docx/abc/extra",
        "https://haiyoutest.w.cnooc.com.cn/docx/abc%2fsecret",
    ):
        assert not document_links(link)


def test_production_application_is_rejected():
    with pytest.raises(ValueError):
        WorkDocumentService(app_id="production-app", app_secret="secret", allowed_owners={"owner"}, repo=None, send=AsyncMock())


@pytest.mark.asyncio
async def test_authorization_read_and_summary_return_to_original_message_without_token_leak():
    instance, repo, api, send, summarize = service()
    try:
        ticket, state, nonce = await begin(instance)
        assert instance.jobs[ticket].scope == "docx:document"
        assert api.await_count == 0
        assert "http://localhost:10086/authorize?ticket=" in send.call_args.args[0].text
        assert send.call_args.args[0].is_final  # Waiting for consent is not an endless thinking card.
        result = await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        assert result["stage"] == "accepted"
        await instance.drain()
        assert instance.jobs[ticket].stage == "completed"
        assert api.await_count == 4
        assert all(c.kwargs.get("token") == "synthetic-user-token" for c in api.call_args_list[1:])
        assert summarize.await_args.args == ("请总结 " + DOC_URL, "测试会议", "会议决定周五上线。")
        final = send.call_args.args[0]
        assert final.is_final and "周五上线" in final.text
        assert final.chat_id == "chat-test" and final.thread_ts == "message-test"
        assert final.owner_user_id == "owner-test"
        for item in send.call_args_list:
            assert "synthetic-user-token" not in item.args[0].text
            assert "synthetic-app-secret" not in item.args[0].text
        assert repo.find_connection_by_external_identity.await_count >= 3
        assert "synthetic-user-token" not in repr(instance.jobs)
        with pytest.raises(WorkError):
            await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_wrong_browser_and_wrong_work_account_cannot_read_or_summarize():
    instance, _, api, _, summarize = service()
    try:
        _, state, nonce = await begin(instance)
        with pytest.raises(WorkError):
            await instance.callback(state=state, code="synthetic-code", browser_session="wrong-browser")
        assert api.await_count == 0
        api.side_effect = [{"access_token": "synthetic-token", "expires_in": 7200}, {"data": {"open_id": "other-person"}}]
        with pytest.raises(WorkError):
            await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        assert api.await_count == 2
        summarize.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_revoked_or_transferred_binding_blocks_callback_before_token_exchange():
    instance, repo, api, _, summarize = service()
    try:
        _, state, nonce = await begin(instance)
        repo.find_connection_by_external_identity.return_value = {"id": "other-connection", "owner_user_id": "other-owner", "status": "connected"}
        with pytest.raises(WorkError):
            await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        api.assert_not_awaited()
        summarize.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_group_and_unapproved_owner_cannot_read_documents():
    for msg in (inbound(metadata={"chat_type": "group"}), inbound(owner_user_id="other-owner"), inbound(connection_id=None)):
        instance, _, api, _, summarize = service()
        try:
            assert await instance.handle(msg)
            assert not instance.jobs
            api.assert_not_awaited()
            summarize.assert_not_awaited()
        finally:
            await instance.close()


@pytest.mark.asyncio
async def test_token_reused_only_for_same_owner_connection_and_can_expire():
    instance, _, api, _, _ = service()
    try:
        _, state, nonce = await begin(instance)
        await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        await instance.drain()
        api.side_effect = [{"data": {"document": {"document_id": "doxTest123", "title": "测试会议"}}}, {"data": {"content": "新内容"}}]
        assert await instance.handle(inbound(thread_ts="message-2", metadata={"chat_type": "p2p", "message_id": "message-2"}))
        await instance.drain()
        assert api.await_count == 6
        for grant in instance.grants.values():
            grant.expires_at = 0
        await instance.handle(inbound(thread_ts="message-3", metadata={"chat_type": "p2p", "message_id": "message-3"}))
        assert api.await_count == 6
        assert list(instance.jobs.values())[-1].stage == "awaiting_authorization"
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_document_permission_failure_is_not_reported_as_success():
    instance, _, api, send, summarize = service()
    try:
        _, state, nonce = await begin(instance)
        api.side_effect = [{"access_token": "synthetic-token", "expires_in": 7200, "scope": "docx:document"}, {"data": {"open_id": "ou-sender"}}, WorkError("document_info", 403, 1770032)]
        await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        await instance.drain()
        assert list(instance.jobs.values())[0].stage == "failed"
        assert "403" in send.call_args.args[0].text and "1770032" in send.call_args.args[0].text
        summarize.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_expired_authorization_and_duplicate_message_are_bounded():
    instance, _, api, send, _ = service()
    try:
        ticket, state, nonce = await begin(instance)
        count = send.await_count
        assert await instance.handle(inbound())
        assert len(instance.jobs) == 1 and send.await_count == count
        instance.jobs[ticket].expires_at = 0
        with pytest.raises(WorkError):
            await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_real_http_adapter_uses_fixed_host_no_redirects_and_no_document_writes():
    from app.channels.work_documents import WorkAPI

    observed = []

    async def handler(request):
        observed.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"content": "test"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False) as client:
        api = WorkAPI(client=client)
        await api("document_content", document_id="doxTest123", token="synthetic-token")
        with pytest.raises(WorkError):
            await api("document_content", document_id="../../secret", token="synthetic-token")
        with pytest.raises(WorkError):
            await api("delete_document", document_id="doxTest123", token="synthetic-token")
    assert len(observed) == 1
    assert str(observed[0].url) == "https://open.w.cnooc.com.cn/open-apis/docx/v1/documents/doxTest123/raw_content"
    assert observed[0].method == "GET"


@pytest.mark.asyncio
async def test_manager_document_hook_preserves_binding_gate(monkeypatch, tmp_path):
    from app.channels.manager import ChannelManager, _BoundIdentityRejection
    from app.channels.message_bus import MessageBus
    from app.channels.store import ChannelStore

    manager = ChannelManager(MessageBus(), ChannelStore(path=tmp_path / "channels.json"))
    manager._work_documents = SimpleNamespace(handle=AsyncMock(return_value=True), _closed=False, vault=SimpleNamespace(runtime_guard=lambda: True))
    manager._get_bound_identity_rejection = AsyncMock(return_value=_BoundIdentityRejection())
    manager._reject_unbound_channel_message = AsyncMock()
    await manager._handle_chat(inbound())
    manager._work_documents.handle.assert_not_awaited()
    manager._get_bound_identity_rejection.return_value = None
    await manager._handle_chat(inbound())
    manager._work_documents.handle.assert_awaited_once()


def test_factory_is_disabled_by_default_and_requires_explicit_owners(monkeypatch):
    from app.channels.work_documents import build_work_document_service

    monkeypatch.delenv("DEER_FLOW_WORK_DOCS_UAT_ENABLED", raising=False)
    assert build_work_document_service(object()) is None
    monkeypatch.setenv("DEER_FLOW_WORK_DOCS_UAT_ENABLED", "true")
    monkeypatch.delenv("DEER_FLOW_WORK_DOCS_ALLOWED_OWNER_IDS", raising=False)
    assert build_work_document_service(object()) is None


@pytest.mark.asyncio
async def test_summary_uses_real_model_factory_with_existing_token_limit(monkeypatch):
    from app.channels.work_documents import summarize_document
    from deerflow.config.model_config import ModelConfig
    from deerflow.models import factory

    model = ModelConfig(name="fake-summary", use="langchain_core.language_models.fake_chat_models:FakeListChatModel", model="fake", max_tokens=128, responses=["离线测试总结"])
    config = SimpleNamespace(models=[model], get_model_config=lambda _name: model)
    monkeypatch.setattr(factory, "get_app_config", lambda: config)
    monkeypatch.delenv("DEER_FLOW_WORK_DOCS_MODEL", raising=False)
    assert await summarize_document("请总结", "测试", "测试内容") == "离线测试总结"


@pytest.mark.asyncio
async def test_followups_reuse_personal_grant_and_never_ask_to_rebind():
    instance, _, api, send, _ = service()
    try:
        _, state, nonce = await begin(instance)
        await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        await instance.drain()

        async def doc_api(op, **kw):
            if op == "document_info":
                return {"data": {"document": {"title": "测试会议"}}}
            assert op == "document_content" and kw["token"] == "synthetic-user-token"
            return {"data": {"content": "会议内容"}}

        api.side_effect = doc_api
        for i in range(5):
            assert await instance.handle(inbound(text="刚才的文档再总结一下", thread_ts=f"followup-{i}"))
            await instance.drain()
        assert len(instance.grants) == 1
        assert all("完成渠道内绑定" not in c.args[0].text for c in send.call_args_list)
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_model_cannot_switch_to_another_document_or_execute_write_before_confirmation():
    instance, _, api, send, _ = service()
    try:
        instance._planner.return_value = Action(operation="document_rename", document_id="doxOther123", title="wrong-target")
        _, state, nonce = await begin(instance)
        await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        await instance.drain()
        assert api.await_count == 2  # Token/identity only; no document access.
        assert not instance.tools.plans
        assert "model_changed_document_target" in send.call_args.args[0].text
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_authorization_only_prepares_write_then_original_sender_confirms():
    instance, _, api, send, _ = service()
    try:
        instance._planner.return_value = Action(operation="document_rename", document_id="doxTest123", title="新名字")
        info = {"data": {"document": {"title": "旧名字", "revision_id": 3}}}
        api.side_effect = [{"access_token": "synthetic-user-token", "expires_in": 7200, "scope": "docx:document"}, {"data": {"open_id": "ou-sender"}}, info]
        _, state, nonce = await begin(instance)
        await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        await instance.drain()
        plan = next(iter(instance.tools.plans.values()))
        assert plan.stage == "pending" and api.await_count == 3
        assert "/work 确认 " + plan.id in send.call_args.args[0].text
        api.side_effect = [info, {"data": {}}, {"data": {"document": {"title": "新名字", "revision_id": 4}}}]
        assert await instance.handle(inbound(text="/work 确认 " + plan.id, thread_ts="confirm-msg"))
        assert plan.stage == "completed"
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_local_revoke_invalidates_unused_oauth_links_and_cannot_be_replayed():
    instance, _, api, _, _ = service()
    try:
        _, state, nonce = await begin(instance)
        await instance.handle(inbound(text="/work 撤销授权", thread_ts="revoke-msg"))
        with pytest.raises(WorkError):
            await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_calendar_authorization_requests_one_complete_explicit_capability_bundle():
    instance, _, api, send, _ = service()
    try:
        await instance.handle(calendar_message())
        ticket = next(iter(instance.jobs))
        result = await instance.authorize(ticket, "")
        query = parse_qs(urlsplit(result["authorize_url"]).query)
        assert query["scope"][0].split() == list(CALENDAR_SCOPES)
        assert "calendar:calendar.event:" not in query["scope"][0]
        assert query["redirect_uri"] == ["http://localhost:10086/auth/callback"]
        assert query["client_id"] == [UAT_APP_ID]
        for description in ("读取日历", "查询日程", "创建日程", "编辑日程"):
            assert description in send.call_args.args[0].text
        assert "单独确认" in send.call_args.args[0].text
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", CALENDAR_SCOPES)
async def test_partial_calendar_consent_is_rejected_before_access_and_preserves_existing_document_grant(missing):
    instance, _, api, send, _ = service()
    try:
        principal = instance._identity(calendar_message())
        previous = Grant("synthetic-existing-document-token", time.monotonic() + 3600, frozenset({"docx:document"}))
        instance.vault.put(principal, previous)
        api.side_effect = [
            {"access_token": "synthetic-partial-token", "expires_in": 7200, "scope": " ".join(s for s in CALENDAR_SCOPES if s != missing)},
            {"data": {"open_id": "ou-sender"}},
        ]
        _, state, nonce = await begin(instance, calendar_message())
        with pytest.raises(WorkError) as error:
            await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        assert error.value.stage == "scope_not_granted"
        assert api.await_count == 2
        instance._planner.assert_not_awaited()
        assert not instance.tools.plans
        assert instance.grants[principal] is previous
        assert "ITC" in send.call_args.args[0].text and "权限" in send.call_args.args[0].text
        assert "synthetic-partial-token" not in send.call_args.args[0].text
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_calendar_write_only_token_requires_new_consent_before_calendar_access():
    instance, _, api, send, _ = service()
    try:
        instance.vault.put(instance._identity(calendar_message()), Grant("synthetic-legacy-token", time.monotonic() + 3600, frozenset({"calendar:calendar"})))
        await instance.handle(calendar_message())
        await instance.drain()
        assert next(iter(instance.jobs.values())).stage == "awaiting_authorization"
        assert "授权并继续" in send.call_args.args[0].text
        api.assert_not_awaited()
        instance._planner.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_previous_fine_grained_grant_requires_new_compatible_authorization():
    instance, _, api, send, _ = service()
    try:
        scopes = frozenset({"calendar:calendar:read", "calendar:calendar.event:read", "calendar:calendar.event:create", "calendar:calendar.event:update"})
        instance.vault.put(instance._identity(calendar_message()), Grant("synthetic-previous-token", time.monotonic() + 3600, scopes))
        await instance.handle(calendar_message())
        await instance.drain()
        assert next(iter(instance.jobs.values())).stage == "awaiting_authorization"
        assert "授权并继续" in send.call_args.args[0].text
        api.assert_not_awaited()
        instance._planner.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_complete_calendar_grant_prepares_then_confirms_and_reuses_without_new_oauth():
    instance, _, api, send, _ = service()
    try:
        action = Action(operation="event_create", title="技术交流", start="2026-10-22T10:00:00+08:00", end="2026-10-22T10:30:00+08:00")
        instance._planner.return_value = action
        primary = {"data": {"calendars": [{"calendar": {"calendar_id": "sender@calendar", "type": "primary", "role": "owner"}}]}}
        api.side_effect = [
            {"access_token": "synthetic-calendar-token", "expires_in": 7200, "scope": " ".join(CALENDAR_SCOPES)},
            {"data": {"open_id": "ou-sender"}},
            primary,
        ]
        _, state, nonce = await begin(instance, calendar_message())
        assert (await instance.callback(state=state, code="synthetic-code", browser_session=nonce))["stage"] == "accepted"
        await instance.drain()
        plan = next(iter(instance.tools.plans.values()))
        assert plan.stage == "pending"
        assert [c.args[0] for c in api.call_args_list] == ["token", "identity", "calendar_primary"]
        assert "/work 确认 " + plan.id in send.call_args.args[0].text

        from app.integrations.work.capabilities import parse_time

        event = {
            "data": {
                "event": {
                    "event_id": "synthetic-event",
                    "summary": action.title,
                    "description": "",
                    "start_time": {"timestamp": str(parse_time(action.start))},
                    "end_time": {"timestamp": str(parse_time(action.end))},
                }
            }
        }
        api.side_effect = [primary, event, event]
        await instance.handle(inbound(text="/work 确认 " + plan.id, thread_ts="calendar-confirm"))
        assert plan.stage == "completed"
        assert sum(c.args[0] == "event_create" for c in api.call_args_list) == 1

        instance._planner.return_value = Action(operation="events_list", start=action.start, end=action.end)
        api.side_effect = [primary, {"data": {"items": []}}]
        await instance.handle(inbound(text="查询我的日历在该时间段的日程", thread_ts="calendar-followup"))
        await instance.drain()
        assert list(instance.jobs.values())[-1].stage == "completed"
        assert sum(c.args[0] == "token" for c in api.call_args_list) == 1
        assert all(c.kwargs.get("token") == "synthetic-calendar-token" for c in api.call_args_list[1:])
        assert "synthetic-calendar-token" not in repr(send.call_args_list)
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_calendar_consent_from_different_work_account_never_installs_token_or_reads_calendar():
    instance, _, api, _, _ = service()
    try:
        api.side_effect = [
            {"access_token": "synthetic-other-user-token", "expires_in": 7200, "scope": " ".join(CALENDAR_SCOPES)},
            {"data": {"open_id": "ou-other-user"}},
        ]
        _, state, nonce = await begin(instance, calendar_message())
        with pytest.raises(WorkError) as error:
            await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        assert error.value.stage == "authorization_user_mismatch"
        assert not instance.grants and api.await_count == 2
        instance._planner.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_work_calendar_permission_rejection_requests_reauthorization_without_rebinding():
    instance, _, api, send, _ = service()
    try:
        instance._planner.return_value = Action(operation="event_create", title="测试", start="2026-10-22T10:00:00+08:00", end="2026-10-22T10:30:00+08:00")
        api.side_effect = [
            {"access_token": "synthetic-calendar-token", "expires_in": 7200, "scope": " ".join(CALENDAR_SCOPES)},
            {"data": {"open_id": "ou-sender"}},
            WorkError("calendar_primary", 400, 99991679),
        ]
        _, state, nonce = await begin(instance, calendar_message())
        await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        await instance.drain()
        assert not instance.grants and not instance.tools.plans
        text = send.call_args.args[0].text
        assert "99991679" in text and "重新授权" in text and "ITC" in text
        assert "未执行写入" in text and "重新绑定" not in text
        assert api.await_count == 3
    finally:
        await instance.close()
