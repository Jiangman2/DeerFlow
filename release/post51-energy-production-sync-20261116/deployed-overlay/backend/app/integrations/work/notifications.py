"""Resolve and send scheduled notifications to verified Work private chats."""

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field

from .config import WorkSettings

logger = logging.getLogger(__name__)
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_OPEN_ID = re.compile(r"ou_[A-Za-z0-9_-]{1,125}")
_CHAT_ID = re.compile(r"oc_[A-Za-z0-9_-]{1,125}")
_MESSAGE_ID = re.compile(r"om_[A-Za-z0-9_-]{1,125}")
_SEND_UUID = re.compile(r"[A-Za-z0-9_-]{1,50}")
_MAX_MESSAGE_BYTES = 12_000


@dataclass(frozen=True)
class WorkNotificationTarget:
    connection_id: str
    label: str
    binding_key: str = field(repr=False)
    chat_id: str = field(repr=False)


class WorkNotificationError(Exception):
    """A fixed stage plus transport certainty, without provider payloads."""

    def __init__(self, stage: str, *, business_code: int | None = None, uncertain: bool = False):
        super().__init__(stage)
        self.stage = stage
        self.business_code = business_code
        self.uncertain = uncertain
        self.retryable = stage == "sender_unavailable"


def _work_settings() -> WorkSettings | None:
    if os.getenv("DEER_FLOW_WORK_SCHEDULED_NOTIFICATIONS_ENABLED") != "true":
        return None
    try:
        settings = WorkSettings.from_env()
    except ValueError:
        return None
    return settings if settings.enabled and settings.persistent else None


def work_notifications_enabled() -> bool:
    return _work_settings() is not None


async def _current_runtime(owner_user_id: str):
    from app.channels.service import get_channel_service

    work = _work_settings()
    if work is None or not owner_user_id or (work.allowed_owners is not None and owner_user_id not in work.allowed_owners):
        return None
    channels = get_channel_service()
    if channels is None:
        return None
    channel = channels.get_channel("feishu")
    repo = channels.manager._connection_repo
    if channel is None or not channel.is_running or repo is None:
        return None
    try:
        # Other Pods may briefly retain a stale client after an administrator
        # changes the shared Work config. The notification must fail closed.
        desired = await asyncio.to_thread(channels._read_channel_config, "feishu")
    except Exception:
        logger.warning("work_notification stage=config_unavailable")
        return None
    if not isinstance(desired, dict) or not desired.get("enabled") or channels.get_channel("feishu") is not channel or not channel.is_running:
        return None
    app_id, secret = desired.get("app_id"), desired.get("app_secret")
    if not (
        isinstance(app_id, str)
        and _IDENTIFIER.fullmatch(app_id)
        and isinstance(secret, str)
        and secret
        and desired.get("domain") == work.api_url
        and channel.config.get("domain") == work.api_url
        and channel.config.get("app_id") == app_id
        and channel.config.get("app_secret") == secret
    ):
        return None
    return work, channel, repo


def _route(work: WorkSettings, channel, tenant: object) -> dict[str, str] | None:
    expected_tenant = os.getenv(f"DEER_FLOW_WORK_{work.profile.upper()}_NOTIFICATION_TENANT_KEY", "")
    if not isinstance(tenant, str) or not _IDENTIFIER.fullmatch(tenant) or (expected_tenant and tenant != expected_tenant):
        return None
    return {"chat_type": "p2p", "profile": work.profile, "api_url": work.api_url, "app_id": channel.config["app_id"], "tenant_key": tenant}


async def remember_work_notification_target(channel, *, connection_id, owner_user_id, sender_id, chat_id, tenant_key) -> None:
    """Called only for an authenticated Work P2P message with a matching app."""
    if not (connection_id and owner_user_id and isinstance(sender_id, str) and _OPEN_ID.fullmatch(sender_id) and isinstance(chat_id, str) and _CHAT_ID.fullmatch(chat_id) and isinstance(tenant_key, str)):
        return
    try:
        async with asyncio.timeout(1):
            current = await _current_runtime(owner_user_id)
            if current is None:
                return
            work, live_channel, repo = current
            guard = channel.config.get("_work_receiver_guard")
            if channel is not live_channel or not channel.transport_connected or channel.config.get("_work_send_only") or not callable(guard):
                return
            target = _route(work, channel, tenant_key)
            if target is not None and await guard():
                await repo.remember_work_notification_target(connection_id=connection_id, owner_user_id=owner_user_id, external_account_id=sender_id, chat_id=chat_id, target=target)
    except Exception:
        # Notification opt-in must never prevent a normal chat response.
        logger.warning("work_notification stage=target_observation_unavailable")


