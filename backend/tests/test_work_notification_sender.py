"""Scheduled Work recipients are verified privately and sent by the app."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.feishu import FeishuChannel
from app.channels.message_bus import MessageBus
from app.integrations.work import notifications
from deerflow.persistence.channel_connections import ChannelConnectionRepository, ChannelConnectionRow, ChannelCredentialRow

UUID = "2520d9ea-cec5-51f8-865a-1cdb46e367cd"


@pytest_asyncio.fixture
async def runtime(monkeypatch):
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    monkeypatch.setenv("DEER_FLOW_WORK_SCHEDULED_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_PROFILE", "production")
    monkeypatch.delenv("DEER_FLOW_WORK_ALLOWED_OWNER_IDS", raising=False)
    monkeypatch.delenv("DEER_FLOW_WORK_DOCS_ALLOWED_OWNER_IDS", raising=False)
    monkeypatch.delenv("DEER_FLOW_WORK_PRODUCTION_NOTIFICATION_TENANT_KEY", raising=False)
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(ChannelConnectionRow.__table__.create)
        await connection.run_sync(ChannelCredentialRow.__table__.create)
    try:
        repo = ChannelConnectionRepository(async_sessionmaker(engine))
        config = {"enabled": True, "app_id": "cli_current", "app_secret": "synthetic", "domain": "https://open.w.cnooc.com.cn"}
        channel = FeishuChannel(MessageBus(), {**config, "connection_repo": repo, "_work_receiver_guard": AsyncMock(return_value=True)})
        channel._running = True
        channel._thread = SimpleNamespace(is_alive=lambda: True)
        channel._ws_connected.set()
        channel._CreateMessageRequest = CreateMessageRequest
        channel._CreateMessageRequestBody = CreateMessageRequestBody
        create = MagicMock(return_value=SimpleNamespace(code=0, raw=SimpleNamespace(status_code=200), data=SimpleNamespace(message_id="om_receipt")))
        channel._api_client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(create=create))))
        channels = SimpleNamespace(manager=SimpleNamespace(_connection_repo=repo), get_channel=lambda name: channel, _read_channel_config=lambda name: dict(config))
        monkeypatch.setattr("app.channels.service.get_channel_service", lambda: channels)
        yield channel, repo, config, create
    finally:
        await engine.dispose()


async def _bind(repo, owner="owner", *, sender="ou_owner", chat="oc_private"):
    return await repo.upsert_connection(
        owner_user_id=owner,
        provider="feishu",
        external_account_id=sender,
        external_account_name="测试用户",
        workspace_id=chat,
        metadata={"message_id": "om_original"},
    )


async def _remember(channel, connection_id, *, owner="owner", tenant="tenant_test", sender="ou_owner", chat="oc_private"):
    await notifications.remember_work_notification_target(
        channel,
        connection_id=connection_id,
        owner_user_id=owner,
        sender_id=sender,
        chat_id=chat,
        tenant_key=tenant,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_type", "app_id", "sender", "usable"), [("p2p", "cli_current", "ou_owner", True), ("group", "cli_current", "ou_owner", False), ("p2p", "cli_other", "ou_owner", False), ("p2p", "cli_current", "ou_stranger", False)]
)
async def test_only_bound_private_message_from_current_app_verifies_a_legacy_target(runtime, monkeypatch, chat_type, app_id, sender, usable):
    channel, repo, _, _ = runtime
    row = await _bind(repo)
    assert await notifications.resolve_work_notification_connection("owner") is None
    prepare = MagicMock()
    monkeypatch.setattr(channel, "_schedule_prepare_inbound", prepare)
    monkeypatch.setattr(channel, "_ensure_running_card_started", MagicMock())
    monkeypatch.setattr(channel, "_add_reaction", AsyncMock())
    event = SimpleNamespace(
        header=SimpleNamespace(app_id=app_id, tenant_key="tenant_test"),
        event=SimpleNamespace(
            message=SimpleNamespace(chat_id="oc_private", message_id="om_original", chat_type=chat_type, content=json.dumps({"text": "合成测试"})),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id=sender)),
        ),
    )
    channel._on_message(event)
    await channel._prepare_inbound(*prepare.call_args.args)
    target = await notifications.resolve_work_notification_connection("owner")
    assert (target is not None) is usable
    if target:
        assert target.connection_id == row["id"]
        assert target.label == "测试用户"
    assert (await repo.list_connections("owner"))[0]["metadata"] == {"message_id": "om_original"}


@pytest.mark.asyncio
async def test_sender_reuses_app_http_client_and_retains_identity_across_secret_rotation(runtime):
    channel, repo, config, create = runtime
    row = await _bind(repo)
    await _remember(channel, row["id"])
    original = await notifications.resolve_work_notification_connection("owner", row["id"])
    config["app_secret"] = channel.config["app_secret"] = "rotated"
    channel.config["_work_send_only"] = True
    assert not channel.transport_connected
    rotated = await notifications.resolve_work_notification_connection("owner", row["id"])
    assert rotated.binding_key == original.binding_key

    message_id = await notifications.send_work_notification("owner", row["id"], "任务完成", UUID, binding_key=original.binding_key)
    assert message_id == "om_receipt"
    request = create.call_args.args[0]
    assert request.receive_id_type == "chat_id"
    assert request.body.receive_id == "oc_private"
    assert request.body.msg_type == "text"
    assert request.body.uuid == UUID
    assert json.loads(request.body.content) == {"text": "任务完成"}


@pytest.mark.asyncio
async def test_wrong_owner_tenant_app_or_binding_key_cannot_send(runtime, monkeypatch):
    channel, repo, config, create = runtime
    row = await _bind(repo)
    await _remember(channel, row["id"])
    original = await notifications.resolve_work_notification_connection("owner", row["id"])
    for owner, binding_key in [("stranger", None), ("owner", "bad")]:
        with pytest.raises(notifications.WorkNotificationError, match="target_unavailable"):
            await notifications.send_work_notification(owner, row["id"], "任务完成", UUID, binding_key=binding_key)
    with monkeypatch.context() as patch:
        patch.setenv("DEER_FLOW_WORK_PRODUCTION_NOTIFICATION_TENANT_KEY", "tenant_other")
        assert await notifications.resolve_work_notification_connection("owner") is None
    config["app_id"] = "cli_new"
    assert await notifications.resolve_work_notification_connection("owner") is None  # stale Pod
    channel.config["app_id"] = config["app_id"]
    await _remember(channel, row["id"])
    assert await notifications.resolve_work_notification_connection("owner") is None  # old evidence
    rebound = await _bind(repo)
    assert rebound["id"] == row["id"]
    await _remember(channel, rebound["id"])
    with pytest.raises(notifications.WorkNotificationError, match="target_unavailable"):
        await notifications.send_work_notification("owner", rebound["id"], "任务完成", UUID, binding_key=original.binding_key)
    create.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_targets_require_an_explicit_connection_and_feature_gate(runtime, monkeypatch):
    channel, repo, _, create = runtime
    first = await _bind(repo)
    await _remember(channel, first["id"])
    second = await _bind(repo, sender="ou_second", chat="oc_second")
    await _remember(channel, second["id"], sender="ou_second", chat="oc_second")
    assert len(await notifications.list_work_notification_connections("owner")) == 2
    assert await notifications.resolve_work_notification_connection("owner") is None
    assert (await notifications.resolve_work_notification_connection("owner", second["id"])).connection_id == second["id"]
    monkeypatch.delenv("DEER_FLOW_WORK_SCHEDULED_NOTIFICATIONS_ENABLED")
    assert not notifications.work_notifications_enabled()
    assert not await notifications.list_work_notification_connections("owner")
    with pytest.raises(notifications.WorkNotificationError, match="sender_unavailable"):
        await notifications.send_work_notification("owner", second["id"], "任务完成", UUID)
    create.assert_not_called()


@pytest.mark.asyncio
async def test_revocation_between_target_reads_stops_send(runtime, monkeypatch):
    channel, repo, _, create = runtime
    row = await _bind(repo)
    await _remember(channel, row["id"])
    original_lookup = repo.list_work_notification_targets

    async def revoke_after_read(*args):
        before = await original_lookup(*args)
        await repo.disconnect_connection(connection_id=row["id"], owner_user_id="owner")
        return before

    monkeypatch.setattr(repo, "list_work_notification_targets", revoke_after_read)
    with pytest.raises(notifications.WorkNotificationError, match="target_unavailable"):
        await notifications.send_work_notification("owner", row["id"], "任务完成", UUID)
    create.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "status", "receipt", "uncertain"), [(99991401, 400, None, False), (None, 500, None, True), (0, 200, None, True)])
async def test_failed_or_unconfirmed_provider_outcome_is_never_retried(runtime, code, status, receipt, uncertain):
    channel, repo, _, create = runtime
    row = await _bind(repo)
    await _remember(channel, row["id"])
    create.return_value = SimpleNamespace(code=code, raw=SimpleNamespace(status_code=status), data=SimpleNamespace(message_id=receipt))
    with pytest.raises(notifications.WorkNotificationError) as error:
        await notifications.send_work_notification("owner", row["id"], "任务完成", UUID)
    assert error.value.uncertain is uncertain
    assert create.call_count == 1
