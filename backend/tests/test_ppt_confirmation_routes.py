"""Real ASGI routes, authorization and CSRF, with disposable SQL stores.

Authentication identity is injected by this fixture, not a production JWT.
Only dependency accessors/native page transport are faked. No external service,
browser, Agent, production database or administrator credential is used.
"""

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import test_ppt_confirmation_controller as fixtures
from app.gateway.authz import AuthContext


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.ControllerTests.asyncSetUp(self)
        self.addAsyncCleanup(fixtures.ControllerTests.asyncTearDown, self)
        asyncio.get_running_loop().set_debug(False)
        await self.repo.admit(self.scope, runtime_profile="ppt-master-runner", skill_name="ppt-master", opted_in=True)
        await self.controller.tick(self.scope)
        self.row = {"run_id": self.scope.task_id, "thread_id": self.scope.thread_id, "user_id": self.scope.owner_id,
                    "assistant_id": "ppt-master-runner", "status": "running"}
        self.runs = SimpleNamespace(get=AsyncMock(side_effect=lambda *_args, **_kw: self.row))
        self.threads = SimpleNamespace(check_access=AsyncMock(side_effect=lambda tid, uid, **kw:
            tid == self.scope.thread_id and uid == self.scope.owner_id and kw.get("require_existing") is True))
        dependencies = ModuleType("app.gateway.deps")
        dependencies.get_run_store = lambda _: self.runs
        dependencies.get_thread_store = lambda _: self.threads
        dependencies.get_config = lambda: None
        artifacts = ModuleType("app.gateway.routers.artifacts")
        artifacts._resolve_artifact_owner_user_id = AsyncMock(return_value=self.scope.owner_id)

        async def forward(request, thread_id):
            self.assertEqual(thread_id, self.scope.thread_id)
            self.assertEqual(await request.body(), b"{}")
            self.assertEqual(request.headers["x-csrf-token"], "fixture-token")
            self.assertFalse((await self.repo.get(self.scope))["policy"]["auto_enabled"])
            return JSONResponse({"url": "/api/threads/thread-one/live-preview/5050/", "auto_enabled": False,
                                 "confirmation_submitted": False, "agent_started": False})

        self.forward = artifacts._forward_live_preview_to_runner = AsyncMock(side_effect=forward)
        config = ModuleType("app.gateway.auth.config")
        config.get_auth_config = lambda: SimpleNamespace(token_expiry_days=1)
        auth_disabled = ModuleType("app.gateway.auth_disabled")
        auth_disabled.is_auth_disabled = lambda: False
        internals = ModuleType("app.gateway.internal_auth")
        internals.INTERNAL_OWNER_USER_ID_HEADER_NAME = "X-DeerFlow-Owner-User-Id"
        internals.INTERNAL_SYSTEM_ROLE = "internal"
        modules = {"app.gateway.deps": dependencies, "app.gateway.routers.artifacts": artifacts,
                   "app.gateway.auth.config": config, "app.gateway.internal_auth": internals,
                   "app.gateway.auth_disabled": auth_disabled}
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        source = Path(__file__).resolve().parents[1] / "app/gateway"

        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        self.routes = load("ppt_confirmation_route_fixture", source / "routers/ppt_confirmations.py")
        self.csrf = load("ppt_confirmation_csrf_fixture", source / "csrf_middleware.py")
        self.csrf.is_auth_disabled = lambda: False
        self.routes.get_session_factory = lambda: self.factory
        # Exercise the real production inclusion chain (app -> features ->
        # confirmation), rather than mounting the child router only in tests.
        self.route_module = patch.dict(sys.modules, {"app.gateway.routers.ppt_confirmations": self.routes})
        self.route_module.start()
        self.addCleanup(self.route_module.stop)
        features = load("ppt_confirmation_features_fixture", source / "routers/features.py")
        app = FastAPI()
        app.include_router(features.router)
        app.add_middleware(self.csrf.CSRFMiddleware)

        @app.middleware("http")
        async def fixture_identity(request, call_next):
            uid = request.headers.get("x-fixture-user", self.scope.owner_id)
            user = SimpleNamespace(id=uid, system_role="admin" if uid == "admin" else "user") if uid != "anonymous" else None
            request.state.auth = AuthContext(user, ["threads:read", "threads:write"])
            return await call_next(request)

        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture.invalid",
                                       cookies={"csrf_token": "fixture-token"})
        self.addAsyncCleanup(self.client.aclose)
        self.base = f"/api/threads/{self.scope.thread_id}/ppt-confirmations/{self.scope.task_id}"

    async def test_get_is_readonly_and_redacts_payload(self):
        before = await self.repo.get(self.scope)
        response = await self.client.get(self.base + "/state")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["gate"]["state"], "waiting_user")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertNotIn("payload", response.text)
        self.assertEqual(before, await self.repo.get(self.scope))
        self.forward.assert_not_awaited()

    async def test_owner_takeover_disables_auto_before_single_forward(self):
        response = await self.client.post(self.base + "/open", content="{}", headers={"x-csrf-token": "fixture-token"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["confirmation_submitted"])
        self.forward.assert_awaited_once()
        state = await self.repo.get(self.scope)
        self.assertIsNone(state["gate"]["due_at"])
        self.assertEqual(state["gate"]["auto_block_reason"], "user_takeover")

    async def test_csrf_is_required_and_does_not_change_consent(self):
        for headers in ({}, {"x-csrf-token": "wrong"}):
            response = await self.client.post(self.base + "/open", content="{}", headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertTrue((await self.repo.get(self.scope))["policy"]["auto_enabled"])
        self.forward.assert_not_awaited()

    async def test_anonymous_foreign_user_and_foreign_admin_write_denied(self):
        for user, expected in (("anonymous", 401), ("other", 404), ("admin", 404)):
            headers = {"x-fixture-user": user, "x-csrf-token": "fixture-token"}
            self.assertEqual((await self.client.post(self.base + "/open", content="{}", headers=headers)).status_code, expected)
        self.forward.assert_not_awaited()

    async def test_admin_read_cannot_open_and_missing_wrong_run_stays_hidden(self):
        response = await self.client.get(self.base + "/state", headers={"x-fixture-user": "admin"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["can_open"])
        self.row["user_id"] = "other"
        self.assertEqual((await self.client.get(self.base + "/state")).status_code, 404)

    async def test_invalid_parameters_or_inactive_task_never_opens(self):
        for body in ('{"port":6060}', '""', 'x' * 10000):
            response = await self.client.post(self.base + "/open", content=body, headers={"x-csrf-token": "fixture-token"})
            self.assertEqual(response.status_code, 400)
        self.row["status"] = "error"
        response = await self.client.post(self.base + "/open", content="{}", headers={"x-csrf-token": "fixture-token"})
        self.assertEqual(response.status_code, 409)
        self.assertFalse((await self.client.get(self.base + "/state")).json()["run_active"])
        self.forward.assert_not_awaited()

    async def test_cancelled_history_has_no_actionable_gate(self):
        snapshot = await self.repo.get(self.scope)
        await self.repo.cancel_task(self.scope, expected_revision=snapshot["revision"])
        response = await self.client.get(self.base + "/state")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["gate"])
        self.assertFalse(response.json()["can_open"])


if __name__ == "__main__":
    unittest.main()
