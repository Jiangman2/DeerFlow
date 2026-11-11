"""ChannelManager — consumes inbound messages and dispatches them to the DeerFlow agent via Gateway."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from langgraph_sdk.errors import ConflictError

from app.channels import feishu_run_policy as _feishu_run_policy  # noqa: F401
from app.channels.commands import KNOWN_CHANNEL_COMMANDS
from app.channels.identity_resolver import resolve_im_identity
from app.channels.message_bus import (
    PENDING_CLARIFICATION_METADATA_KEY,
    InboundMessage,
    InboundMessageType,
    MessageBus,
    OutboundMessage,
    ResolvedAttachment,
)
from app.channels.routing_v2 import (
    ReplyRef,
    RouteAction,
    RouteDecision,
    RouteReason,
    apply_quote,
    failure_message,
    resolve_route,
)
from app.channels.run_policy import CHANNEL_RUN_POLICY, ChannelRunPolicy
from app.channels.store import ChannelStore
from app.gateway.csrf_middleware import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, generate_csrf_token

# Import built-in channel run-policy registrars eagerly so direct
# ChannelManager construction sees the same policy map as gateway bootstrap.
from app.gateway.github import run_policy as _github_run_policy  # noqa: F401
from app.gateway.internal_auth import create_internal_auth_headers
from deerflow.config.agents_config import load_agent_config
from deerflow.config.paths import make_safe_user_id
from deerflow.constants import ENTERPRISE_PRODUCT_NAME
from deerflow.runtime.goal import parse_goal_command
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.skills.slash import parse_slash_skill_reference
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.skills.storage.skill_storage import SkillStorage
from deerflow.utils.messages import ORIGINAL_USER_CONTENT_KEY

logger = logging.getLogger(__name__)

DEFAULT_LANGGRAPH_URL = "http://localhost:8001/api"
DEFAULT_GATEWAY_URL = "http://localhost:8001"
DEFAULT_ASSISTANT_ID = "lead_agent"
CUSTOM_AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")

# Lead-agent recursion budget (LangGraph super-steps for the lead graph only).
# This is independent of subagent depth: a `task()` dispatch runs the whole
# subagent inside ONE lead tools-node step, and subagents enforce their own
# limit via `subagents.max_turns` (see SubagentExecutor). Do not conflate this
# 100 with the general-purpose subagent's max_turns.
DEFAULT_RUN_CONFIG: dict[str, Any] = {"recursion_limit": 100}
DEFAULT_RUN_CONTEXT: dict[str, Any] = {
    "thinking_enabled": True,
    "is_plan_mode": False,
    "subagent_enabled": False,
}
STREAM_UPDATE_MIN_INTERVAL_SECONDS = 1.0
STREAM_UPDATE_MIN_CHARS = 60  # flush immediately when this many chars accumulate
# Stream modes requested from the runtime, and the SSE event names under which
# the message-tuple stream may arrive: the embedded runtime (and LangGraph
# Platform) deliver the requested "messages-tuple" mode as event "messages".
STREAM_MODES = ["messages-tuple", "values"]
MESSAGE_STREAM_EVENTS = ("messages-tuple", "messages")
THREAD_BUSY_MESSAGE = "This conversation is already processing another request. Please wait for it to finish and try again."
BOUND_IDENTITY_REQUIRED_MESSAGE = f"请先在{ENTERPRISE_PRODUCT_NAME}的「设置 → 渠道」中连接此渠道，完成渠道内绑定后再发送消息。"
BOUND_IDENTITY_UNAVAILABLE_MESSAGE = f"渠道连接暂时无法验证，请稍后重试或联系{ENTERPRISE_PRODUCT_NAME}管理员。"
# S08: with the archive enabled an inbound sender must resolve to exactly one
# DeerFlow owner before any thread (or /new) is created. Saying so explicitly
# beats creating a thread nobody owns and hiding it from every sidebar.
IDENTITY_UNBOUND_MESSAGE = f"无法识别您的会话身份：请先在{ENTERPRISE_PRODUCT_NAME}的「设置 → 渠道」中完成飞书绑定，再重新发送消息。"
# S08: /new must never leave the user believing the pointer moved when every
# compare-and-swap lost. Report the failure instead of pretending success.
NEW_THREAD_SWITCH_FAILED_MESSAGE = "无法开始新会话：当前会话正忙，请稍后重试。"
# Registry touches are rate limited per thread: last_active_at is a coarse
# sidebar-recency signal, so writing it on every inbound message would create
# pointless row churn on an active conversation.
THREAD_TOUCH_MIN_INTERVAL_SECONDS = 60
INBOUND_DEDUPE_TTL_SECONDS = 10 * 60
INBOUND_DEDUPE_MAX_ENTRIES = 4096
# Only server-stable provider message ids: client-generated ids (client_msg_id,
# client_id) are not guaranteed identical across a provider's own redelivery, so
# keying dedupe on them would miss exactly the retries we want to absorb.
INBOUND_DEDUPE_METADATA_KEYS = ("event_id", "message_id", "msg_id")

# Channels whose adapter reports structured reply provenance (reply_message_id,
# reply_sender_type, reply_from_self_bot) and may therefore use the durable V2
# routing kernel: per-owner session pointers, message links, restore-on-reply.
# Other adapters keep the legacy topic-derived thread lookup until they can
# supply the same provenance, because a partial signal would turn every reply
# into a routing failure.
ROUTING_V2_CHANNELS = frozenset({"feishu"})

CHANNEL_CAPABILITIES = {
    "dingtalk": {"supports_streaming": False},
    "discord": {"supports_streaming": False},
    "feishu": {"supports_streaming": True},
    "github": {"supports_streaming": False},
    "slack": {"supports_streaming": False},
    "telegram": {"supports_streaming": True},
    "wechat": {"supports_streaming": False},
    "wecom": {"supports_streaming": True},
}

InboundFileReader = Callable[[dict[str, Any], httpx.AsyncClient], Awaitable[bytes | None]]

_METADATA_DROP_KEYS = frozenset({"raw_message", "ref_msg"})


def _slim_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of *meta* with known-large keys removed."""
    return {k: v for k, v in meta.items() if k not in _METADATA_DROP_KEYS}


INBOUND_FILE_READERS: dict[str, InboundFileReader] = {}


def register_inbound_file_reader(channel_name: str, reader: InboundFileReader) -> None:
    INBOUND_FILE_READERS[channel_name] = reader


async def _read_http_inbound_file(file_info: dict[str, Any], client: httpx.AsyncClient) -> bytes | None:
    url = file_info.get("url")
    if not isinstance(url, str) or not url:
        return None

    resp = await client.get(url)
    resp.raise_for_status()
    return resp.content


async def _read_wecom_inbound_file(file_info: dict[str, Any], client: httpx.AsyncClient) -> bytes | None:
    data = await _read_http_inbound_file(file_info, client)
    if data is None:
        return None

    aeskey = file_info.get("aeskey") if isinstance(file_info.get("aeskey"), str) else None
    if not aeskey:
        return data

    try:
        from aibot.crypto_utils import decrypt_file
    except Exception:
        logger.exception("[Manager] failed to import WeCom decrypt_file")
        return None

    return decrypt_file(data, aeskey)


async def _read_wechat_inbound_file(file_info: dict[str, Any], client: httpx.AsyncClient) -> bytes | None:
    raw_path = file_info.get("path")
    if isinstance(raw_path, str) and raw_path.strip():
        try:
            return await asyncio.to_thread(Path(raw_path).read_bytes)
        except OSError:
            logger.exception("[Manager] failed to read WeChat inbound file from local path: %s", raw_path)
            return None

    full_url = file_info.get("full_url")
    if isinstance(full_url, str) and full_url.strip():
        return await _read_http_inbound_file({"url": full_url}, client)

    return None


register_inbound_file_reader("wecom", _read_wecom_inbound_file)
register_inbound_file_reader("wechat", _read_wechat_inbound_file)


class InvalidChannelSessionConfigError(ValueError):
    """Raised when IM channel session overrides contain invalid agent config."""


class SlashSkillCommandResolutionError(RuntimeError):
    """Raised when IM slash-skill command resolution cannot complete safely."""


@dataclass(frozen=True, slots=True)
class _SlashSkillCommandResolution:
    route_to_chat: bool = False
    failure_message: str | None = None


@dataclass(frozen=True, slots=True)
class _BoundIdentityRejection:
    message: str = BOUND_IDENTITY_REQUIRED_MESSAGE
    # Server-side connection id that may be used only as an outbound routing
    # hint for the rejection message. This is never copied from the inbound
    # message; it comes from the repository re-read when available.
    outbound_connection_id: str | None = None
    # Server-side owner for the outbound routing connection above. It lets
    # channel senders preserve per-connection context without trusting the
    # rejected inbound identity assertion.
    outbound_owner_user_id: str | None = None


@dataclass(slots=True)
class _SerializedThreadRunState:
    """Per-thread lock state for channels that queue same-thread turns."""

    lock: asyncio.Lock
    waiters: int = 0


