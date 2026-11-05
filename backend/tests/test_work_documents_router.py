"""The loopback OAuth relay must authenticate independently of browser login."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import work_documents


def test_relay_rejects_missing_or_wrong_key_before_body_parsing(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_DOCS_RELAY_KEY", "synthetic-relay-key-" + "x" * 32)
    monkeypatch.setattr(work_documents, "_active_service", lambda: object())
    app = FastAPI()
    app.include_router(work_documents.router)
    with TestClient(app) as client:
        for headers in ({}, {"Authorization": "Bearer wrong-key"}, {"Cookie": "access_token=browser-login-is-not-relay-auth"}):
            response = client.post("/api/webhooks/work-documents/oauth-relay", content=b"not-json", headers=headers)
            assert response.status_code == 401


def test_relay_is_default_off_and_does_not_broaden_auth_paths(monkeypatch):
    from app.gateway.auth_middleware import _is_public

    monkeypatch.delenv("DEER_FLOW_WORK_DOCS_UAT_ENABLED", raising=False)
    monkeypatch.setenv("DEER_FLOW_WORK_DOCS_RELAY_KEY", "synthetic-relay-key-" + "x" * 32)
    app = FastAPI()
    app.include_router(work_documents.router)
    with TestClient(app) as client:
        response = client.post("/api/webhooks/work-documents/oauth-relay", json={"action": "health"}, headers={"Authorization": "Bearer " + "synthetic-relay-key-" + "x" * 32})
        assert response.status_code == 404
    assert _is_public("/api/webhooks/work-documents/oauth-relay")
    assert not _is_public("/api/channels/connections")
    assert not _is_public("/api/work-documents/anything")


def test_relay_only_passes_valid_bounded_requests_to_service(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_DOCS_UAT_ENABLED", "true")
    key = "synthetic-relay-key-" + "x" * 32
    monkeypatch.setenv("DEER_FLOW_WORK_DOCS_RELAY_KEY", key)
    service = SimpleNamespace(
        authorize=AsyncMock(return_value={"authorize_url": "https://accounts.w.cnooc.com.cn/example", "browser_session": "x" * 43}), callback=AsyncMock(return_value={"stage": "accepted", "message": "已授权"}), jobs={}, grants={}
    )
    monkeypatch.setattr(work_documents, "_active_service", lambda: service)
    app = FastAPI()
    app.include_router(work_documents.router)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + key}
        assert client.post("/api/webhooks/work-documents/oauth-relay", json={"action": "authorize", "ticket": "x" * 43}, headers=headers).status_code == 200
        assert service.authorize.await_count == 1
        assert client.post("/api/webhooks/work-documents/oauth-relay", json={"action": "callback", "state": "x" * 43, "code": "fake-code", "browser_session": "y" * 43}, headers=headers).status_code == 200
        assert service.callback.await_count == 1
        for data in ({"action": "delete_document"}, {"action": "authorize", "ticket": "wrong"}, {"action": "callback", "state": "x" * 43, "code": "secret", "browser_session": "y" * 43, "url": "https://evil.test"}):
            assert client.post("/api/webhooks/work-documents/oauth-relay", json=data, headers=headers).status_code == 400
        response = client.post("/api/webhooks/work-documents/oauth-relay", content=b"x" * 17000, headers=headers)
        assert response.status_code == 413
