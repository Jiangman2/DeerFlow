"""Real Gateway forwarding into the dedicated Runner's production route tree.

Only session identity, durable stores, Sandbox discovery and socket transports
are fixtures. The forwarding function, both route handlers, owner permissions,
CSRF middleware and Runner features-router inclusion are real. No lifespan,
database, model, external server or process is started.
"""

import ast
import copy
import importlib.util
import sys
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI

from app.gateway.authz import AuthContext
from app.gateway.csrf_middleware import CSRFMiddleware
from app.gateway.routers import artifacts, features, ppt_confirmations
from deerflow.config import app_config
from deerflow.sandbox import sandbox_provider

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "docker/ppt-master-runner/production-source/backend/app/gateway"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def swapped_module(name, module):
    """Replace one import edge without clearing unrelated lazy imports."""
    missing = object()
    previous = sys.modules.get(name, missing)
    sys.modules[name] = module
    try:
        yield
    finally:
        if previous is missing:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


class RunnerReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.runner_routes = load("fixture_runner_confirmation_receiver", RUNNER / "routers/ppt_confirmations.py")
        # Load the receiver's real features.py, not the ordinary Gateway file.
        with swapped_module("app.gateway.routers.ppt_confirmations", self.runner_routes):
            self.runner_features = load("fixture_runner_features", RUNNER / "routers/features.py")
        self.runner_artifacts = load("fixture_runner_artifacts", RUNNER / "routers/artifacts.py")
        self.patches.enter_context(patch.object(ppt_confirmations, "os", SimpleNamespace(environ={})))
        self.runner_routes.os = SimpleNamespace(environ={"DEER_FLOW_RUNTIME_PROFILE": "ppt-master-runner"})
        self.patches.enter_context(patch("app.gateway.csrf_middleware.is_auth_disabled", return_value=False))
        self.patches.enter_context(patch.object(artifacts, "PPT_MASTER_RUNNER_INTERNAL_URL", "http://runner.invalid:8003"))
        self.snapshot = {"revision": 1, "cancelled": False, "policy": {"auto_enabled": True}, "gate": {"state": "waiting_user"}}
        self.disables = []
        self.forward_requests = []
        self.host_requests = []
        self.row = {"run_id": "fixture-task", "thread_id": "fixture-thread", "user_id": "owner", "assistant_id": "ppt-master-runner", "status": "running"}
        self.runner_row = dict(self.row)
        self.provider = SimpleNamespace(get_for_thread=Mock(return_value=SimpleNamespace(base_url="http://sandbox.invalid:8080")))
        self.patches.enter_context(patch.object(sandbox_provider, "get_sandbox_provider", return_value=self.provider))
        self.patches.enter_context(patch.object(app_config, "get_app_config", return_value=SimpleNamespace(sandbox=SimpleNamespace(sandbox_api_key="fixture-host-auth"))))

        class Repository:
            async def get(_repo, scope):
                self.assertEqual((scope.owner_id, scope.thread_id, scope.task_id), ("owner", "fixture-thread", "fixture-task"))
                return copy.deepcopy(self.snapshot)

            async def disable_auto(_repo, scope, *, expected_revision):
                self.assertEqual(expected_revision, self.snapshot["revision"])
                self.disables.append(scope)
                self.snapshot["policy"]["auto_enabled"] = False
                self.snapshot["revision"] += 1

        for module in (ppt_confirmations, self.runner_routes):
            self.patches.enter_context(patch.object(module, "get_session_factory", return_value=object()))
            self.patches.enter_context(patch.object(module, "ConfirmationRepository", side_effect=lambda _factory: Repository()))

        # The imported owner helper is byte-identical in both source trees.
        # Keep the actual Gateway forward; use the actual Runner owner helper.
        artifact_dispatch = SimpleNamespace(
            _forward_live_preview_to_runner=artifacts._forward_live_preview_to_runner,
            _resolve_artifact_owner_user_id=self.runner_artifacts._resolve_artifact_owner_user_id,
        )
        self.patches.enter_context(swapped_module("app.gateway.routers.artifacts", artifact_dispatch))
        self.gateway = self.make_app(features.router, runner=False)
        self.receiver = self.make_app(self.runner_features.router, runner=True)
        self.client_class = httpx.AsyncClient
        self.gateway_client = self.client_class(transport=httpx.ASGITransport(app=self.gateway), base_url="http://browser.invalid")
        self.receiver_client = self.client_class(transport=httpx.ASGITransport(app=self.receiver), base_url="http://runner.invalid:8003")
        self.addAsyncCleanup(self.gateway_client.aclose)
        self.addAsyncCleanup(self.receiver_client.aclose)
        # Replace sockets only: neither _forward_live_preview_to_runner nor
        # open_native_page is mocked. Forwarded path/body/cookies reach ASGI.
        self.patches.enter_context(patch.object(httpx, "AsyncClient", side_effect=self.transport_client))
        self.path = "/api/threads/fixture-thread/ppt-confirmations/fixture-task/open"

    def make_app(self, router, *, runner):
        app = FastAPI()
        app.include_router(router)
        app.state.thread_store = SimpleNamespace(
            get=AsyncMock(return_value={"user_id": "owner"}),
            check_access=AsyncMock(side_effect=lambda tid, uid, **kw: tid == "fixture-thread" and uid == "owner" and kw.get("require_existing") is True),
        )
        app.state.run_store = SimpleNamespace(get=AsyncMock(side_effect=lambda *_args, **_kw: self.runner_row if runner else self.row))
        app.add_middleware(CSRFMiddleware)

        @app.middleware("http")
        async def fixture_identity(request, call_next):
            # Synthetic session resolver, deliberately not a production JWT.
            identity = request.cookies.get("fixture_user")
            user = SimpleNamespace(id=identity, system_role="admin" if identity == "admin" else "user") if identity else None
            request.state.auth = AuthContext(user, ["threads:read", "threads:write"])
            return await call_next(request)

        return app

    def transport_client(self, **kwargs):
        self.assertFalse(kwargs.get("trust_env", True))
        self.assertFalse(kwargs.get("follow_redirects", True))
        return self.client_class(transport=httpx.MockTransport(self.dispatch), **kwargs)

    async def dispatch(self, request):
        if request.url.host == "runner.invalid":
            self.assertFalse(self.snapshot["policy"]["auto_enabled"])
            self.forward_requests.append(request)
            self.assertEqual(await request.aread(), b"{}")
            self.assertEqual(request.headers["x-csrf-token"], "fixture-csrf")
            # Authenticated cookies must survive the real Gateway bridge.
            self.assertIn("fixture_user=owner", request.headers["cookie"])
            transport = httpx.ASGITransport(app=self.receiver)
            return await transport.handle_async_request(request)
        self.assertEqual((request.url.host, request.url.port), ("sandbox.invalid", 8788))
        self.assertEqual(request.headers["authorization"], "Bearer fixture-host-auth")
        self.host_requests.append(request)
        self.assertFalse(self.snapshot["policy"]["auto_enabled"])
        if request.method == "GET":
            self.assertEqual(request.url.path, "/v1/tasks/fixture-task/confirmation")
            return httpx.Response(200, json={"state": "owner_unreachable"})
        self.assertEqual((request.method, request.url.path), ("POST", "/v1/tasks/fixture-task/confirmation/recover"))
        self.assertEqual(await request.aread(), b"{}")
        return httpx.Response(200, json={"state": "waiting_user", "port": 5051})

    def headers(self, user="owner", csrf=True):
        headers = {"cookie": f"fixture_user={user}; csrf_token=fixture-csrf" if user else "csrf_token=fixture-csrf"}
        if csrf:
            headers["x-csrf-token"] = "fixture-csrf"
        return headers

    async def test_real_gateway_forward_reaches_runner_then_authenticated_host_recovery(self):
        response = await self.gateway_client.post(self.path, content=b"{}", headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"url": "/api/threads/fixture-thread/live-preview/5051/", "auto_enabled": False, "confirmation_submitted": False, "agent_started": False})
        self.assertEqual(len(self.disables), 1)
        self.assertEqual(len(self.forward_requests), 1)
        self.assertEqual(self.forward_requests[0].url.path, self.path)
        self.assertEqual(len(self.host_requests), 2)
        self.provider.get_for_thread.assert_called_once_with("fixture-thread", user_id="owner")

    async def test_missing_receiver_reproduces_404_through_actual_forwarder(self):
        # Pre-fix Runner app included features but had neither confirmation
        # endpoint. Removing only those routes reproduces that registration.
        self.receiver.router.routes[:] = [route for route in self.receiver.router.routes if "ppt-confirmations" not in str(getattr(route, "path", ""))]
        response = await self.gateway_client.post(self.path, content=b"{}", headers=self.headers())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.forward_requests), 1)
        self.assertEqual(self.host_requests, [])

    async def test_receiver_independently_checks_owner_and_anonymous_identity(self):
        for user, expected in (("other", 404), ("admin", 404), (None, 401)):
            with self.subTest(user=user):
                response = await self.receiver_client.post(self.path, content=b"{}", headers=self.headers(user))
                self.assertEqual(response.status_code, expected, response.text)
        self.assertEqual(self.disables, [])
        self.assertEqual(self.host_requests, [])

    async def test_both_hops_require_csrf_and_reject_browser_selected_targets(self):
        for client in (self.gateway_client, self.receiver_client):
            response = await client.post(self.path, content=b"{}", headers=self.headers(csrf=False))
            self.assertEqual(response.status_code, 403)
            response = await client.post(self.path, content=b'{"port":8788}', headers=self.headers())
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.forward_requests, [])
        self.assertEqual(self.host_requests, [])

    async def test_receiver_rechecks_run_identity_after_gateway_authorization(self):
        self.runner_row["assistant_id"] = "ordinary-agent"
        response = await self.gateway_client.post(self.path, content=b"{}", headers=self.headers())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.forward_requests), 1)
        self.assertEqual(self.host_requests, [])

    async def test_receiver_missing_sandbox_does_not_create_agent_or_contact_host(self):
        self.provider.get_for_thread.return_value = None
        response = await self.gateway_client.post(self.path, content=b"{}", headers=self.headers())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.host_requests, [])

    def test_receiver_sources_and_app_inclusion_match_shared_contract(self):
        for name in ("features.py", "ppt_confirmations.py"):
            self.assertEqual((RUNNER / "routers" / name).read_bytes(), (ROOT / "backend/app/gateway/routers" / name).read_bytes())
        tree = ast.parse((RUNNER / "app.py").read_text(encoding="utf-8"))
        self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router" and node.args and ast.unparse(node.args[0]) == "features.router" for node in ast.walk(tree)))
        receiver_paths = {getattr(route, "path", "") for route in self.receiver.routes}
        self.assertIn("/api/threads/{thread_id}/ppt-confirmations/{task_id}/open", receiver_paths)


if __name__ == "__main__":
    unittest.main()
