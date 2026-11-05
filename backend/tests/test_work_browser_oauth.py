"""Browser callback security without contacting Work or a real user account."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import work_documents
from app.integrations.work.config import BROWSER_AUTHORIZE_PATH, BROWSER_CALLBACK_PATH, WorkSettings


def client_for(monkeypatch):
    settings = WorkSettings(enabled=True, document_host="documents.example.test")
    service = SimpleNamespace(settings=settings, authorize=AsyncMock(return_value={"authorize_url": settings.authorize_url + "?state=synthetic", "browser_session": "x" * 43}), callback=AsyncMock(return_value={"stage": "accepted"}))
    monkeypatch.setattr(work_documents, "_active_service", lambda: service)
    app = FastAPI()
    app.include_router(work_documents.router)
    return TestClient(app, base_url="https://cnoocclaw.cnooc.com.cn"), service


def test_exact_work_browser_routes_do_not_exempt_siblings_from_auth():
    from app.gateway.auth_middleware import _is_public

    assert _is_public(BROWSER_AUTHORIZE_PATH)
    assert _is_public(BROWSER_CALLBACK_PATH)
    for path in ("/api/integrations/work/tokens", BROWSER_CALLBACK_PATH + "/extra", "/api/channels/connections"):
        assert not _is_public(path)


def test_enterprise_manifest_selects_production_without_forcing_activation():
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / "k8s" / "enterprise" / "runtime-patches.yaml"
    gateway = next(item for item in yaml.safe_load_all(path.read_text()) if item and item.get("kind") == "Deployment" and item["metadata"]["name"] == "gateway")
    container = next(item for item in gateway["spec"]["template"]["spec"]["containers"] if item["name"] == "gateway")
    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env["DEER_FLOW_WORK_PROFILE"] == "production"
    assert env["DEER_FLOW_WORK_PRODUCTION_DOCUMENT_HOST"] == "hnwork.w.cnooc.com.cn"
    assert env["DEER_FLOW_WORK_PRODUCTION_CALLBACK_URL"] == "https://cnoocclaw.cnooc.com.cn/api/integrations/work/oauth/callback"
    assert env.get("DEER_FLOW_WORK_ENABLED") != "true"


def test_authorize_sets_hardened_browser_cookie_and_fixed_redirect(monkeypatch):
    client, service = client_for(monkeypatch)
    with client:
        response = client.get(BROWSER_AUTHORIZE_PATH, params={"ticket": "x" * 43}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith(service.settings.authorize_url)
        cookie = response.headers["set-cookie"]
        assert "__Host-work-oauth=" in cookie and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_callback_requires_browser_session_and_never_returns_code_or_token(monkeypatch):
    client, service = client_for(monkeypatch)
    with client:
        params = {"state": "s" * 43, "code": "synthetic-secret-code"}
        assert client.get(BROWSER_CALLBACK_PATH, params=params).status_code == 400
        service.callback.assert_not_awaited()
        client.cookies.set("__Host-work-oauth", "b" * 43)
        response = client.get(BROWSER_CALLBACK_PATH, params=params)
        assert response.status_code == 200
        assert "synthetic-secret-code" not in response.text and "access_token" not in response.text
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        service.callback.assert_awaited_once_with(state="s" * 43, code="synthetic-secret-code", browser_session="b" * 43, error="")


def test_duplicate_or_oversized_oauth_query_is_rejected_before_service(monkeypatch):
    client, service = client_for(monkeypatch)
    with client:
        client.cookies.set("__Host-work-oauth", "b" * 43)
        for query in ("state=" + "s" * 43 + "&code=one&code=two", "state=" + "s" * 43 + "&code=" + "c" * 4097):
            assert client.get(BROWSER_CALLBACK_PATH + "?" + query).status_code == 400
        service.callback.assert_not_awaited()


def test_oauth_values_are_scrubbed_from_access_log_scope(monkeypatch):
    from app.gateway.work_oauth_logging import WorkOAuthQueryRedactionMiddleware

    app = FastAPI()
    observed = []

    @app.get(BROWSER_CALLBACK_PATH)
    async def endpoint():
        return {"ok": True}

    class AccessLogProbe:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            async def logged_send(message):
                if message["type"] == "http.response.start":
                    observed.append(scope.get("query_string"))
                await send(message)

            await self.inner(scope, receive, logged_send)

    with TestClient(AccessLogProbe(WorkOAuthQueryRedactionMiddleware(app))) as client:
        assert client.get(BROWSER_CALLBACK_PATH + "?code=synthetic-secret&state=synthetic-state").status_code == 200
    assert observed == [b""]
