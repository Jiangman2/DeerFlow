"""Authenticated Work card actions and server-owned rendering metadata."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

CARD_CALLBACK_PATH = "/api/integrations/work/card/callback"
CARD_PREVIEW_KEY = "work_card_preview"
CARD_REPLY_TARGET_KEY = "work_card_reply_target"
CARD_PREVIEW_RECORD = "card_preview"
CARD_EVENT_RECORD = "card_event"
WORK_TENANT_METADATA_KEY = "work_tenant_key"
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_REFERENCE = re.compile(r"[A-Za-z0-9_-]{32}\Z")
_SIGNATURE = re.compile(r"[a-f0-9]{64}\Z")
_SELECT_VERBS = ("select_1", "select_2", "select_3", "select_4", "select_5")
_CARD_VERBS = ("confirm", "cancel", *_SELECT_VERBS)
logger = logging.getLogger(__name__)
_TOASTS = {
    "accepted": "已收到，正在核对。若卡片没有更新，可再次点击。",
    "duplicate": "已收到这次操作，请查看卡片结果。",
    "busy": "暂时无法受理，请稍后再次点击。",
    "invalid": "卡片已处理、过期或与你的当前会话不匹配。",
    "unavailable": "暂时无法受理，请稍后再次点击。",
}


@dataclass(frozen=True)
class CardSettings:
    app_id: str
    tenant_key: str
    verification_token: str = field(repr=False)
    encrypt_key: str = field(repr=False)
    transport: str = "http"

    @classmethod
    def from_env(cls, profile: str, *, app_id: str = ""):
        prefix = f"DEER_FLOW_WORK_{profile.upper()}_CARD_"
        if os.getenv(prefix + "ENABLED") != "true":
            return None
        transport = os.getenv(prefix + "TRANSPORT", "http")
        if transport == "websocket":
            tenant = os.getenv(prefix + "TENANT_KEY", "")
            if not _ID.fullmatch(app_id) or tenant and not _ID.fullmatch(tenant):
                return None
            return cls(app_id, tenant, "", "", transport="websocket")
        if transport != "http":
            return None
        app, tenant, token, key = (os.getenv(prefix + name, "") for name in ("APP_ID", "TENANT_KEY", "VERIFICATION_TOKEN", "ENCRYPT_KEY"))
        if not _ID.fullmatch(app) or not _ID.fullmatch(tenant) or not (16 <= len(token) <= 256 and 16 <= len(key) <= 256):
            return None
        return cls(app, tenant, token, key)

    def preview_tenant(self, metadata: Mapping[str, object]) -> str | None:
        if self.transport == "http":
            return self.tenant_key
        tenant = metadata.get(WORK_TENANT_METADATA_KEY)
        return tenant if isinstance(tenant, str) and _ID.fullmatch(tenant) and (not self.tenant_key or tenant == self.tenant_key) else None


@dataclass(frozen=True)
class CardPreview:
    reference: str = field(repr=False)
    text: str = field(repr=False)
    bind: Callable[[str], Awaitable[bool]] = field(repr=False)
    choice_count: int = 0

    def __post_init__(self) -> None:
        if type(self.choice_count) is not int or not 0 <= self.choice_count <= len(_SELECT_VERBS):
            raise ValueError("invalid_card_choice_count")

    def elements(self) -> list[dict]:
        buttons = (("confirm", "确认执行", "primary"), ("cancel", "取消本次修改", "default"))
        if self.choice_count:
            buttons = tuple((verb, f"读取 {index}", "default") for index, verb in enumerate(_SELECT_VERBS[: self.choice_count], 1))
        return [
            {
                "tag": "action",
                "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": style, "value": {"work_action": verb, "work_reference": self.reference}} for verb, label, style in buttons],
            }
        ]


@dataclass(frozen=True)
class CardReplyTarget:
    message_id: str


@dataclass(frozen=True)
class CardClick:
    event_id: str
    sender: str
    chat_id: str
    message_id: str
    reference: str = field(repr=False)
    verb: str
    tenant_key: str


def _signature_valid(raw: bytes, headers: Mapping[str, str], key: str, now: float) -> bool:
    timestamp = headers.get("x-lark-request-timestamp", "")
    nonce = headers.get("x-lark-request-nonce", "")
    supplied = headers.get("x-lark-signature", "")
    if not re.fullmatch(r"[0-9]{10}", timestamp) or not _ID.fullmatch(nonce) or not _SIGNATURE.fullmatch(supplied):
        return False
    if abs(now - int(timestamp)) > 300:
        return False
    expected = hashlib.sha256((timestamp + nonce + key).encode() + raw).hexdigest()
    return hmac.compare_digest(expected, supplied)


def decode_callback(raw: bytes, headers: Mapping[str, str], settings: CardSettings, *, now: float | None = None) -> CardClick | str:
    """Return an authenticated click or the URL-verification challenge."""
    if settings.transport != "http":
        raise ValueError("invalid_card_transport")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("invalid_card_callback")
    encrypted = payload.get("encrypt")
    signed = False
    if encrypted is not None:
        if set(payload) != {"encrypt"} or not isinstance(encrypted, str):
            raise ValueError("invalid_card_callback")
        signed = _signature_valid(raw, headers, settings.encrypt_key, time.time() if now is None else now)
        if headers.get("x-lark-signature") and not signed:
            raise ValueError("invalid_card_signature")
        from lark_oapi.core.utils import AESCipher

        payload = json.loads(AESCipher(settings.encrypt_key).decrypt_str(encrypted))
    if not isinstance(payload, dict):
        raise ValueError("invalid_card_callback")
    if payload.get("type") == "url_verification":
        challenge, token = payload.get("challenge"), payload.get("token")
        if not isinstance(challenge, str) or not (1 <= len(challenge) <= 256) or not _matches(token, settings.verification_token):
            raise ValueError("invalid_card_challenge")
        return challenge
    # URL verification may be encrypted without signature headers. Only its
    # token-verified challenge can pass without a signed event envelope.
    if not signed:
        raise ValueError("invalid_card_callback")
    return _decode_click(payload, settings)


def _decode_click(payload: object, settings: CardSettings) -> CardClick:
    if not isinstance(payload, dict) or payload.get("schema") != "2.0":
        raise ValueError("invalid_card_callback")
    header, event = payload.get("header"), payload.get("event")
    if not isinstance(header, dict) or not isinstance(event, dict):
        raise ValueError("invalid_card_callback")
    tenant = header.get("tenant_key")
    if header.get("event_type") != "card.action.trigger" or header.get("app_id") != settings.app_id or not isinstance(tenant, str) or not _ID.fullmatch(tenant) or settings.tenant_key and tenant != settings.tenant_key:
        raise ValueError("invalid_card_authority")
    if settings.transport == "http" and not _matches(header.get("token"), settings.verification_token):
        raise ValueError("invalid_card_authority")
    operator, context, action = (event.get(key) for key in ("operator", "context", "action"))
    if not all(isinstance(part, dict) for part in (operator, context, action)):
        raise ValueError("invalid_card_action")
    if operator.get("tenant_key") not in (None, tenant) or action.get("tag") != "button":
        raise ValueError("invalid_card_action")
    value = action.get("value")
    if not isinstance(value, dict) or set(value) != {"work_action", "work_reference"}:
        raise ValueError("invalid_card_action")
    verb, reference = value["work_action"], value["work_reference"]
    identifiers = (header.get("event_id"), operator.get("open_id"), context.get("open_chat_id"), context.get("open_message_id"))
    if not all(isinstance(x, str) and _ID.fullmatch(x) for x in identifiers) or not isinstance(verb, str) or verb not in _CARD_VERBS or not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
        raise ValueError("invalid_card_action")
    return CardClick(*identifiers, reference, verb, tenant)


def _matches(value: object, expected: str) -> bool:
    return isinstance(value, str) and hmac.compare_digest(value.encode(), expected.encode())


def card_callback_response(status: str) -> dict:
    return {"toast": {"type": "info", "content": _TOASTS.get(status, _TOASTS["unavailable"])}}


def _websocket_response(status: str, *, stage: str = "admission") -> dict:
    status = status if status in _TOASTS else "unavailable"
    logger.info("work_card transport=websocket stage=%s result=%s", stage, status)
    return card_callback_response(status)


async def dispatch_websocket_card_click(payload: object, *, channel) -> dict:
    """Admit a callback only from the current authenticated Work receiver."""
    from app.channels.service import get_channel_service

    from .config import WorkSettings
    from .service import active_work_service

    try:
        async with asyncio.timeout(1.8):
            work = WorkSettings.from_env()
            channels = get_channel_service()
            if not work.enabled or not work.persistent or not channels or channels.get_channel("feishu") is not channel or not channel.transport_connected:
                return _websocket_response("unavailable", stage="receiver")
            settings = CardSettings.from_env(work.profile, app_id=channel.config.get("app_id", ""))
            guard = channel.config.get("_work_receiver_guard")
            if not settings or settings.transport != "websocket" or channel.config.get("_work_send_only") or not callable(guard):
                return _websocket_response("unavailable", stage="settings")
            try:
                click = _decode_click(payload, settings)
            except ValueError:
                return _websocket_response("invalid", stage="validation")

            def current_receiver():
                return (
                    get_channel_service() is channels
                    and channels.get_channel("feishu") is channel
                    and channel.transport_connected
                    and not channel.config.get("_work_send_only")
                    and channel.config.get("app_id") == settings.app_id
                    and channel.config.get("_work_receiver_guard") is guard
                )

            async def authority():
                return current_receiver() and await guard() and current_receiver()

            service = await active_work_service(channels.manager)
            if service is None or service.app_id != settings.app_id or not await authority():
                return _websocket_response("unavailable", stage="authority")
            return _websocket_response(await service.enqueue_card_click(click, settings, authority=authority))
    except TimeoutError:
        return _websocket_response("busy", stage="deadline")
    except Exception:
        logger.warning("work_card stage=websocket_admission_failed")
        return card_callback_response("unavailable")