def _is_thread_busy_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    if isinstance(exc, ConflictError):
        return True
    return "already running a task" in str(exc)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _merge_dicts(*layers: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for layer in layers:
        if isinstance(layer, Mapping):
            merged.update(layer)
    return merged


def _normalize_custom_agent_name(raw_value: str) -> str:
    """Normalize legacy channel assistant IDs into valid custom agent names."""
    normalized = raw_value.strip().lower().replace("_", "-")
    if not normalized:
        raise InvalidChannelSessionConfigError("Channel session assistant_id is empty. Use 'lead_agent' or a valid custom agent name.")
    if not CUSTOM_AGENT_NAME_PATTERN.fullmatch(normalized):
        raise InvalidChannelSessionConfigError(f"Invalid channel session assistant_id {raw_value!r}. Use 'lead_agent' or a custom agent name containing only letters, digits, and hyphens.")
    return normalized


def _extract_response_text(result: dict | list) -> str:
    """Extract the last AI message text from a LangGraph runs.wait result.

    ``runs.wait`` returns the final state dict which contains a ``messages``
    list.  Each message is a dict with at least ``type`` and ``content``.

    Handles special cases:
    - Regular AI text responses
    - Clarification interrupts (``ask_clarification`` tool messages)
    """
    if isinstance(result, list):
        messages = result
    elif isinstance(result, dict):
        messages = result.get("messages", [])
    else:
        return ""

    # Walk backwards to find usable response text, but stop at the last
    # human message to avoid returning text from a previous turn.
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type")

        # Stop at the last human message — anything before it is a previous turn
        if msg_type == "human":
            if _is_hidden_human_control_message(msg):
                continue
            break

        # Check for tool messages from ask_clarification (interrupt case)
        if msg_type == "tool" and msg.get("name") == "ask_clarification":
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                return content

        # Regular AI message with text content
        if msg_type == "ai":
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                return content
            # content can be a list of content blocks
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        parts.append(block)
                text = "".join(parts)
                if text:
                    return text
    return ""


def _messages_from_result(result: dict | list) -> list[Any]:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        messages = result.get("messages", [])
        if isinstance(messages, list):
            return messages
    return []


def _current_turn_messages(result: dict | list) -> list[dict[str, Any]]:
    messages = _messages_from_result(result)
    current_turn: list[dict[str, Any]] = []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "human":
            break
        current_turn.append(msg)
    current_turn.reverse()
    return current_turn


def _has_current_turn_clarification(result: dict | list) -> bool:
    """Return True only when the current turn's final result is clarification."""
    for msg in reversed(_current_turn_messages(result)):
        msg_type = msg.get("type")
        if msg_type == "tool":
            return msg.get("name") == "ask_clarification"
        if msg_type == "ai":
            content = msg.get("content")
            if isinstance(content, str):
                if content:
                    return False
            elif content:
                return False
            if msg.get("tool_calls"):
                return False
    return False


def _response_metadata(base_metadata: dict[str, Any], *, pending_clarification: bool = False) -> dict[str, Any]:
    metadata = _slim_metadata(base_metadata)
    if pending_clarification:
        metadata[PENDING_CLARIFICATION_METADATA_KEY] = True
    return metadata


def _thread_channel_metadata(msg: InboundMessage) -> dict[str, Any]:
    channel_source: dict[str, Any] = {
        "type": "im_channel",
        "provider": msg.channel_name,
        "chat_id": msg.chat_id,
    }
    if msg.topic_id:
        channel_source["topic_id"] = msg.topic_id
    if msg.thread_ts:
        channel_source["thread_ts"] = msg.thread_ts
    if msg.connection_id:
        channel_source["connection_id"] = msg.connection_id

    return {"channel_source": channel_source}


def _extract_text_content(content: Any) -> str:
    """Extract text from a streaming payload content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                else:
                    nested = block.get("content")
                    if isinstance(nested, str):
                        parts.append(nested)
        return "".join(parts)
    if isinstance(content, Mapping):
        for key in ("text", "content"):
            value = content.get(key)
            if isinstance(value, str):
                return value
    return ""


def _merge_stream_text(existing: str, chunk: str) -> str:
    """Merge either delta text or cumulative text into a single snapshot."""
    if not chunk:
        return existing
    if not existing:
        return chunk
    # Cumulative re-delivery: strictly longer and starts with existing.
    if len(chunk) > len(existing) and chunk.startswith(existing):
        return chunk
    # Everything else is a delta — always append, even when the delta
    # happens to match the buffer suffix (e.g. 'hel' + 'l') or equals
    # the buffer (CJK reduplication: '谢' + '谢' = '谢谢'). Channels feed
    # only delta ('messages-tuple') events to this function; 'values'
    # snapshots are consumed via a separate branch, so a same-content
    # delta (chunk == existing) still represents a fresh token to keep.
    return existing + chunk


def _extract_stream_message_id(payload: Any, metadata: Any) -> str | None:
    """Best-effort extraction of the streamed AI message identifier."""
    candidates = [payload, metadata]
    if isinstance(payload, Mapping):
        candidates.append(payload.get("kwargs"))

    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        for key in ("id", "message_id"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _accumulate_stream_text(
    buffers: dict[str, str],
    current_message_id: str | None,
    event_data: Any,
) -> tuple[str | None, str | None]:
    """Convert a ``messages-tuple`` event into the latest displayable AI text."""
    payload = event_data
    metadata: Any = None
    if isinstance(event_data, (list, tuple)):
        if event_data:
            payload = event_data[0]
        if len(event_data) > 1:
            metadata = event_data[1]

    if isinstance(payload, str):
        message_id = current_message_id or "__default__"
        buffers[message_id] = _merge_stream_text(buffers.get(message_id, ""), payload)
        return buffers[message_id], message_id

    if not isinstance(payload, Mapping):
        return None, current_message_id

    payload_type = str(payload.get("type", "")).lower()
    if "tool" in payload_type:
        return None, current_message_id

    text = _extract_text_content(payload.get("content"))
    if not text and isinstance(payload.get("kwargs"), Mapping):
        text = _extract_text_content(payload["kwargs"].get("content"))
    if not text:
        return None, current_message_id

    message_id = _extract_stream_message_id(payload, metadata) or current_message_id or "__default__"
    buffers[message_id] = _merge_stream_text(buffers.get(message_id, ""), text)
    return buffers[message_id], message_id


def _extract_artifacts(result: dict | list) -> list[str]:
    """Extract artifact paths from the last AI response cycle only.

    Instead of reading the full accumulated ``artifacts`` state (which contains
    all artifacts ever produced in the thread), this inspects the messages after
    the last human message and collects file paths from ``present_files`` tool
    calls.  This ensures only newly-produced artifacts are returned.
    """
    if isinstance(result, list):
        messages = result
    elif isinstance(result, dict):
        messages = result.get("messages", [])
    else:
        return []

    artifacts: list[str] = []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        # Stop at the last human message — anything before it is a previous turn
        if msg.get("type") == "human":
            if _is_hidden_human_control_message(msg):
                continue
            break
        # Look for AI messages with present_files tool calls
        if msg.get("type") == "ai":
            for tc in msg.get("tool_calls", []):
                if isinstance(tc, dict) and tc.get("name") == "present_files":
                    args = tc.get("args", {})
                    paths = args.get("filepaths", [])
                    if isinstance(paths, list):
                        artifacts.extend(p for p in paths if isinstance(p, str))
    return artifacts


def _is_hidden_human_control_message(msg: Mapping[str, Any]) -> bool:
    """Return whether a human message is an internal control message hidden from UI."""
    if msg.get("type") != "human":
        return False

    additional_kwargs = msg.get("additional_kwargs")
    if not isinstance(additional_kwargs, Mapping):
        return False

    return additional_kwargs.get("hide_from_ui") is True


def _format_artifact_text(artifacts: list[str]) -> str:
    """Format artifact paths into a human-readable text block listing filenames."""
    import posixpath

    filenames = [posixpath.basename(p) for p in artifacts]
    if len(filenames) == 1:
        return f"Created File: 📎 {filenames[0]}"
    return "Created Files: 📎 " + "、".join(filenames)


_OUTPUTS_VIRTUAL_PREFIX = "/mnt/user-data/outputs/"


def _unknown_command_reply(command: str | None = None) -> str:
    available = " | ".join(sorted(KNOWN_CHANNEL_COMMANDS))
    if command:
        return f"Unknown command: /{command}. Available commands: {available}"
    return f"Unknown command. Available commands: {available}"


def _human_input_message(content: str, *, original_content: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "human", "content": content}
    if original_content is not None and original_content != content:
        message["additional_kwargs"] = {ORIGINAL_USER_CONTENT_KEY: original_content}
    return message


def _auth_disabled_owner_user_id() -> str | None:
    try:
        from app.gateway.auth_disabled import AUTH_DISABLED_USER_ID, is_auth_disabled
    except Exception:
        logger.debug("Unable to inspect auth-disabled mode for channel owner fallback", exc_info=True)
        return None
    return AUTH_DISABLED_USER_ID if is_auth_disabled() else None


def _effective_owner_user_id(msg: InboundMessage) -> str | None:
    return _auth_disabled_owner_user_id() or msg.owner_user_id


def _apply_effective_owner(msg: InboundMessage) -> InboundMessage:
    owner_user_id = _effective_owner_user_id(msg)
    if owner_user_id:
        msg.owner_user_id = owner_user_id
    return msg


def _owner_headers(msg: InboundMessage) -> dict[str, str] | None:
    owner_user_id = _effective_owner_user_id(msg)
    if not owner_user_id:
        return None
    return create_internal_auth_headers(owner_user_id=owner_user_id)


def _safe_user_id_for_run(raw_user_id: str) -> str:
    from deerflow.config.paths import get_paths

    try:
        return get_paths().prepare_user_dir_for_raw_id(raw_user_id)
    except Exception:
        logger.exception("Failed to prepare channel run user directory")
        return make_safe_user_id(raw_user_id)


def _channel_storage_user_id(msg: InboundMessage) -> str | None:
    """Resolve the canonical DeerFlow user id for a channel-triggered message.

    Single source of truth for both the agent **run identity**
    (``_resolve_run_params`` → ``run_context["user_id"]``) and the **file/artifact
    storage bucket** (``receive_file`` / ``_ingest_inbound_files`` /
    ``_prepare_artifact_delivery``), so the bucket the agent reads/writes always
    matches where channel files are staged. Prefer the bound DeerFlow owner,
    otherwise fall back to the sanitized raw platform user id. Without that
    fallback, an unbound auth-enabled channel would run under ``safe(msg.user_id)``
    but stage files under ``get_effective_user_id()`` (the dispatcher task's unset
    contextvar → ``"default"``), so uploads would land in ``users/default/...``
    while the agent reads ``users/{safe_platform_user_id}/...``. Returns ``None``
    only when neither identity is available, leaving the caller to fall back to the
    contextvar/default user.

    Distinct from :func:`_owner_headers`, which deliberately sends the *raw* owner
    id (no sanitize, no platform fallback) over HTTP for gateway to re-resolve;
    this helper is the in-process, sanitized, filesystem-facing identity.
    """
    owner_user_id = _effective_owner_user_id(msg)
    if owner_user_id:
        return _safe_user_id_for_run(owner_user_id)
    if msg.user_id:
        return _safe_user_id_for_run(msg.user_id)
    return None


def _resolve_slash_skill_command(
    text: str,
    available_skills: set[str] | None = None,
    storage: SkillStorage | Callable[[], SkillStorage] | None = None,
) -> _SlashSkillCommandResolution | None:
    reference = parse_slash_skill_reference(text)
    if reference is None:
        return None
    try:
        resolved_storage = storage() if callable(storage) else storage or get_or_new_skill_storage()
        skills = resolved_storage.load_skills(enabled_only=False)

        skill = next((candidate for candidate in skills if candidate.name == reference.name), None)
        if skill is None:
            return None
        if not skill.enabled:
            return _SlashSkillCommandResolution(failure_message=f"Skill `/{reference.name}` is installed but disabled. Enable it before using slash activation.")
        if available_skills is not None and reference.name not in available_skills:
            return _SlashSkillCommandResolution(failure_message=f"Skill `/{reference.name}` is not available for this agent.")

        return _SlashSkillCommandResolution(route_to_chat=True)
    except Exception as exc:
        logger.exception("[Manager] failed to resolve slash skill command")
        raise SlashSkillCommandResolutionError("Failed to resolve slash skill command. Please check the skill configuration.") from exc


def _resolve_attachments(thread_id: str, artifacts: list[str], *, user_id: str | None = None) -> list[ResolvedAttachment]:
    """Resolve virtual artifact paths to host filesystem paths with metadata.

    Only paths under ``/mnt/user-data/outputs/`` are accepted; any other
    virtual path is rejected with a warning to prevent exfiltrating uploads
    or workspace files via IM channels.

    Skips artifacts that cannot be resolved (missing files, invalid paths)
    and logs warnings for them.
    """
    from deerflow.config.paths import get_paths

    attachments: list[ResolvedAttachment] = []
    paths = get_paths()
    effective_user_id = user_id or get_effective_user_id()
    outputs_dir = paths.sandbox_outputs_dir(thread_id, user_id=effective_user_id).resolve()
    for virtual_path in artifacts:
        # Security: only allow files from the agent outputs directory
        if not virtual_path.startswith(_OUTPUTS_VIRTUAL_PREFIX):
            logger.warning("[Manager] rejected non-outputs artifact path: %s", virtual_path)
            continue
        try:
            actual = paths.resolve_virtual_path(thread_id, virtual_path, user_id=effective_user_id)
            # Verify the resolved path is actually under the outputs directory
            # (guards against path-traversal even after prefix check)
            try:
                actual.resolve().relative_to(outputs_dir)
            except ValueError:
                logger.warning("[Manager] artifact path escapes outputs dir: %s -> %s", virtual_path, actual)
                continue
            if not actual.is_file():
                logger.warning("[Manager] artifact not found on disk: %s -> %s", virtual_path, actual)
                continue
            mime, _ = mimetypes.guess_type(str(actual))
            mime = mime or "application/octet-stream"
            attachments.append(
                ResolvedAttachment(
                    virtual_path=virtual_path,
                    actual_path=actual,
                    filename=actual.name,
                    mime_type=mime,
                    size=actual.stat().st_size,
                    is_image=mime.startswith("image/"),
                )
            )
        except (ValueError, OSError) as exc:
            logger.warning("[Manager] failed to resolve artifact %s: %s", virtual_path, exc)
    return attachments


def _prepare_artifact_delivery(
    thread_id: str,
    response_text: str,
    artifacts: list[str],
    *,
    user_id: str | None = None,
) -> tuple[str, list[ResolvedAttachment]]:
    """Resolve attachments and append filename fallbacks to the text response."""
    attachments: list[ResolvedAttachment] = []
    if not artifacts:
        return response_text, attachments

    attachments = _resolve_attachments(thread_id, artifacts, user_id=user_id)
    resolved_virtuals = {attachment.virtual_path for attachment in attachments}
    unresolved = [path for path in artifacts if path not in resolved_virtuals]

    if unresolved:
        artifact_text = _format_artifact_text(unresolved)
        response_text = (response_text + "\n\n" + artifact_text) if response_text else artifact_text

    # Always include resolved attachment filenames as a text fallback so files
    # remain discoverable even when the upload is skipped or fails.
    if attachments:
        resolved_text = _format_artifact_text([attachment.virtual_path for attachment in attachments])
        response_text = (response_text + "\n\n" + resolved_text) if response_text else resolved_text

    return response_text, attachments


async def _ingest_inbound_files(thread_id: str, msg: InboundMessage, *, user_id: str | None = None) -> list[dict[str, Any]]:
    if not msg.files:
        return []

    from deerflow.uploads.manager import (
        UnsafeUploadPathError,
        claim_unique_filename,
        ensure_uploads_dir,
        normalize_filename,
        write_upload_file_no_symlink,
    )

    def _prepare_uploads_dir() -> tuple[Path, set[str]]:
        # Worker thread: ensure_uploads_dir's mkdir and the iterdir enumeration are
        # blocking filesystem IO that must stay off the event loop.
        target = ensure_uploads_dir(thread_id, user_id=user_id)
        existing = {entry.name for entry in target.iterdir() if entry.is_file()}
        return target, existing

    uploads_dir, seen_names = await asyncio.to_thread(_prepare_uploads_dir)

    created: list[dict[str, Any]] = []
    file_reader = INBOUND_FILE_READERS.get(msg.channel_name, _read_http_inbound_file)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        for idx, f in enumerate(msg.files):
            if not isinstance(f, dict):
                continue

            ftype = f.get("type") if isinstance(f.get("type"), str) else "file"
            filename = f.get("filename") if isinstance(f.get("filename"), str) else ""

            try:
                data = await file_reader(f, client)
            except Exception:
                logger.exception(
                    "[Manager] failed to read inbound file: channel=%s, file=%s",
                    msg.channel_name,
                    f.get("url") or filename or idx,
                )
                continue

            if data is None:
                logger.warning(
                    "[Manager] inbound file reader returned no data: channel=%s, file=%s",
                    msg.channel_name,
                    f.get("url") or filename or idx,
                )
                continue

            if not filename:
                ext = ".bin"
                if ftype == "image":
                    ext = ".png"
                filename = f"{msg.thread_ts or 'msg'}_{idx}{ext}"

            try:
                safe_name = claim_unique_filename(normalize_filename(filename), seen_names)
            except ValueError:
                logger.warning(
                    "[Manager] skipping inbound file with unsafe filename: channel=%s, file=%r",
                    msg.channel_name,
                    filename,
                )
                continue

            dest = uploads_dir / safe_name
            try:
                dest = await asyncio.to_thread(write_upload_file_no_symlink, uploads_dir, safe_name, data)
            except UnsafeUploadPathError:
                logger.warning("[Manager] skipping inbound file with unsafe destination: %s", safe_name)
                continue
            except Exception:
                logger.exception("[Manager] failed to write inbound file: %s", dest)
                continue

            created.append(
                {
                    "filename": safe_name,
                    "size": len(data),
                    "path": f"/mnt/user-data/uploads/{safe_name}",
                    "is_image": ftype == "image",
                }
            )

    return created


def _format_uploaded_files_block(files: list[dict[str, Any]]) -> str:
    lines = [
        "<uploaded_files>",
        "The following files were uploaded in this message:",
        "",
    ]
    if not files:
        lines.append("(empty)")
    else:
        for f in files:
            filename = f.get("filename", "")
            size = int(f.get("size") or 0)
            size_kb = size / 1024 if size else 0
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
            path = f.get("path", "")
            is_image = bool(f.get("is_image"))
            file_kind = "image" if is_image else "file"
            lines.append(f"- {filename} ({size_str})")
            lines.append(f"  Type: {file_kind}")
            lines.append(f"  Path: {path}")
            lines.append("")
    lines.append("Use `read_file` for text-based files and documents.")
    lines.append("Use `view_image` for image files (jpg, jpeg, png, webp) so the model can inspect the image content.")
    lines.append("</uploaded_files>")
    return "\n".join(lines)


class ChannelManager:
    """Core dispatcher that bridges IM channels to the DeerFlow agent.

    It reads from the MessageBus inbound queue, creates/reuses threads on
    Gateway's LangGraph-compatible API, sends messages via ``runs.wait``, and publishes
    outbound responses back through the bus.
    """

    def __init__(
        self,
        bus: MessageBus,
        store: ChannelStore,
        *,
        max_concurrency: int = 5,
        langgraph_url: str = DEFAULT_LANGGRAPH_URL,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        assistant_id: str = DEFAULT_ASSISTANT_ID,
        default_session: dict[str, Any] | None = None,
        channel_sessions: dict[str, Any] | None = None,
        connection_repo: Any | None = None,
        require_bound_identity: bool = False,
        routing_repo: Any | None = None,
        route_v2_enabled: bool = True,
        binding_repo: Any | None = None,
        s08_enabled: bool = False,
        registry_repo: Any | None = None,
        catalog_repo: Any | None = None,
        group_catalog_ttl_seconds: int = 3600,
    ) -> None:
        self.bus = bus
        self.store = store
        self._max_concurrency = max_concurrency
        self._langgraph_url = langgraph_url
        self._gateway_url = gateway_url
        self._assistant_id = assistant_id
        self._default_session = _as_dict(default_session)
        self._channel_sessions = dict(channel_sessions or {})
        self._connection_repo = connection_repo
        self._require_bound_identity = require_bound_identity
        # V2 routing: session pointers + message links + durable event inbox.
        # Only active when the SQL routing repository is available, so a
        # deployment without a database keeps the legacy channel-store path.
        self._routing_repo = routing_repo
        self._route_v2_enabled = route_v2_enabled
        # S08 session archive: user-level identity bindings + (PR-3) thread
        # registry/group catalog. Gated by its own flag so the legacy behaviour
        # stays byte-identical when the feature is off.
        self._binding_repo = binding_repo
        self._s08_enabled = s08_enabled
        # S08 archive: ownership/classification registry and the group name
        # cache. Both are optional; when absent the feature degrades to the
        # pre-S08 behaviour instead of failing message delivery.
        self._registry_repo = registry_repo
        self._catalog_repo = catalog_repo
        self._group_catalog_ttl_seconds = max(0, int(group_catalog_ttl_seconds))
        self._thread_touch_cache: dict[str, datetime] = {}
        self._work_documents = None  # Opt-in Work integration; production state is shared in SQL.
        self._work_unavailable_semaphore: asyncio.Semaphore | None = None
        self._client = None  # lazy init — langgraph_sdk async client
        self._channel_metadata_synced: set[str] = set()
        # Per-conversation locks so concurrent inbound messages for the same
        # chat don't race to create duplicate threads (see _get_or_create_thread).
        self._thread_create_locks: dict[tuple[str, str, str | None], asyncio.Lock] = {}
        # Per-thread run locks for channels that want in-manager serialization
        # instead of surfacing the runtime's generic busy reply.
        self._serialized_thread_runs: dict[tuple[str, str], _SerializedThreadRunState] = {}
        self._skill_storage: SkillStorage | None = None
        self._csrf_token = generate_csrf_token()
        self._semaphore: asyncio.Semaphore | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        # Insertion order == chronological (keys are never re-inserted), so an
        # OrderedDict lets us evict expired/overflow entries from the front in
        # O(k) instead of scanning all entries on every inbound message.
        self._recent_inbound_events: OrderedDict[tuple[str, str, str, str], float] = OrderedDict()

    @staticmethod
    def _channel_supports_streaming(channel_name: str) -> bool:
        from .service import get_channel_service

        service = get_channel_service()
        if service:
            channel = service.get_channel(channel_name)
            if channel is not None:
                return channel.supports_streaming
        return CHANNEL_CAPABILITIES.get(channel_name, {}).get("supports_streaming", False)

    def _resolve_session_layer(self, msg: InboundMessage) -> tuple[dict[str, Any], dict[str, Any]]:
        channel_layer = _as_dict(self._channel_sessions.get(msg.channel_name))
        users_layer = _as_dict(channel_layer.get("users"))
        user_layer = _as_dict(users_layer.get(msg.user_id))
        return channel_layer, user_layer

    def _begin_serialized_thread_run(
        self,
        *,
        channel_name: str,
        thread_id: str,
    ) -> tuple[_SerializedThreadRunState | None, bool]:
        policy = CHANNEL_RUN_POLICY.get(channel_name)
        if policy is None or not policy.serialize_thread_runs:
            return None, False

        key = (channel_name, thread_id)
        state = self._serialized_thread_runs.get(key)
        if state is None:
            state = _SerializedThreadRunState(lock=asyncio.Lock())
            self._serialized_thread_runs[key] = state
        queued = state.lock.locked()
        state.waiters += 1
        return state, queued

    def _finish_serialized_thread_run(
        self,
        *,
        channel_name: str,
        thread_id: str,
        state: _SerializedThreadRunState | None,
        lock_acquired: bool,
    ) -> None:
        if state is None:
            return

        if lock_acquired:
            state.lock.release()
        state.waiters -= 1
        if state.waiters == 0 and not state.lock.locked():
            self._serialized_thread_runs.pop((channel_name, thread_id), None)

    async def _publish_progress_update(self, msg: InboundMessage, thread_id: str, text: str) -> None:
        await self.bus.publish_outbound(
            OutboundMessage(
                channel_name=msg.channel_name,
                chat_id=msg.chat_id,
                thread_id=thread_id,
                text=text,
                is_final=False,
                thread_ts=msg.thread_ts,
                connection_id=msg.connection_id,
                owner_user_id=msg.owner_user_id,
                metadata=_response_metadata(msg.metadata),
            )
        )

    def _resolve_run_params(self, msg: InboundMessage, thread_id: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
        channel_layer, user_layer = self._resolve_session_layer(msg)

        # Per-message agent override (e.g. GitHub webhook fan-out: multiple
        # agents may bind the same repo, each gets its own inbound message
        # with its own agent_name in metadata).  Honors the same shape as
        # channel/user session config: the bare agent name routes through
        # the lead_agent + agent_name context pattern below.
        message_assistant_id: str | None = None
        msg_metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
        meta_assistant_id = msg_metadata.get("assistant_id") or msg_metadata.get("agent_name")
        if isinstance(meta_assistant_id, str) and meta_assistant_id.strip():
            message_assistant_id = meta_assistant_id

        assistant_id = message_assistant_id or user_layer.get("assistant_id") or channel_layer.get("assistant_id") or self._default_session.get("assistant_id") or self._assistant_id
        if not isinstance(assistant_id, str) or not assistant_id.strip():
            assistant_id = self._assistant_id

        run_config = _merge_dicts(
            DEFAULT_RUN_CONFIG,
            self._default_session.get("config"),
            channel_layer.get("config"),
            user_layer.get("config"),
        )

        configurable = run_config.get("configurable")
        if isinstance(configurable, Mapping):
            configurable = dict(configurable)
        else:
            configurable = {}
        run_config["configurable"] = configurable
        # Pin channel-triggered runs to the root graph namespace so follow-up
        # turns continue from the same conversation checkpoint.
        configurable["checkpoint_ns"] = ""
        configurable["thread_id"] = thread_id

        # ``user_id`` drives DeerFlow-owned memory, files, and thread buckets.
        # For browser-connected IM channels, prefer the DeerFlow account that
        # owns the connection. Preserve the raw platform user under
        # ``channel_user_id`` for platform-facing lookups and audits.
        run_context_identity: dict[str, Any] = {"thread_id": thread_id}
        # ``channel_name`` lets in-graph code (e.g. ``_make_lead_agent``)
        # decide whether a tool is safe to expose for this run. Webhook
        # channels carry untrusted external prompts (GitHub comments,
        # Telegram chats from non-owners, etc.), so admin-shaped tools
        # like ``update_agent`` are dropped when the run was triggered
        # via one. See ``_make_lead_agent`` for the gate.
        run_context_identity["channel_name"] = msg.channel_name
        # Session configuration is not a producer of live Work availability.
        # The Feishu chat dispatch path may supply its current server hint.
        run_context_identity["work_capabilities"] = None
        # Single source of truth for the run identity: the same helper that scopes
        # inbound files and outbound artifacts, so the bucket the agent reads/writes
        # always matches where channel files are staged.
        run_user_id = _channel_storage_user_id(msg)
        if run_user_id:
            run_context_identity["user_id"] = run_user_id
        if msg.user_id:
            run_context_identity["channel_user_id"] = msg.user_id

        run_context = _merge_dicts(
            DEFAULT_RUN_CONTEXT,
            self._default_session.get("context"),
            channel_layer.get("context"),
            user_layer.get("context"),
            run_context_identity,
        )

        # Custom agents are implemented as lead_agent + agent_name context.
        # Keep backward compatibility for channel configs that set
        # assistant_id: <custom-agent-name> by routing through lead_agent.
        if assistant_id != DEFAULT_ASSISTANT_ID:
            run_context.setdefault("agent_name", _normalize_custom_agent_name(assistant_id))
            assistant_id = DEFAULT_ASSISTANT_ID

        # Apply per-channel run policy (recursion_limit bump for webhook
        # channels, etc.). Looking the policy up by channel_name keeps
        # GitHub-specific knobs out of this method — adding the next
        # webhook channel is a one-row CHANNEL_RUN_POLICY entry, not a
        # new if-branch here.
        policy = CHANNEL_RUN_POLICY.get(msg.channel_name)
        if policy is not None and policy.default_recursion_limit is not None:
            # Per-message override (via msg.metadata[channel_name]) honors
            # the operator's explicit per-agent recursion_limit verbatim —
            # including values below the channel default. A safety-conscious
            # ``github.recursion_limit: 50`` on a review-only agent now halts
            # at 50 super-steps as documented in GitHubAgentConfig, instead
            # of being silently clamped up to the channel default. When no
            # override is present, the channel default acts as a floor over
            # whatever session config supplied (the higher value wins).
            channel_meta = (msg.metadata or {}).get(msg.channel_name, {})
            override = channel_meta.get("recursion_limit") if isinstance(channel_meta, dict) else None
            if isinstance(override, int) and override > 0:
                run_config["recursion_limit"] = override
            else:
                run_config["recursion_limit"] = max(run_config.get("recursion_limit", 100), policy.default_recursion_limit)

        return assistant_id, run_config, run_context

    async def _apply_channel_policy(self, msg: InboundMessage, run_context: dict[str, Any]) -> ChannelRunPolicy | None:
        """Apply per-channel run policy that needs ``run_context`` access.

        Run AFTER ``_resolve_run_params`` (which produced ``run_context``)
        and BEFORE the agent runs. Covers:

        * ``disable_clarification`` for non-interactive channels —
          ``ClarificationMiddleware`` would otherwise dead-end a webhook
          run waiting for a synchronous reply that only arrives as a
          later, separate webhook delivery.
        * Channel-specific credentials provider — e.g. the GitHub channel
          installs a token-mint callable so ``bash_tool`` can resolve a
          fresh installation token on every invocation (longer than the
          1h GitHub TTL).

        ``recursion_limit`` is applied inside :meth:`_resolve_run_params`
        instead because it lives on ``run_config`` (not ``run_context``)
        and the resolver already builds ``run_config``.

        Returns the resolved :class:`ChannelRunPolicy` (or ``None`` when
        the channel has no entry) so :meth:`_handle_chat` can branch on
        flags like ``fire_and_forget`` without doing a second dict
        lookup.
        """
        policy = CHANNEL_RUN_POLICY.get(msg.channel_name)
        if policy is None:
            return None
        if not policy.is_interactive:
            run_context["disable_clarification"] = True
        if policy.credentials_provider is not None:
            try:
                await policy.credentials_provider(msg, run_context)
            except Exception:
                # Credential failures must NOT drop the delivery — the
                # provider's own logging records the cause; we keep the
                # run going (read-only is better than no response).
                logger.warning(
                    "[Manager] channel=%s credentials_provider raised; run proceeds without injected credentials",
                    msg.channel_name,
                    exc_info=True,
                )
        return policy

    def _resolve_available_skill_names(self, msg: InboundMessage) -> set[str] | None:
        thread_id = self.store.get_thread_id(msg.channel_name, msg.chat_id, topic_id=msg.topic_id) or ""
        _, _, run_context = self._resolve_run_params(msg, thread_id)
        if run_context.get("is_bootstrap"):
            return {"bootstrap"}

        agent_name = run_context.get("agent_name")
        if not isinstance(agent_name, str) or not agent_name.strip():
            return None

        # Read the agent config from the same owner bucket the run uses:
        # ``run_context["user_id"]`` is the resolved owner (``_channel_storage_user_id``),
        # but without it ``load_agent_config`` falls back to the dispatch loop's unset
        # contextvar (``"default"``), reading the wrong user's per-user custom agent.
        agent_config = load_agent_config(_normalize_custom_agent_name(agent_name), user_id=run_context.get("user_id"))
        if agent_config and agent_config.skills is not None:
            return set(agent_config.skills)
        return None

    # -- LangGraph SDK client (lazy) ----------------------------------------

    def _get_client(self):
        """Return the ``langgraph_sdk`` async client, creating it on first use."""
        if self._client is None:
            from langgraph_sdk import get_client

            self._client = get_client(
                url=self._langgraph_url,
                headers={
                    **create_internal_auth_headers(),
                    CSRF_HEADER_NAME: self._csrf_token,
                    "Cookie": f"{CSRF_COOKIE_NAME}={self._csrf_token}",
                },
            )
        return self._client

    def _get_skill_storage(self) -> SkillStorage:
        if self._skill_storage is None:
            self._skill_storage = get_or_new_skill_storage()
        return self._skill_storage

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the dispatch loop."""
        if self._running:
            return
        self._running = True
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._task = asyncio.create_task(self._dispatch_loop())
        logger.info("ChannelManager started (max_concurrency=%d)", self._max_concurrency)

    async def stop(self) -> None:
        """Stop the dispatch loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._work_documents is not None:
            await self._work_documents.close()
            self._work_documents = None
        logger.info("ChannelManager stopped")

    # -- dispatch loop -----------------------------------------------------

    async def _dispatch_loop(self) -> None:
        logger.info("[Manager] dispatch loop started, waiting for inbound messages")
        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.get_inbound(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # Dedupe before logging "received" so a provider retrying an event N
            # times does not log N accepts; duplicates are logged once as ignored.
            # Note: this manager-level dedupe only guards the agent run / final
            # answer. Provider adapters may emit ack side-effects (a "Working on
            # it…" reply, an "eyes" reaction) before publish_inbound, so those are
            # intentionally not deduped here.
            if self._is_duplicate_inbound(msg):
                continue
            logger.info(
                "[Manager] received inbound: channel=%s, chat_id=%s, type=%s, text_len=%d, files=%d",
                msg.channel_name,
                msg.chat_id,
                msg.msg_type.value,
                len(msg.text or ""),
                len(msg.files),
            )
            task = asyncio.create_task(self._handle_message(msg))
            task.add_done_callback(self._log_task_error)

    @staticmethod
    def _inbound_dedupe_key(msg: InboundMessage) -> tuple[str, str, str, str] | None:
        metadata = msg.metadata or {}
        message_id = None
        for key in INBOUND_DEDUPE_METADATA_KEYS:
            value = metadata.get(key)
            if value:
                message_id = str(value)
                break
        if message_id is None:
            raw_message = metadata.get("raw_message")
            if isinstance(raw_message, Mapping):
                for key in INBOUND_DEDUPE_METADATA_KEYS:
                    value = raw_message.get(key)
                    if value:
                        message_id = str(value)
                        break
        if message_id is None:
            return None

        # Fail closed: without a workspace/team/guild identifier we cannot tell two
        # workspaces apart (e.g. Slack channel ids are not globally unique), so
        # skip dedupe rather than risk collapsing distinct workspaces' messages.
        workspace_id = msg.workspace_id or metadata.get("workspace_id") or metadata.get("team_id") or metadata.get("guild_id") or metadata.get("aibotid")
        if not workspace_id:
            return None
        return (msg.channel_name, str(workspace_id), msg.chat_id, message_id)

    def _is_duplicate_inbound(self, msg: InboundMessage) -> bool:
        key = self._inbound_dedupe_key(msg)
        if key is None:
            return False

        now = time.monotonic()
        # Entries are in chronological insertion order, so expired ones cluster at
        # the front: pop from the front until we hit a still-live entry.
        while self._recent_inbound_events:
            _, oldest_at = next(iter(self._recent_inbound_events.items()))
            if now - oldest_at > INBOUND_DEDUPE_TTL_SECONDS:
                self._recent_inbound_events.popitem(last=False)
            else:
                break
        while len(self._recent_inbound_events) > INBOUND_DEDUPE_MAX_ENTRIES:
            self._recent_inbound_events.popitem(last=False)

        if key in self._recent_inbound_events:
            logger.info(
                "[Manager] duplicate inbound ignored: channel=%s, chat_id=%s, message_id=%s",
                msg.channel_name,
                msg.chat_id,
                key[-1],
            )
            return True

        self._recent_inbound_events[key] = now
        return False

    def _release_inbound_dedupe_key(self, msg: InboundMessage) -> None:
        """Drop a recorded dedupe key so a provider redelivery can be reprocessed.

        Called only on transient/unexpected handling failures: the key was
        recorded on receipt so retries arriving *while* the message is being
        handled are still deduped, but if handling fails we must not turn a
        recoverable error into a TTL-long black hole for the same message_id.
        """
        key = self._inbound_dedupe_key(msg)
        if key is not None:
            self._recent_inbound_events.pop(key, None)

    @staticmethod
    def _log_task_error(task: asyncio.Task) -> None:
        """Surface unhandled exceptions from background tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("[Manager] unhandled error in message task: %s", exc, exc_info=exc)

    # -- V2 routing: pointers, message links, event inbox ------------------

    def _routing_v2_active(self, msg: InboundMessage | None = None) -> bool:
        """True when this message must go through the durable V2 router.

        Requires an SQL routing repository: without it the legacy channel store
        (one JSON pointer per ``channel:chat[:topic]``) stays authoritative, so
        existing single-node deployments keep working unchanged.
        """
        if self._routing_repo is None or not self._route_v2_enabled:
            return False
        if msg is not None and msg.channel_name not in ROUTING_V2_CHANNELS:
            return False
        return True

    @staticmethod
    def _routing_channel_type(msg: InboundMessage) -> str:
        chat_type = str((msg.metadata or {}).get("chat_type") or "").lower()
        return "group" if chat_type in {"group", "topic"} else "private"

    def _s08_active(self, msg: InboundMessage | None = None) -> bool:
        """True when the S08 archive may act on this message.

        The archive is strictly additive: it needs its own flag *and* the V2
        routing kernel, because archived threads are exactly the threads the V2
        kernel owns. When either is missing every legacy code path is unchanged.
        """
        if not self._s08_enabled:
            return False
        return self._routing_v2_active(msg)

    @staticmethod
    def _im_scope(msg: InboundMessage) -> tuple[str, str]:
        """Return ``(tenant_key, bot_app_id)`` carried by the adapter metadata."""
        metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
        tenant_key = str(metadata.get("tenant_key") or "").strip()
        bot_app_id = str(metadata.get("bot_app_id") or "").strip()
        return tenant_key, bot_app_id

    async def _register_thread(self, msg: InboundMessage, thread_id: str, *, title: str | None = None) -> None:
        """Record a newly created thread in the S08 ownership registry.

        This is the single registration point: it is called from
        :meth:`_create_thread` only, so every thread the V2 kernel can later
        restore has exactly one registry row.
        """
        if not self._s08_active(msg) or self._registry_repo is None or not thread_id:
            return
        tenant_key, bot_app_id = self._im_scope(msg)
        try:
            await self._registry_repo.register(
                thread_id=thread_id,
                owner_user_id=msg.owner_user_id or "",
                channel_type=self._routing_channel_type(msg),
                external_conversation_id=msg.chat_id,
                provider=msg.channel_name,
                connection_id=msg.connection_id or "",
                tenant_key=tenant_key,
                bot_app_id=bot_app_id,
                external_topic_id=msg.topic_id or "",
                status="active",
                classification_source="RUNTIME",
                title_snapshot=title,
                last_active_at=datetime.now(UTC),
            )
        except Exception:
            # Archiving must never break message delivery; the row can be
            # rebuilt from im_message_links by the backfill script.
            logger.warning(
                "[Manager] failed to register archived thread: chat=%s owner=%s thread=%s",
                msg.chat_id,
                msg.owner_user_id,
                thread_id,
                exc_info=True,
            )

    async def _touch_thread(self, thread_id: str, *, at: datetime | None = None) -> None:
        """Advance ``last_active_at`` for a thread, rate limited per thread."""
        if not self._s08_enabled or self._registry_repo is None or not thread_id:
            return
        now = at or datetime.now(UTC)
        previous = self._thread_touch_cache.get(thread_id)
        if previous is not None and (now - previous).total_seconds() < THREAD_TOUCH_MIN_INTERVAL_SECONDS:
            return
        if len(self._thread_touch_cache) >= INBOUND_DEDUPE_MAX_ENTRIES:
            self._thread_touch_cache.clear()
        self._thread_touch_cache[thread_id] = now
        try:
            await self._registry_repo.touch(thread_id, at=now)
        except Exception:
            logger.warning("[Manager] failed to touch archived thread: thread=%s", thread_id, exc_info=True)

    async def _ensure_group_catalog(self, msg: InboundMessage) -> None:
        """Make sure a group chat has a catalog row and refresh it when stale."""
        if not self._s08_active(msg) or self._catalog_repo is None:
            return
        if self._routing_channel_type(msg) != "group":
            return
        tenant_key, bot_app_id = self._im_scope(msg)
        metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
        chat_type = str(metadata.get("chat_type") or "").strip().lower()
        try:
            await self._catalog_repo.ensure(
                chat_id=msg.chat_id,
                provider=msg.channel_name,
                tenant_key=tenant_key,
                bot_app_id=bot_app_id,
                chat_type=chat_type,
            )
            if not await self._catalog_repo.needs_sync(
                msg.chat_id,
                ttl_seconds=self._group_catalog_ttl_seconds,
                tenant_key=tenant_key,
                bot_app_id=bot_app_id,
            ):
                return
            claimed = await self._catalog_repo.try_claim_sync(
                msg.chat_id,
                now=datetime.now(UTC),
                ttl_seconds=self._group_catalog_ttl_seconds,
            )
            if not claimed:
                return
            await self._sync_group_meta(msg, tenant_key=tenant_key, bot_app_id=bot_app_id)
        except Exception:
            logger.warning(
                "[Manager] failed to refresh group catalog: chat=%s", msg.chat_id, exc_info=True
            )

    async def _sync_group_meta(self, msg: InboundMessage, *, tenant_key: str, bot_app_id: str) -> None:
        """Fetch chat metadata from the adapter and store it in the catalog."""
        if self._catalog_repo is None:
            return
        meta = await self._fetch_chat_meta(msg.channel_name, msg.chat_id)
        if meta is None:
            await self._catalog_repo.update_meta(
                chat_id=msg.chat_id,
                display_name=None,
                name_source="UNKNOWN",
                sync_error="FETCH_FAILED",
                tenant_key=tenant_key,
                bot_app_id=bot_app_id,
            )
            return
        name = str(meta.get("name") or "").strip()
        await self._catalog_repo.update_meta(
            chat_id=msg.chat_id,
            display_name=name or None,
            name_source="CHAT_INFO" if name else "UNKNOWN",
            chat_status=str(meta.get("chat_status") or "").strip() or None,
            member_count=meta.get("member_count"),
            sync_error="",
            tenant_key=tenant_key,
            bot_app_id=bot_app_id,
        )

    async def _fetch_chat_meta(self, channel_name: str, chat_id: str) -> dict[str, Any] | None:
        """Ask a channel adapter for chat metadata (Feishu implements this)."""
        try:
            from .service import get_channel_service

            service = get_channel_service()
            channel = service.get_channel(channel_name) if service is not None else None
            fetcher = getattr(channel, "fetch_chat_meta", None)
            if fetcher is None:
                return None
            meta = await asyncio.wait_for(fetcher(chat_id), timeout=10)
            return meta if isinstance(meta, dict) else None
        except Exception:
            logger.warning(
                "[Manager] failed to fetch chat meta: channel=%s chat=%s",
                channel_name,
                chat_id,
                exc_info=True,
            )
            return None

    @staticmethod
    def _routing_event_key(msg: InboundMessage) -> str | None:
        message_id = str((msg.metadata or {}).get("message_id") or "").strip()
        if not message_id:
            return None
        return f"{msg.channel_name}:{msg.chat_id}:{message_id}"

    async def _claim_routing_event(self, msg: InboundMessage, event_key: str) -> bool:
        return await self._routing_repo.claim_event(
            provider=msg.channel_name,
            event_key=event_key,
            chat_id=msg.chat_id,
            owner_user_id=msg.owner_user_id or "",
            message_id=str((msg.metadata or {}).get("message_id") or ""),
            connection_id=msg.connection_id or "",
            route_reason="INBOUND",
        )

    async def _finish_routing_event(self, msg: InboundMessage, event_key: str) -> None:
        await self._routing_repo.finish_event(
            provider=msg.channel_name,
            event_key=event_key,
            status="COMPLETED",
        )

    async def _release_routing_event(self, msg: InboundMessage, event_key: str | None) -> None:
        if event_key is None:
            return
        await self._routing_repo.release_event(provider=msg.channel_name, event_key=event_key)

    async def _get_pointer(self, msg: InboundMessage):
        return await self._routing_repo.get_pointer(
            provider=msg.channel_name,
            owner_user_id=msg.owner_user_id or "",
            chat_id=msg.chat_id,
            channel_type=self._routing_channel_type(msg),
            connection_id=msg.connection_id or "",
        )

    async def _get_pointer_thread_id(self, msg: InboundMessage) -> str | None:
        pointer = await self._get_pointer(msg)
        return pointer.thread_id if pointer is not None else None

    async def _current_thread_id(self, msg: InboundMessage) -> str | None:
        """Current thread for outbound bookkeeping (pointer under V2)."""
        if self._routing_v2_active(msg):
            return await self._get_pointer_thread_id(msg)
        return await self._lookup_thread_id(msg)

    async def _ensure_pointer_thread(self, client, msg: InboundMessage) -> str:
        """Return the pointer thread, creating the first one atomically.

        Two racing first messages may both create a DeerFlow thread; the unique
        pointer key decides the winner and the loser's empty thread is
        discarded, so a chat never ends up with two "current" threads.
        """
        existing = await self._get_pointer_thread_id(msg)
        if existing:
            return existing
        candidate = await self._create_thread(client, msg, store_mapping=False)
        pointer = await self._routing_repo.insert_pointer_if_absent(
            provider=msg.channel_name,
            owner_user_id=msg.owner_user_id or "",
            chat_id=msg.chat_id,
            thread_id=candidate,
            channel_type=self._routing_channel_type(msg),
            connection_id=msg.connection_id or "",
        )
        if pointer.thread_id != candidate:
            logger.info(
                "[Manager] lost pointer creation race for chat=%s owner=%s; adopting thread=%s",
                msg.chat_id,
                msg.owner_user_id,
                pointer.thread_id,
            )
            # S08: the losing candidate is an empty thread nobody will ever see
            # again. Mark it orphaned instead of leaving it looking active.
            if self._s08_active(msg) and self._registry_repo is not None:
                try:
                    await self._registry_repo.mark_status(candidate, "orphaned")
                except Exception:
                    logger.warning(
                        "[Manager] failed to mark orphaned thread: %s", candidate, exc_info=True
                    )
        return pointer.thread_id

    @staticmethod
    def _reply_ref(msg: InboundMessage) -> ReplyRef | None:
        metadata = msg.metadata or {}
        quoted_id = str(metadata.get("reply_message_id") or "").strip()
        if not quoted_id:
            return None
        return ReplyRef(
            message_id=quoted_id,
            sender_type=str(metadata.get("reply_sender_type") or "unknown"),
            sender_id=str(metadata.get("reply_sender_id") or ""),
            from_self_bot=metadata.get("reply_from_self_bot"),
        )

    async def _thread_exists(self, client, thread_id: str) -> bool:
        """Verify a restored thread still exists.

        Any failure to verify (deleted thread, permission error, gateway down)
        is treated as "cannot restore": ADR-04 requires failing closed instead
        of answering in the current conversation under the guise of a restore.
        """
        if not thread_id:
            return False
        try:
            await client.threads.get(thread_id)
            return True
        except Exception:
            logger.warning("[Manager] cannot verify restored thread %s; failing closed", thread_id, exc_info=True)
            return False

    async def _resolve_v2_route(self, client, msg: InboundMessage) -> tuple[str | None, RouteDecision]:
        reply = self._reply_ref(msg)
        link = None
        registry_owner_ok: bool | None = None
        if reply is not None:
            link = await self._routing_repo.get_link(provider=msg.channel_name, message_id=reply.message_id)
            if link is not None and self._s08_active(msg) and self._registry_repo is not None:
                # An archived thread must never be restored by another account,
                # even if a stale message link still points at it.
                try:
                    row = await self._registry_repo.get(link.thread_id)
                except Exception:
                    logger.warning(
                        "[Manager] cannot verify registry ownership for thread=%s",
                        link.thread_id,
                        exc_info=True,
                    )
                    row = None
                if row is not None:
                    if row.status not in ("active", "archived"):
                        # A deleted/orphaned thread is never revived, even if a
                        # stale message link still points at it.
                        return None, RouteDecision(
                            action=RouteAction.FAIL_CLOSED,
                            reason=RouteReason.THREAD_DELETED,
                            error_code=RouteReason.THREAD_DELETED.value,
                        )
                    registry_owner_ok = row.owner_user_id == (msg.owner_user_id or "")
        decision = resolve_route(
            chat_id=msg.chat_id,
            owner_user_id=msg.owner_user_id,
            reply=reply,
            link=link,
            quoted_text=(msg.metadata or {}).get("quoted_text"),
            registry_owner_ok=registry_owner_ok,
        )
        if decision.action is RouteAction.RESTORE:
            thread_id = decision.thread_id or ""
            if not await self._thread_exists(client, thread_id):
                return None, RouteDecision(
                    action=RouteAction.FAIL_CLOSED,
                    reason=RouteReason.THREAD_DELETED,
                    error_code=RouteReason.THREAD_DELETED.value,
                )
            return thread_id, decision
        if decision.blocks_run:
            return None, decision
        return await self._ensure_pointer_thread(client, msg), decision

    async def _record_inbound_link(self, msg: InboundMessage, thread_id: str, *, route_reason: str) -> None:
        metadata = msg.metadata or {}
        message_id = str(metadata.get("message_id") or "").strip()
        if not message_id or not thread_id:
            return
        await self._routing_repo.record_link(
            provider=msg.channel_name,
            message_id=message_id,
            chat_id=msg.chat_id,
            owner_user_id=msg.owner_user_id or "",
            thread_id=thread_id,
            direction="inbound",
            message_kind="message",
            connection_id=msg.connection_id or "",
            sender_external_id=msg.user_id,
            root_message_id=str(metadata.get("root_id") or ""),
            parent_message_id=str(metadata.get("parent_id") or ""),
        )
        logger.info(
            "[Manager] routed inbound: chat=%s owner=%s thread=%s reason=%s",
            msg.chat_id,
            msg.owner_user_id,
            thread_id,
            route_reason,
        )

    async def _send_routing_failure(self, msg: InboundMessage, decision: RouteDecision) -> None:
        logger.info(
            "[Manager] routing failure: chat=%s owner=%s code=%s action=%s",
            msg.chat_id,
            msg.owner_user_id,
            decision.error_code,
            decision.action.value,
        )
        outbound = OutboundMessage(
            channel_name=msg.channel_name,
            chat_id=msg.chat_id,
            thread_id=decision.thread_id or "",
            text=failure_message(decision.error_code),
            thread_ts=msg.thread_ts,
            connection_id=msg.connection_id,
            owner_user_id=msg.owner_user_id,
            metadata=_slim_metadata(msg.metadata),
        )
        await self.bus.publish_outbound(outbound)

    async def _handle_routed_chat(self, msg: InboundMessage, *, extra_context: dict[str, Any] | None = None) -> None:
        client = self._get_client()
        thread_id, decision = await self._resolve_v2_route(client, msg)
        if thread_id is None or decision.blocks_run:
            await self._send_routing_failure(msg, decision)
            return
        if decision.action is RouteAction.QUOTE_ONLY:
            msg.text = apply_quote(
                msg.text,
                decision.quote_text,
                cross_owner=decision.reason is RouteReason.CROSS_OWNER_QUOTE,
            )
        await self._record_inbound_link(msg, thread_id, route_reason=decision.reason.value)
        await self._ensure_group_catalog(msg)
        await self._touch_thread(thread_id)
        await self._run_chat_on_thread(
            client,
            msg,
            thread_id,
            created=False,
            extra_context=extra_context,
            storage_user_id=_channel_storage_user_id(msg),
        )

    async def _handle_new_conversation_v2(self, msg: InboundMessage) -> str:
        """``/new``: create a thread and move the pointer with compare-and-swap."""
        client = self._get_client()
        chat_id = msg.chat_id
        owner_user_id = msg.owner_user_id or ""
        channel_type = self._routing_channel_type(msg)
        connection_id = msg.connection_id or ""
        thread_id = await self._create_thread(client, msg, store_mapping=False)
        for _ in range(5):
            pointer = await self._get_pointer(msg)
            if pointer is None:
                adopted = await self._routing_repo.insert_pointer_if_absent(
                    provider=msg.channel_name,
                    owner_user_id=owner_user_id,
                    chat_id=chat_id,
                    thread_id=thread_id,
                    channel_type=channel_type,
                    connection_id=connection_id,
                )
                if adopted.thread_id == thread_id:
                    return "New conversation started."
                expected_generation = adopted.generation
            else:
                expected_generation = pointer.generation
            switched = await self._routing_repo.cas_switch_pointer(
                provider=msg.channel_name,
                owner_user_id=owner_user_id,
                chat_id=chat_id,
                thread_id=thread_id,
                expected_generation=expected_generation,
                channel_type=channel_type,
                connection_id=connection_id,
            )
            if switched:
                return "New conversation started."
        logger.warning(
            "[Manager] /new pointer switch contention for chat=%s owner=%s; keeping thread=%s",
            chat_id,
            owner_user_id,
            thread_id,
        )
        # The pointer never moved, so the freshly created thread has no owner
        # path to it. Say the switch failed instead of claiming success (the
        # user would otherwise keep talking in the old conversation believing
        # it is new).
        if self._s08_active(msg) and self._registry_repo is not None:
            try:
                await self._registry_repo.mark_status(thread_id, "orphaned")
            except Exception:
                logger.warning("[Manager] failed to mark orphaned /new thread: %s", thread_id, exc_info=True)
        return NEW_THREAD_SWITCH_FAILED_MESSAGE

    async def _handle_message(self, msg: InboundMessage) -> None:
        msg = _apply_effective_owner(msg)
        routing_event_key = self._routing_event_key(msg) if self._routing_v2_active(msg) else None
        if routing_event_key is not None and not await self._claim_routing_event(msg, routing_event_key):
            logger.info(
                "[Manager] dropping duplicate inbound event: channel=%s chat=%s key=%s",
                msg.channel_name,
                msg.chat_id,
                routing_event_key,
            )
            return
        try:
            # Non-command chat can be rejected before it consumes a semaphore
            # slot. Commands are handled below because provider adapters consume
            # binding commands before manager dispatch, and _handle_command()
            # applies its own admission gate for manager-level commands.
            bound_identity_rejection = None
            if msg.msg_type != InboundMessageType.COMMAND:
                bound_identity_rejection = await self._get_bound_identity_rejection(msg)
            if bound_identity_rejection is not None:
                # Release the dedupe claim: once the user binds their identity,
                # a provider redelivery of the same message must be processable
                # instead of being dropped as a duplicate forever.
                await self._release_routing_event(msg, routing_event_key)
                await self._reject_unbound_channel_message(msg, bound_identity_rejection=bound_identity_rejection)
                return

            async with self._semaphore:
                if msg.msg_type == InboundMessageType.COMMAND:
                    await self._handle_command(msg)
                else:
                    await self._handle_chat(msg, bound_identity_checked=True)
        except InvalidChannelSessionConfigError as exc:
            logger.warning(
                "Invalid channel session config for %s (chat=%s): %s",
                msg.channel_name,
                msg.chat_id,
                exc,
            )
            await self._release_routing_event(msg, routing_event_key)
            await self._send_error(msg, str(exc))
        except SlashSkillCommandResolutionError as exc:
            logger.warning(
                "Slash skill command resolution failed for %s (chat=%s): %s",
                msg.channel_name,
                msg.chat_id,
                exc,
            )
            await self._release_routing_event(msg, routing_event_key)
            await self._send_error(msg, str(exc))
        except Exception:
            logger.exception(
                "Error handling message from %s (chat=%s)",
                msg.channel_name,
                msg.chat_id,
            )
            # Transient/unexpected failure: release the dedupe key so a provider
            # redelivery of the same message can recover instead of being dropped
            # for the dedupe TTL.
            self._release_inbound_dedupe_key(msg)
            await self._release_routing_event(msg, routing_event_key)
            await self._send_error(msg, "An internal error occurred. Please try again.")
        else:
            if routing_event_key is not None:
                await self._finish_routing_event(msg, routing_event_key)

    # -- chat handling -----------------------------------------------------

    async def _get_bound_identity_rejection(self, msg: InboundMessage) -> _BoundIdentityRejection | None:
        """Return None when *msg* may proceed; otherwise return rejection routing hints.

        The returned object means the message lacks a verified bound identity.
        Its fields are intentionally limited to server-side values re-read from
        the connection repository, so rejection outbounds never trust a rejected
        inbound message's asserted connection metadata.
        """
        if not self._require_bound_identity and not self._s08_active(msg):
            return None
        # Webhook-authenticated channels (GitHub) opt out via
        # ChannelRunPolicy.requires_bound_identity=False. Authenticity is
        # enforced at the webhook route by HMAC, and the "sender → DeerFlow
        # user" binding is encoded in the agent's config.yaml ownership, not
        # in the channel-connections table — there is no per-sender
        # /connect handshake to perform.
        policy = CHANNEL_RUN_POLICY.get(msg.channel_name)
        if policy is not None and not policy.requires_bound_identity:
            return None
        if _auth_disabled_owner_user_id():
            return None

        has_connection = bool(msg.connection_id)
        has_owner = bool(msg.owner_user_id)

        if self._s08_active(msg):
            return await self._resolve_s08_identity_rejection(msg)

        if not (has_connection and has_owner):
            return _BoundIdentityRejection()
        if self._connection_repo is None:
            return _BoundIdentityRejection(message=BOUND_IDENTITY_UNAVAILABLE_MESSAGE)

        # The manager is the run-creation security boundary, so it does not
        # trust mutable InboundMessage identity fields by themselves. Re-read
        # the binding by provider identity before creating DeerFlow threads or
        # runs. If the asserted identity does not match, keep only the
        # server-side connection fields as outbound routing hints.
        connection = await self._connection_repo.find_connection_by_external_identity(
            provider=msg.channel_name,
            external_account_id=msg.user_id,
            workspace_id=msg.workspace_id or None,
        )
        if connection is None:
            return _BoundIdentityRejection()

        connection_id = connection.get("id")
        owner_user_id = connection.get("owner_user_id")
        if connection_id == msg.connection_id and owner_user_id == msg.owner_user_id:
            return None
        return _BoundIdentityRejection(outbound_connection_id=connection_id, outbound_owner_user_id=owner_user_id)

    async def _resolve_s08_identity_rejection(self, msg: InboundMessage) -> _BoundIdentityRejection | None:
        """Verify the sender through the S08 identity chain, adopting its owner.

        The adapter's asserted identity is only a hint: a user-level binding
        (``im_user_bindings``) is authoritative, and the chat-scoped /
        workspace-less connection rows remain as fallbacks for deployments that
        never wrote a binding. An unresolved sender is rejected *before* any
        thread is created, so a group can never accumulate unowned threads that
        the web sidebar cannot attribute to anybody.
        """
        if self._connection_repo is None and self._binding_repo is None:
            return _BoundIdentityRejection(message=BOUND_IDENTITY_UNAVAILABLE_MESSAGE)

        tenant_key, bot_app_id = self._im_scope(msg)
        resolution = await resolve_im_identity(
            binding_repo=self._binding_repo,
            connection_repo=self._connection_repo,
            provider=msg.channel_name,
            external_user_id=msg.user_id,
            chat_id=msg.chat_id,
            tenant_key=tenant_key,
            bot_app_id=bot_app_id,
        )
        if not resolution.resolved:
            return _BoundIdentityRejection(message=IDENTITY_UNBOUND_MESSAGE)

        if msg.owner_user_id and msg.owner_user_id != resolution.owner_user_id:
            # Keep only the server-side values as outbound routing hints.
            return _BoundIdentityRejection(
                outbound_connection_id=resolution.connection_id,
                outbound_owner_user_id=resolution.owner_user_id,
            )

        msg.owner_user_id = resolution.owner_user_id
        if resolution.connection_id:
            msg.connection_id = resolution.connection_id
        return None

    async def _reject_unbound_channel_message(
        self,
        msg: InboundMessage,
        *,
        bound_identity_rejection: _BoundIdentityRejection,
    ) -> None:
        logger.info(
            "[Manager] rejecting unbound channel message: channel=%s, chat_id=%s",
            msg.channel_name,
            msg.chat_id,
        )
        outbound = OutboundMessage(
            channel_name=msg.channel_name,
            chat_id=msg.chat_id,
            thread_id="",
            text=("【本地 UAT】" if msg.channel_name == "feishu" and os.getenv("DEER_FLOW_WORK_DOCS_UAT_ENABLED") == "true" else "") + bound_identity_rejection.message,
            thread_ts=msg.thread_ts,
            connection_id=bound_identity_rejection.outbound_connection_id,
            owner_user_id=bound_identity_rejection.outbound_owner_user_id,
            metadata=_slim_metadata(msg.metadata),
        )
        await self.bus.publish_outbound(outbound)

    async def _lookup_thread_id(self, msg: InboundMessage) -> str | None:
        if msg.connection_id and self._connection_repo is not None:
            return await self._connection_repo.get_thread_id(
                msg.connection_id,
                msg.chat_id,
                msg.topic_id,
            )
        return self.store.get_thread_id(msg.channel_name, msg.chat_id, topic_id=msg.topic_id)

    async def _store_thread_id(self, msg: InboundMessage, thread_id: str) -> None:
        if msg.connection_id and msg.owner_user_id and self._connection_repo is not None:
            await self._connection_repo.set_thread_id(
                connection_id=msg.connection_id,
                owner_user_id=msg.owner_user_id,
                provider=msg.channel_name,
                external_conversation_id=msg.chat_id,
                external_topic_id=msg.topic_id,
                thread_id=thread_id,
            )
            return

        self.store.set_thread_id(
            msg.channel_name,
            msg.chat_id,
            thread_id,
            topic_id=msg.topic_id,
            user_id=msg.user_id,
        )

    async def _create_thread(self, client, msg: InboundMessage, *, store_mapping: bool = True) -> str:
        """Create through Gateway; recovery publishes the mapping separately."""
        metadata = _thread_channel_metadata(msg)
        owner_headers = _owner_headers(msg)
        # Some channels (notably GitHub) supply a deterministic preferred
        # thread id so a (repo, PR/issue number) always lands on the same
        # LangGraph thread, even after a store wipe. When absent, Gateway
        # mints a random id as before.
        meta = msg.metadata if isinstance(msg.metadata, dict) else {}
        preferred_thread_id = meta.get("preferred_thread_id")
        create_kwargs: dict[str, Any] = {"metadata": metadata}
        if isinstance(preferred_thread_id, str) and preferred_thread_id:
            create_kwargs["thread_id"] = preferred_thread_id
        if owner_headers:
            create_kwargs["headers"] = owner_headers
        try:
            thread = await client.threads.create(**create_kwargs)
        except ConflictError as exc:
            # True race: two webhook deliveries for the same (repo, number)
            # land within ms with the same preferred_thread_id. The Gateway
            # ``POST /threads`` route is idempotent on sequential reads (it
            # returns the existing record when present), so this branch only
            # fires for a real concurrent-create conflict that the underlying
            # store surfaced as 409.
            #
            # Narrow the recovery to ConflictError specifically: any other
            # exception (transient DB outage, network error, 5xx) used to
            # land here too and silently wrote ``preferred_thread_id`` into
            # the store, mapping subsequent webhooks to a thread that was
            # never created — every later run would 404 forever with no
            # retry path. Those non-conflict failures now propagate so the
            # caller fails the delivery cleanly.
            if not (isinstance(preferred_thread_id, str) and preferred_thread_id):
                # Without a preferred id we cannot deterministically recover.
                raise
            # Verify the racing-write target actually exists before we
            # cache the mapping. If ConflictError fires but threads.get
            # also rejects, the store underneath is in an inconsistent
            # state and we surface the failure rather than poisoning the
            # mapping for every future delivery on this issue/PR.
            try:
                get_kwargs: dict[str, Any] = {}
                if owner_headers:
                    get_kwargs["headers"] = owner_headers
                await client.threads.get(preferred_thread_id, **get_kwargs)
            except Exception as verify_exc:
                logger.warning(
                    "[Manager] threads.create raced on preferred_thread_id=%s (%s) but follow-up threads.get failed (%s); not caching the mapping",
                    preferred_thread_id,
                    exc.__class__.__name__,
                    verify_exc.__class__.__name__,
                )
                raise
            logger.info(
                "[Manager] threads.create raced on preferred_thread_id=%s (%s); reusing the deterministic id",
                preferred_thread_id,
                exc.__class__.__name__,
            )
            if store_mapping:
                await self._store_thread_id(msg, preferred_thread_id)
            await self._register_thread(msg, preferred_thread_id)
            return preferred_thread_id
        thread_id = thread["thread_id"]
        if store_mapping:
            await self._store_thread_id(msg, thread_id)
        title = thread.get("title") if isinstance(thread, dict) else None
        await self._register_thread(msg, thread_id, title=str(title) if title else None)
        logger.info("[Manager] new thread created through Gateway: thread_id=%s for chat_id=%s topic_id=%s", thread_id, msg.chat_id, msg.topic_id)
        return thread_id

    async def _get_or_create_thread(self, client, msg: InboundMessage) -> tuple[str, bool]:
        """Return ``(thread_id, created)``, creating a thread only if needed.

        Each inbound message is dispatched on its own task, so two messages that
        arrive close together for the same chat would both look up a missing
        thread and then both create one — the second store silently overwrites
        the first, orphaning a Gateway thread and splitting the conversation.
        Serialize the create path per conversation and re-check inside the lock
        so only the first message creates a thread and the rest reuse it.
        """
        from deerflow.persistence.channel_connections.recovery import ChannelThreadBinding, ChannelThreadRecovery
        from deerflow.persistence.channel_connections.sql import ChannelConnectionRepository

        recovery = None
        meta = msg.metadata if isinstance(msg.metadata, dict) else {}
        # Legacy file mappings and deterministic webhook threads retain their
        # existing lifecycle. Only a fully identified SQL binding can recover.
        if msg.connection_id and msg.owner_user_id and not meta.get("preferred_thread_id") and isinstance(self._connection_repo, ChannelConnectionRepository):
            recovery = ChannelThreadRecovery(
                self._connection_repo.session_factory,
                ChannelThreadBinding(msg.connection_id, msg.owner_user_id, msg.channel_name, msg.user_id, msg.workspace_id or "", msg.chat_id, msg.topic_id or ""),
            )
        thread_id = await self._lookup_thread_id(msg)
        recovering_missing = False
        if thread_id:
            if recovery is None:
                return thread_id, False
            current = await recovery.inspect()
            if not current.missing:
                return current.thread_id, False
            recovering_missing = True

        key = (msg.channel_name, msg.chat_id, msg.topic_id)
        lock = self._thread_create_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                if recovering_missing:
                    # A revoked/removed binding must not fall through to the
                    # ordinary create path while we were waiting on this lock.
                    return await recovery.recover(lambda: self._create_thread(client, msg, store_mapping=False))
                # A concurrent message for the same chat may have created the
                # thread while we were waiting on the lock.
                thread_id = await self._lookup_thread_id(msg)
                if thread_id:
                    if recovery is not None:
                        return await recovery.recover(lambda: self._create_thread(client, msg, store_mapping=False))
                    return thread_id, False
                return await self._create_thread(client, msg), True
        finally:
            # Once the thread is stored, later messages short-circuit on the
            # lookup above and never reach this lock, so it's safe to drop the
            # entry and keep the registry bounded to in-flight conversations.
            self._thread_create_locks.pop(key, None)

    async def _update_thread_channel_metadata(self, client, msg: InboundMessage, thread_id: str) -> None:
        """Best-effort source metadata backfill for existing IM-created threads."""
        # The metadata (provider/chat/topic) is constant for a thread, so one
        # successful backfill per manager lifetime is enough — skip the
        # redundant PATCH on every subsequent inbound message.
        if thread_id in self._channel_metadata_synced:
            return
        update_kwargs: dict[str, Any] = {"metadata": _thread_channel_metadata(msg)}
        if owner_headers := _owner_headers(msg):
            update_kwargs["headers"] = owner_headers
        try:
            await client.threads.update(thread_id, **update_kwargs)
        except Exception:
            logger.debug("[Manager] failed to update channel metadata for thread_id=%s", thread_id, exc_info=True)
            return
        if len(self._channel_metadata_synced) > 4096:
            self._channel_metadata_synced.clear()
        self._channel_metadata_synced.add(thread_id)

    async def _handle_chat(
        self,
        msg: InboundMessage,
        extra_context: dict[str, Any] | None = None,
        *,
        bound_identity_checked: bool = False,
    ) -> None:
        # Normal entry paths already run the bound-identity check in
        # _handle_message() or _handle_command(). Keep this default False so
        # direct callers and future internal paths still fail closed.
        bound_identity_rejection = None if bound_identity_checked else await self._get_bound_identity_rejection(msg)
        if bound_identity_rejection is not None:
            await self._reject_unbound_channel_message(msg, bound_identity_rejection=bound_identity_rejection)
            return

        if msg.channel_name == "feishu":
            from app.integrations.work.config import WorkSettings
            from app.integrations.work.service import active_work_service, handle_unavailable_work

            extra_context = dict(extra_context or {})
            extra_context.pop("work_capabilities", None)
            try:
                work_service = await active_work_service(self)
            except Exception:
                # Initialization/configuration errors may contain credentials.
                # This fallback classifies intent only; it never executes Work.
                logger.warning("Work integration unavailable before dispatch")
                work_service = None
            if work_service is not None:
                if await work_service.handle(msg):
                    return
            else:
                if self._work_unavailable_semaphore is None:
                    self._work_unavailable_semaphore = asyncio.Semaphore(2)
                if await handle_unavailable_work(msg, send=self.bus.publish_outbound, semaphore=self._work_unavailable_semaphore):
                    return
            try:
                work_enabled = WorkSettings.from_env().enabled
            except ValueError:
                work_enabled = False
            if work_enabled:
                extra_context["work_capabilities"] = {"available": bool(work_service)}

        if self._routing_v2_active(msg):
            await self._handle_routed_chat(msg, extra_context=extra_context)
            return

        client = self._get_client()
        storage_user_id = _channel_storage_user_id(msg)

        # Look up the existing DeerFlow thread, creating one if this is the
        # first message for the chat. topic_id may be None (e.g. Telegram
        # private chats) — the store handles this by using the "channel:chat_id"
        # key without a topic suffix.
        thread_id, created = await self._get_or_create_thread(client, msg)
        if not created:
            logger.info("[Manager] reusing thread: thread_id=%s for topic_id=%s", thread_id, msg.topic_id)
        await self._run_chat_on_thread(
            client,
            msg,
            thread_id,
            created=created,
            extra_context=extra_context,
            storage_user_id=storage_user_id,
        )

    async def _run_chat_on_thread(
        self,
        client,
        msg: InboundMessage,
        thread_id: str,
        *,
        created: bool,
        extra_context: dict[str, Any] | None = None,
        storage_user_id: str | None = None,
    ) -> None:
        """Run one chat turn on an already resolved thread.

        Serialized per thread so two messages for the same conversation never
        interleave; different threads still run concurrently.
        """
        if storage_user_id is None:
            storage_user_id = _channel_storage_user_id(msg)
        if not created:
            await self._update_thread_channel_metadata(client, msg, thread_id)

        serial_state, queued = self._begin_serialized_thread_run(
            channel_name=msg.channel_name,
            thread_id=thread_id,
        )
        serial_lock_acquired = False
        try:
            if queued:
                await self._publish_progress_update(
                    msg,
                    thread_id,
                    "Queued behind another request in this conversation. I’ll start working on this as soon as it finishes.",
                )
            if serial_state is not None:
                await serial_state.lock.acquire()
                serial_lock_acquired = True
            if queued:
                await self._publish_progress_update(msg, thread_id, "thinking...")
            await self._handle_chat_on_thread(
                client,
                msg,
                thread_id,
                extra_context=extra_context,
                storage_user_id=storage_user_id,
            )
        finally:
            self._finish_serialized_thread_run(
                channel_name=msg.channel_name,
                thread_id=thread_id,
                state=serial_state,
                lock_acquired=serial_lock_acquired,
            )

    async def _handle_chat_on_thread(
        self,
        client,
        msg: InboundMessage,
        thread_id: str,
        *,
        extra_context: dict[str, Any] | None = None,
        storage_user_id: str | None = None,
    ) -> None:
        if storage_user_id is None:
            storage_user_id = _channel_storage_user_id(msg)

        assistant_id, run_config, run_context = self._resolve_run_params(msg, thread_id)

        # Apply per-channel policy: credentials provider (e.g. GitHub
        # installation-token mint) and the non-interactive flag for
        # webhook channels. Driven by CHANNEL_RUN_POLICY so each new
        # webhook channel is a one-row registration, not a fresh
        # if-branch here.
        policy = await self._apply_channel_policy(msg, run_context)

        # If the inbound message contains file attachments, let the channel
        # materialize (download) them and update msg.text to include sandbox file paths.
        # This enables downstream models to access user-uploaded files by path.
        # Channels that do not support file download will simply return the original message.
        if msg.files:
            from .service import get_channel_service

            service = get_channel_service()
            channel = service.get_channel(msg.channel_name) if service else None
            logger.info("[Manager] preparing receive file context for %d attachments", len(msg.files))
            msg = await channel.receive_file(msg, thread_id, user_id=storage_user_id) if channel else msg
        if extra_context:
            run_context.update(extra_context)

        original_text = msg.text
        uploaded = await _ingest_inbound_files(thread_id, msg, user_id=storage_user_id)
        if uploaded:
            msg.text = f"{_format_uploaded_files_block(uploaded)}\n\n{msg.text}".strip()
        human_message = _human_input_message(msg.text, original_content=original_text)

        if self._channel_supports_streaming(msg.channel_name):
            await self._handle_streaming_chat(
                client,
                msg,
                thread_id,
                assistant_id,
                run_config,
                run_context,
                human_message,
                storage_user_id=storage_user_id,
            )
            return

        run_kwargs: dict[str, Any] = {
            "input": {"messages": [human_message]},
            "config": run_config,
            "context": run_context,
            "multitask_strategy": "reject",
        }
        if owner_headers := _owner_headers(msg):
            run_kwargs["headers"] = owner_headers

        if policy is not None and policy.fire_and_forget:
            # Fire-and-forget path: the channel does its own outbound
            # during the run (GitHub agents post to the issue/PR via the
            # ``gh`` CLI from inside the sandbox), so there is nothing
            # for the manager to ferry back. Use ``runs.create`` — a
            # short POST that returns once the run is ``pending`` — to
            # avoid the SDK's 300s ``httpx.ReadTimeout`` on legitimately
            # long autonomous runs, and the false "internal error"
            # outbound that follows when it fires. ``ConflictError`` is
            # still raised synchronously by ``start_run`` if a previous
            # run on this thread is still active, so the existing
            # busy-thread path is preserved.
            logger.info(
                "[Manager] invoking runs.create(thread_id=%s, text_len=%d) [fire_and_forget]",
                thread_id,
                len(msg.text or ""),
            )
            try:
                await client.runs.create(thread_id, assistant_id, **run_kwargs)
            except Exception as exc:
                if _is_thread_busy_error(exc):
                    logger.warning("[Manager] thread busy (concurrent run rejected): thread_id=%s", thread_id)
                    await self._send_error(msg, THREAD_BUSY_MESSAGE)
                    return
                raise
            return

        logger.info("[Manager] invoking runs.wait(thread_id=%s, text_len=%d)", thread_id, len(msg.text or ""))
        try:
            result = await client.runs.wait(
                thread_id,
                assistant_id,
                **run_kwargs,
            )
        except Exception as exc:
            if _is_thread_busy_error(exc):
                logger.warning("[Manager] thread busy (concurrent run rejected): thread_id=%s", thread_id)
                await self._send_error(msg, THREAD_BUSY_MESSAGE)
                return
            else:
                raise

        response_text = _extract_response_text(result)
        pending_clarification = _has_current_turn_clarification(result)
        artifacts = _extract_artifacts(result)

        logger.info(
            "[Manager] agent response received: thread_id=%s, response_len=%d, artifacts=%d",
            thread_id,
            len(response_text) if response_text else 0,
            len(artifacts),
        )

        # Reuse the storage owner cached at the top of _handle_chat so uploads and
        # artifact delivery always resolve to the same bucket, even if a future
        # channel.receive_file returns a rewritten InboundMessage.
        response_text, attachments = _prepare_artifact_delivery(thread_id, response_text, artifacts, user_id=storage_user_id)

        if not response_text:
            if attachments:
                response_text = _format_artifact_text([a.virtual_path for a in attachments])
            else:
                response_text = "(No response from agent)"

        outbound = OutboundMessage(
            channel_name=msg.channel_name,
            chat_id=msg.chat_id,
            thread_id=thread_id,
            text=response_text,
            artifacts=artifacts,
            attachments=attachments,
            thread_ts=msg.thread_ts,
            connection_id=msg.connection_id,
            owner_user_id=msg.owner_user_id,
            metadata=_response_metadata(msg.metadata, pending_clarification=pending_clarification),
        )
        logger.info("[Manager] publishing outbound message to bus: channel=%s, chat_id=%s", msg.channel_name, msg.chat_id)
        await self.bus.publish_outbound(outbound)

    async def _handle_streaming_chat(
        self,
        client,
        msg: InboundMessage,
        thread_id: str,
        assistant_id: str,
        run_config: dict[str, Any],
        run_context: dict[str, Any],
        human_message: dict[str, Any],
        storage_user_id: str | None = None,
    ) -> None:
        logger.info("[Manager] invoking runs.stream(thread_id=%s, text_len=%d)", thread_id, len(msg.text or ""))

        last_values: dict[str, Any] | list | None = None
        streamed_buffers: dict[str, str] = {}
        current_message_id: str | None = None
        latest_text = ""
        last_published_text = ""
        last_published_len = 0
        last_publish_at = 0.0
        stream_error: BaseException | None = None
        stream_kwargs: dict[str, Any] = {
            "input": {"messages": [human_message]},
            "config": run_config,
            "context": run_context,
            "stream_mode": list(STREAM_MODES),
            "multitask_strategy": "reject",
        }
        if owner_headers := _owner_headers(msg):
            stream_kwargs["headers"] = owner_headers

        try:
            async for chunk in client.runs.stream(
                thread_id,
                assistant_id,
                **stream_kwargs,
            ):
                event = getattr(chunk, "event", "")
                data = getattr(chunk, "data", None)

                if event in MESSAGE_STREAM_EVENTS:
                    accumulated_text, current_message_id = _accumulate_stream_text(streamed_buffers, current_message_id, data)
                    if accumulated_text:
                        latest_text = accumulated_text
                elif event == "values" and isinstance(data, (dict, list)):
                    last_values = data
                    # Clarification text is only in the values snapshot;
                    # publish it so the user sees the question mid-stream.
                    if _has_current_turn_clarification(data):
                        clarification_text = _extract_response_text(data)
                        if clarification_text and clarification_text != latest_text:
                            latest_text = clarification_text

                if not latest_text or latest_text == last_published_text:
                    continue

                now = time.monotonic()
                new_chars = len(latest_text) - last_published_len
                # OR logic: flush when interval elapsed OR enough chars accumulated
                if last_published_text:
                    if now - last_publish_at < STREAM_UPDATE_MIN_INTERVAL_SECONDS and new_chars < STREAM_UPDATE_MIN_CHARS:
                        continue

                display_text = latest_text + " ▉"
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel_name=msg.channel_name,
                        chat_id=msg.chat_id,
                        thread_id=thread_id,
                        text=display_text,
                        is_final=False,
                        thread_ts=msg.thread_ts,
                        connection_id=msg.connection_id,
                        owner_user_id=msg.owner_user_id,
                        metadata=_response_metadata(msg.metadata),
                    )
                )
                last_published_text = latest_text
                last_published_len = len(latest_text)
                last_publish_at = now
        except Exception as exc:
            stream_error = exc
            if _is_thread_busy_error(exc):
                logger.warning("[Manager] thread busy (concurrent run rejected): thread_id=%s", thread_id)
            else:
                logger.exception("[Manager] streaming error: thread_id=%s", thread_id)
        finally:
            result = last_values if last_values is not None else {"messages": [{"type": "ai", "content": latest_text}]}
            response_text = _extract_response_text(result)
            pending_clarification = _has_current_turn_clarification(result)
            artifacts = _extract_artifacts(result)
            # Reuse the storage owner resolved by _handle_chat so artifact delivery
            # matches the upload bucket and we avoid re-running _safe_user_id_for_run
            # (and its possible filesystem touch) on the streaming-error path.
            response_text, attachments = _prepare_artifact_delivery(thread_id, response_text, artifacts, user_id=storage_user_id)

            if not response_text:
                if attachments:
                    response_text = _format_artifact_text([attachment.virtual_path for attachment in attachments])
                elif stream_error:
                    if _is_thread_busy_error(stream_error):
                        response_text = THREAD_BUSY_MESSAGE
                    else:
                        response_text = "An error occurred while processing your request. Please try again."
                else:
                    response_text = latest_text or "(No response from agent)"

            logger.info(
                "[Manager] streaming response completed: thread_id=%s, response_len=%d, artifacts=%d, error=%s",
                thread_id,
                len(response_text),
                len(artifacts),
                stream_error,
            )
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel_name=msg.channel_name,
                    chat_id=msg.chat_id,
                    thread_id=thread_id,
                    text=response_text,
                    artifacts=artifacts,
                    attachments=attachments,
                    is_final=True,
                    thread_ts=msg.thread_ts,
                    connection_id=msg.connection_id,
                    owner_user_id=msg.owner_user_id,
                    metadata=_response_metadata(msg.metadata, pending_clarification=pending_clarification),
                )
            )

    # -- command handling --------------------------------------------------

    async def _handle_command(self, msg: InboundMessage) -> None:
        # Commands are the other run-creation entry point besides chat: /new
        # calls _create_thread() directly, and /bootstrap routes into
        # _handle_chat(). Apply the same bound-identity admission boundary here
        # so unbound platform users cannot create unowned threads/checkpoints or
        # query Gateway state via commands. Provider-level binding flows
        # (/connect <code>, /start <code>) are consumed by the provider adapter
        # before the message reaches the manager, so they are unaffected.
        bound_identity_rejection = await self._get_bound_identity_rejection(msg)
        if bound_identity_rejection is not None:
            await self._reject_unbound_channel_message(msg, bound_identity_rejection=bound_identity_rejection)
            return

        raw_text = msg.text
        text = raw_text.strip()
        parts = text.split(maxsplit=1)
        reply: str | None = None
        if not parts:
            command = None
            reply = _unknown_command_reply()
        else:
            command = parts[0].lower().removeprefix("/")

        if reply is None and not raw_text.startswith("/"):
            reply = _unknown_command_reply(command)

        if reply is None and command == "bootstrap":
            from dataclasses import replace as _dc_replace

            chat_text = parts[1] if len(parts) > 1 else "Initialize workspace"
            chat_msg = _dc_replace(msg, text=chat_text, msg_type=InboundMessageType.CHAT)
            await self._handle_chat(chat_msg, extra_context={"is_bootstrap": True}, bound_identity_checked=True)
            return

        if reply is None and command == "new":
            # Create a new thread through Gateway
            if self._routing_v2_active(msg):
                reply = await self._handle_new_conversation_v2(msg)
                switched_thread_id = await self._current_thread_id(msg)
                if switched_thread_id:
                    await self._record_inbound_link(msg, switched_thread_id, route_reason=RouteReason.NORMAL.value)
            else:
                client = self._get_client()
                await self._create_thread(client, msg)
                reply = "New conversation started."
        elif reply is None and command == "status":
            thread_id = await self._current_thread_id(msg)
            reply = f"Active thread: {thread_id}" if thread_id else "No active conversation."
        elif reply is None and command == "models":
            reply = await self._fetch_gateway("/api/models", "models", msg=msg)
        elif reply is None and command == "memory":
            reply = await self._fetch_gateway("/api/memory", "memory", msg=msg)
        elif reply is None and command == "goal":
            reply = await self._handle_goal_command(msg, parts[1] if len(parts) > 1 else "")
            if reply is None:
                return
        elif reply is None and command == "help":
            reply = (
                "Available commands:\n"
                "/bootstrap — Start a bootstrap session (enables agent setup)\n"
                "/goal [condition|clear] — Set, show, or clear an active goal\n"
                "/new — Start a new conversation\n"
                "/status — Show current thread info\n"
                "/models — List available models\n"
                "/memory — Show memory status\n"
                "/<skill-name> <task> — Activate an enabled skill for one turn\n"
                "/help — Show this help"
            )
        elif reply is None:
            slash_resolution = await asyncio.to_thread(
                lambda: _resolve_slash_skill_command(
                    raw_text,
                    self._resolve_available_skill_names(msg),
                    self._get_skill_storage,
                )
            )
            if slash_resolution and slash_resolution.failure_message:
                reply = slash_resolution.failure_message
            elif slash_resolution and slash_resolution.route_to_chat:
                from dataclasses import replace as _dc_replace

                chat_msg = _dc_replace(msg, msg_type=InboundMessageType.CHAT)
                await self._handle_chat(chat_msg, bound_identity_checked=True)
                return
            else:
                reply = _unknown_command_reply(command)

        outbound = OutboundMessage(
            channel_name=msg.channel_name,
            chat_id=msg.chat_id,
            thread_id=await self._current_thread_id(msg) or "",
            text=reply,
            thread_ts=msg.thread_ts,
            connection_id=msg.connection_id,
            owner_user_id=msg.owner_user_id,
            metadata=_slim_metadata(msg.metadata),
        )
        await self.bus.publish_outbound(outbound)

    async def _goal_request(
        self,
        method: str,
        thread_id: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient() as http:
            request = getattr(http, method.lower())
            kwargs: dict[str, Any] = {"timeout": 10, "headers": headers}
            if json is not None:
                kwargs["json"] = json
            response = await request(f"{self._gateway_url}/api/threads/{quote(thread_id, safe='')}/goal", **kwargs)
            response.raise_for_status()
            return response.json() or {}

    async def _handle_goal_command(self, msg: InboundMessage, args: str) -> str | None:
        command = parse_goal_command(args)
        thread_id = await self._lookup_thread_id(msg)
        headers = _owner_headers(msg) or create_internal_auth_headers()

        if command.kind == "status":
            if not thread_id:
                return "No active goal."
            try:
                goal = (await self._goal_request("get", thread_id, headers=headers)).get("goal")
            except Exception:
                logger.exception("Failed to fetch goal from gateway")
                return "Failed to fetch goal information."
            return f"Goal: {goal.get('objective')}" if goal else "No active goal."

        if command.kind == "clear":
            if not thread_id:
                return "Goal cleared."
            try:
                await self._goal_request("delete", thread_id, headers=headers)
            except Exception:
                logger.exception("Failed to clear goal through gateway")
                return "Failed to clear goal."
            return "Goal cleared."

        if not thread_id:
            thread_id = await self._create_thread(self._get_client(), msg)

        try:
            await self._goal_request("put", thread_id, headers=headers, json={"objective": command.objective})
        except Exception:
            logger.exception("Failed to set goal through gateway")
            return "Failed to set goal."

        from dataclasses import replace as _dc_replace

        chat_msg = _dc_replace(msg, text=command.objective, msg_type=InboundMessageType.CHAT)
        await self._handle_chat(chat_msg, bound_identity_checked=True)
        return None

    async def _fetch_gateway(self, path: str, kind: str, *, msg: InboundMessage | None = None) -> str:
        """Fetch data from the Gateway API for command responses."""
        import httpx

        try:
            headers = _owner_headers(msg) if msg is not None else None
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    f"{self._gateway_url}{path}",
                    timeout=10,
                    headers=headers or create_internal_auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("Failed to fetch %s from gateway", kind)
            return f"Failed to fetch {kind} information."

        if kind == "models":
            names = [m["name"] for m in data.get("models", [])]
            return ("Available models:\n" + "\n".join(f"• {n}" for n in names)) if names else "No models configured."
        elif kind == "memory":
            facts = data.get("facts", [])
            return f"Memory contains {len(facts)} fact(s)."
        return str(data)

    # -- error helper ------------------------------------------------------

    async def _send_error(self, msg: InboundMessage, error_text: str) -> None:
        outbound = OutboundMessage(
            channel_name=msg.channel_name,
            chat_id=msg.chat_id,
            thread_id=await self._current_thread_id(msg) or "",
            text=error_text,
            thread_ts=msg.thread_ts,
            connection_id=msg.connection_id,
            owner_user_id=msg.owner_user_id,
            metadata=_slim_metadata(msg.metadata),
        )
        await self.bus.publish_outbound(outbound)
