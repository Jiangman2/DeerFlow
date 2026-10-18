"""Exact source-Runner hook orchestration with fake HTTP/DB boundaries only."""

from __future__ import annotations

import ast
import asyncio
import hmac
import os
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from deerflow.ppt_confirmation.host_protocol import ConfirmationHostCompatibilityError

SOURCE = Path(__file__).resolve().parents[1] / "packages/harness/deerflow/agents/ppt_master_runner/agent.py"


class Human:
    def __init__(self, mode=None):
        self.additional_kwargs = {} if mode is None else {"ppt_confirmation_mode": mode}


def source_namespace():
    names = {"_confirmation_mode", "_confirmation_admission", "_stop_confirmation_task", "_poll_agent_turn", "_run_external_ppt_master", "_run_external_ppt_master_once", "_confirmation_slot"}
    nodes = [node for node in ast.parse(SOURCE.read_bytes()).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == names
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
    namespace = {
        "asyncio": asyncio,
        "os": os,
        "HumanMessage": Human,
        "_POLL_INTERVAL_SECONDS": 0,
        "_TERMINAL_STATUSES": {"completed", "failed", "cancelled"},
        "_CONFIRMATION_CLEANUP_LEASES": [],
        "_CONFIRMATION_SLOTS": threading.BoundedSemaphore(8),
        "contextmanager": contextmanager,
        "nullcontext": __import__("contextlib").nullcontext,
        "ConfirmationHostCompatibilityError": ConfirmationHostCompatibilityError,
    }
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace


class SourceRunnerHooks(unittest.IsolatedAsyncioTestCase):
    def fixture(self, mode=None):
        ns, events, trace, payloads = source_namespace(), [], [], []

        class Monitor:
            async def __aenter__(self):
                trace.append("monitor-enter")
                return self

            async def __aexit__(self, *args):
                trace.append("monitor-drained")

        class Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, **kwargs):
                trace.append("cancel-post")
                return SimpleNamespace(raise_for_status=lambda: None)

        async def acquire(*args, **kwargs):
            trace.append("sandbox-acquire")
            return "sandbox"

        def lease(*args):
            trace.append("lease-acquire")
            return "lease"

        def release_lease(*args):
            trace.append("lease-release")

        async def start(**kwargs):
            payloads.append(kwargs["payload"])
            trace.append("start")
            return SimpleNamespace(raise_for_status=lambda: None)

        async def poll(**kwargs):
            trace.append("poll")
            return {"status": "completed", "final_message": "fixture", "artifacts": []}

        provider = SimpleNamespace(
            acquire_async=acquire, get=lambda *args: SimpleNamespace(base_url="http://fixture.invalid:8080"), acquire_execution_lease=lease, release_execution_lease=release_lease, release=lambda *args: trace.append("sandbox-release")
        )
        ns.update(
            {
                "httpx": SimpleNamespace(Timeout=lambda *args, **kwargs: None, AsyncClient=Client),
                "_latest_user_text": lambda messages: "/ppt-master fixture",
                "_original_ppt_master_request": lambda messages, text: text,
                "build_ppt_master_runner_system_prompt": lambda *args, **kwargs: "fixture",
                "get_sandbox_provider": lambda: provider,
                "resolve_runtime_user_id": lambda runtime: "owner",
                "derive_agent_host_url": lambda url: "http://fixture.invalid:8788",
                "get_stream_writer": lambda: events.append,
                "_auth_headers": lambda key: {},
                "_wait_for_agent_host": AsyncMock(),
                "require_confirmation_host": AsyncMock(),
                "_start_agent_task_with_retry": start,
                "_poll_agent_turn": poll,
                "requested_delivery_contract": lambda request: {},
                "_report_agent_usage": lambda *args: {},
                "completed_agent_message": lambda *args: {},
                "_transport_failure_kind": lambda error: "fixture",
                "logger": SimpleNamespace(exception=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
                "create_monitor": lambda *args: Monitor(),
            }
        )
        state = {"messages": [Human(mode)]}
        runtime = SimpleNamespace(context={"thread_id": "thread", "run_id": "run"})
        config = SimpleNamespace(sandbox=SimpleNamespace(sandbox_api_key="fixture-only"))
        return ns, state, runtime, config, payloads, trace

    async def run_fixture(self, fixture):
        ns, state, runtime, config, *_ = fixture
        return await ns["_run_external_ppt_master"](state, {}, runtime, app_config=config)

    async def test_off_path_preserves_original_payload_and_has_no_broker_or_monitor(self):
        f = self.fixture()
        f[0]["_confirmation_admission"] = AsyncMock(side_effect=AssertionError("off broker"))
        f[0]["create_monitor"] = lambda *args: self.fail("off monitor")
        with patch.dict(os.environ, {"PPT_CONFIRMATION_TRANSPORT_ENABLED": "false"}):
            await self.run_fixture(f)
        self.assertEqual(set(f[4][0]), {"task_id", "thread_id", "prompt", "resume", "delivery_contract"})
        self.assertEqual(f[5][-2:], ["lease-release", "sandbox-release"])

    async def test_auto_metadata_selects_shared_monitor_and_drains_before_lease_release(self):
        f = self.fixture("auto_after_60s")
        f[0]["_confirmation_admission"] = AsyncMock(return_value={"runtime_capability": "fixture.token", "confirmation_protocol": "ppt-confirmation-v1"})
        with patch.dict(os.environ, {"PPT_CONFIRMATION_TRANSPORT_ENABLED": "false"}):
            await self.run_fixture(f)
        self.assertEqual(f[4][0]["confirmation_protocol"], "ppt-confirmation-v1")
        self.assertEqual(f[4][0]["runtime_capability"], "fixture.token")
        self.assertLess(f[5].index("monitor-drained"), f[5].index("lease-release"))
        self.assertTrue(f[0]["_confirmation_admission"].call_args.kwargs["strict_auto"])

    async def test_real_broker_hook_rejects_unavailable_or_unknown_auto_without_retry(self):
        for body in ({"capability": "fixture.token"}, {"capability": "fixture.token", "confirmation_protocol": "unknown"}, {"capability": "invalid"}):
            with self.subTest(body=body):
                ns, calls = source_namespace(), []

                class Client:
                    def __init__(self, **kwargs):
                        self.kwargs = kwargs
                        self_outer.assertFalse(kwargs["trust_env"])
                        self_outer.assertFalse(kwargs["follow_redirects"])

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *args):
                        return None

                    async def post(self, url, **kwargs):
                        calls.append((url, kwargs))
                        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

                self_outer = self
                ns.update(httpx=SimpleNamespace(AsyncClient=Client, Timeout=lambda *args: None), _auth_headers=lambda key: {})
                with self.assertRaises(RuntimeError):
                    await ns["_confirmation_admission"]("run", "thread", "fixture-key", strict_auto=True)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][1]["json"], {"run_id": "run", "thread_id": "thread"})
        f = self.fixture("auto_after_60s")
        f[0]["_confirmation_admission"] = AsyncMock(side_effect=RuntimeError("unavailable"))
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            await self.run_fixture(f)
        self.assertFalse(f[4])
        self.assertNotIn("sandbox-acquire", f[5])

    async def test_poll_waits_for_current_confirmation_and_exposes_safe_notice(self):
        ns, seen, events = source_namespace(), [], []

        class Monitor:
            def take_notice(self):
                return {"state": "waiting_user", "message": "fixture"} if not seen else None

            def observe_task(self, payload):
                seen.append(payload)

            def hold_completed(self):
                return len(seen) < 2

        async def get(**kwargs):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"task_id": "run", "thread_id": "thread", "status": "completed", "process_cleanup_complete": True})

        ns.update(_get_agent_task_with_retry=get, codex_event_to_messages=lambda payload: [])
        result = await asyncio.wait_for(ns["_poll_agent_turn"](client=None, host_url="http://fixture.invalid:8788", task_id="run", thread_id="thread", writer=events.append, confirmation_monitor=Monitor()), 1)
        self.assertEqual(result["task_id"], "run")
        self.assertEqual(len(seen), 2)
        self.assertEqual(events[0]["ppt_confirmation"]["state"], "waiting_user")

    async def test_unknown_enabled_cleanup_retains_exact_lease(self):
        f = self.fixture("auto_after_60s")
        f[0]["_confirmation_admission"] = AsyncMock(return_value={"runtime_capability": "fixture.token", "confirmation_protocol": "ppt-confirmation-v1"})
        f[0]["_poll_agent_turn"] = AsyncMock(side_effect=RuntimeError("fixture failure"))
        f[0]["_stop_confirmation_task"] = AsyncMock(side_effect=RuntimeError("unknown stop"))
        with self.assertRaises(RuntimeError):
            await self.run_fixture(f)
        self.assertNotIn("lease-release", f[5])
        self.assertNotIn("sandbox-release", f[5])
        self.assertEqual(f[0]["_CONFIRMATION_CLEANUP_LEASES"][0][1:], ("lease", "sandbox"))
        self.assertIn("monitor-drained", f[5])

    async def test_stopping_before_host_start_releases_slots_without_cancel_rpc(self):
        f = self.fixture("auto_after_60s")
        ns = f[0]
        ns["_confirmation_admission"] = AsyncMock(return_value={"runtime_capability": "fixture.token", "confirmation_protocol": "ppt-confirmation-v1"})
        ns["_wait_for_agent_host"] = AsyncMock(side_effect=asyncio.CancelledError())
        client = ns["httpx"].AsyncClient

        class UnavailableHost(client):
            async def post(self, *args, **kwargs):
                f[5].append("cancel-post")
                raise OSError("fixture host unavailable before task start")

        ns["httpx"].AsyncClient = UnavailableHost
        for _ in range(9):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_fixture(f)
        self.assertFalse(f[4])
        self.assertNotIn("cancel-post", f[5])
        self.assertEqual(ns["_CONFIRMATION_CLEANUP_LEASES"], [])
        self.assertEqual(f[5].count("lease-release"), 9)
        self.assertEqual(f[5].count("sandbox-release"), 9)

    async def test_unverified_stop_preserves_cancel_intent_and_exposes_cleanup_notice(self):
        f = self.fixture("auto_after_60s")
        ns = f[0]
        ns["_confirmation_admission"] = AsyncMock(return_value={"runtime_capability": "fixture.token", "confirmation_protocol": "ppt-confirmation-v1"})
        ns["_poll_agent_turn"] = AsyncMock(side_effect=asyncio.CancelledError())
        ns["_stop_confirmation_task"] = AsyncMock(side_effect=OSError("PRIVATE_FIXTURE_UNKNOWN_STOP"))
        with self.assertRaises(asyncio.CancelledError):
            await self.run_fixture(f)
        events = ns["get_stream_writer"]().__self__
        self.assertEqual([event["type"] for event in events], ["task_started", "task_cancelled"])
        self.assertIn("无法核实外部 Agent 已退出", events[-1]["error"])
        self.assertEqual(events[-1]["model_name"], "ppt-master-agent-host")
        self.assertNotIn("PRIVATE_FIXTURE", events[-1]["error"])
        self.assertEqual(ns["_CONFIRMATION_CLEANUP_LEASES"][0][1:], ("lease", "sandbox"))
        self.assertNotIn("lease-release", f[5])
        self.assertNotIn("sandbox-release", f[5])

    async def test_verified_stop_releases_resources_and_keeps_original_cancel_event(self):
        f = self.fixture("auto_after_60s")
        ns = f[0]
        ns["_confirmation_admission"] = AsyncMock(return_value={"runtime_capability": "fixture.token", "confirmation_protocol": "ppt-confirmation-v1"})
        ns["_poll_agent_turn"] = AsyncMock(side_effect=asyncio.CancelledError())
        ns["_stop_confirmation_task"] = AsyncMock()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_fixture(f)
        ns["_stop_confirmation_task"].assert_awaited_once()
        self.assertEqual(ns["get_stream_writer"]().__self__[-1], {"type": "task_cancelled", "task_id": "run", "thread_id": "thread"})
        self.assertEqual(ns["_CONFIRMATION_CLEANUP_LEASES"], [])
        self.assertEqual(f[5][-2:], ["lease-release", "sandbox-release"])

    async def test_off_stop_preserves_original_cancel_rpc_and_event(self):
        f = self.fixture()
        ns = f[0]
        ns["_poll_agent_turn"] = AsyncMock(side_effect=asyncio.CancelledError())
        ns["_confirmation_admission"] = AsyncMock(side_effect=AssertionError("off broker"))
        ns["_stop_confirmation_task"] = AsyncMock(side_effect=AssertionError("off confirmation stop"))
        with patch.dict(os.environ, {"PPT_CONFIRMATION_TRANSPORT_ENABLED": "false"}):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_fixture(f)
        self.assertEqual(f[5].count("cancel-post"), 1)
        ns["_stop_confirmation_task"].assert_not_awaited()
        self.assertEqual(ns["get_stream_writer"]().__self__[-1], {"type": "task_cancelled", "task_id": "run", "thread_id": "thread"})
        self.assertEqual(f[5][-2:], ["lease-release", "sandbox-release"])

    async def test_cancel_intent_is_once_and_matching_cleanup_is_required(self):
        ns, calls = source_namespace(), []

        class Client:
            async def post(self, url):
                calls.append("post")
                return SimpleNamespace(raise_for_status=lambda: None)

            async def get(self, url, **kwargs):
                calls.append("get")
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"task_id": "other", "thread_id": "thread", "status": "completed", "process_cleanup_complete": True})

        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            await ns["_stop_confirmation_task"](Client(), "http://fixture.invalid:8788", "run", "thread")
        self.assertEqual(calls, ["post", "get"])

    async def test_enabled_slots_are_bounded_before_admission_and_unknown_keeps_slot(self):
        ns = source_namespace()
        contexts = [ns["_confirmation_slot"](True) for _ in range(8)]
        slots = [context.__enter__() for context in contexts]
        with self.assertRaisesRegex(RuntimeError, "capacity unavailable"):
            with ns["_confirmation_slot"](True):
                self.fail("ninth slot entered")
        slots[0]["retained"] = True
        for context in contexts:
            context.__exit__(None, None, None)
        acquired = [ns["_CONFIRMATION_SLOTS"].acquire(blocking=False) for _ in range(8)]
        self.assertEqual(acquired, [True] * 7 + [False])
        for value in acquired:
            if value:
                ns["_CONFIRMATION_SLOTS"].release()


