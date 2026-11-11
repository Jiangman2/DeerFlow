"""Decision table for the V2 reply router (pure, no I/O).

The table is the executable form of ADR-01..ADR-04:

* a plain message follows the session pointer (``CURRENT``);
* a reply to *my own* earlier bot answer or question restores that thread
  without moving the pointer (``RESTORE``);
* a reply to somebody else's answer may only quote the public text into the
  sender's current thread (``QUOTE_ONLY``);
* anything that cannot be proven fails closed or is rejected, and never
  silently degrades to the current thread.
"""

from __future__ import annotations

import pytest

from app.channels.routing_v2 import (
    DEFAULT_FAILURE_MESSAGE,
    ReplyRef,
    RouteAction,
    RouteReason,
    apply_quote,
    failure_message,
    resolve_route,
)
from deerflow.persistence.im_routing import MessageLink

CHAT = "chat-a"
OWNER = "alice"


def _link(*, chat_id: str = CHAT, owner: str = OWNER, thread_id: str = "thread-old", message_id: str = "om_bot", direction: str = "outbound", message_kind: str = "card") -> MessageLink:
    return MessageLink(
        provider="feishu",
        message_id=message_id,
        chat_id=chat_id,
        owner_user_id=owner,
        thread_id=thread_id,
        direction=direction,
        message_kind=message_kind,
        connection_id="conn-1",
        sender_external_id="ou_bot",
    )


def test_no_reply_follows_the_pointer():
    decision = resolve_route(chat_id=CHAT, owner_user_id=OWNER, reply=None, link=None)
    assert decision.action is RouteAction.CURRENT
    assert decision.reason is RouteReason.NORMAL
    assert decision.blocks_run is False


def test_reply_to_own_bot_answer_restores_that_thread():
    decision = resolve_route(
        chat_id=CHAT,
        owner_user_id=OWNER,
        reply=ReplyRef(message_id="om_bot", sender_type="app", sender_id="ou_bot", from_self_bot=True),
        link=_link(),
    )
    assert decision.action is RouteAction.RESTORE
    assert decision.reason is RouteReason.SELF_REPLY_RESTORE
    assert decision.thread_id == "thread-old"
    assert decision.is_restore is True


def test_reply_to_own_earlier_question_restores_that_thread():
    decision = resolve_route(
        chat_id=CHAT,
        owner_user_id=OWNER,
        reply=ReplyRef(message_id="om_me", sender_type="user", sender_id="ou_alice"),
        link=_link(message_id="om_me", direction="inbound", message_kind="message"),
    )
    assert decision.action is RouteAction.RESTORE
    assert decision.reason is RouteReason.OWN_QUESTION_RESTORE
    assert decision.thread_id == "thread-old"


def test_reply_to_own_bot_answer_without_link_fails_closed():
    decision = resolve_route(
        chat_id=CHAT,
        owner_user_id=OWNER,
        reply=ReplyRef(message_id="om_bot", sender_type="app", sender_id="ou_bot", from_self_bot=True),
        link=None,
    )
    assert decision.action is RouteAction.FAIL_CLOSED
    assert decision.reason is RouteReason.REPLY_MAPPING_MISSING
    assert decision.error_code == "REPLY_MAPPING_MISSING"
    assert decision.blocks_run is True
    assert decision.thread_id is None


def test_reply_to_another_bot_is_rejected():
    decision = resolve_route(
        chat_id=CHAT,
        owner_user_id=OWNER,
        reply=ReplyRef(message_id="om_other", sender_type="app", sender_id="ou_other_bot", from_self_bot=False),
        link=None,
    )
    assert decision.action is RouteAction.REJECT
    assert decision.reason is RouteReason.REPLY_SOURCE_UNKNOWN
    assert decision.blocks_run is True


