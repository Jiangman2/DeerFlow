from __future__ import annotations

import base64
import hashlib
import hmac
import json
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.deps import get_current_user_from_request
from app.gateway.routers import skillhub_session

_USER_ID = UUID("b950e6f3-8803-4121-b02e-21caad34277f")
_SECRET = "skillhub-session-bridge-test-secret-2026"


def _user(*, disabled: bool = False) -> User:
    return User(
        id=_USER_ID,
        email="user@example.com",
        display_name="测试 用户",
        password_hash="x",
        system_role="admin",
        is_disabled=disabled,
    )


def _client(*, disabled: bool = False) -> TestClient:
    user = _user(disabled=disabled)
    app = make_authed_test_app(user_factory=lambda: user)
    app.dependency_overrides[get_current_user_from_request] = lambda: user
    app.include_router(skillhub_session.router)
    return TestClient(app)


def _decode_unpadded(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_assertion_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SKILLHUB_ENABLED", raising=False)
    monkeypatch.setenv("SKILLHUB_SESSION_BRIDGE_SECRET", _SECRET)

    with _client() as client:
        response = client.get("/internal/skillhub/session/assertion")

    assert response.status_code == 503
    assert "X-SkillHub-Identity" not in response.headers


def test_assertion_requires_a_strong_bridge_secret(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SKILLHUB_ENABLED", "true")
    monkeypatch.setenv("SKILLHUB_SESSION_BRIDGE_SECRET", "too-short")

    with _client() as client:
        response = client.get("/internal/skillhub/session/assertion")

    assert response.status_code == 503


def test_assertion_is_short_lived_signed_and_contains_no_roles(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SKILLHUB_ENABLED", "true")
    monkeypatch.setenv("SKILLHUB_SESSION_BRIDGE_SECRET", _SECRET)
    monkeypatch.setattr(skillhub_session.time, "time", lambda: 1_788_534_000)
    monkeypatch.setattr(skillhub_session.secrets, "token_urlsafe", lambda _size: "fixed-nonce")

    with _client() as client:
        response = client.get("/internal/skillhub/session/assertion")

    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    encoded = response.headers["x-skillhub-identity"]
    signature = response.headers["x-skillhub-signature"]
    expected = hmac.new(_SECRET.encode(), encoded.encode(), hashlib.sha256).digest()
    assert hmac.compare_digest(_decode_unpadded(signature), expected)

    claims = json.loads(_decode_unpadded(encoded))
    assert claims == {
        "aud": "skillhub",
        "email": "user@example.com",
        "exp": 1_788_534_030,
        "iat": 1_788_534_000,
        "iss": "deerflow-gateway",
        "name": "测试 用户",
        "nonce": "fixed-nonce",
        "sub": str(_USER_ID),
        "v": 1,
    }
    assert "roles" not in claims
    assert "system_role" not in claims


def test_disabled_user_cannot_receive_an_assertion(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SKILLHUB_ENABLED", "true")
    monkeypatch.setenv("SKILLHUB_SESSION_BRIDGE_SECRET", _SECRET)

    with _client(disabled=True) as client:
        response = client.get("/internal/skillhub/session/assertion")

    assert response.status_code == 403
    assert "X-SkillHub-Identity" not in response.headers
