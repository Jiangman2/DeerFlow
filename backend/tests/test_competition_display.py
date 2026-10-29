import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_employee_square_analytics import EMPTY, PUBLIC
from test_employee_square_analytics import setup as analytics_setup  # noqa: F401

from app.gateway.competition_display_store import DisplayConfig, DisplayConflict, read_display, save_display
from app.gateway.routers import competition_usage as feed


@pytest.fixture()
def display(request, monkeypatch, tmp_path):
    client, _ = request.getfixturevalue("analytics_setup")
    monkeypatch.setenv("COMPETITION_DISPLAY_PATH", str(tmp_path / "board/display.json"))
    monkeypatch.setattr(feed, "_display_cache", None)
    monkeypatch.setattr(feed, "_lock", asyncio.Lock())
    client.app.include_router(feed.router)
    return client


def payload(entries=(PUBLIC, EMPTY), revision=0, enabled=True):
    return {"revision": revision, "title": "数字员工创新赛", "enabled": enabled, "employees": [{"identity": e.selection_key, "publication": e.token, "name": "forged name"} for e in entries]}


def test_admin_selects_public_viewer_cannot_mutate_and_no_personal_fields(display):
    url = "/api/admin/competition/display"
    assert display.get("/api/competition/display").status_code == 404
    assert display.put(url, json=payload()).status_code == 401
    assert display.put(url, json=payload(), headers={"x-test-role": "user"}).status_code == 403
    response = display.put(url, json=payload(), headers={"x-test-role": "admin"})
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["updated_by"], str)
    assert response.json()["employees"][0]["name"] == PUBLIC.name
    public = display.get("/api/competition/display")
    assert public.status_code == 200
    assert public.json()["items"][0]["total_clicks"] == 1
    assert set(public.json()["items"][0]) == {"identity", "name", "total_clicks", "today_clicks"}
    assert "updated_by" not in public.text and "email" not in public.text and "publication" not in public.text
    assert display.put(url, json=payload(), headers={"x-test-role": "admin"}).status_code == 409
    stopped = display.put(url, json=payload(revision=1, enabled=False), headers={"x-test-role": "admin"})
    assert stopped.status_code == 200
    stopped_response = display.get("/api/competition/display")
    assert stopped_response.status_code == 404
    assert stopped_response.headers["cache-control"] == "no-store"


def test_withdrawal_hides_counts_without_fake_zero_and_still_can_stop(display, monkeypatch):
    url = "/api/admin/competition/display"
    assert display.put(url, json=payload(), headers={"x-test-role": "admin"}).status_code == 200
    assert display.get("/api/competition/display").status_code == 200
    monkeypatch.setattr(feed.analytics, "_published_catalog", lambda: [])
    result = display.get("/api/competition/display").json()
    assert result["items"] == [] and result["unavailable_count"] == 2
    assert display.put(url, json=payload(revision=1), headers={"x-test-role": "admin"}).status_code == 409
    assert display.put(url, json=payload(revision=1, enabled=False), headers={"x-test-role": "admin"}).status_code == 200


def test_selection_search_and_duplicate_or_private_identity_rejected(display):
    choices = display.get("/api/admin/competition/employees", params={"query": "policy", "limit": 1}, headers={"x-test-role": "admin"}).json()
    assert choices["total"] == 1 and choices["items"][0]["identity"] == PUBLIC.selection_key
    assert display.get("/api/admin/competition/employees").status_code == 401
    duplicate = payload((PUBLIC, PUBLIC))
    assert display.put("/api/admin/competition/display", json=duplicate, headers={"x-test-role": "admin"}).status_code == 422
    private = payload()
    private["employees"][0]["identity"] = "f" * 64
    assert display.put("/api/admin/competition/display", json=private, headers={"x-test-role": "admin"}).status_code == 409
    for change in ({"enabled": "true"}, {"title": "   "}, {"revision": True}):
        assert display.put("/api/admin/competition/display", json={**payload(), **change}, headers={"x-test-role": "admin"}).status_code == 422


def test_shared_store_cas_conflict_and_corruption_never_resets(tmp_path, monkeypatch):
    path = tmp_path / "display.json"
    monkeypatch.setenv("COMPETITION_DISPLAY_PATH", str(path))
    assert read_display().revision == 0 and not path.exists()

    def write(actor):
        try:
            save_display(DisplayConfig(title=actor), actor)
            return True
        except DisplayConflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, ("admin-a", "admin-b"))) == [False, True]
    assert read_display().revision == 1
    path.write_text("broken")
    with pytest.raises(Exception):
        save_display(DisplayConfig(revision=1), "admin")
    assert path.read_text() == "broken"


def test_admin_save_keeps_real_csrf_boundary(display, monkeypatch):
    from app.gateway import csrf_middleware

    monkeypatch.setattr(csrf_middleware, "is_auth_disabled", lambda: False)
    display.app.add_middleware(csrf_middleware.CSRFMiddleware)
    url = "/api/admin/competition/display"
    headers = {"x-test-role": "admin"}
    assert display.put(url, json=payload(), headers=headers).status_code == 403
    display.cookies.set("csrf_token", "same-session-test-token")
    assert display.put(url, json=payload(), headers={**headers, "X-CSRF-Token": "wrong"}).status_code == 403
    assert display.put(url, json=payload(), headers={**headers, "X-CSRF-Token": "same-session-test-token"}).status_code == 200
    display.cookies.clear()
    assert display.get("/api/competition/display").status_code == 200
