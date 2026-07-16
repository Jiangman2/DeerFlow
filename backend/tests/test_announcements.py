from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.routers import announcements
from deerflow.persistence.announcement import AnnouncementRepository
from deerflow.persistence.base import Base

pytestmark = pytest.mark.asyncio


def _admin_user() -> User:
    return User(
        id=UUID("11111111-2222-3333-4444-555555555555"),
        email="admin@example.com",
        password_hash="x",
        system_role="admin",
    )


def _regular_user() -> User:
    return User(
        id=UUID("99999999-8888-7777-6666-555555555555"),
        email="user@example.com",
        password_hash="x",
        system_role="user",
    )


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return sf, engine


def _make_client(sf, monkeypatch: pytest.MonkeyPatch, user_factory=_admin_user):
    app = make_authed_test_app(user_factory=user_factory)
    app.include_router(announcements.router)
    monkeypatch.setattr(announcements, "get_session_factory", lambda: sf)
    return TestClient(app)


async def test_admin_can_publish_active_announcement(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    try:
        with _make_client(sf, monkeypatch) as client:
            response = client.post(
                "/api/admin/announcements",
                json={
                    "title": "维护通知",
                    "content": "今晚 22:00 进行维护。",
                    "severity": "warning",
                    "status": "published",
                    "dismissible": True,
                },
            )
            assert response.status_code == 200
            item = response.json()["item"]
            assert item["created_by_email"] == "admin@example.com"

            active = client.get("/api/announcements/active")
            assert active.status_code == 200
            assert active.json()["announcement"]["title"] == "维护通知"
    finally:
        await engine.dispose()


async def test_non_admin_cannot_manage_announcements(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    try:
        with _make_client(sf, monkeypatch, _regular_user) as client:
            response = client.post(
                "/api/admin/announcements",
                json={
                    "title": "维护通知",
                    "content": "今晚 22:00 进行维护。",
                    "severity": "info",
                    "status": "draft",
                    "dismissible": True,
                },
            )
            assert response.status_code == 403
    finally:
        await engine.dispose()


async def test_published_announcements_cannot_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    now = datetime.now(UTC)
    repo = AnnouncementRepository(sf)
    try:
        await repo.create(
            {
                "title": "公告一",
                "content": "第一条",
                "severity": "info",
                "status": "published",
                "dismissible": True,
                "starts_at": now,
                "ends_at": now + timedelta(hours=2),
            }
        )

        with _make_client(sf, monkeypatch) as client:
            response = client.post(
                "/api/admin/announcements",
                json={
                    "title": "公告二",
                    "content": "第二条",
                    "severity": "critical",
                    "status": "published",
                    "starts_at": (now + timedelta(minutes=30)).isoformat(),
                    "ends_at": (now + timedelta(hours=3)).isoformat(),
                    "dismissible": False,
                },
            )
            assert response.status_code == 409
    finally:
        await engine.dispose()
