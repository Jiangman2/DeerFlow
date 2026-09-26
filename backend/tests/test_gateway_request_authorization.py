"""Pinned route/model/me contracts with enterprise identity and loop boundaries."""

import asyncio
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from langgraph.errors import GraphBubbleUp

from app.gateway import deps, request_authorization, sandbox_authz
from app.gateway.auth.models import User, UserResponse
from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.authz import _ALL_PERMISSIONS, require_permission, resolve_route_permissions_for_request
from app.gateway.deps import get_config
from app.gateway.internal_auth import create_internal_auth_headers
from app.gateway.routers import auth, models
from deerflow.authz.provider import AuthzDecision
from deerflow.config.authorization_config import AuthorizationConfig, AuthorizationProviderConfig

_USER = User(id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), email="viewer@example.com", system_role="user", display_name="企业用户", organization_name="组织")
_ADMIN = User(id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), email="admin@example.com", system_role="admin")


def _config(*, enabled=True, fail_closed=True):
    catalog = [SimpleNamespace(name=name, model="provider/" + name, display_name=name, description=None, supports_thinking=False, supports_reasoning_effort=False) for name in ("basic", "private")]
    return SimpleNamespace(
        authorization=AuthorizationConfig(
            enabled=enabled,
            fail_closed=fail_closed,
            provider=AuthorizationProviderConfig(
                use="deerflow.authz.rbac:RbacAuthorizationProvider",
                config={
                    "roles": {
                        "user": {"routes": {"allow": ["threads:read", "runs:read"]}, "models": {"allow": ["basic"]}},
                        "admin": {"routes": {"allow": True}, "models": {"allow": True}},
                    }
                },
            ),
        ),
        models=catalog,
        get_model_config=lambda name: next((model for model in catalog if model.name == name), None),
        token_usage=SimpleNamespace(enabled=True),
    )


def _request(user=_USER):
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user = user
    request.state.auth_source = "session"
    return request


@pytest.fixture
def setup(monkeypatch):
    config = _config()
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(models.router)
    app.include_router(auth.router)
    app.dependency_overrides[get_config] = lambda: config
    # Exercise real AuthMiddleware and decorators; only JWT/database retrieval
    # is replaced. Request state, permissions and model authorization are real.
    original = deps.get_current_user_from_request

    async def authenticated(request):
        if getattr(request.state, "auth_source", None):
            return await original(request)
        token = request.cookies.get("access_token")
        if token not in {"user", "admin"}:
            raise HTTPException(401, "invalid test credential")
        return _USER if token == "user" else _ADMIN

    monkeypatch.setattr(deps, "get_current_user_from_request", authenticated)
    app.state.thread_store = SimpleNamespace(check_access=AsyncMock(return_value=True))

    @app.get("/api/thread/{thread_id}")
    @require_permission("threads", "read", owner_check=True)
    async def read(thread_id: str, request: Request):
        return {"ok": True}

    @app.post("/api/thread/{thread_id}")
    @require_permission("threads", "write", owner_check=True, require_existing=True)
    async def write(thread_id: str, request: Request):
        return {"ok": True}

    return app, config


def test_middleware_me_and_models_share_effective_policy(setup):
    app, _ = setup
    with TestClient(app) as client:
        client.cookies.set("access_token", "user")
        me = client.get("/api/v1/auth/me").json()
        assert me["permissions"] == ["threads:read", "runs:read"]
        assert me["display_name"] == "企业用户" and me["organization_name"] == "组织"
        assert client.get("/api/thread/owned").status_code == 200
        assert client.post("/api/thread/owned").status_code == 403
        listing = client.get("/api/models").json()
        assert [item["name"] for item in listing["models"]] == ["basic"]
        assert listing["token_usage"] == {"enabled": True}
        assert client.get("/api/models/basic").status_code == 200
        assert client.get("/api/models/private").status_code == 403
        assert client.get("/api/models/missing").status_code == 404


def test_admin_allow_does_not_grant_cross_owner_mutation(setup):
    app, _ = setup
    app.state.thread_store.check_access.return_value = False
    with TestClient(app) as client:
        client.cookies.set("access_token", "admin")
        assert client.get("/api/thread/other-owner").status_code == 200
        assert client.post("/api/thread/other-owner").status_code == 404
        assert client.get("/api/v1/auth/me").json()["permissions"] == _ALL_PERMISSIONS
    app.state.thread_store.check_access.assert_awaited_once_with("other-owner", str(_ADMIN.id), require_existing=True)


