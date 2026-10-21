"""Real bounded HTTP fingerprint and both exact Runner task-start boundaries."""

import ast
import asyncio
import json
import logging
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from deerflow.ppt_confirmation import host_protocol as protocol

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    ROOT / "backend/packages/harness/deerflow/agents/ppt_master_runner/agent.py",
    ROOT / "docker/ppt-master-runner/production-source/backend/packages/harness/deerflow/agents/ppt_master_runner/agent.py",
)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for part in self.parts:
            self.reads += 1
            yield part

    async def aclose(self):
        self.closed = True


def reply(status, body, *, content_type="application/json"):
    raw = json.dumps(body).encode() if isinstance(body, dict) else body
    return httpx.Response(status, headers={"content-type": content_type}, stream=Chunks([raw]))


class HostProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, replies):
        requests = []

        def dispatch(request):
            requests.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.headers["authorization"], "Bearer fixture-host-auth")
            self.assertEqual(request.headers["accept-encoding"], "identity")
            self.assertEqual((request.url.host, request.url.port), ("host.invalid", 8788))
            result = replies[len(requests) - 1]
            if isinstance(result, Exception):
                raise result
            return result

        async with httpx.AsyncClient(transport=httpx.MockTransport(dispatch), headers={"Authorization": "Bearer fixture-host-auth"}, trust_env=False) as client:
            try:
                await protocol.require_confirmation_host(client=client, host_url="http://host.invalid:8788", task_id="fresh-run")
            except protocol.ConfirmationHostCompatibilityError as error:
                error.requests = requests
                raise
        return requests

    async def test_exact_new_fingerprint_has_authenticated_absence_and_random_64hex_receipt(self):
        requests = await self.probe([reply(404, {"detail": "task not found"}), reply(409, {"detail": "confirmation_continuation_unavailable"})])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].url.path, "/v1/tasks/fresh-run")
        self.assertEqual(requests[0].url.params["after_seq"], "2147483647")
        self.assertRegex(requests[1].url.path, r"^/v1/tasks/fresh-run/confirmation/continuation-receipts/[0-9a-f]{64}$")

    async def test_exact_old_route_is_visible_new_conversation_failure(self):
        with self.assertRaisesRegex(protocol.ConfirmationHostCompatibilityError, "请新建会话") as caught:
            await self.probe([reply(404, {"detail": "task not found"}), reply(404, {"detail": "not found"})])
        self.assertEqual(caught.exception.reason, "legacy_host")
        self.assertEqual(len(caught.exception.requests), 2)

    async def test_existing_run_is_never_reposted_or_body_downloaded(self):
        response = reply(200, b"PRIVATE_EXISTING_TASK" * 10000)
        stream = response.stream
        with self.assertRaisesRegex(protocol.ConfirmationHostCompatibilityError, "未重复提交") as caught:
            await self.probe([response])
        self.assertEqual(len(caught.exception.requests), 1)
        self.assertEqual(stream.reads, 0)
        self.assertTrue(stream.closed)

    async def test_unrecognized_status_body_mime_redirect_or_oversize_never_admits(self):
        bad_replies = [
            reply(409, {"detail": "other conflict"}),
            reply(409, {"detail": "confirmation_continuation_unavailable", "extra": True}),
            reply(404, {"detail": "task not found"}),
            reply(200, {"detail": "confirmation_continuation_unavailable"}),
            reply(409, b'{"detail":"other","detail":"confirmation_continuation_unavailable"}'),
            reply(409, {"detail": "confirmation_continuation_unavailable"}, content_type="text/html"),
            reply(302, b"PRIVATE_REDIRECT"),
            reply(409, b"x" * 4097),
            reply(409, b"not JSON"),
        ]
        for response in bad_replies:
            with self.subTest(status=response.status_code), self.assertRaises(protocol.ConfirmationHostCompatibilityError) as caught:
                await self.probe([reply(404, {"detail": "task not found"}), response])
            self.assertEqual(caught.exception.reason, "unavailable")
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertEqual(len(caught.exception.requests), 2)

    async def test_auth_failure_wrong_absence_or_timeout_has_no_second_probe(self):
        for response in (reply(401, b"PRIVATE_AUTH"), reply(403, b"PRIVATE_AUTH"), reply(404, {"detail": "not found"}), httpx.ReadTimeout("PRIVATE_TIMEOUT")):
            with self.subTest(reply=type(response).__name__), self.assertRaises(protocol.ConfirmationHostCompatibilityError) as caught:
                await self.probe([response])
            self.assertEqual(len(caught.exception.requests), 1)
            self.assertNotIn("PRIVATE", str(caught.exception))

    async def test_total_deadline_and_cancellation_do_not_turn_into_admission(self):
        async def delayed(_request):
            await asyncio.sleep(1)
            return reply(404, {"detail": "task not found"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(delayed), trust_env=False) as client:
            with patch.object(protocol, "_TOTAL_TIMEOUT_SECONDS", 0.01), self.assertRaises(protocol.ConfirmationHostCompatibilityError):
                await protocol.require_confirmation_host(client=client, host_url="http://host.invalid:8788", task_id="fresh-run")
            with self.assertRaises(asyncio.CancelledError):
                task = asyncio.create_task(protocol.require_confirmation_host(client=client, host_url="http://host.invalid:8788", task_id="fresh-run"))
                await asyncio.sleep(0)
                task.cancel()
                await task

    def test_both_runner_images_ship_exact_same_small_protocol_helper(self):
        module = "packages/harness/deerflow/ppt_confirmation/host_protocol.py"
        self.assertEqual((ROOT / "backend" / module).read_bytes(), (ROOT / "docker/ppt-master-runner/production-source/backend" / module).read_bytes())


class RunnerStartBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def run_source(self, source, *, legacy=False, collision=False, unknown=False, resumed=False, admitted=True):
        production = "production-source" in source.as_posix()
        name = "_run_external_ppt_master" if production else "_run_external_ppt_master_once"
        names = {name, "_start_agent_task_with_retry", "_auth_headers"}
        nodes = [node for node in ast.parse(source.read_bytes()).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        self.assertEqual({node.name for node in nodes}, names)
        tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
        events, calls, releases, retained = [], [], [], []

        def dispatch(request):
            calls.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer fixture-host-auth")
            if request.method == "POST":
                self.assertEqual(request.url.path, "/v1/tasks")
                return reply(202, {})
            if request.url.path == "/v1/tasks/fresh-run":
                return reply(200 if collision else 404, {"detail": "task not found"})
            self.assertRegex(request.url.path, r"/confirmation/continuation-receipts/[0-9a-f]{64}$")
            if unknown:
                return reply(409, {"detail": "PRIVATE_UNKNOWN_CONFLICT"})
            return reply(404, {"detail": "not found"}) if legacy else reply(409, {"detail": "confirmation_continuation_unavailable"})

        class Monitor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        provider = SimpleNamespace(
            acquire_async=AsyncMock(return_value="sandbox"),
            get=lambda _sid: SimpleNamespace(base_url="http://host.invalid:8080"),
            acquire_execution_lease=lambda _sid: "lease",
            release_execution_lease=lambda *_args: releases.append("lease"),
            release=lambda _sid: releases.append("sandbox"),
        )

        async def release(*_args):
            releases.extend(["lease", "sandbox"])

        capability = {"runtime_capability": "fixture.capability", **({"confirmation_protocol": "ppt-confirmation-v1"} if admitted else {})}
        namespace = {
            "asyncio": asyncio,
            "nullcontext": nullcontext,
            "logger": logging.getLogger("host-protocol-fixture"),
            "httpx": SimpleNamespace(Timeout=httpx.Timeout, AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(dispatch), **kwargs)),
            "ConfirmationHostCompatibilityError": protocol.ConfirmationHostCompatibilityError,
            "require_confirmation_host": protocol.require_confirmation_host,
            "_latest_user_text": lambda _messages: "继续" if resumed else "/ppt-master fixture",
            "_original_ppt_master_request": lambda *_args: "/ppt-master fixture",
            "build_ppt_master_runner_system_prompt": lambda *_args, **_kw: "fixture",
            "_confirmation_mode": lambda _messages: (admitted, False),
            "_confirmation_admission": AsyncMock(return_value=capability),
            "_mint_ppt_capability": AsyncMock(return_value={"capability": "fixture.capability", **({"confirmation_protocol": "ppt-confirmation-v1"} if admitted else {})}),
            "get_sandbox_provider": lambda: provider,
            "resolve_runtime_user_id": lambda _runtime: "fixture-owner",
            "derive_agent_host_url": lambda _url: "http://host.invalid:8788",
            "get_stream_writer": lambda: events.append,
            "_wait_for_agent_host": AsyncMock(),
            "create_monitor": lambda *_args: Monitor(),
            "requested_delivery_contract": lambda _request: {},
            "_poll_agent_turn": AsyncMock(return_value={"status": "completed", "final_message": "fixture", "artifacts": []}),
            "_report_agent_usage": lambda *_args: None,
            "observed_session_usage": lambda _value: None,
            "completed_agent_message": lambda *_args: {},
            "_transport_failure_kind": lambda error: type(error).__name__,
            "_CONFIRMATION_CLEANUP_LEASES": [],
            "_retain_ppt_cleanup_guard": lambda *args: retained.append(args),
            "PPT_MASTER_AGENT_HOST_REQUEST_RETRY_ATTEMPTS": 1,
            "_acquire_ppt_sandbox": AsyncMock(return_value="sandbox"),
            "_acquire_ppt_execution_lease": AsyncMock(return_value="lease"),
            "_release_ppt_resources": release,
            "PptProviderReceipts": lambda *_args, **_kw: SimpleNamespace(),
        }
        slot = {"retained": False}
        self.last_namespace, self.last_retained, self.last_slot = namespace, retained, slot
        exec(compile(ast.fix_missing_locations(tree), str(source), "exec"), namespace)
        with patch("deerflow.ppt_confirmation.monitor.create_monitor", side_effect=lambda *_args: Monitor()):
            try:
                result = await namespace[name](
                    {"messages": []},
                    {},
                    SimpleNamespace(context={"thread_id": "fixture-thread", "run_id": "fresh-run"}),
                    app_config=SimpleNamespace(sandbox=SimpleNamespace(sandbox_api_key="fixture-host-auth")),
                    **({"confirmation_slot": slot} if not production else {}),
                )
            except RuntimeError:
                result = None
        return result, events, calls, releases

    async def test_both_sources_reject_old_host_before_post_cancel_and_publish_durable_failure(self):
        for source in SOURCES:
            with self.subTest(source=source.as_posix()):
                result, events, calls, releases = await self.run_source(source, legacy=True, resumed=True)
                self.assertIsNone(result)
                self.assertEqual([request.method for request in calls], ["GET", "GET"])
                self.assertEqual(events[-1]["type"], "task_failed")
                self.assertIn("请新建会话", events[-1]["error"])
                self.assertEqual(releases, ["lease", "sandbox"])

    async def test_both_sources_reject_existing_run_without_repost_or_cancel(self):
        for source in SOURCES:
            with self.subTest(source=source.as_posix()):
                result, events, calls, releases = await self.run_source(source, collision=True)
                self.assertIsNone(result)
                self.assertEqual([request.method for request in calls], ["GET"])
                self.assertEqual(events[-1]["type"], "task_failed")
                self.assertIn("未重复提交", events[-1]["error"])
                self.assertEqual(releases, [])
                if "production-source" in source.as_posix():
                    self.assertEqual(len(self.last_retained), 1)
                    self.assertEqual(self.last_retained[0][1:], ("lease", "sandbox", "http://host.invalid:8788", "fresh-run", "fixture-thread", "fixture-host-auth"))
                else:
                    self.assertEqual(self.last_namespace["_CONFIRMATION_CLEANUP_LEASES"][0][1:], ("lease", "sandbox"))
                    self.assertIs(self.last_slot["retained"], True)

    async def test_both_sources_reject_unknown_conflict_with_safe_durable_failure(self):
        for source in SOURCES:
            with self.subTest(source=source.as_posix()):
                result, events, calls, releases = await self.run_source(source, unknown=True)
                self.assertIsNone(result)
                self.assertEqual([request.method for request in calls], ["GET", "GET"])
                self.assertEqual(events[-1]["type"], "task_failed")
                self.assertIn("无法核实", events[-1]["error"])
                self.assertNotIn("PRIVATE", events[-1]["error"])
                self.assertEqual(releases, ["lease", "sandbox"])

    async def test_both_sources_admit_new_host_fresh_task_without_changing_protocol_or_run(self):
        for source in SOURCES:
            with self.subTest(source=source.as_posix()):
                result, events, calls, releases = await self.run_source(source)
                self.assertIsNotNone(result)
                self.assertEqual([request.method for request in calls], ["GET", "GET", "POST"])
                body = json.loads(calls[-1].content)
                self.assertEqual(body["confirmation_protocol"], "ppt-confirmation-v1")
                self.assertEqual(body["task_id"], "fresh-run")
                self.assertIs(body["resume"], False)
                self.assertEqual(events[-1]["type"], "task_completed")
                self.assertEqual(releases, ["lease", "sandbox"])

    async def test_both_sources_without_broker_protocol_keep_original_task_path(self):
        for source in SOURCES:
            with self.subTest(source=source.as_posix()):
                result, _events, calls, releases = await self.run_source(source, admitted=False, resumed=True)
                self.assertIsNotNone(result)
                self.assertEqual([request.method for request in calls], ["POST"])
                self.assertNotIn("confirmation_protocol", json.loads(calls[-1].content))
                self.assertIs(json.loads(calls[-1].content)["resume"], True)
                self.assertEqual(releases, ["lease", "sandbox"])


class ExistingTaskCleanupGuardTests(unittest.IsolatedAsyncioTestCase):
    async def invoke_guard(self, payloads, *, stop_without_terminal=False):
        source = SOURCES[1]
        node = next(node for node in ast.parse(source.read_bytes()).body if isinstance(node, ast.FunctionDef) and node.name == "_retain_ppt_cleanup_guard")
        calls, releases = [], []

        def dispatch(request):
            calls.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/v1/tasks/existing-run")
            self.assertEqual(request.url.params["after_seq"], "2147483647")
            self.assertEqual(request.headers["authorization"], "Bearer fixture-host-auth")
            return reply(200, payloads[min(len(calls) - 1, len(payloads) - 1)])

        async def release(*args):
            releases.append((len(calls), args))

        async def pause(_seconds):
            await asyncio.sleep(3600 if stop_without_terminal else 0)

        namespace = {
            "asyncio": SimpleNamespace(create_task=asyncio.create_task, CancelledError=asyncio.CancelledError, sleep=pause),
            "httpx": SimpleNamespace(Timeout=httpx.Timeout, AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(dispatch), **kwargs)),
            "_auth_headers": lambda key: {"Authorization": "Bearer " + key},
            "_TERMINAL_STATUSES": {"completed", "failed", "cancelled"},
            "_PPT_CLEANUP_GUARDS": set(),
            "_release_ppt_resources": release,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        provider = object()
        namespace["_retain_ppt_cleanup_guard"](provider, "exact-lease", "sandbox", "http://host.invalid:8788", "existing-run", "thread", "fixture-host-auth")
        task = next(iter(namespace["_PPT_CLEANUP_GUARDS"]))
        if stop_without_terminal:
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        else:
            await asyncio.wait_for(task, timeout=1)
        return calls, releases, provider

    async def test_only_exact_terminal_cleanup_releases_collision_lease(self):
        exact = {"task_id": "existing-run", "thread_id": "thread", "status": "completed", "process_cleanup_complete": True}
        calls, releases, provider = await self.invoke_guard(
            [
                {**exact, "thread_id": "foreign"},
                {**exact, "task_id": "foreign"},
                {**exact, "status": "running"},
                {**exact, "process_cleanup_complete": False},
                exact,
            ]
        )
        self.assertEqual(len(calls), 5)
        self.assertEqual(releases, [(5, (provider, "exact-lease", "sandbox"))])

    async def test_shutdown_without_terminal_cleanup_never_authorizes_release(self):
        _calls, releases, _provider = await self.invoke_guard(
            [
                {"task_id": "existing-run", "thread_id": "thread", "status": "running", "process_cleanup_complete": False},
            ],
            stop_without_terminal=True,
        )
        self.assertEqual(releases, [])


if __name__ == "__main__":
    unittest.main()