async def _targets(runtime, owner_user_id: str, connection_id: str | None = None) -> list[WorkNotificationTarget]:
    work, channel, repo = runtime
    targets = []
    for row in await repo.list_work_notification_targets(owner_user_id, connection_id):
        observed = row["work_notification_target"]
        expected = _route(work, channel, observed.get("tenant_key"))
        recipient, chat_id = row.get("external_account_id"), row.get("workspace_id")
        if expected is None or observed != expected or not isinstance(recipient, str) or not _OPEN_ID.fullmatch(recipient) or not isinstance(chat_id, str) or not _CHAT_ID.fullmatch(chat_id):
            continue
        identity = json.dumps([owner_user_id, row["id"], recipient, chat_id, expected], sort_keys=True, separators=(",", ":")).encode()
        binding_key = hashlib.sha256(identity).hexdigest()
        name = row.get("external_account_name")
        name = name.strip() if isinstance(name, str) else ""
        label = name[:80] if name and name not in {recipient, chat_id} else "海能 Work 私聊"
        targets.append(WorkNotificationTarget(row["id"], label, binding_key, chat_id))
    return targets


async def list_work_notification_connections(owner_user_id: str) -> list[WorkNotificationTarget]:
    current = await _current_runtime(owner_user_id)
    return await _targets(current, owner_user_id) if current is not None else []


async def resolve_work_notification_connection(owner_user_id: str, connection_id: str | None = None) -> WorkNotificationTarget | None:
    current = await _current_runtime(owner_user_id)
    targets = await _targets(current, owner_user_id, connection_id) if current is not None else []
    return targets[0] if len(targets) == 1 else None


async def send_work_notification(owner_user_id: str, connection_id: str, text: str, uuid: str, *, binding_key: str | None = None, markdown: bool = False) -> str:
    """Perform one bot-identity send, returning only a verified provider ID."""
    if type(markdown) is not bool or not connection_id or not isinstance(uuid, str) or not _SEND_UUID.fullmatch(uuid) or not isinstance(text, str) or not text.strip() or len(text.encode()) > _MAX_MESSAGE_BYTES:
        raise WorkNotificationError("invalid_notification")
    current = await _current_runtime(owner_user_id)
    if current is None or current[1]._api_client is None:
        raise WorkNotificationError("sender_unavailable")
    channel = current[1]
    targets = await _targets(current, owner_user_id, connection_id)
    if len(targets) != 1 or (binding_key is not None and targets[0].binding_key != binding_key):
        raise WorkNotificationError("target_unavailable")
    # Verify the current app again after the DB read, before contacting Work.
    current = await _current_runtime(owner_user_id)
    if current is None or current[1] is not channel or channel._api_client is None:
        raise WorkNotificationError("sender_unavailable")
    checked = await _targets(current, owner_user_id, connection_id)
    if len(checked) != 1 or checked[0].binding_key != targets[0].binding_key:
        raise WorkNotificationError("target_unavailable")
    if not channel.is_running or channel._api_client is None:
        raise WorkNotificationError("sender_unavailable")
    # Only an explicit host caller opts into the existing Work Markdown card
    # format. Keep recipient, owner rechecks, UUID and send-once semantics.
    content = {"config": {"wide_screen_mode": True}, "elements": [{"tag": "markdown", "content": text}]} if markdown else {"text": text}
    request = (
        channel._CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(channel._CreateMessageRequestBody.builder().receive_id(targets[0].chat_id).msg_type("interactive" if markdown else "text").content(json.dumps(content, ensure_ascii=False)).uuid(uuid).build())
        .build()
    )
    try:
        response = await asyncio.to_thread(channel._api_client.im.v1.message.create, request)
    except Exception:
        # A request that timed out may have reached the remote service.
        raise WorkNotificationError("provider_outcome_unknown", uncertain=True) from None
    code = getattr(response, "code", None)
    status = getattr(getattr(response, "raw", None), "status_code", None)
    code = code if type(code) is int else None
    status = status if type(status) is int else None
    if code != 0 or (status is not None and not 200 <= status < 300):
        explicit_denial = status is not None and 400 <= status < 500 and status != 408
        business_denial = code not in {None, 0} and (status is None or 200 <= status < 300)
        if explicit_denial or business_denial:
            raise WorkNotificationError("provider_rejected", business_code=code)
        raise WorkNotificationError("provider_outcome_unknown", business_code=code, uncertain=True)
    message_id = getattr(getattr(response, "data", None), "message_id", None)
    if not isinstance(message_id, str) or not _MESSAGE_ID.fullmatch(message_id):
        raise WorkNotificationError("provider_outcome_unknown", uncertain=True)
    return message_id
