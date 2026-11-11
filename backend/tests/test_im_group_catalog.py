"""SQL contract of ``im_group_catalog``.

The catalog is a *cache*: it stores a group's display name and lifecycle status,
but never decides whether a group directory exists (that is registry-only, see
S08-15). These tests pin the idempotent ensure, the TTL claim and the audit
statuses used by the Feishu group events.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from deerflow.persistence.im_routing import ImGroupCatalogRepository
from deerflow.persistence.im_routing.catalog import ImGroupCatalogRow

pytestmark = pytest.mark.anyio

GROUP_X = "oc_group_x"
GROUP_Y = "oc_group_y"


@pytest.fixture
async def env(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}", sqlite_dir=str(tmp_path))
    factory = get_session_factory()
    try:
        yield ImGroupCatalogRepository(factory), factory
    finally:
        await close_engine()


async def _rows(factory) -> int:
    async with factory() as session:
        return int((await session.execute(select(func.count()).select_from(ImGroupCatalogRow))).scalar_one())


async def test_ensure_is_idempotent(env):
    repo, factory = env
    await repo.ensure(chat_id=GROUP_X, chat_type="group")
    await repo.ensure(chat_id=GROUP_X, chat_type="group")

    assert await _rows(factory) == 1
    meta = (await repo.get_many([GROUP_X]))[GROUP_X]
    assert meta.chat_id == GROUP_X
    assert meta.chat_type == "group"
    assert meta.chat_status == "ACTIVE"
    assert meta.name_source == "UNKNOWN"
    assert meta.display_name is None
    assert meta.last_synced_at is None

    # An empty chat id is a no-op, not a row with a blank key.
    await repo.ensure(chat_id="")
    assert await _rows(factory) == 1


async def test_try_claim_sync_only_once_per_ttl(env):
    repo, _ = env
    await repo.ensure(chat_id=GROUP_X)
    now = datetime.now(UTC)

    assert await repo.needs_sync(GROUP_X, ttl_seconds=3600) is True
    assert await repo.try_claim_sync(GROUP_X, now=now, ttl_seconds=3600) is True
    # A second worker within the TTL must not call Feishu again.
    assert await repo.try_claim_sync(GROUP_X, now=now + timedelta(seconds=30), ttl_seconds=3600) is False
    assert await repo.needs_sync(GROUP_X, ttl_seconds=3600) is False

    # After the TTL, the claim is available again.
    assert await repo.try_claim_sync(GROUP_X, now=now + timedelta(seconds=3601), ttl_seconds=3600) is True

    # A missing row can never be claimed (the caller must ensure it first).
    assert await repo.try_claim_sync(GROUP_Y, now=now, ttl_seconds=3600) is False
    assert await repo.needs_sync(GROUP_Y, ttl_seconds=3600) is True


async def test_update_meta_sets_name_source_and_status(env):
    repo, _ = env
    await repo.ensure(chat_id=GROUP_X)
    await repo.update_meta(
        chat_id=GROUP_X,
        display_name="Alpha Team",
        name_source="CHAT_INFO",
        chat_status="ACTIVE",
        member_count=12,
    )

    meta = (await repo.get_many([GROUP_X]))[GROUP_X]
    assert (meta.display_name, meta.name_source) == ("Alpha Team", "CHAT_INFO")
    assert meta.chat_status == "ACTIVE"
    assert meta.member_count == 12
    assert meta.sync_error == ""

    # A sync failure is recorded without wiping the last known name.
    await repo.update_meta(chat_id=GROUP_X, display_name=None, name_source="UNKNOWN", sync_error="FORBIDDEN")
    meta = (await repo.get_many([GROUP_X]))[GROUP_X]
    assert meta.display_name is None
    assert meta.sync_error == "FORBIDDEN"


async def test_mark_status_left_and_dissolved(env):
    repo, _ = env
    await repo.ensure(chat_id=GROUP_X)
    await repo.ensure(chat_id=GROUP_Y)

    await repo.mark_status(GROUP_X, "LEFT")
    await repo.mark_status(GROUP_Y, "DISSOLVED")

    metas = await repo.get_many([GROUP_X, GROUP_Y])
    assert metas[GROUP_X].chat_status == "LEFT"
    assert metas[GROUP_Y].chat_status == "DISSOLVED"


async def test_get_many_returns_map(env):
    repo, _ = env
    await repo.ensure(chat_id=GROUP_X)
    await repo.ensure(chat_id=GROUP_Y)

    metas = await repo.get_many([GROUP_X, GROUP_Y, GROUP_X, "oc_missing", ""])
    assert set(metas) == {GROUP_X, GROUP_Y}
    assert await repo.get_many([]) == {}
