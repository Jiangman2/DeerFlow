from __future__ import annotations

from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.auth.password import hash_password_async
from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
from app.gateway.routers import admin_users
from deerflow.persistence.base import Base
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

pytestmark = pytest.mark.asyncio

ADMIN_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = "99999999-8888-7777-6666-555555555555"


def _admin_user() -> User:
    return User(
        id=UUID(ADMIN_ID),
        email="admin@example.com",
        password_hash="x",
        system_role="admin",
    )


def _regular_user() -> User:
    return User(
        id=UUID(USER_ID),
        email="user@example.com",
        password_hash="x",
        system_role="user",
    )


async def _session_factory():
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return sf, engine


async def _seed_users(sf) -> None:
    async with sf() as session:
        session.add_all(
            [
                UserRow(
                    id=ADMIN_ID,
                    email="admin@example.com",
                    password_hash=await hash_password_async("admin-password"),
                    system_role="admin",
                    is_disabled=False,
                ),
                UserRow(
                    id=USER_ID,
                    email="user@example.com",
                    password_hash=await hash_password_async("user-password"),
                    system_role="user",
                    is_disabled=False,
                ),
                ThreadMetaRow(thread_id="thread-1", assistant_id="agent", user_id=USER_ID, display_name="hello"),
            ]
        )
        await session.commit()


def _make_client(sf, monkeypatch: pytest.MonkeyPatch, user_factory=_admin_user):
    app = make_authed_test_app(user_factory=user_factory)
    app.include_router(admin_users.router)
    monkeypatch.setattr(admin_users, "get_session_factory", lambda: sf)
    return TestClient(app)


async def test_admin_can_list_disable_and_reset_user(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    try:
        await _seed_users(sf)
        with _make_client(sf, monkeypatch) as client:
            listed = client.get("/api/admin/users")
            assert listed.status_code == 200
            user_item = next(item for item in listed.json()["items"] if item["id"] == USER_ID)
            assert user_item["thread_count"] == 1

            disabled = client.patch(f"/api/admin/users/{USER_ID}", json={"is_disabled": True})
            assert disabled.status_code == 200
            assert disabled.json()["item"]["is_disabled"] is True

            reset = client.post(f"/api/admin/users/{USER_ID}/reset-password", json={"needs_setup": True})
            assert reset.status_code == 200
            assert reset.json()["temporary_password"]

        repo = SQLiteUserRepository(sf)
        assert await repo.get_user_by_email("user@example.com") is not None
        assert await repo.get_user_by_email("user@example.com")
    finally:
        await engine.dispose()


async def test_non_admin_cannot_manage_users(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    try:
        await _seed_users(sf)
        with _make_client(sf, monkeypatch, _regular_user) as client:
            response = client.get("/api/admin/users")
            assert response.status_code == 403
    finally:
        await engine.dispose()


async def test_admin_cannot_disable_last_active_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    try:
        await _seed_users(sf)
        with _make_client(sf, monkeypatch) as client:
            response = client.patch(f"/api/admin/users/{ADMIN_ID}", json={"is_disabled": True})
            assert response.status_code == 400

        async with sf() as session:
            admin = (await session.execute(select(UserRow).where(UserRow.id == ADMIN_ID))).scalar_one()
            assert admin.is_disabled is False
    finally:
        await engine.dispose()
