"""Exercise analytics against SQL with authenticated and anonymous callers."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.gateway.auth.models import User
from app.gateway.employee_featured_store import EmployeeIdentity
from app.gateway.routers import agents
from app.gateway.routers import employee_analytics as analytics
from deerflow.persistence.base import Base
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.observability.model import AuditLogRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

USER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
PUBLIC = EmployeeIdentity("team", "policy-helper", "publisher", "release-one")
EXTERNAL = EmployeeIdentity("external", "专业文档解析专家", "")
EMPTY = EmployeeIdentity("team", "zero-click-helper", "publisher", "release-two")
START = datetime.now(UTC) - timedelta(days=7)


def window():
    # Full-suite collection can precede this test by more than five minutes.
    # Bound the query at request time so newly recorded real click timestamps
    # remain inside the test window; do not freeze production clock behavior.
    return {"start_at": START.isoformat(), "end_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()}


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "get_agents_api_config", lambda: SimpleNamespace(enabled=True))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'analytics.db'}", poolclass=NullPool)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async def initialize():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id=USER, email="alice@example.com", display_name="Alice", organization_name="Research"),
                    UserRow(id=OTHER, email="bob@example.com", display_name="Bob", organization_name="Production"),
                    ThreadMetaRow(thread_id="t", user_id=OTHER, display_name="A conversation"),
                    FeedbackRow(feedback_id="f1", user_id=USER, thread_id="t", run_id="r1", rating=1, comment="private comment not selected"),
                    FeedbackRow(feedback_id="f2", user_id=OTHER, thread_id="t", run_id="r2", rating=-1),
                    FeedbackRow(feedback_id="f3", user_id="deleted-user", thread_id="gone", run_id="gone", rating=1),
                    FeedbackRow(feedback_id="f4", user_id=USER, thread_id="old", run_id="old", rating=-1, created_at=START - timedelta(days=1)),
                    AuditLogRow(user_id=OTHER, action="external_agent.click", resource_id=EXTERNAL.name, method="POST", path="/old", status_code=204, result="success"),
                    AuditLogRow(
                        user_id=USER, action=analytics.CLICK_ACTION, resource_type="employee_square", resource_id=PUBLIC.selection_key, method="POST", path="/click", status_code=204, result="success", created_at=START - timedelta(days=1)
                    ),
                ]
            )
            await session.commit()

    asyncio.run(initialize())
    monkeypatch.setattr(analytics, "_sessions", lambda: sf)
    monkeypatch.setattr(analytics, "_session_factory", lambda: sf)
    monkeypatch.setattr(analytics, "_published_catalog", lambda: [PUBLIC, EXTERNAL, EMPTY])
    app = FastAPI()

    @app.middleware("http")
    async def test_identity(request, call_next):
        role = request.headers.get("x-test-role")
        if role:
            request.state.user = User(id=USER, email="alice@example.com", system_role=role)
            request.state.auth_source = "session"
        return await call_next(request)

    app.include_router(analytics.router)
    yield TestClient(app), sf
    asyncio.run(engine.dispose())


def test_launch_records_server_identity_and_rejects_private_stale_or_forged_actor(setup):
    client, sf = setup
    url = "/api/employee-directory/team/policy-helper/click"
    body = {"expected_identity": PUBLIC.token}
    assert client.post(url, json=body).status_code == 401
    headers = {"x-test-role": "user"}
    assert client.post(url, headers=headers, json={**body, "user_id": OTHER}).status_code == 422
    assert client.post(url, headers=headers, json={"expected_identity": "a" * 64}).status_code == 409
    assert client.post(url.replace("/team/", "/personal/"), headers=headers, json=body).status_code == 422
    assert client.post(url.replace("policy-helper", "unpublished"), headers=headers, json=body).status_code == 404
    assert client.post(url, headers=headers, json=body).status_code == 204

    async def read_actor():
        async with sf() as session:
            return (await session.scalars(select(AuditLogRow).where(AuditLogRow.action == analytics.CLICK_ACTION).order_by(AuditLogRow.id.desc()))).first()

    event = asyncio.run(read_actor())
    assert event.user_id == USER and event.resource_id == PUBLIC.selection_key
    assert event.details_json == {"source": "team", "name": "policy-helper", "publication_identity": PUBLIC.token}


def test_click_ranking_has_zero_rows_legacy_counts_distinct_users_and_pagination(setup):
    client, _ = setup
    for _ in range(2):
        assert client.post("/api/employee-directory/team/policy-helper/click", headers={"x-test-role": "user"}, json={"expected_identity": PUBLIC.token}).status_code == 204
    result = client.get("/api/admin/dashboard/employee-clicks", headers={"x-test-role": "admin"}, params=window()).json()
    by_name = {row["name"]: row for row in result["items"]}
    assert by_name[PUBLIC.name]["clicks"] == 2
    assert by_name[PUBLIC.name]["users"] == 1
    assert by_name[PUBLIC.name]["total_clicks"] == 3
    assert by_name[EXTERNAL.name]["clicks"] == 1
    assert by_name[EMPTY.name]["clicks"] == 0
    page = client.get("/api/admin/dashboard/employee-clicks", headers={"x-test-role": "admin"}, params={**window(), "limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert page["items"][0]["name"] == EXTERNAL.name


@pytest.mark.parametrize("endpoint", ["employee-clicks", "feedback-actors"])
def test_admin_reads_require_admin_and_reject_invalid_paging(setup, endpoint):
    client, _ = setup
    url = "/api/admin/dashboard/" + endpoint
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"x-test-role": "user"}).status_code == 403
    assert client.get(url, headers={"x-test-role": "admin"}, params={"limit": 500}).status_code == 422
    assert client.get(url, headers={"x-test-role": "admin"}, params={"offset": -1}).status_code == 422


def test_feedback_uses_actual_actor_not_thread_owner_and_omits_private_fields(setup):
    client, _ = setup
    url = "/api/admin/dashboard/feedback-actors"
    result = client.get(url, headers={"x-test-role": "admin"}, params=window())
    assert result.status_code == 200
    data = result.json()
    assert data["total"] == 3
    rows = {item["feedback_id"]: item for item in data["items"]}
    assert rows["f1"]["display_name"] == "Alice"  # Thread belongs to Bob.
    assert rows["f1"]["email"] == "alice@example.com"
    assert rows["f3"]["display_name"] is None
    assert "password" not in result.text and "private comment" not in result.text
    filtered = client.get(url, headers={"x-test-role": "admin"}, params={**window(), "rating": -1, "query": "bob"}).json()
    assert filtered["total"] == 1 and filtered["items"][0]["feedback_id"] == "f2"
    assert client.get(url, headers={"x-test-role": "admin"}, params={**window(), "query": "%"}).json()["total"] == 0
    assert client.get(url, headers={"x-test-role": "admin"}, params={**window(), "rating": 0}).status_code == 422


def test_disabled_agent_management_rejects_launch_without_write(setup, monkeypatch):
    client, sf = setup
    monkeypatch.setattr(agents, "get_agents_api_config", lambda: SimpleNamespace(enabled=False))
    result = client.post("/api/employee-directory/team/policy-helper/click", headers={"x-test-role": "user"}, json={"expected_identity": PUBLIC.token})
    assert result.status_code == 403


def test_republish_preserves_click_history_but_rejects_stale_token(setup, monkeypatch):
    client, _ = setup
    replacement = EmployeeIdentity("team", "policy-helper", "publisher", "release-three")
    assert replacement.selection_key == PUBLIC.selection_key
    assert replacement.token != PUBLIC.token
    monkeypatch.setattr(analytics, "_published_catalog", lambda: [replacement])
    url = "/api/employee-directory/team/policy-helper/click"
    assert client.post(url, headers={"x-test-role": "user"}, json={"expected_identity": PUBLIC.token}).status_code == 409
    assert client.post(url, headers={"x-test-role": "user"}, json={"expected_identity": replacement.token}).status_code == 204
    result = client.get("/api/admin/dashboard/employee-clicks", headers={"x-test-role": "admin"}, params=window()).json()
    assert result["items"][0]["total_clicks"] == 2
    assert result["items"][0]["clicks"] == 1


def test_report_uses_current_display_name_without_changing_history_identity(setup, monkeypatch):
    client, _ = setup
    entry = analytics.PublishedEmployee(PUBLIC.source, PUBLIC.name, PUBLIC.owner_id, PUBLIC.publication_id, "制度研究助手")
    monkeypatch.setattr(analytics, "_published_catalog", lambda: [entry])
    result = client.get("/api/admin/dashboard/employee-clicks", headers={"x-test-role": "admin"}, params={**window(), "query": "制度"}).json()
    assert result["total"] == 1
    assert result["items"][0]["name"] == "制度研究助手"
    assert result["items"][0]["identity"] == PUBLIC.selection_key
    assert result["items"][0]["total_clicks"] == 1
