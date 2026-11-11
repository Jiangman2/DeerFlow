"""SQL contract of ``im_user_bindings`` — the Feishu identity → Web user map.

The partial unique index is the concurrency guarantee: at most one ACTIVE row
per Feishu identity, while REVOKED history stays queryable for audit. These tests
run against real SQLite so the index predicate is exercised by the database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from deerflow.persistence.im_routing import ImUserBindingRepository
from deerflow.persistence.im_routing.bindings import ImUserBindingRow

pytestmark = pytest.mark.anyio

OPEN_A = "ou_open_a"
BOT = "cli_bot"


@pytest.fixture
async def env(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'bindings.db'}", sqlite_dir=str(tmp_path))
    factory = get_session_factory()
    try:
        yield ImUserBindingRepository(factory), factory
    finally:
        await close_engine()


async def _statuses(factory) -> list[tuple[str, str, str]]:
    async with factory() as session:
        rows = (await session.execute(select(ImUserBindingRow.feishu_user_id, ImUserBindingRow.owner_user_id, ImUserBindingRow.status))).all()
    return sorted((feishu, owner, status) for feishu, owner, status in rows)


async def test_partial_unique_allows_revoked_history(env):
    repo, factory = env
    await repo.upsert_active(owner_user_id="u_alice", feishu_user_id=OPEN_A, bot_app_id=BOT)
    await repo.upsert_active(owner_user_id="u_bob", feishu_user_id=OPEN_A, bot_app_id=BOT)

    # One ACTIVE row for Bob, Alice's row kept as REVOKED (audit, no data loss).
    assert await _statuses(factory) == [
        (OPEN_A, "u_alice", "REVOKED"),
        (OPEN_A, "u_bob", "ACTIVE"),
    ]
    assert await repo.find_active(feishu_user_id=OPEN_A, bot_app_id=BOT) == "u_bob"


async def test_upsert_active_revokes_previous_owner(env):
    repo, _ = env
    await repo.upsert_active(owner_user_id="u_alice", feishu_user_id=OPEN_A, bot_app_id=BOT)
    # Rebinding the same identity to another account revokes the old owner.
    await repo.upsert_active(owner_user_id="u_bob", feishu_user_id=OPEN_A, bot_app_id=BOT)
    assert await repo.find_active(feishu_user_id=OPEN_A, bot_app_id=BOT) == "u_bob"

    # The same owner connecting with a second Feishu account revokes the first:
    # one active identity per (owner, bot).
    await repo.upsert_active(owner_user_id="u_bob", feishu_user_id="ou_open_b", bot_app_id=BOT)
    assert await repo.find_active(feishu_user_id=OPEN_A, bot_app_id=BOT) is None
    assert await repo.find_active(feishu_user_id="ou_open_b", bot_app_id=BOT) == "u_bob"

    # Re-binding the same pair is a refresh, not a new row.
    await repo.upsert_active(owner_user_id="u_bob", feishu_user_id="ou_open_b", bot_app_id=BOT)
    assert await repo.find_active(feishu_user_id="ou_open_b", bot_app_id=BOT) == "u_bob"


async def test_find_active_ambiguous_returns_none(env):
    repo, _ = env
    # Two tenants may each bind the same open_id to a different owner; the loose
    # lookup cannot pick one, so it must stay unresolved instead of guessing.
    await repo.upsert_active(owner_user_id="u_alice", feishu_user_id=OPEN_A, tenant_key="tenant_1", bot_app_id=BOT)
    await repo.upsert_active(owner_user_id="u_bob", feishu_user_id=OPEN_A, tenant_key="tenant_2", bot_app_id=BOT)

    assert await repo.find_active(feishu_user_id=OPEN_A, tenant_key="", bot_app_id=BOT) is None
    # An exact tenant still resolves.
    assert await repo.find_active(feishu_user_id=OPEN_A, tenant_key="tenant_1", bot_app_id=BOT) == "u_alice"
    assert await repo.find_active(feishu_user_id=OPEN_A, tenant_key="tenant_2", bot_app_id=BOT) == "u_bob"
    assert await repo.find_active(feishu_user_id="ou_unknown", bot_app_id=BOT) is None


async def test_find_active_tenant_exact_then_loose(env):
    repo, _ = env
    await repo.upsert_active(owner_user_id="u_alice", feishu_user_id=OPEN_A, tenant_key="", bot_app_id=BOT)

    # A binding written before tenant_key was recorded still resolves (loose).
    assert await repo.find_active(feishu_user_id=OPEN_A, tenant_key="tenant_later", bot_app_id=BOT) == "u_alice"

    # A different bot is a different identity namespace.
    assert await repo.find_active(feishu_user_id=OPEN_A, bot_app_id="cli_other") is None

    await repo.revoke(feishu_user_id=OPEN_A, bot_app_id=BOT)
    assert await repo.find_active(feishu_user_id=OPEN_A, bot_app_id=BOT) is None
    async with env[1]() as session:
        active = (
            await session.execute(select(func.count()).select_from(ImUserBindingRow).where(ImUserBindingRow.status == "ACTIVE"))
        ).scalar_one()
    assert active == 0

async def test_known_tenant_never_matches_another_tenant(env):
    """A binding is scoped to its tenant: no cross-tenant identity leak."""
    repo, _ = env
    await repo.upsert_active(owner_user_id="u_alice", feishu_user_id=OPEN_A, tenant_key="tenant_2", bot_app_id=BOT)

    assert await repo.find_active(feishu_user_id=OPEN_A, tenant_key="tenant_1", bot_app_id=BOT) is None
    assert await repo.find_active(feishu_user_id=OPEN_A, tenant_key="tenant_2", bot_app_id=BOT) == "u_alice"