def test_owner_denial_survives_route_allow(setup):
    app, _ = setup
    app.state.thread_store.check_access.return_value = False
    with TestClient(app) as client:
        client.cookies.set("access_token", "user")
        assert client.get("/api/thread/other-owner").status_code == 404


def test_disabled_keeps_registered_permissions_and_catalog(setup):
    app, config = setup
    config.authorization.enabled = False
    config.authorization.provider = None
    with TestClient(app) as client:
        client.cookies.set("access_token", "user")
        assert client.get("/api/v1/auth/me").json()["permissions"] == _ALL_PERMISSIONS
        assert len(client.get("/api/models").json()["models"]) == 2
        assert client.post("/api/thread/owned").status_code == 200


@pytest.mark.parametrize("fail_closed", [True, False])
def test_provider_resolution_error_uses_valid_flag(setup, fail_closed):
    app, config = setup
    config.authorization.fail_closed = fail_closed
    config.authorization.provider.use = "unavailable_module:Provider"
    with TestClient(app) as client:
        client.cookies.set("access_token", "user")
        assert client.get("/api/v1/auth/me").json()["permissions"] == ([] if fail_closed else _ALL_PERMISSIONS)
        assert len(client.get("/api/models").json()["models"]) == (0 if fail_closed else 2)
        assert client.get("/api/models/private").status_code == (403 if fail_closed else 200)


@pytest.mark.parametrize("bad", ["read_error", "missing", "mock"])
def test_bad_config_is_never_implicit_disabled(setup, monkeypatch, bad):
    app, _ = setup
    invalid = SimpleNamespace() if bad == "missing" else MagicMock()
    getter = MagicMock(side_effect=RuntimeError("SECRET_OPERATOR_VALUE")) if bad == "read_error" else lambda: invalid
    monkeypatch.setattr("deerflow.config.get_app_config", getter)
    with TestClient(app) as client:
        client.cookies.set("access_token", "user")
        response = client.get("/api/v1/auth/me")
        assert response.json()["permissions"] == []
        assert "SECRET" not in response.text
        assert client.get("/api/models").json()["models"] == []
        assert client.get("/api/models/basic").status_code == 403
        assert client.get("/api/thread/owned").status_code == 403


def test_unauthed_requests_cannot_use_enabled_policy(setup):
    app, _ = setup
    with TestClient(app) as client:
        for path in ("/api/v1/auth/me", "/api/models", "/api/models/basic", "/api/thread/owned"):
            assert client.get(path).status_code == 401


@pytest.mark.parametrize("owner_found", [True, False])
def test_internal_owner_uses_real_role_not_default_or_header(setup, monkeypatch, owner_found):
    app, config = setup
    config.authorization.default_role = "admin"
    lookup = AsyncMock(return_value=_USER if owner_found else None)
    monkeypatch.setattr(sandbox_authz, "get_local_provider", lambda: SimpleNamespace(get_user=lookup))
    with TestClient(app) as client:
        headers = create_internal_auth_headers(owner_user_id=str(_USER.id))
        response = client.get("/api/models", headers=headers)
        assert response.status_code == 200
        assert [item["name"] for item in response.json()["models"]] == (["basic"] if owner_found else [])
        assert client.post("/api/thread/owned", headers=headers).status_code == 403


def test_model_and_route_snapshot_hot_reload_next_request(setup):
    _, config = setup

    async def run():
        request = _request()
        first = await request_authorization.resolve_request_authorization(request)
        config.authorization.provider.config["roles"]["user"]["routes"]["allow"] = True
        config.authorization.provider.config["roles"]["user"]["models"]["allow"] = True
        assert await first.filter_models(["basic", "private"]) == ["basic"]
        assert not await first.allowed("route", "write", "threads:write")
        assert await request_authorization.resolve_request_authorization(request) is first
        fresh = await request_authorization.resolve_request_authorization(_request())
        assert await fresh.allowed("route", "write", "threads:write")
        assert await fresh.filter_models(["basic", "private"]) == ["basic", "private"]

    asyncio.run(run())


