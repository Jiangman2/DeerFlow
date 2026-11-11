"""Work button callbacks use synthetic signed events and the real plan fence."""

import asyncio
import base64
import hashlib
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import test_work_production as production_fixtures
from Crypto.Cipher import AES
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_work_calendar_batch import SCOPES, Provider, action
from test_work_production import principal_message, production_service

from app.channels import service as channel_service
from app.channels.feishu import FeishuChannel
from app.channels.message_bus import MessageBus, OutboundMessage
from app.gateway import auth_middleware, csrf_middleware
from app.gateway.routers import work_cards
from app.integrations.work import service as work_service
from app.integrations.work.auth import Grant
from app.integrations.work.cards import (
    CARD_CALLBACK_PATH,
    CARD_PREVIEW_KEY,
    CARD_REPLY_TARGET_KEY,
    WORK_TENANT_METADATA_KEY,
    CardClick,
    CardPreview,
    CardReplyTarget,
    CardSettings,
    card_callback_response,
    decode_callback,
    dispatch_websocket_card_click,
)

SETTINGS = CardSettings("cli_production_fixture", "tenant-synthetic", "synthetic-card-verify-token", "synthetic-card-encrypt-key")
stores = production_fixtures.stores


@pytest.fixture
def card_env(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_PROFILE", "production")
    monkeypatch.delenv("DEER_FLOW_WORK_PRODUCTION_CARD_TRANSPORT", raising=False)
    for name, value in {
        "ENABLED": "true",
        "APP_ID": SETTINGS.app_id,
        "TENANT_KEY": SETTINGS.tenant_key,
        "VERIFICATION_TOKEN": SETTINGS.verification_token,
        "ENCRYPT_KEY": SETTINGS.encrypt_key,
    }.items():
        monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_CARD_" + name, value)


@pytest.fixture
def websocket_card_env(card_env, monkeypatch):
    prefix = "DEER_FLOW_WORK_PRODUCTION_CARD_"
    for name in ("APP_ID", "TENANT_KEY", "VERIFICATION_TOKEN", "ENCRYPT_KEY"):
        monkeypatch.delenv(prefix + name)
    monkeypatch.setenv(prefix + "TRANSPORT", "websocket")


def payload():
    return {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger", "event_id": "callback-1", "app_id": SETTINGS.app_id, "tenant_key": SETTINGS.tenant_key, "token": SETTINGS.verification_token},
        "event": {
            "operator": {"open_id": "ou-A", "tenant_key": SETTINGS.tenant_key},
            "context": {"open_chat_id": "chat-A", "open_message_id": "om_card"},
            "action": {"tag": "button", "value": {"work_action": "confirm", "work_reference": "a" * 32}},
        },
    }


def signed_body(data, *, timestamp=None):
    iv = b"0123456789abcdef"
    raw = json.dumps(data).encode()
    pad = 16 - len(raw) % 16
    cipher = AES.new(hashlib.sha256(SETTINGS.encrypt_key.encode()).digest(), AES.MODE_CBC, iv)
    encoded = base64.b64encode(iv + cipher.encrypt(raw + bytes([pad]) * pad)).decode()
    body = json.dumps({"encrypt": encoded}).encode()
    stamp, nonce = str(int(time.time()) if timestamp is None else timestamp), "synthetic-nonce"
    return body, {"x-lark-request-timestamp": stamp, "x-lark-request-nonce": nonce, "x-lark-signature": hashlib.sha256((stamp + nonce + SETTINGS.encrypt_key).encode() + body).hexdigest()}


@pytest.mark.parametrize("mutation", ["raw", "signature", "timestamp", "token", "app", "tenant", "operator_tenant", "no_chat", "command", "event_type"])
def test_card_event_rejects_untrusted_envelopes(mutation):
    data = payload()
    if mutation in ("token", "app", "tenant", "event_type"):
        data["header"][{"token": "token", "app": "app_id", "tenant": "tenant_key", "event_type": "event_type"}[mutation]] = "wrong"
    elif mutation == "operator_tenant":
        data["event"]["operator"]["tenant_key"] = "wrong"
    elif mutation == "no_chat":
        del data["event"]["context"]["open_chat_id"]
    elif mutation == "command":
        data["event"]["action"]["value"]["work_action"] = "/work 确认 arbitrary"
    body, headers = signed_body(data, timestamp=int(time.time()) - 301 if mutation == "timestamp" else None)
    if mutation == "raw":
        body = json.dumps(data).encode()
    elif mutation == "signature":
        body += b" "
    with pytest.raises(ValueError):
        decode_callback(body, headers, SETTINGS)


@pytest.mark.parametrize("choice_count", [1, 3, 5])
def test_search_card_renders_numbered_buttons_with_only_the_opaque_reference(choice_count):
    preview = CardPreview("a" * 32, "候选文件", AsyncMock(), choice_count=choice_count)
    (element,) = preview.elements()
    assert element["tag"] == "action"
    assert [button["text"]["content"] for button in element["actions"]] == [f"读取 {index}" for index in range(1, choice_count + 1)]
    assert [button["value"] for button in element["actions"]] == [{"work_action": f"select_{index}", "work_reference": preview.reference} for index in range(1, choice_count + 1)]


@pytest.mark.parametrize("choice_count", [-1, 6, True, "1"])
def test_card_rejects_invalid_choice_count(choice_count):
    with pytest.raises(ValueError, match="invalid_card_choice_count"):
        CardPreview("a" * 32, "候选文件", AsyncMock(), choice_count=choice_count)


@pytest.mark.parametrize("index", range(1, 6))
def test_signed_card_callback_preserves_numbered_selection(index):
    data = payload()
    data["event"]["action"]["value"]["work_action"] = f"select_{index}"
    body, headers = signed_body(data)
    assert decode_callback(body, headers, SETTINGS) == CardClick("callback-1", "ou-A", "chat-A", "om_card", "a" * 32, f"select_{index}", SETTINGS.tenant_key)


@pytest.mark.parametrize("verb", ["select_0", "select_6", "select_01", "select_１", "SELECT_1", 1, ["select_1"], None])
def test_signed_card_callback_rejects_noncanonical_selection(verb):
    data = payload()
    data["event"]["action"]["value"]["work_action"] = verb
    body, headers = signed_body(data)
    with pytest.raises(ValueError, match="invalid_card_action"):
        decode_callback(body, headers, SETTINGS)


def test_exact_callback_endpoint_accepts_only_its_own_machine_authority(card_env, monkeypatch):
    monkeypatch.setattr(auth_middleware, "is_auth_disabled", lambda: False)
    monkeypatch.setattr(csrf_middleware, "is_auth_disabled", lambda: False)
    service = AsyncMock()
    service.enqueue_card_click.return_value = "accepted"
    monkeypatch.setattr(work_cards, "_resolve_service", AsyncMock(return_value=service))
    app = FastAPI()
    app.add_middleware(auth_middleware.AuthMiddleware)
    app.add_middleware(csrf_middleware.CSRFMiddleware)
    app.include_router(work_cards.router)
    with TestClient(app) as client:
        body, headers = signed_body(payload())
        response = client.post(CARD_CALLBACK_PATH, content=body, headers=headers)
        assert response.status_code == 200 and "正在核对" in response.json()["toast"]["content"]
        assert service.enqueue_card_click.await_args.args[0] == CardClick("callback-1", "ou-A", "chat-A", "om_card", "a" * 32, "confirm", SETTINGS.tenant_key)
        assert client.post(CARD_CALLBACK_PATH + "/nearby", content=body, headers=headers).status_code in {401, 403}
        assert client.get(CARD_CALLBACK_PATH).status_code == 401
        assert client.post(CARD_CALLBACK_PATH, content=body).status_code == 400
        assert client.post(CARD_CALLBACK_PATH, content=b"x" * 16_385).status_code == 413
        challenge = {"type": "url_verification", "token": SETTINGS.verification_token, "challenge": "synthetic-challenge"}
        assert client.post(CARD_CALLBACK_PATH, json=challenge).json() == {"challenge": "synthetic-challenge"}
        encrypted, encryption_headers = signed_body(challenge)
        assert client.post(CARD_CALLBACK_PATH, content=encrypted, headers=encryption_headers).json() == {"challenge": "synthetic-challenge"}
        assert client.post(CARD_CALLBACK_PATH, content=encrypted).json() == {"challenge": "synthetic-challenge"}
        service.enqueue_card_click.assert_awaited_once()
        monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_CARD_TRANSPORT", "websocket")
        assert client.post(CARD_CALLBACK_PATH, content=body, headers=headers).status_code == 404
        monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_CARD_TRANSPORT", "http")
        assert client.post(CARD_CALLBACK_PATH, content=body, headers=headers).status_code == 200
        monkeypatch.delenv("DEER_FLOW_WORK_PRODUCTION_CARD_ENABLED")
        assert client.post(CARD_CALLBACK_PATH, content=body, headers=headers).status_code == 404


async def make_preview(service, *, tenant=None):
    msg = principal_message(text="新建个人日程", source="om_request")
    if tenant is not None:
        msg.metadata[WORK_TENANT_METADATA_KEY] = tenant
    p = service._identity(msg)
    owner = service.vault.principal_key(p)
    await service.vault.install(p, Grant("synthetic-A", time.monotonic() + 600, SCOPES))
    turn = await service.store.reserve_turn(owner, "turn-preview")
    ref = await service.store.finish_turn(owner, turn.turn_id, mode="new", task={"domain": "calendar"})
    plan = await service.tools.prepare_batch(p, [action()], task_ref=ref)
    preview = await service._card_preview(msg, p, plan)
    assert preview is not None
    return msg, owner, plan, preview


@pytest.mark.asyncio
async def test_cross_instance_confirmation_checks_actor_card_and_writes_once(card_env, stores):
    first_store, other_store, _ = stores
    provider = Provider()
    first = production_service(first_store, provider)
    second = production_service(other_store, provider)
    try:
        msg, owner, plan, preview = await make_preview(first)
        channel = FeishuChannel(MessageBus(), {})
        channel._reply_card = AsyncMock(return_value="om_card")
        await first._reply(msg, first._preview_reply(plan), card_preview=preview)
        outbound = first.send.await_args.args[0]
        assert outbound.metadata[CARD_PREVIEW_KEY].reference == preview.reference
        assert "/work 确认 " + plan.id in outbound.text
        await channel._send_card_message(outbound)
        channel._reply_card.assert_awaited_once()
        display = channel._reply_card.await_args.args[1]
        assert "【海能 Work】" in display and "/work" not in display and plan.id not in display
        rendered = json.loads(channel._build_card_content(display, preview))
        buttons = rendered["elements"][-1]["actions"]
        assert [button["text"]["content"] for button in buttons] == ["确认执行", "取消本次修改"]
        assert {button["value"]["work_reference"] for button in buttons} == {preview.reference}
        assert plan.id not in json.dumps(buttons)
        click = CardClick("event-1", "ou-A", "chat-A", "om_card", preview.reference, "confirm", SETTINGS.tenant_key)
        for changed in (replace(click, sender="ou-B"), replace(click, chat_id="chat-B"), replace(click, message_id="om_other")):
            assert await second.enqueue_card_click(changed, SETTINGS) == "invalid"
        assert not provider.writes
        # Two distinct request handlers may receive the same valid delivery.
        results = await asyncio.gather(first.enqueue_card_click(click, SETTINGS), second.enqueue_card_click(click, SETTINGS))
        assert "accepted" in results
        await asyncio.gather(first.drain(), second.drain())
        assert len(provider.writes) == 1
        assert (await other_store.read("plan", plan.id, owner=owner)).status == "completed"
        assert await second.enqueue_card_click(replace(click, event_id="event-2"), SETTINGS) == "invalid"
        final = [call.args[0] for service in (first, second) for call in service.send.await_args_list if call.args[0].metadata.get(CARD_REPLY_TARGET_KEY) and call.args[0].is_final]
        assert len(final) == 1 and final[0].metadata[CARD_REPLY_TARGET_KEY].message_id == "om_card"
    finally:
        await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_cancel_button_cancels_only_the_pending_preview(card_env, stores):
    one, two, _ = stores
    provider = Provider()
    first = production_service(one, provider)
    second = production_service(two, provider)
    try:
        _, owner, plan, preview = await make_preview(first)
        await preview.bind("om_card")
        click = CardClick("event-cancel", "ou-A", "chat-A", "om_card", preview.reference, "cancel", SETTINGS.tenant_key)
        assert await second.enqueue_card_click(click, SETTINGS) == "accepted"
        await second.drain()
        assert (await one.read("plan", plan.id, owner=owner)).status == "cancelled"
        assert not provider.writes
        assert await second.enqueue_card_click(replace(click, verb="confirm", event_id="event-after-cancel"), SETTINGS) == "invalid"
    finally:
        await asyncio.gather(first.close(), second.close())


def websocket_receiver(monkeypatch, work):
    channel = SimpleNamespace(transport_connected=True, config={"app_id": SETTINGS.app_id, "_work_receiver_guard": AsyncMock(return_value=True)})
    registry = SimpleNamespace(get_channel=lambda name: channel if name == "feishu" else None, manager=object())
    monkeypatch.setattr(channel_service, "get_channel_service", lambda: registry)
    monkeypatch.setattr(work_service, "active_work_service", AsyncMock(return_value=work))
    return channel


@pytest.mark.asyncio
async def test_websocket_card_requires_current_receiver_lease_and_provider_identity(websocket_card_env, monkeypatch):
    service = SimpleNamespace(app_id=SETTINGS.app_id, enqueue_card_click=AsyncMock(return_value="accepted"))
    channel = websocket_receiver(monkeypatch, service)
    data = payload()
    data["header"].pop("token")
    assert CardSettings.from_env("production") is None
    settings = CardSettings.from_env("production", app_id=SETTINGS.app_id)
    assert settings == CardSettings(SETTINGS.app_id, "", "", "", transport="websocket")
    assert await dispatch_websocket_card_click(data, channel=channel) == card_callback_response("accepted")
    assert service.enqueue_card_click.await_args.args == (CardClick("callback-1", "ou-A", "chat-A", "om_card", "a" * 32, "confirm", SETTINGS.tenant_key), settings)
    service.enqueue_card_click.reset_mock()

    for field, value in (("app_id", "cli_other"), ("tenant_key", None), ("event_type", "im.message.receive_v1")):
        forged = {**data, "header": {**data["header"], field: value}}
        assert await dispatch_websocket_card_click(forged, channel=channel) == card_callback_response("invalid")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_CARD_TENANT_KEY", "other-tenant")
    assert await dispatch_websocket_card_click(data, channel=channel) == card_callback_response("invalid")
    monkeypatch.delenv("DEER_FLOW_WORK_PRODUCTION_CARD_TENANT_KEY")
    assert await dispatch_websocket_card_click(data, channel=object()) == card_callback_response("unavailable")
    channel.config["_work_receiver_guard"].return_value = False
    assert await dispatch_websocket_card_click(data, channel=channel) == card_callback_response("unavailable")
    channel.config["_work_receiver_guard"].return_value = True
    channel.config["_work_send_only"] = True
    assert await dispatch_websocket_card_click(data, channel=channel) == card_callback_response("unavailable")
    channel.config["_work_send_only"] = False
    channel.transport_connected = False
    assert await dispatch_websocket_card_click(data, channel=channel) == card_callback_response("unavailable")
    service.enqueue_card_click.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_preview_binds_original_tenant_and_survives_across_services(websocket_card_env, monkeypatch, stores):
    one, two, _ = stores
    provider = Provider()
    first = production_service(one, provider)
    second = production_service(two, provider)
    try:
        msg, owner, plan, preview = await make_preview(first, tenant=SETTINGS.tenant_key)
        p = first._identity(msg)
        missing = replace(msg, metadata={"chat_type": "p2p"})
        assert await first._card_preview(missing, p, plan) is None
        group = replace(msg, metadata={**msg.metadata, "chat_type": "group"})
        assert await first._card_preview(group, p, plan) is None
        monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_CARD_TENANT_KEY", "other-tenant")
        assert await first._card_preview(msg, p, plan) is None
        monkeypatch.delenv("DEER_FLOW_WORK_PRODUCTION_CARD_TENANT_KEY")
        assert await preview.bind("om_card")
        saved = first._data(work_service.DocumentJob(msg, "", "", time.monotonic() + 600))
        assert saved["message"]["metadata"][WORK_TENANT_METADATA_KEY] == SETTINGS.tenant_key

        click = CardClick("cross-transport", "ou-A", "chat-A", "om_card", preview.reference, "confirm", SETTINGS.tenant_key)
        assert await second.enqueue_card_click(click, SETTINGS) == "invalid"
        channel = websocket_receiver(monkeypatch, second)
        data = payload()
        data["header"].pop("token")
        data["event"]["action"]["value"]["work_reference"] = preview.reference
        wrong_tenant = {**data, "header": {**data["header"], "tenant_key": "other-tenant"}, "event": {**data["event"], "operator": {"open_id": "ou-A", "tenant_key": "other-tenant"}}}
        assert await dispatch_websocket_card_click(wrong_tenant, channel=channel) == card_callback_response("invalid")
        assert not provider.writes
        guard = channel.config["_work_receiver_guard"]
        guard.side_effect = [True, False]
        assert await dispatch_websocket_card_click(data, channel=channel) == card_callback_response("unavailable")
        waiting = await one.read("card_event", "callback-1", owner=owner)
        assert waiting is not None and waiting.status == "waiting" and not second._card_tasks
        assert not provider.writes
        guard.side_effect = None
        assert await dispatch_websocket_card_click(data, channel=channel) == card_callback_response("accepted")
        await second.drain()
        assert len(provider.writes) == 1
        assert (await one.read("plan", plan.id, owner=owner)).status == "completed"
        assert await dispatch_websocket_card_click(data, channel=channel) == card_callback_response("invalid")
    finally:
        await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_button_delivery_falls_back_to_text_and_receipt_targets_original_card():
    channel = FeishuChannel(MessageBus(), {})
    bind = AsyncMock(return_value=False)
    preview = CardPreview("b" * 32, "短预览", bind)
    channel._reply_card = AsyncMock(side_effect=[RuntimeError("synthetic button rejection"), "om_text"])
    assert await channel._deliver_work_preview("om_request", "文字确认指令", preview) == "om_text"
    bind.assert_not_awaited()
    channel._update_card = AsyncMock()
    channel._reply_card = AsyncMock(return_value="om_button")
    assert await channel._deliver_work_preview("om_request", "文字确认指令", preview) == "om_button"
    bind.assert_awaited_once_with("om_button")
    channel._update_card.assert_awaited_once_with("om_button", "文字确认指令")
    channel._reply_card = AsyncMock(side_effect=[None, "om_text"])
    assert await channel._deliver_work_preview("om_request", "文字确认指令", preview) == "om_text"
    assert channel._reply_card.await_args.args == ("om_request", "文字确认指令")
    channel._update_card.reset_mock()
    out = OutboundMessage("feishu", "chat-A", "", "已执行", thread_ts="om_request", metadata={CARD_REPLY_TARGET_KEY: CardReplyTarget("om_card")}, is_final=False)
    await channel._send_card_message(out)
    channel._update_card.assert_not_awaited()
    await channel._send_card_message(replace(out, is_final=True))
    channel._update_card.assert_awaited_once_with("om_card", "已执行")