class FeatureReadiness(unittest.TestCase):
    def feature(self, factory):
        path = Path(__file__).resolve().parents[1] / "app/gateway/routers/features.py"
        function = next(node for node in ast.parse(path.read_bytes()).body if isinstance(node, ast.FunctionDef) and node.name == "_ppt_confirmation_available")
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function], type_ignores=[])
        namespace = {"os": os, "hmac": hmac, "get_session_factory": factory}
        exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
        return namespace["_ppt_confirmation_available"]

    def test_qwen_does_not_need_upstream_model_credentials_for_capability_readiness(self):
        ready = self.feature(lambda: object())
        with patch.dict(os.environ, {"PPT_CONFIRMATION_TRANSPORT_ENABLED": "true", "PPT_MASTER_RESPONSES_RELAY_KEY": "fixture-signing-only", "SANDBOX_API_KEY": "fixture-service"}, clear=True):
            self.assertTrue(ready(SimpleNamespace(sandbox=SimpleNamespace(sandbox_api_key="fixture-service"))))

    def test_missing_signing_auth_mismatched_config_or_uninitialized_store_disable_ui(self):
        good = {"PPT_CONFIRMATION_TRANSPORT_ENABLED": "true", "PPT_MASTER_RESPONSES_RELAY_KEY": "fixture-signing-only", "SANDBOX_API_KEY": "fixture-service"}
        for change, config_key, factory in [
            ({"PPT_CONFIRMATION_TRANSPORT_ENABLED": "false"}, "fixture-service", lambda: object()),
            ({"PPT_MASTER_RESPONSES_RELAY_KEY": "short"}, "fixture-service", lambda: object()),
            ({"SANDBOX_API_KEY": ""}, "fixture-service", lambda: object()),
            ({}, "different", lambda: object()),
            ({}, "fixture-service", lambda: None),
        ]:
            with self.subTest(change=list(change), config_key_match=config_key == "fixture-service"):
                with patch.dict(os.environ, {**good, **change}, clear=True):
                    self.assertFalse(self.feature(factory)(SimpleNamespace(sandbox=SimpleNamespace(sandbox_api_key=config_key))))


if __name__ == "__main__":
    unittest.main()