def test_me_uses_middleware_permissions_without_extra_decisions(setup, monkeypatch):
    app, _ = setup
    real = request_authorization.RequestAuthorization.allowed
    calls = []

    async def counted(self, resource, action, target):
        calls.append(target)
        return await real(self, resource, action, target)

    monkeypatch.setattr(request_authorization.RequestAuthorization, "allowed", counted)
    with TestClient(app) as client:
        client.cookies.set("access_token", "user")
        assert client.get("/api/v1/auth/me").status_code == 200
    assert calls == _ALL_PERMISSIONS


def test_me_without_middleware_uses_same_resolver(setup):
    async def run():
        result = await auth.get_me(_request())
        assert result.permissions == ["threads:read", "runs:read"]

    asyncio.run(run())
    assert UserResponse(id=str(_USER.id), email=_USER.email, system_role="user").permissions is None


@pytest.mark.parametrize("fail_closed", [True, False])
@pytest.mark.parametrize("kind", ["deny", "error", "malformed"])
def test_runtime_provider_outcomes_are_per_permission(setup, fail_closed, kind):
    _, config = setup
    config.authorization.fail_closed = fail_closed

    async def run():
        resolved = await request_authorization.resolve_request_authorization(_request())
        decision = AuthzDecision(allow=False) if kind == "deny" else "bad"
        provider = SimpleNamespace(aauthorize=AsyncMock(return_value=decision))
        if kind == "error":
            provider.aauthorize.side_effect = RuntimeError("SECRET_PROVIDER_VALUE")
        resolved.provider = provider
        assert await resolved.allowed("model", "use", "private") is (kind != "deny" and not fail_closed)

    asyncio.run(run())


@pytest.mark.parametrize("selected", [["private", "invented", "basic", "basic"], "basic", [None]])
@pytest.mark.parametrize("fail_closed", [True, False])
def test_filter_validates_subset_order_and_malformed_result(setup, selected, fail_closed):
    _, config = setup
    config.authorization.fail_closed = fail_closed

    async def run():
        resolved = await request_authorization.resolve_request_authorization(_request())
        resolved.provider = SimpleNamespace(filter_resources=MagicMock(return_value=selected))
        expected = ["basic", "private"] if isinstance(selected, list) and all(isinstance(x, str) for x in selected) or not fail_closed else []
        assert await resolved.filter_models(["basic", "private"]) == expected

    asyncio.run(run())


@pytest.mark.parametrize("error", [asyncio.CancelledError, GraphBubbleUp])
def test_policy_control_flow_is_not_fail_open(setup, error):
    _, config = setup
    config.authorization.fail_closed = False

    async def run():
        resolved = await request_authorization.resolve_request_authorization(_request())
        resolved.provider = SimpleNamespace(aauthorize=AsyncMock(side_effect=error))
        with pytest.raises(error):
            await resolved.allowed("route", "write", "threads:write")

    asyncio.run(run())


def test_provider_is_request_local_loop_affine_and_filter_off_loop(setup, monkeypatch):
    _, config = setup
    made = []

    class LoopProvider:
        name = "loop"

        def __init__(self):
            self.loop = asyncio.get_running_loop()
            self.thread = threading.get_ident()
            made.append(self)

        def authorize(self, request):
            raise AssertionError("use async decision")

        async def aauthorize(self, request):
            assert asyncio.get_running_loop() is self.loop
            return AuthzDecision(allow=request.principal.role == "admin")

        def filter_resources(self, principal, resource_type, candidates):
            assert threading.get_ident() != self.thread
            return candidates if principal.role == "admin" else []

    module = ModuleType("gateway_loop_policy_test")
    module.Provider = LoopProvider
    monkeypatch.setitem(sys.modules, module.__name__, module)
    config.authorization.provider.use = module.__name__ + ":Provider"
    config.authorization.provider.config = {}

    async def run():
        viewer, admin = _request(), _request(_ADMIN)
        permissions = await asyncio.gather(resolve_route_permissions_for_request(viewer, _USER), resolve_route_permissions_for_request(admin, _ADMIN))
        assert permissions == [[], _ALL_PERMISSIONS]
        resolved = await request_authorization.resolve_request_authorization(admin)
        assert await resolved.filter_models(["basic"]) == ["basic"]

    asyncio.run(run())
    asyncio.run(run())  # A new loop cannot receive an instance from the prior one.
    assert len(made) == 4