def test_unverifiable_bot_identity_fails_closed():
    """``from_self_bot is None`` means "could not prove"; never guess."""
    decision = resolve_route(
        chat_id=CHAT,
        owner_user_id=OWNER,
        reply=ReplyRef(message_id="om_bot", sender_type="app", sender_id="", from_self_bot=None),
        link=_link(),
    )
    assert decision.action is RouteAction.FAIL_CLOSED
    assert decision.reason is RouteReason.REPLY_SOURCE_UNKNOWN


def test_unreadable_quoted_sender_fails_closed():
    decision = resolve_route(
        chat_id=CHAT,
        owner_user_id=OWNER,
        reply=ReplyRef(message_id="om_unknown", sender_type="unknown"),
        link=_link(message_id="om_unknown"),
    )
    assert decision.action is RouteAction.FAIL_CLOSED
    assert decision.reason is RouteReason.REPLY_SOURCE_UNKNOWN


def test_bot_reply_from_another_chat_is_rejected():
    decision = resolve_route(
        chat_id=CHAT,
        owner_user_id=OWNER,
        reply=ReplyRef(message_id="om_bot", sender_type="app", sender_id="ou_bot", from_self_bot=True),
        link=_link(chat_id="chat-other"),
    )
    assert decision.action is RouteAction.REJECT
    assert decision.reason is RouteReason.CROSS_CHAT_REFERENCE


def test_human_quote_from_another_chat_is_rejected():
    decision = resolve_route(
        chat_id=CHAT,
        owner_user_id=OWNER,
        reply=ReplyRef(message_id="om_me", sender_type="user", sender_id="ou_alice"),
        link=_link(chat_id="chat-other", message_id="om_me"),
    )
    assert decision.action is RouteAction.REJECT
    assert decision.reason is RouteReason.CROSS_CHAT_REFERENCE


def test_colleague_replying_to_my_answer_only_gets_the_quote_text():
    decision = resolve_route(
        chat_id=CHAT,
        owner_user_id="bob",
        reply=ReplyRef(message_id="om_bot", sender_type="app", sender_id="ou_bot", from_self_bot=True),
        link=_link(owner=OWNER),
        quoted_text="上季度的营收是 12 亿",
    )
    assert decision.action is RouteAction.QUOTE_ONLY
    assert decision.reason is RouteReason.CROSS_OWNER_QUOTE
    assert decision.blocks_run is False
    assert decision.thread_id is None
    assert apply_quote("那今年呢？", decision.quote_text, cross_owner=True) == ("[引用] 群里另一位同事的消息：上季度的营收是 12 亿\n\n那今年呢？")


def test_plain_human_quote_is_injected_into_the_current_thread():
    decision = resolve_route(
        chat_id=CHAT,
        owner_user_id=OWNER,
        reply=ReplyRef(message_id="om_nothere", sender_type="user", sender_id="ou_bob"),
        link=None,
        quoted_text="明天十点开会",
    )
    assert decision.action is RouteAction.QUOTE_ONLY
    assert decision.reason is RouteReason.NORMAL_QUOTE
    assert apply_quote("收到", decision.quote_text, cross_owner=False) == "[引用] 先前的消息：明天十点开会\n\n收到"


def test_quote_is_skipped_when_the_text_could_not_be_read():
    assert apply_quote("收到", None, cross_owner=False) == "收到"
    assert apply_quote("收到", "", cross_owner=True) == "收到"


@pytest.mark.parametrize(
    "code",
    [
        "REPLY_MAPPING_MISSING",
        "THREAD_DELETED",
        "REPLY_SOURCE_UNKNOWN",
        "CROSS_CHAT_REFERENCE",
        "OWNER_MISMATCH",
        "ROUTE_DISABLED",
    ],
)
def test_every_failure_code_has_a_user_facing_message(code):
    text = failure_message(code)
    assert text
    assert text != DEFAULT_FAILURE_MESSAGE


def test_unknown_failure_code_falls_back_to_the_generic_message():
    assert failure_message(None) == DEFAULT_FAILURE_MESSAGE
    assert failure_message("SOMETHING_NEW") == DEFAULT_FAILURE_MESSAGE
