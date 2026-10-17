"""Transport renewal preserves admission, consent and the research budget."""

import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_ppt_confirmation_service_authority import broker

RUN = "11111111-1111-4111-8111-111111111111"
THREAD = "22222222-2222-4222-8222-222222222222"
HOST = Path(__file__).resolve().parents[2] / "docker/sandbox-ppt-master"


class RenewalAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, snapshot, *, status="running", cancel=False, authenticated=True, extra=None):
        repo = SimpleNamespace(get=AsyncMock(return_value=snapshot))
        body = {"run_id": RUN, "thread_id": THREAD, **(extra or {})}
        request = SimpleNamespace(headers={"Authorization": "Bearer " + ("fixture-service" if authenticated else "wrong")})
        with (
            patch.dict(os.environ, {"SANDBOX_API_KEY": "fixture-service"}),
            patch.object(broker, "bounded_body", AsyncMock(return_value=body)),
            patch.object(broker, "_service_run_owner", AsyncMock(return_value=({"status": status, "cancel_action": cancel}, "owner"))),
            patch.object(broker, "admit_confirmation", AsyncMock()) as admission,
            patch.object(broker, "encode_scope", return_value="renewed.capability"),
            patch("deerflow.persistence.get_session_factory", return_value=object()),
            patch("deerflow.persistence.ppt_confirmation.sql.ConfirmationRepository", return_value=repo),
        ):
            response = await broker.dispatch_runtime(request, "confirmation-capability-refresh")
        admission.assert_not_awaited()
        self.assertEqual(repo.get.await_args.args[0].owner_id, "owner")
        self.assertEqual(response.headers["cache-control"], "no-store")
        return json.loads(response.body)

    async def test_manual_takeover_and_unknown_gate_are_never_reset_by_renewal(self):
        state = {"cancelled": False, "policy": {"auto_enabled": False}, "gate": {"state": "submission_unknown"}}
        before = copy.deepcopy(state)
        value = await self.invoke(state)
        self.assertEqual(value["task_id"], RUN)
        self.assertEqual(state, before)
        self.assertNotIn("auto_enabled", value)

    async def test_missing_cancelled_inactive_and_untrusted_requests_refused(self):
        for state, options in ((None, {}), ({"cancelled": True}, {}), ({"cancelled": False}, {"status": "completed"}),
                               ({"cancelled": False}, {"cancel": True}), ({"cancelled": False}, {"authenticated": False}),
                               ({"cancelled": False}, {"extra": {"auto_enabled": True}})):
            with self.subTest(options=options), self.assertRaises(broker.HTTPException):
                await self.invoke(state, **options)


class HostRenewalTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("confirmation_renewal_control", HOST / "ppt_confirmation_control.py")
        self.control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.control)
        self.task = SimpleNamespace(task_id=RUN, thread_id=THREAD, env={"PPT_RUNTIME_CAPABILITY": "old.capability"},
                                    secret_values=["old.capability"], state={"status": "completed"})

    def renew(self, **changes):
        value = {"capability": "new.capability", "task_id": RUN, "thread_id": THREAD,
                 "confirmation_protocol": "ppt-confirmation-v1", **changes}
        with patch.dict(os.environ, {"SANDBOX_API_KEY": "fixture-service"}), patch.object(self.control, "request_json", return_value=value) as rpc:
            result = self.control.refresh_capability(self.task)
        self.assertEqual(rpc.call_args.kwargs["body"], {"run_id": RUN, "thread_id": THREAD})
        return result

    def test_renewal_remains_private_and_retains_original_fingerprint_seed(self):
        before = copy.deepcopy(self.task.state)
        self.renew()
        self.renew(capability="third.capability")
        self.assertEqual(self.task.env["PPT_RESEARCH_GUARD_KEY"], "old.capability")
        self.assertEqual(self.task.env["PPT_RUNTIME_CAPABILITY"], "third.capability")
        self.assertEqual(self.task.state, before)
        self.assertIn("third.capability", self.task.secret_values)
        self.assertFalse(any(value == "fixture-service" for value in self.task.env.values()))

    def test_mismatched_renewal_never_changes_task(self):
        before = dict(self.task.env)
        for change in ({"task_id": "other"}, {"thread_id": "other"}, {"capability": ""}, {"confirmation_protocol": None}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.renew(**change)
            self.assertEqual(self.task.env, before)

    def test_repeated_research_failure_budget_survives_expired_token_replacement(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules), patch.object(sys, "path", [str(HOST), *sys.path]):
            import ppt_research_stdio as stdio
            from ppt_research_guard import ResearchGuard

            ledger = ResearchGuard.create(Path(folder) / "guard.sqlite", "old.capability")
            environment = {"PPT_RESEARCH_GUARD_PATH": str(ledger.path), "PPT_RESEARCH_GUARD_REQUIRED": "1",
                           "PPT_RESEARCH_GUARD_KEY": "old.capability", "PPT_RUNTIME_CAPABILITY": "old.capability"}
            with patch.dict(os.environ, environment), patch.object(stdio, "call_tool", side_effect=TimeoutError()) as provider:
                stdio.guarded_call({"name": "web_search", "arguments": {"query": "public fixture"}})
                self.renew()
                os.environ["PPT_RUNTIME_CAPABILITY"] = self.task.env["PPT_RUNTIME_CAPABILITY"]
                for _ in range(3):
                    stdio.guarded_call({"name": "web_search", "arguments": {"query": "public fixture"}})
                self.assertEqual(provider.call_count, 3)
            self.assertEqual(ledger.snapshot()["reason"], "repeated_research_failure")
            self.assertEqual(ledger.snapshot()["attempts"], 3)


if __name__ == "__main__":
    unittest.main()
