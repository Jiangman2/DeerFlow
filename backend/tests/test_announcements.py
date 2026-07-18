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


async def test_scheduled_announcement_only_appears_inside_its_window() -> None:
    sf, engine = await _session_factory()
    now = datetime.now(UTC)
    repo = AnnouncementRepository(sf)
    try:
        item = await repo.create(
            {
                "title": "定时公告",
                "content": "仅在排期内展示",
                "status": "published",
                "starts_at": now + timedelta(hours=1),
                "ends_at": now + timedelta(hours=2),
            }
        )
        assert await repo.get_active(at=now) is None
        assert (await repo.get_active(at=now + timedelta(hours=1, minutes=1)))["id"] == item["id"]
        assert await repo.get_active(at=now + timedelta(hours=2)) is None
    finally:
        await engine.dispose()


async def test_adjacent_published_windows_are_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    now = datetime.now(UTC)
    repo = AnnouncementRepository(sf)
    try:
        await repo.create(
            {
                "title": "公告一",
                "content": "第一段时间",
                "status": "published",
                "starts_at": now,
                "ends_at": now + timedelta(hours=1),
            }
        )
        with _make_client(sf, monkeypatch) as client:
            response = client.post(
                "/api/admin/announcements",
                json={
                    "title": "公告二",
                    "content": "紧邻第一段时间",
                    "status": "published",
                    "starts_at": (now + timedelta(hours=1)).isoformat(),
                    "ends_at": (now + timedelta(hours=2)).isoformat(),
                },
            )
            assert response.status_code == 200
    finally:
        await engine.dispose()


async def test_published_announcement_can_be_withdrawn_and_republished(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    try:
        with _make_client(sf, monkeypatch) as client:
            created = client.post(
                "/api/admin/announcements",
                json={"title": "临时公告", "content": "可撤下", "status": "published"},
            ).json()["item"]

            withdrawn = client.post(
                f"/api/admin/announcements/{created['id']}/status",
                json={"status": "draft"},
            )
            assert withdrawn.status_code == 200
            assert withdrawn.json()["item"]["status"] == "draft"
            assert client.get("/api/announcements/active").json()["announcement"] is None

            republished = client.post(
                f"/api/admin/announcements/{created['id']}/status",
                json={"status": "published"},
            )
            assert republished.status_code == 200
            assert client.get("/api/announcements/active").json()["announcement"]["id"] == created["id"]
    finally:
        await engine.dispose()


async def test_update_can_clear_schedule_and_validates_merged_window(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    now = datetime.now(UTC)
    try:
        with _make_client(sf, monkeypatch) as client:
            created = client.post(
                "/api/admin/announcements",
                json={
                    "title": "排期草稿",
                    "content": "测试时间窗口",
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(hours=2)).isoformat(),
                },
            ).json()["item"]

            invalid = client.patch(
                f"/api/admin/announcements/{created['id']}",
                json={"ends_at": (now - timedelta(minutes=1)).isoformat()},
            )
            assert invalid.status_code == 422

            cleared = client.patch(
                f"/api/admin/announcements/{created['id']}",
                json={"starts_at": None, "ends_at": None},
            )
            assert cleared.status_code == 200
            assert cleared.json()["item"]["starts_at"] is None
            assert cleared.json()["item"]["ends_at"] is None
    finally:
        await engine.dispose()


async def test_blank_title_and_content_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    try:
        with _make_client(sf, monkeypatch) as client:
            response = client.post(
                "/api/admin/announcements",
                json={"title": "   ", "content": "\n\t", "status": "draft"},
            )
            assert response.status_code == 422
    finally:
        await engine.dispose()
