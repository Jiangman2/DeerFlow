"""HTTP contract of the read-only ``/api/im`` sidebar endpoints.

The suite runs against a real SQLite database and asserts two things the UI
depends on: the tree only contains threads the registry proved exist for the
calling owner, and serving any of these routes never mutates the pointer or the
registry (the Feishu "current conversation" must survive a Web page view).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.gateway.auth.models import User
from app.gateway.im_conversations import ImConversationService
from app.gateway.routers import im_conversations as im_router
from deerflow.persistence.im_routing import (
    ImGroupCatalogRepository,
    ImRoutingRepository,
    ImThreadRegistryRepository,
)
from deerflow.persistence.im_routing.model import ImSessionPointerRow
from deerflow.persistence.im_routing.registry_model import ImThreadRegistryRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow

pytestmark = pytest.mark.anyio

OWNER_A = "11111111-1111-1111-1111-111111111111"
OWNER_B = "22222222-2222-2222-2222-222222222222"
PRIVATE_A = "oc_dm_alice_bot"
PRIVATE_B = "oc_dm_bob_bot"
GROUP_X = "oc_group_x"
GROUP_Y = "oc_group_y"
GROUP_Z = "oc_group_z"
GROUP_LEFT = "oc_group_left"

NOW = datetime.now(UTC)


def _user(owner: str) -> User:
    return User(id=UUID(owner), email=f"{owner[:8]}@example.com", password_hash="x", system_role="user")


def _stamp(minutes: int) -> datetime:
    return NOW - timedelta(minutes=minutes)


async def _seed_thread(session, thread_id: str, *, owner: str, channel_type: str, chat_id: str,
                       display_name: str | None, minutes: int, status: str = "active") -> None:
    when = _stamp(minutes)
    session.add(
        ThreadMetaRow(
            thread_id=thread_id,
            assistant_id="assistant",
            user_id=owner,
            display_name=display_name,
            created_at=when,
            updated_at=when,
        ),
    )
    session.add(
        ImThreadRegistryRow(
            thread_id=thread_id,
            provider="feishu",
            connection_id="",
            tenant_key="",
            bot_app_id="cli_test",
            owner_user_id=owner,
            channel_type=channel_type,
            external_conversation_id=chat_id,
            external_topic_id="",
            source_platform="feishu",
            generation=1,
            status=status,
            classification_source="RUNTIME",
            title_snapshot=None,
            last_active_at=when,
            created_at=when,
            updated_at=when,
        ),
    )


@pytest.fixture
async def env(tmp_path, monkeypatch):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 's08_api.db'}", sqlite_dir=str(tmp_path))
    factory = get_session_factory()
    assert factory is not None
    async with factory() as session:
        # Owner A: two private threads, two X threads, one Y thread, one thread
        # whose threads_meta row is gone, and one archived thread.
        await _seed_thread(session, "p1", owner=OWNER_A, channel_type="private", chat_id=PRIVATE_A,
                           display_name="私聊旧话题", minutes=90)
        await _seed_thread(session, "p2", owner=OWNER_A, channel_type="private", chat_id=PRIVATE_A,
                           display_name="私聊新话题", minutes=5)
        await _seed_thread(session, "x1", owner=OWNER_A, channel_type="group", chat_id=GROUP_X,
                           display_name="研发评审", minutes=60)
        await _seed_thread(session, "x2", owner=OWNER_A, channel_type="group", chat_id=GROUP_X,
                           display_name="研发排期", minutes=10)
        await _seed_thread(session, "y1", owner=OWNER_A, channel_type="group", chat_id=GROUP_Y,
                           display_name="产品讨论一", minutes=20)
        await _seed_thread(session, "l1", owner=OWNER_A, channel_type="group", chat_id=GROUP_LEFT,
                           display_name="旧项目", minutes=120)
        await _seed_thread(session, "th_archived", owner=OWNER_A, channel_type="group", chat_id=GROUP_LEFT,
                           display_name="已归档", minutes=130, status="archived")
        await _seed_thread(session, "th_deleted", owner=OWNER_A, channel_type="group", chat_id=GROUP_Y,
                           display_name="已删除", minutes=140, status="deleted")
        # Owner B keeps one thread in the same group X: cross-owner isolation.
        await _seed_thread(session, "b_x1", owner=OWNER_B, channel_type="group", chat_id=GROUP_X,
                           display_name="B 的私密结论", minutes=15)
        await session.commit()

    # th_gone: registry row without a threads_meta row (deleted behind our back).
    async with factory() as session:
        session.add(
            ImThreadRegistryRow(
                thread_id="th_gone",
                provider="feishu",
                connection_id="",
                tenant_key="",
                bot_app_id="cli_test",
                owner_user_id=OWNER_A,
                channel_type="group",
                external_conversation_id=GROUP_X,
                external_topic_id="",
                source_platform="feishu",
                generation=1,
                status="active",
                classification_source="RUNTIME",
                title_snapshot="残留会话",
                last_active_at=_stamp(200),
                created_at=_stamp(200),
                updated_at=_stamp(200),
            ),
        )
        await session.commit()

    routing = ImRoutingRepository(factory)
    await routing.insert_pointer_if_absent(
        provider="feishu", owner_user_id=OWNER_A, chat_id=PRIVATE_A, thread_id="p2", channel_type="private",
    )
    await routing.insert_pointer_if_absent(
        provider="feishu", owner_user_id=OWNER_A, chat_id=GROUP_X, thread_id="x2", channel_type="group",
    )
    await routing.insert_pointer_if_absent(
        provider="feishu", owner_user_id=OWNER_B, chat_id=GROUP_X, thread_id="b_x1", channel_type="group",
    )

    catalog = ImGroupCatalogRepository(factory)
    await catalog.ensure(chat_id=GROUP_X, bot_app_id="cli_test", chat_type="group")
    await catalog.update_meta(chat_id=GROUP_X, bot_app_id="cli_test", display_name="研发一群", name_source="CHAT_INFO",
                              chat_status="ACTIVE")
    await catalog.ensure(chat_id=GROUP_Y, bot_app_id="cli_test", chat_type="group")
    await catalog.update_meta(chat_id=GROUP_Y, bot_app_id="cli_test", display_name="产品讨论", name_source="CHAT_INFO",
                              chat_status="ACTIVE")
    await catalog.ensure(chat_id=GROUP_LEFT, bot_app_id="cli_test", chat_type="group")
    await catalog.update_meta(chat_id=GROUP_LEFT, bot_app_id="cli_test", display_name="旧项目群",
                              name_source="CHAT_INFO", chat_status="LEFT")
    # Catalog-only group: it must never become an empty directory (S08-15).
    await catalog.ensure(chat_id=GROUP_Z, bot_app_id="cli_test", chat_type="group")
    await catalog.update_meta(chat_id=GROUP_Z, bot_app_id="cli_test", display_name="空群", name_source="CHAT_INFO")

    service = ImConversationService(
        registry_repo=ImThreadRegistryRepository(factory),
        catalog_repo=catalog,
        routing_repo=routing,
        enabled=True,
    )
    monkeypatch.setattr(im_router, "_service", lambda: service)
    try:
        yield factory, service
    finally:
        await close_engine()


def _app(owner: str = OWNER_A, *, owner_check_passes: bool = True) -> FastAPI:
    app = make_authed_test_app(user_factory=lambda: _user(owner), owner_check_passes=owner_check_passes)
    app.include_router(im_router.router)
    return app


async def _get(app: FastAPI, path: str, **params) -> tuple[int, dict]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, params=params)
    return response.status_code, response.json()


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


async def test_capabilities_are_off_by_default(env, monkeypatch):
    monkeypatch.delenv("DEER_FLOW_S08_ENABLED", raising=False)
    status, body = await _get(_app(), "/api/im/capabilities")
    assert status == 200
    assert body["im_session_tree_enabled"] is False
    assert body["search_enabled"] is False


async def test_capabilities_follow_the_server_flag(env, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_S08_ENABLED", "true")
    status, body = await _get(_app(), "/api/im/capabilities")
    assert status == 200
    assert body["im_session_tree_enabled"] is True
    assert body["provider"] == "feishu"


# ---------------------------------------------------------------------------
# session tree
# ---------------------------------------------------------------------------


async def test_session_tree_groups_private_and_current_markers(env):
    status, body = await _get(_app(), "/api/im/session-tree")
    assert status == 200

    assert body["private"]["total"] == 2
    assert [t["thread_id"] for t in body["private"]["threads"]] == ["p2", "p1"]
    assert body["private"]["threads"][0]["is_current"] is True
    assert body["private"]["threads"][1]["is_current"] is False
    assert body["private"]["threads"][0]["title"] == "私聊新话题"

    groups = {group["chat_id"]: group for group in body["groups"]}
    # GROUP_Z only has a catalog row, never a thread -> no empty directory.
    assert GROUP_Z not in groups
    assert set(groups) == {GROUP_X, GROUP_Y, GROUP_LEFT}
    assert body["groups_total"] == 3

    x = groups[GROUP_X]
    assert x["name"] == "研发一群"
    assert x["chat_status"] == "ACTIVE"
    assert x["thread_count"] == 3  # x1, x2, th_gone (visible statuses only)
    assert x["current_thread_id"] == "x2"
    assert [t["thread_id"] for t in x["threads"]] == ["x2", "x1", "th_gone"]
    assert x["threads"][0]["is_current"] is True

    left = groups[GROUP_LEFT]
    assert left["name"] == "旧项目群"
    assert left["chat_status"] == "LEFT"
    assert {t["thread_id"] for t in left["threads"]} == {"l1", "th_archived"}
    assert left["current_thread_id"] is None


async def test_session_tree_respects_thread_and_group_limits(env):
    status, body = await _get(_app(), "/api/im/session-tree", groups_limit=2, threads_per_group=1)
    assert status == 200
    assert len(body["groups"]) == 2
    assert body["groups_total"] == 3
    for group in body["groups"]:
        assert len(group["threads"]) == 1
    assert [t["thread_id"] for t in body["private"]["threads"]] == ["p2"]

    status, body = await _get(_app(), "/api/im/session-tree", group_offset=2)
    assert status == 200
    assert [group["chat_id"] for group in body["groups"]] == [GROUP_LEFT]


async def test_session_tree_is_scoped_to_the_calling_owner(env):
    status, body = await _get(_app(OWNER_B), "/api/im/session-tree")
    assert status == 200
    assert body["private"]["total"] == 0
    assert [group["chat_id"] for group in body["groups"]] == [GROUP_X]
    assert [t["thread_id"] for t in body["groups"][0]["threads"]] == ["b_x1"]
    assert body["groups"][0]["current_thread_id"] == "b_x1"


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


async def test_group_threads_paginate_with_cursor(env):
    status, first = await _get(_app(), f"/api/im/groups/{GROUP_X}/threads", limit=2)
    assert status == 200
    assert [t["thread_id"] for t in first["threads"]] == ["x2", "x1"]
    assert first["next_cursor"]

    status, second = await _get(_app(), f"/api/im/groups/{GROUP_X}/threads", limit=2,
                                cursor=first["next_cursor"])
    assert status == 200
    assert [t["thread_id"] for t in second["threads"]] == ["th_gone"]
    assert second["next_cursor"] is None


async def test_group_threads_never_leak_another_owner(env):
    status, body = await _get(_app(OWNER_B), f"/api/im/groups/{GROUP_Y}/threads")
    assert status == 200
    assert body["threads"] == []


async def test_private_threads_paginate(env):
    status, body = await _get(_app(), "/api/im/threads", limit=10)
    assert status == 200
    assert [t["thread_id"] for t in body["threads"]] == ["p2", "p1"]
    assert body["next_cursor"] is None

    status, first = await _get(_app(), "/api/im/threads", limit=1)
    assert [t["thread_id"] for t in first["threads"]] == ["p2"]
    status, second = await _get(_app(), "/api/im/threads", limit=1, cursor=first["next_cursor"])
    assert [t["thread_id"] for t in second["threads"]] == ["p1"]


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def test_search_matches_thread_title_and_group_name(env):
    status, body = await _get(_app(), "/api/im/threads/search", q="研发排期")
    assert status == 200
    assert [t["thread_id"] for t in body["results"]] == ["x2"]
    assert body["results"][0]["chat_name"] == "研发一群"

    status, body = await _get(_app(), "/api/im/threads/search", q="研发一群")
    assert status == 200
    assert {t["thread_id"] for t in body["results"]} == {"x1", "x2", "th_gone"}


async def test_search_is_owner_scoped(env):
    status, body = await _get(_app(), "/api/im/threads/search", q="B 的私密结论")
    assert status == 200
    assert body["results"] == []

    status, body = await _get(_app(OWNER_B), "/api/im/threads/search", q="B 的私密结论")
    assert [t["thread_id"] for t in body["results"]] == ["b_x1"]


async def test_search_route_is_not_shadowed_by_the_origin_route(env):
    status, body = await _get(_app(), "/api/im/threads/search", q="研发排期")
    assert status == 200
    assert "results" in body


# ---------------------------------------------------------------------------
# origin
# ---------------------------------------------------------------------------


async def test_origin_reports_registry_provenance(env):
    status, body = await _get(_app(), "/api/im/threads/x1/origin")
    assert status == 200
    assert body["thread_id"] == "x1"
    assert body["owner_user_id"] == OWNER_A
    assert body["channel_type"] == "group"
    assert body["chat_id"] == GROUP_X
    assert body["chat_name"] == "研发一群"
    assert body["status"] == "active"
    assert body["thread_exists"] is True
    assert body["is_current"] is False


async def test_origin_marks_a_thread_whose_meta_row_disappeared(env):
    status, body = await _get(_app(), "/api/im/threads/th_gone/origin")
    assert status == 200
    assert body["thread_exists"] is False


async def test_origin_returns_404_for_a_non_im_thread(env):
    status, _ = await _get(_app(), "/api/im/threads/no_such_thread/origin")
    assert status == 404


async def test_origin_is_denied_when_the_owner_check_fails(env):
    status, _ = await _get(_app(OWNER_A, owner_check_passes=False), "/api/im/threads/x1/origin")
    assert status in (403, 404)


# ---------------------------------------------------------------------------
# read-only guarantees and authentication
# ---------------------------------------------------------------------------


async def test_serving_the_tree_never_writes(env):
    factory, _ = env

    async def snapshot() -> tuple[list[tuple], int]:
        async with factory() as session:
            pointers = (
                await session.execute(
                    select(
                        ImSessionPointerRow.id,
                        ImSessionPointerRow.thread_id,
                        ImSessionPointerRow.generation,
                        ImSessionPointerRow.owner_user_id,
                        ImSessionPointerRow.channel_type,
                        ImSessionPointerRow.external_conversation_id,
                    ).order_by(ImSessionPointerRow.id),
                )
            ).all()
            registry = await session.scalar(select(func.count()).select_from(ImThreadRegistryRow))
        return list(pointers), int(registry or 0)

    before = await snapshot()
    for path in (
        "/api/im/session-tree",
        f"/api/im/groups/{GROUP_X}/threads",
        "/api/im/threads",
        "/api/im/threads/search",
        "/api/im/threads/x1/origin",
    ):
        status, _ = await _get(_app(), path, **({"q": "研发"} if path.endswith("search") else {}))
        assert status == 200
    after = await snapshot()

    assert before == after
    # The Feishu pointer still designates the same threads after browsing.
    assert {row[1] for row in after[0] if row[3] == OWNER_A} == {"p2", "x2"}


async def test_unauthenticated_requests_are_rejected():
    app = FastAPI()
    app.include_router(im_router.router)
    status, _ = await _get(app, "/api/im/session-tree")
    assert status == 401