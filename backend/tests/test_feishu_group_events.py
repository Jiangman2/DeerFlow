"""S08 Feishu group events and the ``im.v1.chat.get`` metadata reader.

The group catalog is a cache: it supplies the display name/status next to a
group directory that the thread registry already proved exists. These tests
keep the adapter's real code and fake only the lark API client, so the cache
updates are verified without any network access.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.channels.feishu import FeishuChannel
from app.channels.message_bus import MessageBus

pytestmark = pytest.mark.anyio

CHAT = "oc_group_x"
BOT = "cli_app_1"


class FakeCatalogRepo:
    def __init__(self) -> None:
        self.status_calls: list[tuple[str, str]] = []
        self.meta_calls: list[dict[str, Any]] = []
        self.ensured: list[str] = []

    async def ensure(self, *, chat_id, provider="feishu", tenant_key="", bot_app_id="", chat_type=""):
        self.ensured.append(chat_id)

    async def mark_status(self, chat_id, chat_status, *, tenant_key="", bot_app_id=""):
        self.status_calls.append((chat_id, chat_status))
        return True

    async def update_meta(self, **kwargs):
        self.meta_calls.append(kwargs)
        return True


class FakeChatApi:
    def __init__(self, *, response: Any = None, exc: BaseException | None = None) -> None:
        self.response = response
        self.exc = exc
        self.calls: list[Any] = []

    def get(self, request):
        self.calls.append(request)
        if self.exc is not None:
            raise self.exc
        return self.response


def _channel(*, catalog=None) -> FeishuChannel:
    channel = FeishuChannel(bus=MessageBus(), config={"app_id": BOT, "catalog_repo": catalog})
    return channel


def _event(chat_id: str = CHAT, *, nested: bool = False):
    if nested:
        return SimpleNamespace(event=SimpleNamespace(chat=SimpleNamespace(chat_id=chat_id)))
    return SimpleNamespace(event=SimpleNamespace(chat_id=chat_id))


def _ok_response(name: str = "群聊 X", status: str = "normal", members: int = 7):
    return SimpleNamespace(
        code=0,
        data=SimpleNamespace(name=name, chat_status=status, chat_type="group", member_count=members),
    )


# ---------------------------------------------------------------------------
# fetch_chat_meta
# ---------------------------------------------------------------------------


async def test_fetch_chat_meta_returns_name_and_status():
    channel = _channel()
    api = FakeChatApi(response=_ok_response())
    channel._api_client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(chat=api)))

    meta = await channel.fetch_chat_meta(CHAT)

    assert meta == {"name": "群聊 X", "chat_status": "normal", "chat_type": "group", "member_count": 7}
    assert len(api.calls) == 1


async def test_fetch_chat_meta_returns_none_on_business_error():
    channel = _channel()
    channel._api_client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(chat=FakeChatApi(response=SimpleNamespace(code=230002, data=None))))
    )
    assert await channel.fetch_chat_meta(CHAT) is None


async def test_fetch_chat_meta_returns_none_on_exception_and_without_client():
    channel = _channel()
    channel._api_client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(chat=FakeChatApi(exc=RuntimeError("boom"))))
    )
    assert await channel.fetch_chat_meta(CHAT) is None

    channel._api_client = None
    assert await channel.fetch_chat_meta(CHAT) is None


# ---------------------------------------------------------------------------
# chat lifecycle events
# ---------------------------------------------------------------------------


async def test_chat_updated_event_refreshes_the_cached_name():
    catalog = FakeCatalogRepo()
    channel = _channel(catalog=catalog)
    channel._api_client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(chat=FakeChatApi(response=_ok_response(name="新群名"))))
    )

    await channel._apply_chat_event(CHAT, "updated")

    assert catalog.meta_calls == [
        {
            "chat_id": CHAT,
            "display_name": "新群名",
            "name_source": "CHAT_INFO",
            "chat_status": "normal",
            "member_count": 7,
            "bot_app_id": BOT,
        }
    ]
    assert catalog.status_calls == []


async def test_chat_updated_event_without_metadata_writes_nothing():
    catalog = FakeCatalogRepo()
    channel = _channel(catalog=catalog)
    channel._api_client = None

    await channel._apply_chat_event(CHAT, "updated")

    assert catalog.meta_calls == []
    assert catalog.status_calls == []


async def test_disbanded_event_marks_the_group_dissolved():
    catalog = FakeCatalogRepo()
    channel = _channel(catalog=catalog)

    await channel._apply_chat_event(CHAT, "disbanded")

    assert catalog.status_calls == [(CHAT, "DISSOLVED")]


async def test_bot_removed_event_marks_the_group_left():
    catalog = FakeCatalogRepo()
    channel = _channel(catalog=catalog)

    await channel._apply_chat_event(CHAT, "bot_removed")

    assert catalog.status_calls == [(CHAT, "LEFT")]


async def test_event_chat_id_reads_flat_and_nested_payloads():
    assert FeishuChannel._event_chat_id(_event()) == CHAT
    assert FeishuChannel._event_chat_id(_event(nested=True)) == CHAT
    assert FeishuChannel._event_chat_id(SimpleNamespace(event=SimpleNamespace())) == ""


async def test_events_are_ignored_without_a_catalog_repository():
    channel = _channel(catalog=None)
    channel._main_loop = None

    # Neither the bridge nor the application path may raise when S08 is off.
    channel._on_chat_disbanded_event(_event())
    await channel._apply_chat_event(CHAT, "disbanded")


async def test_event_handlers_are_registered_on_the_dispatcher():
    registered: list[str] = []

    class FakeBuilder:
        def register_p2_im_message_receive_v1(self, _handler):
            registered.append("im.message.receive_v1")
            return self

        def register_p2_im_message_message_read_v1(self, _handler):
            registered.append("read")
            return self

        def register_p2_im_message_reaction_created_v1(self, _handler):
            registered.append("reaction.created")
            return self

        def register_p2_im_message_reaction_deleted_v1(self, _handler):
            registered.append("reaction.deleted")
            return self

        def register_p2_im_message_recalled_v1(self, _handler):
            registered.append("recalled")
            return self

        def register_p2_im_chat_updated_v1(self, _handler):
            registered.append("im.chat.updated_v1")
            return self

        def register_p2_im_chat_disbanded_v1(self, _handler):
            registered.append("im.chat.disbanded_v1")
            return self

        def register_p2_im_member_bot_deleted_v1(self, _handler):
            registered.append("im.member.bot.deleted_v1")
            return self

        def build(self):
            return object()

    class FakeDispatcher:
        @staticmethod
        def builder(_a, _b):
            return FakeBuilder()

    _channel()._build_event_handler(SimpleNamespace(EventDispatcherHandler=FakeDispatcher))

    assert {
        "im.chat.updated_v1",
        "im.chat.disbanded_v1",
        "im.member.bot.deleted_v1",
    } <= set(registered)