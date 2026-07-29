from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.routers import dashboard, observability
from deerflow.persistence.base import Base
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow, ThreadMetaRow
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


async def _session_factory():
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


def _client(sf, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = make_authed_test_app(user_factory=_admin_user)
    app.include_router(dashboard.router)
    monkeypatch.setattr(dashboard, "get_session_factory", lambda: sf)
    return TestClient(app)


def _run(
    run_id: str,
    thread_id: str,
    status: str,
    created_at: datetime,
) -> RunRow:
    return RunRow(
        run_id=run_id,
        thread_id=thread_id,
        user_id=USER_ID,
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )


async def test_summary_uses_selected_thread_creations_and_weekly_activity_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf, engine = await _session_factory()
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 6, 30, tzinfo=UTC)
    try:
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"),
                    UserRow(id=USER_ID, email="user@example.com", system_role="user"),
                    ThreadCreationRow(
                        thread_id="old-thread",
                        user_id=USER_ID,
                        created_at=datetime(2026, 3, 1, tzinfo=UTC),
                    ),
                    ThreadCreationRow(
                        thread_id="thread-1",
                        user_id=USER_ID,
                        created_at=datetime(2026, 4, 2, tzinfo=UTC),
                    ),
                    ThreadCreationRow(
                        thread_id="thread-2",
                        user_id=USER_ID,
                        created_at=datetime(2026, 6, 20, tzinfo=UTC),
                    ),
                    ThreadMetaRow(
                        thread_id="thread-1",
                        user_id=USER_ID,
                        display_name="会话一",
                        status="idle",
                        created_at=datetime(2026, 4, 2, tzinfo=UTC),
                    ),
                    ThreadMetaRow(
                        thread_id="thread-2",
                        user_id=USER_ID,
                        display_name="会话二",
                        status="running",
                        created_at=datetime(2026, 6, 20, tzinfo=UTC),
                    ),
                    _run("run-success", "thread-1", "success", datetime(2026, 4, 2, 1, tzinfo=UTC)),
                    _run("run-error", "thread-1", "error", datetime(2026, 4, 2, 2, tzinfo=UTC)),
                    _run("run-running", "thread-2", "running", datetime(2026, 6, 20, 1, tzinfo=UTC)),
                ]
            )
            await session.commit()

        with _client(sf, monkeypatch) as client:
            response = client.get(
                "/api/admin/dashboard/summary",
                params={
                    "hours": 24 * 400,
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["overview"]["threads"] == 2
        assert payload["overview"]["all_time_threads"] == 3
        assert payload["overview"]["existing_threads"] == 2
        assert payload["overview"]["runs"] == 3
        assert payload["overview"]["terminal_runs"] == 2
        assert payload["overview"]["terminal_runs"] + payload["overview"]["running_runs"] == payload["overview"]["runs"]
        assert payload["overview"]["successful_runs"] + payload["overview"]["failed_runs"] == 2
        assert payload["overview"]["success_rate"] == 0.5
        assert payload["activity_bucket"] == "week"
        assert len(payload["user_activity"]) <= 14
        assert sum(item["threads"] for item in payload["user_activity"]) == 2
    finally:
        await engine.dispose()


async def test_activity_buckets_adapt_to_arbitrary_date_ranges() -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    assert dashboard._activity_bucket_days(start, datetime(2020, 1, 31, tzinfo=UTC)) == 1
    assert dashboard._activity_bucket_days(start, datetime(2020, 12, 31, tzinfo=UTC)) == 7
    assert dashboard._activity_bucket_days(start, datetime(2022, 1, 1, tzinfo=UTC)) == 30
    assert dashboard._activity_bucket_days(start, datetime(2026, 1, 1, tzinfo=UTC)) == 365


async def test_registered_user_sessions_exclude_runs_from_deleted_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf, engine = await _session_factory()
    created_at = datetime(2026, 7, 20, tzinfo=UTC)
    try:
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"),
                    UserRow(id=USER_ID, email="user@example.com", system_role="user"),
                    ThreadMetaRow(
                        thread_id="existing-thread",
                        user_id=USER_ID,
                        display_name="仍然存在",
                        status="idle",
                        created_at=created_at,
                        updated_at=created_at,
                    ),
                    _run("existing-run", "existing-thread", "success", created_at),
                    _run("deleted-run", "deleted-thread", "error", created_at),
                ]
            )
            await session.commit()

        with _client(sf, monkeypatch) as client:
            users_response = client.get("/api/admin/dashboard/users", params={"limit": 50})
            sessions_response = client.get(
                f"/api/admin/dashboard/users/{USER_ID}/sessions",
                params={"limit": 50},
            )

        assert users_response.status_code == 200
        user_item = next(item for item in users_response.json()["items"] if item["user_id"] == USER_ID)
        assert user_item["existing_threads"] == 1
        assert user_item["runs"] == 1
        assert user_item["successful_runs"] == 1
        assert user_item["failed_runs"] == 0

        assert sessions_response.status_code == 200
        assert sessions_response.json()["total"] == 1
        assert [item["thread_id"] for item in sessions_response.json()["items"]] == ["existing-thread"]
        assert sessions_response.json()["items"][0]["runs"] == 1
        assert sessions_response.json()["items"][0]["can_open"] is True
        assert sessions_response.json()["items"][0]["chat_url"] == "/workspace/chats/existing-thread?admin_review=1"
    finally:
        await engine.dispose()


async def test_registered_user_session_link_is_exposed_only_to_its_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf, engine = await _session_factory()
    created_at = datetime(2026, 7, 21, tzinfo=UTC)
    try:
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"),
                    ThreadMetaRow(
                        thread_id="admin-thread",
                        user_id=ADMIN_ID,
                        display_name="管理员会话",
                        status="idle",
                        created_at=created_at,
                        updated_at=created_at,
                    ),
                ]
            )
            await session.commit()

        with _client(sf, monkeypatch) as client:
            response = client.get(
                f"/api/admin/dashboard/users/{ADMIN_ID}/sessions",
                params={"limit": 50},
            )

        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["can_open"] is True
        assert item["chat_url"] == "/workspace/chats/admin-thread?admin_review=1"
    finally:
        await engine.dispose()


async def test_observability_analytics_accepts_arbitrary_dashboard_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRepository:
        async def analytics(self, **kwargs):
            return {"hours": kwargs["hours"], "bucket": kwargs["bucket"]}

    app = make_authed_test_app(user_factory=_admin_user)
    app.include_router(observability.router)
    monkeypatch.setattr(observability, "_repo", lambda: FakeRepository())

    with TestClient(app) as client:
        response = client.get(
            "/api/admin/observability/analytics",
            params={"hours": 24 * 400, "bucket": "day"},
        )

    assert response.status_code == 200
    assert response.json() == {"hours": 24 * 400, "bucket": "day"}
