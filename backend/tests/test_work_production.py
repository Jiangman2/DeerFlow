"""Production Work boundaries; synthetic credentials and isolated databases only."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.integrations.work.config import WorkSettings, apply_work_credential_defaults
from app.integrations.work.store import WorkStore
from deerflow.persistence.work.model import WorkRecordRow


@pytest.fixture
def clean_work_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith("DEER_FLOW_WORK_"):
            monkeypatch.delenv(name)


def test_production_is_default_profile_but_feature_is_opt_in(clean_work_env):
    settings = WorkSettings.from_env()
    assert settings.profile == "production" and not settings.enabled
    assert settings.document_host == "hnwork.w.cnooc.com.cn"
    assert settings.callback_url == "https://cnoocclaw.cnooc.com.cn/api/integrations/work/oauth/callback"


def test_production_full_rollout_does_not_require_an_owner_list(clean_work_env, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_ENABLED", "true")
    assert WorkSettings.from_env().allowed_owners is None


def test_explicit_empty_owner_list_overrides_legacy_developer_filter(clean_work_env, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_DOCS_ALLOWED_OWNER_IDS", "synthetic-developer")
    monkeypatch.setenv("DEER_FLOW_WORK_ALLOWED_OWNER_IDS", "")
    assert WorkSettings.from_env().allowed_owners is None


def test_ui_credentials_win_and_environment_only_bootstraps_missing_values(clean_work_env, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_PROFILE", "production")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_APP_ID", "cli_env")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_APP_SECRET", "synthetic-env-secret")
    config = {"feishu": {"enabled": True, "app_id": "cli_ui", "app_secret": "synthetic-ui-secret"}}
    apply_work_credential_defaults(config)
    assert config["feishu"]["app_id"] == "cli_ui"
    assert config["feishu"]["app_secret"] == "synthetic-ui-secret"
    assert config["feishu"]["domain"] == "https://open.w.cnooc.com.cn"
    empty = {}
    apply_work_credential_defaults(empty)
    assert empty["feishu"]["app_id"] == "cli_env"
    disabled = {"feishu": {"_runtime_disabled": True, "enabled": False}}
    apply_work_credential_defaults(disabled)
    assert not disabled["feishu"]["enabled"]


def test_legacy_local_uat_profile_is_preserved(clean_work_env, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_DOCS_UAT_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_DOCS_ALLOWED_OWNER_IDS", "synthetic-owner")
    settings = WorkSettings.from_env()
    assert settings.profile == "uat" and settings.enabled
    assert settings.document_host == "haiyoutest.w.cnooc.com.cn"
    assert settings.callback_url == "http://localhost:10086/auth/callback"


def test_production_rejects_loopback_callback_and_explicitly_empty_document_host(clean_work_env, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_DOCUMENT_HOST", "")
    with pytest.raises(ValueError):
        WorkSettings.from_env()
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_DOCUMENT_HOST", "documents.example.test")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_CALLBACK_URL", "http://localhost:10086/auth/callback")
    with pytest.raises(ValueError):
        WorkSettings.from_env()


def test_production_document_links_do_not_accept_uat_host(clean_work_env):
    from app.integrations.work.client import document_links

    settings = WorkSettings.from_env()
    url = "https://hnwork.w.cnooc.com.cn/docx/doxSynthetic123"
    assert document_links(url, settings.document_host) == [("doxSynthetic123", url)]
    assert not document_links("https://haiyoutest.w.cnooc.com.cn/docx/doxSynthetic123", settings.document_host)


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def stores(tmp_path, request):
    import os

    url = "sqlite+aiosqlite:///" + str(tmp_path / "work.db")
    if request.param == "postgres":
        url = os.getenv("WORK_TEST_POSTGRES_URL", "")
        if not url:
            pytest.skip("Dedicated isolated PostgreSQL test container not selected")
        assert "@127.0.0.1:5432/work_isolated_test" in url
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(WorkRecordRow.__table__.create)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    key = "c3ludGhldGljLXRlc3Qta2V5LTMyLWJ5dGVzISEhISE="
    first = WorkStore(sf, key=key, namespace="production:synthetic-app")
    second = WorkStore(sf, key=key, namespace="production:synthetic-app")
    try:
        yield first, second, sf
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(WorkRecordRow.__table__.drop)
        await engine.dispose()


@pytest.mark.asyncio
async def test_records_are_encrypted_and_bound_to_namespace_kind_id_and_owner(stores):
    one, two, sf = stores
    await one.create("grant", "user-A", {"token": "synthetic-private-token"}, owner="principal-A", ttl=300)
    assert (await two.read("grant", "user-A", owner="principal-A")).value["token"] == "synthetic-private-token"
    assert await two.read("grant", "user-A", owner="principal-B") is None
    assert await two.read("state", "user-A", owner="principal-A") is None
    other = WorkStore(sf, key=one.key, namespace="uat:synthetic-app")
    assert await other.read("grant", "user-A", owner="principal-A") is None
    async with sf() as session:
        row = (await session.execute(select(WorkRecordRow))).scalar_one()
        assert "synthetic-private-token" not in row.ciphertext
        assert "principal-A" not in repr(row)
        # Copying ciphertext into a different security context must fail authentication.
        row.owner_hash = one.hash("principal-B")
        await session.commit()
    with pytest.raises(Exception, match="work_state_unavailable"):
        await two.read("grant", "user-A", owner="principal-B")


@pytest.mark.asyncio
async def test_cross_instance_claim_once_and_stale_revoke_cannot_delete_new_grant(stores):
    one, two, _ = stores
    record = await one.create("plan", "plan-id", {"data": 1}, owner="A", ttl=300, status="pending")
    claimed = await asyncio.gather(*(store.change("plan", "plan-id", record, status="executing") for store in (one, two)))
    assert sum(item is not None for item in claimed) == 1
    old = await one.create("grant", "A", {"token": "old"}, owner="A", ttl=300)
    new = await two.change("grant", "A", old, value={"token": "new"})
    assert new is not None
    assert not await one.remove("grant", "A", old)
    assert (await one.read("grant", "A", owner="A")).value["token"] == "new"


@pytest.mark.asyncio
async def test_shared_admission_counts_only_active_jobs_and_is_fair(stores):
    one, two, _ = stores
    for index in range(32):
        await one.create("job", str(index), {}, owner="A", ttl=900, status="completed")
    assert await two.admit("job", "new", {}, owner="A", ttl=300, active={"waiting", "processing"}, limit=2, owner_limit=1)
    assert await one.admit("job", "second-A", {}, owner="A", ttl=300, active={"waiting", "processing"}, limit=2, owner_limit=1) is None
    assert await one.admit("job", "first-B", {}, owner="B", ttl=300, active={"waiting", "processing"}, limit=2, owner_limit=1)
    assert await two.admit("job", "first-C", {}, owner="C", ttl=300, active={"waiting", "processing"}, limit=2, owner_limit=1) is None


@pytest.mark.asyncio
async def test_expired_records_and_wrong_owner_cannot_be_consumed(stores):
    one, two, _ = stores
    record = await one.create("state", "one-use", {"browser": "A"}, owner="A", ttl=-1)
    assert await two.read("state", "one-use", owner="A") is None
    assert await two.change("state", "one-use", record, status="consumed") is None


@pytest.mark.asyncio
async def test_shared_lease_has_exact_owner_and_no_unconditional_release(stores):
    one, two, _ = stores
    lease = await one.acquire("receiver", "process-one", ttl=60)
    assert lease
    assert await two.acquire("receiver", "process-two", ttl=60) is None
    assert not await two.release("receiver", "process-two")
    assert await one.release("receiver", "process-one")
    assert await two.acquire("receiver", "process-two", ttl=60)


@pytest.mark.asyncio
async def test_deleted_and_recreated_grant_is_not_vulnerable_to_aba_revocation(stores):
    one, two, _ = stores
    old = await one.create("grant", "A", {"token": "old"}, owner="A", ttl=300)
    assert await one.remove("grant", "A", old)
    await two.create("grant", "A", {"token": "new"}, owner="A", ttl=300)
    assert not await one.remove("grant", "A", old)
    assert (await two.read("grant", "A", owner="A")).value["token"] == "new"


def principal_message(name="A", text=None, source="message-1"):
    from app.channels.message_bus import InboundMessage

    return InboundMessage(
        channel_name="feishu",
        chat_id="chat-" + name,
        user_id="ou-" + name,
        owner_user_id="owner-" + name,
        connection_id="conn-" + name,
        workspace_id="chat-" + name,
        text=text or "总结 https://documents.example.test/docx/doxTest123",
        thread_ts=source,
        metadata={"chat_type": "p2p", "message_id": source},
    )


def production_service(store, api=None, planner=None):
    from app.integrations.work.capabilities import Action
    from app.integrations.work.persistent import PersistentWorkService

    async def find(**kwargs):
        name = kwargs["external_account_id"].removeprefix("ou-")
        return {"id": "conn-" + name, "owner_user_id": "owner-" + name, "status": "connected"}

    repo = SimpleNamespace(find_connection_by_external_identity=AsyncMock(side_effect=find))
    return PersistentWorkService(
        app_id="cli_production_fixture",
        app_secret="synthetic-secret",
        allowed_owners=None,
        repo=repo,
        send=AsyncMock(),
        store=store,
        settings=WorkSettings(enabled=True, document_host="documents.example.test"),
        api=api or AsyncMock(),
        summarize=AsyncMock(return_value="会议总结"),
        planner=planner or AsyncMock(return_value=Action(operation="document_read", document_id="doxTest123")),
    )


@pytest.mark.asyncio
async def test_oauth_can_start_authorize_and_callback_in_different_processes_after_restart(stores):
    from urllib.parse import parse_qs, urlsplit

    one, two, _ = stores
    first = production_service(one)
    await first.handle(principal_message())
    ticket = next(iter(first.jobs))
    assert "https://cnoocclaw.cnooc.com.cn/api/integrations/work/oauth/authorize?ticket=" in first.send.call_args.args[0].text
    await first.close()
    second = production_service(two)
    start = await second.authorize(ticket, "")
    state = parse_qs(urlsplit(start["authorize_url"]).query)["state"][0]
    await second.close()
    api = AsyncMock(side_effect=[{"access_token": "synthetic-user-A", "expires_in": 7200, "scope": "docx:document"}, {"data": {"open_id": "ou-A"}}, {"data": {"document": {"title": "会议"}}}, {"data": {"content": "讨论上线"}}])
    third = production_service(one, api)
    try:
        await third.callback(state=state, code="synthetic-code", browser_session=start["browser_session"])
        await third.drain()
        assert "会议总结" in third.send.call_args.args[0].text
        assert api.await_count == 4
        with pytest.raises(Exception):
            await third.callback(state=state, code="synthetic-code", browser_session=start["browser_session"])
        assert api.await_count == 4
        p = third._identity(principal_message())
        assert (await third.vault.require(p, "docx:document")).token == "synthetic-user-A"
        with pytest.raises(Exception):
            await third.vault.require(third._identity(principal_message("B")), "docx:document")
    finally:
        await third.close()


@pytest.mark.asyncio
async def test_preview_survives_restart_wrong_user_rejected_and_duplicate_confirm_writes_once(stores):
    from app.integrations.work.auth import Grant
    from app.integrations.work.capabilities import Action

    one, two, sf = stores
    first, second = production_service(one), production_service(two)
    p = first._identity(principal_message())
    await first.vault.install(p, Grant("synthetic-A", time.monotonic() + 300, frozenset({"docx:document"})))
    plan = await first.tools.prepare(p, Action(operation="document_create", title="新文档"))
    first._api.assert_not_awaited()
    await first.close()
    writes = []

    async def api(operation, **kwargs):
        assert kwargs["token"] == "synthetic-A"
        if operation == "document_create":
            writes.append(operation)
            return {"data": {"document": {"document_id": "doxNew123"}}}
        return {"data": {"document": {"title": "新文档", "document_id": "doxNew123"}}}

    second.tools.api = api
    third = production_service(one, api)
    try:
        with pytest.raises(Exception):
            await second.tools.confirm(second._identity(principal_message("B")), plan.id)
        results = await asyncio.gather(second.tools.confirm(p, plan.id), third.tools.confirm(p, plan.id), return_exceptions=True)
        assert len(writes) == 1
        assert sum(isinstance(x, str) for x in results) == 1
        record = await one.read("plan", plan.id, owner=first.vault.principal_key(p))
        assert record.status == "completed" and "receipt" in record.value
        assert "payload_json" not in record.value and "preview" not in record.value
        async with sf() as session:
            rows = (await session.execute(select(WorkRecordRow).where(WorkRecordRow.kind == "audit"))).scalars().all()
            assert len(rows) >= 3
    finally:
        await second.close()
        await third.close()


@pytest.mark.asyncio
async def test_old_inflight_request_cannot_revoke_replaced_shared_grant(stores):
    from app.integrations.work.auth import Grant

    one, two, _ = stores
    first, second = production_service(one), production_service(two)
    p = first._identity(principal_message())
    try:
        await first.vault.install(p, Grant("old", time.monotonic() + 300, frozenset({"docx:document"})))
        old = await first.vault.require(p, "docx:document")
        await second.vault.revoke_shared(p)
        await second.vault.install(p, Grant("new", time.monotonic() + 300, frozenset({"docx:document"})))
        await first.vault.revoke_shared(p, expected=old)
        assert (await first.vault.require(p, "docx:document")).token == "new"
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_revoke_invalidates_pending_authorization_in_other_process(stores):
    from urllib.parse import parse_qs, urlsplit

    one, two, _ = stores
    first, second = production_service(one), production_service(two)
    try:
        await first.handle(principal_message())
        ticket = next(iter(first.jobs))
        start = await second.authorize(ticket, "")
        state = parse_qs(urlsplit(start["authorize_url"]).query)["state"][0]
        await first.handle(principal_message(text="/work 撤销授权", source="revoke-msg"))
        with pytest.raises(Exception):
            await second.callback(state=state, code="synthetic-code", browser_session=start["browser_session"])
        second._api.assert_not_awaited()
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_credential_change_closes_cached_integration_without_losing_user_bindings(monkeypatch):
    from app.integrations.work import service

    old = SimpleNamespace(_closed=False, vault=SimpleNamespace(runtime_guard=lambda: False), close=AsyncMock())
    fresh = SimpleNamespace()
    manager = SimpleNamespace(_work_documents=old)
    monkeypatch.setattr(service, "build_work_document_service", lambda _: fresh)
    assert await service.active_work_service(manager) is fresh
    old.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_standby_work_sender_does_not_start_a_second_websocket():
    from app.channels.feishu import FeishuChannel
    from app.channels.message_bus import MessageBus

    channel = FeishuChannel(MessageBus(), {"app_id": "synthetic", "app_secret": "synthetic", "_work_send_only": True})
    try:
        await channel.start()
        assert channel.is_running and channel._api_client is not None
        assert channel._thread is None and not channel.transport_connected
    finally:
        await channel.stop()


@pytest.mark.asyncio
async def test_receiver_that_lost_lease_cannot_bind_or_dispatch_messages():
    from app.channels.feishu import FeishuChannel
    from app.channels.message_bus import MessageBus

    bus = MessageBus()
    channel = FeishuChannel(bus, {"_work_receiver_guard": AsyncMock(return_value=False)})
    channel._add_reaction = AsyncMock()
    assert await channel._bind_connection_from_connect_code(message_id="m", chat_id="c", user_id="u", code="fake")
    await channel._prepare_inbound("message", principal_message())
    channel._add_reaction.assert_not_awaited()
    assert bus.inbound_queue.empty()


@pytest.mark.asyncio
async def test_revoke_during_code_exchange_cannot_reinstall_old_consent(stores):
    from urllib.parse import parse_qs, urlsplit

    first, second = production_service(stores[0]), production_service(stores[1])
    started, proceed = asyncio.Event(), asyncio.Event()

    async def api(operation, **kwargs):
        if operation == "token":
            started.set()
            await proceed.wait()
            return {"access_token": "synthetic-private-A", "expires_in": 7200, "scope": "docx:document"}
        return {"data": {"open_id": "ou-A"}}

    second._api = api
    try:
        await first.handle(principal_message())
        ticket = next(iter(first.jobs))
        authorization = await second.authorize(ticket, "")
        state = parse_qs(urlsplit(authorization["authorize_url"]).query)["state"][0]
        callback = asyncio.create_task(second.callback(state=state, code="synthetic-code", browser_session=authorization["browser_session"]))
        await asyncio.wait_for(started.wait(), 5)
        await first.handle(principal_message(text="/work 撤销授权", source="revoke"))
        proceed.set()
        result = await asyncio.gather(callback, return_exceptions=True)
        assert isinstance(result[0], Exception)
        with pytest.raises(Exception):
            await second.vault.require(second._identity(principal_message()), "docx:document")
    finally:
        proceed.set()
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_different_writes_for_one_user_are_serialized_across_processes(stores):
    from app.integrations.work.auth import Grant
    from app.integrations.work.capabilities import Action

    first, second = production_service(stores[0]), production_service(stores[1])
    p = first._identity(principal_message())
    entered, finish = asyncio.Event(), asyncio.Event()
    writes = []

    async def api(operation, **kwargs):
        if operation == "document_create":
            writes.append(operation)
            entered.set()
            await finish.wait()
            return {"data": {"document": {"document_id": "doxNew123"}}}
        return {"data": {"document": {"title": "test", "document_id": "doxNew123"}}}

    first.tools.api, second.tools.api = api, api
    try:
        await first.vault.install(p, Grant("synthetic-A", time.monotonic() + 300, frozenset({"docx:document"})))
        plans = [await first.tools.prepare(p, Action(operation="document_create", title="test")) for _ in range(2)]
        task = asyncio.create_task(first.tools.confirm(p, plans[0].id))
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(Exception, match="another_write_in_progress"):
            await second.tools.confirm(p, plans[1].id)
        finish.set()
        await task
        assert len(writes) == 1
    finally:
        finish.set()
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_cached_factory_uses_new_ui_secret_and_disables_cleanly(stores, clean_work_env, monkeypatch):
    from app.integrations.work import service

    monkeypatch.setenv("DEER_FLOW_WORK_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_DOCUMENT_HOST", "documents.example.test")
    monkeypatch.setenv("DEER_FLOW_WORK_STATE_KEY", stores[0].key)
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_APP_SECRET", "synthetic-env-secret-not-authoritative")
    monkeypatch.setattr("deerflow.persistence.get_session_factory", lambda: stores[2])
    channels = SimpleNamespace(_config={"feishu": {"enabled": True, "app_id": "cli_prod_fixture", "app_secret": "synthetic-ui-old", "domain": "https://open.w.cnooc.com.cn"}})
    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: channels)
    manager = SimpleNamespace(_work_documents=None, _connection_repo=object(), bus=SimpleNamespace(publish_outbound=AsyncMock()))
    first = await service.active_work_service(manager)
    try:
        assert first._secret == "synthetic-ui-old"
        channels._config["feishu"]["app_secret"] = "synthetic-ui-new"
        second = await service.active_work_service(manager)
        assert first._closed and second is not first
        assert second._secret == "synthetic-ui-new" and second.store.namespace != first.store.namespace
        monkeypatch.setenv("DEER_FLOW_WORK_ENABLED", "false")
        assert await service.active_work_service(manager) is None
        assert second._closed
    finally:
        if manager._work_documents:
            await manager._work_documents.close()


@pytest.mark.asyncio
async def test_uncertain_write_is_persisted_and_never_retried_after_restart(stores):
    from app.integrations.work.auth import Grant
    from app.integrations.work.capabilities import Action

    first, second = production_service(stores[0]), production_service(stores[1])
    p = first._identity(principal_message())
    first.tools.api = AsyncMock(side_effect=TimeoutError("synthetic response lost"))
    try:
        await first.vault.install(p, Grant("synthetic-A", time.monotonic() + 300, frozenset({"docx:document"})))
        plan = await first.tools.prepare(p, Action(operation="document_create", title="test"))
        with pytest.raises(TimeoutError):
            await first.tools.confirm(p, plan.id)
        record = await stores[1].read("plan", plan.id, owner=first.vault.principal_key(p))
        assert record.status == "uncertain"
        with pytest.raises(Exception):
            await second.tools.confirm(p, plan.id)
        second._api.assert_not_awaited()
        assert "不会自动重试" in await second.tools.terminal_notice(p, plan.id)
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_user_b_authorizing_user_a_link_cannot_install_a_grant_or_read_documents(stores):
    from urllib.parse import parse_qs, urlsplit

    first, second = production_service(stores[0]), production_service(stores[1])
    second._api.side_effect = [{"access_token": "synthetic-B", "expires_in": 7200, "scope": "docx:document"}, {"data": {"open_id": "ou-B"}}]
    try:
        await first.handle(principal_message("A"))
        ticket = next(iter(first.jobs))
        start = await second.authorize(ticket, "")
        state = parse_qs(urlsplit(start["authorize_url"]).query)["state"][0]
        with pytest.raises(Exception, match="authorization_user_mismatch"):
            await second.callback(state=state, code="synthetic-B-code", browser_session=start["browser_session"])
        assert second._api.await_count == 2
        second._summarize.assert_not_awaited()
        for name in ("A", "B"):
            with pytest.raises(Exception, match="authorization_required"):
                await second.vault.require(second._identity(principal_message(name)), "docx:document")
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_retained_unknown_execution_receipts_do_not_exhaust_preview_quota(stores):
    from app.integrations.work.auth import Grant
    from app.integrations.work.capabilities import Action

    service = production_service(stores[0])
    p = service._identity(principal_message())
    owner = service.vault.principal_key(p)
    try:
        for index in range(9):
            await stores[0].create("plan", "retained-" + str(index), {"id": "retained"}, owner=owner, ttl=90 * 86400, status="executing")
        await service.vault.install(p, Grant("synthetic-A", time.monotonic() + 300, frozenset({"docx:document"})))
        plan = await service.tools.prepare(p, Action(operation="document_create", title="test"))
        assert plan.stage == "pending"
    finally:
        await service.close()
