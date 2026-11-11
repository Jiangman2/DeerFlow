"""V2 routing decisions for IM channels (reply-based thread restore).

This module is deliberately **pure**: it takes the facts a channel adapter
already resolved (the quoted message's sender, the persisted message link) and
returns a decision. It never performs I/O, which keeps the routing contract
unit-testable and prevents the "silently fall back to the current thread"
failure mode that ADR-04 forbids.

The contract implemented here (see
``1-开发文档/飞书 × DeerFlow 企业级分群会话路由系统.md``):

============================  ===============================================
Situation                     Decision
============================  ===============================================
No reply reference            CURRENT (use the session pointer)
Reply to a normal member      NORMAL_QUOTE (quote text, current thread)
Reply to own earlier message  OWN_QUESTION_RESTORE (that thread, pointer不移动)
Reply to own bot reply        SELF_REPLY_RESTORE (that thread, pointer不移动)
Reply to someone else's bot   CROSS_OWNER_QUOTE (quote text, current thread)
message (same chat)
Reply to a bot message from   CROSS_CHAT_REFERENCE -> REJECT (no run)
another chat
Reply to an app that is not   REPLY_SOURCE_UNKNOWN -> REJECT (no run)
this bot (other bot / 未识别)
Reply to this bot's message   REPLY_MAPPING_MISSING -> FAIL_CLOSED (no run)
but the link is gone
Quoted message unreadable /   REPLY_SOURCE_UNKNOWN -> FAIL_CLOSED (no run)
sender identity unavailable
============================  ===============================================
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deerflow.persistence.im_routing import MessageLink


class RouteAction(StrEnum):
    """What the manager should do with an inbound message."""

    CURRENT = "current"
    RESTORE = "restore"
    QUOTE_ONLY = "quote_only"
    FAIL_CLOSED = "fail_closed"
    REJECT = "reject"


class RouteReason(StrEnum):
    """Stable audit reason / error code for a routing decision."""

    NORMAL = "NORMAL"
    NORMAL_QUOTE = "NORMAL_QUOTE"
    SELF_REPLY_RESTORE = "SELF_REPLY_RESTORE"
    OWN_QUESTION_RESTORE = "OWN_QUESTION_RESTORE"
    CROSS_OWNER_QUOTE = "CROSS_OWNER_QUOTE"
    REPLY_MAPPING_MISSING = "REPLY_MAPPING_MISSING"
    THREAD_DELETED = "THREAD_DELETED"
    REPLY_SOURCE_UNKNOWN = "REPLY_SOURCE_UNKNOWN"
    CROSS_CHAT_REFERENCE = "CROSS_CHAT_REFERENCE"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    THREAD_OWNERSHIP_MISMATCH = "THREAD_OWNERSHIP_MISMATCH"


# User-visible failure text. Deliberately explicit about *not* answering in the
# current conversation: a silent fallback would make the user believe the
# follow-up question refers to the old thread's context.
_FAILURE_MESSAGES: dict[str, str] = {
    RouteReason.REPLY_MAPPING_MISSING.value: ("无法恢复原会话：这条引用来自本机器人，但没有找到对应的会话记录。请直接 @我 重新提问，或发送 /new 开始新会话。"),
    RouteReason.THREAD_DELETED.value: ("无法恢复原会话：原会话已被删除或当前无权访问。请直接 @我 重新提问，或发送 /new 开始新会话。"),
    RouteReason.REPLY_SOURCE_UNKNOWN.value: ("无法处理这次引用：无法确认被引用的消息属于本机器人。请直接 @我 重新提问，不要引用其它机器人的消息。"),
    RouteReason.CROSS_CHAT_REFERENCE.value: ("无法恢复原会话：被引用的消息来自其他会话。请在我所在的这个会话里 @我 重新提问。"),
    RouteReason.OWNER_MISMATCH.value: "无法恢复原会话：这条引用不属于当前账号。请直接 @我 重新提问。",
    RouteReason.THREAD_OWNERSHIP_MISMATCH.value: ("无法恢复原会话：原会话不属于当前账号。请直接 @我 重新提问，或发送 /new 开始新会话。"),
    "ROUTE_DISABLED": "会话路由暂不可用，请稍后重试。",
}

DEFAULT_FAILURE_MESSAGE = "无法恢复原会话。请直接 @我 重新提问，或发送 /new 开始新会话。"


def failure_message(code: str | None) -> str:
    """Return the user-facing text for a failure code (never leaks internals)."""
    if not code:
        return DEFAULT_FAILURE_MESSAGE
    return _FAILURE_MESSAGES.get(code, DEFAULT_FAILURE_MESSAGE)


@dataclass(frozen=True)
class ReplyRef:
    """The quoted message a user is replying to, as seen by the adapter.

    ``sender_type`` is ``"user"`` / ``"app"`` / ``"unknown"``. ``from_self_bot``
    is ``True`` only when the adapter could prove the quoted message was sent
    by *this* bot (Feishu: ``sender.id.open_id == bot_open_id``); ``None``
    means "could not verify", which must fail closed.
    """

    message_id: str
    sender_type: str = "unknown"
    sender_id: str = ""
    from_self_bot: bool | None = None


@dataclass(frozen=True)
class RouteDecision:
    """Result of :func:`resolve_route`."""

    action: RouteAction
    reason: RouteReason
    thread_id: str | None = None
    error_code: str | None = None
    quote_text: str | None = None

    @property
    def is_restore(self) -> bool:
        return self.action is RouteAction.RESTORE

    @property
    def blocks_run(self) -> bool:
        return self.action in (RouteAction.FAIL_CLOSED, RouteAction.REJECT)


def resolve_route(
    *,
    chat_id: str,
    owner_user_id: str | None,
    reply: ReplyRef | None,
    link: MessageLink | None,
    quoted_text: str | None = None,
    registry_owner_ok: bool | None = None,
) -> RouteDecision:
    """Decide how an inbound message should be routed.

    Args:
        chat_id: The conversation the message arrived in.
        owner_user_id: DeerFlow owner of the *sender* (None when unbound).
        reply: The quoted message, or None when the message is not a reply.
        link: Persisted link for ``reply.message_id``, if one exists.
        quoted_text: Public text of the quoted message, when the adapter could
            read it. Only used for quote-only decisions.
        registry_owner_ok: Whether the archive registry confirms that the
            sender owns ``link.thread_id``. ``None`` means "not checked" (S08
            disabled or no registry row); ``False`` turns a would-be restore
            into an explicit failure so a thread can never be restored by a
            different account.
    """

    def _restore(decision: RouteDecision) -> RouteDecision:
        if registry_owner_ok is False:
            return RouteDecision(
                action=RouteAction.FAIL_CLOSED,
                reason=RouteReason.THREAD_OWNERSHIP_MISMATCH,
                error_code=RouteReason.THREAD_OWNERSHIP_MISMATCH.value,
            )
        return decision
    if reply is None:
        return RouteDecision(action=RouteAction.CURRENT, reason=RouteReason.NORMAL)

    # ---- quoted message sender classification -------------------------
    if reply.sender_type == "user":
        # A member's own earlier message: restore only when we have an
        # inbound link that provably belongs to the sender.
        if link is not None and link.chat_id == chat_id and link.owner_user_id == (owner_user_id or ""):
            return _restore(
                RouteDecision(
                    action=RouteAction.RESTORE,
                    reason=RouteReason.OWN_QUESTION_RESTORE,
                    thread_id=link.thread_id,
                )
            )
        if link is not None and link.chat_id != chat_id:
            return RouteDecision(
                action=RouteAction.REJECT,
                reason=RouteReason.CROSS_CHAT_REFERENCE,
                error_code=RouteReason.CROSS_CHAT_REFERENCE.value,
            )
        return RouteDecision(
            action=RouteAction.QUOTE_ONLY,
            reason=RouteReason.NORMAL_QUOTE,
            quote_text=quoted_text,
        )

    if reply.sender_type != "app":
        # Unreadable / unknown sender: an unverifiable history reference must
        # fail closed rather than quietly answering in the current thread.
        return RouteDecision(
            action=RouteAction.FAIL_CLOSED,
            reason=RouteReason.REPLY_SOURCE_UNKNOWN,
            error_code=RouteReason.REPLY_SOURCE_UNKNOWN.value,
        )

    if reply.from_self_bot is None:
        return RouteDecision(
            action=RouteAction.FAIL_CLOSED,
            reason=RouteReason.REPLY_SOURCE_UNKNOWN,
            error_code=RouteReason.REPLY_SOURCE_UNKNOWN.value,
        )

    if reply.from_self_bot is False:
        # Another app/bot. Cross-bot references are never restored and never
        # answered (the other bot may be speaking for a different agent).
        return RouteDecision(
            action=RouteAction.REJECT,
            reason=RouteReason.REPLY_SOURCE_UNKNOWN,
            error_code=RouteReason.REPLY_SOURCE_UNKNOWN.value,
        )

    # ---- this bot's own message: the link must exist -------------------
    if link is None:
        return RouteDecision(
            action=RouteAction.FAIL_CLOSED,
            reason=RouteReason.REPLY_MAPPING_MISSING,
            error_code=RouteReason.REPLY_MAPPING_MISSING.value,
        )

    if link.chat_id != chat_id:
        return RouteDecision(
            action=RouteAction.REJECT,
            reason=RouteReason.CROSS_CHAT_REFERENCE,
            error_code=RouteReason.CROSS_CHAT_REFERENCE.value,
        )

    if link.owner_user_id != (owner_user_id or ""):
        # Colleague replying to another colleague's answer: never restore the
        # other owner's thread, but it is safe to feed the public quote text
        # into the sender's *current* thread.
        return RouteDecision(
            action=RouteAction.QUOTE_ONLY,
            reason=RouteReason.CROSS_OWNER_QUOTE,
            quote_text=quoted_text,
        )

    return _restore(
        RouteDecision(
            action=RouteAction.RESTORE,
            reason=RouteReason.SELF_REPLY_RESTORE,
            thread_id=link.thread_id,
        )
    )


def quote_prefix(*, cross_owner: bool) -> str:
    """Prefix used when quote text is injected into the current thread."""
    return "[引用] 群里另一位同事的消息" if cross_owner else "[引用] 先前的消息"


def apply_quote(text: str, quoted: str | None, *, cross_owner: bool) -> str:
    """Prepend quoted context, mirroring the legacy adapter behaviour."""
    if not quoted:
        return text
    return f"{quote_prefix(cross_owner=cross_owner)}：{quoted}\n\n{text}"
