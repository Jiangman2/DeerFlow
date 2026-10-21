"""No service request/model prose can manufacture durable auto consent."""

import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/harness"))
SPEC = importlib.util.spec_from_file_location("confirmation_broker_test", Path(__file__).resolve().parents[1] / "app/gateway/ppt_runtime_broker.py")
broker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(broker)


class ServiceAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.scope = {"owner": "owner", "thread": "thread", "run": "run"}
        self.snapshot = {
            "policy": {"auto_enabled": True},
            "cancelled": False,
            "revision": 5,
            "gate": {
                "state": "submission_pending",
                "task_id": "run",
                "thread_id": "thread",
                "owner_id": "owner",
                "submission": {"id": "op", "mode": "automatic"},
                "version": "a" * 64,
                "owner_version": "b" * 64,
                "source_digest": "c" * 64,
                "payload_digest": "d" * 64,
                "project_id": "project",
                "stage": "stage1",
                "gate_id": "e" * 64,
                "revision": 3,
            },
        }
        self.repo = SimpleNamespace(get=AsyncMock(side_effect=lambda scope: self.snapshot))

    async def invoke(self, operation="confirmation-claim", body=None):
        with (
            patch.object(broker, "require_scope", AsyncMock(return_value=self.scope)) as authenticate,
            patch.object(broker, "bounded_body", AsyncMock(return_value=body or {"operation_id": "op"})),
            patch("deerflow.persistence.get_session_factory", return_value=object()),
            patch("deerflow.persistence.ppt_confirmation.sql.ConfirmationRepository", return_value=self.repo),
        ):
            result = await broker.dispatch_runtime(object(), operation)
        authenticate.assert_awaited_once()
        return json.loads(result.body)

    async def test_matching_committed_automatic_claim_only_returns_metadata(self):
        self.snapshot["gate"]["unreviewed_payload"] = "private"
        value = await self.invoke()
        self.assertEqual(value["task_id"], "run")
        self.assertEqual(value["claim"]["submission"]["id"], "op")
        self.assertNotIn("private", json.dumps(value))
        self.assertNotIn("unreviewed_payload", value["claim"])

    async def test_unadmitted_disabled_cancelled_unknown_or_other_claim_refused(self):
        original = json.loads(json.dumps(self.snapshot))
        for mutation in ("missing", "disabled", "cancelled", "unknown", "other_op", "other_task", "manual"):
            self.snapshot = json.loads(json.dumps(original))
            if mutation == "missing":
                self.snapshot = None
            elif mutation == "disabled":
                self.snapshot["policy"]["auto_enabled"] = False
            elif mutation == "cancelled":
                self.snapshot["cancelled"] = True
            elif mutation == "unknown":
                self.snapshot["gate"]["state"] = "submission_unknown"
            elif mutation == "other_op":
                self.snapshot["gate"]["submission"]["id"] = "different"
            elif mutation == "other_task":
                self.snapshot["gate"]["task_id"] = "different"
            else:
                self.snapshot["gate"]["submission"]["mode"] = "manual"
            with self.subTest(mutation=mutation), self.assertRaises(broker.HTTPException) as failed:
                await self.invoke()
            self.assertEqual(failed.exception.status_code, 409)

    async def test_request_cannot_supply_policy_claim_or_owner(self):
        for body in ({"operation_id": "op", "auto_enabled": True}, {"operation_id": "op", "owner_id": "other"}, {"claim": self.snapshot["gate"]}):
            with self.assertRaises(broker.HTTPException) as failed:
                await self.invoke(body=body)
            self.assertEqual(failed.exception.status_code, 400)
        self.repo.get.assert_not_awaited()

    async def test_policy_read_requires_explicit_admission_and_never_creates_it(self):
        result = await self.invoke("confirmation-policy", {"task_id": "run"})
        self.assertEqual(result["auto_enabled"], True)
        self.snapshot = None
        result = await self.invoke("confirmation-policy", {"task_id": "run"})
        self.assertEqual(result["admitted"], False)
        self.assertEqual(result["auto_enabled"], False)

    async def test_continuation_requires_committed_original_run_claim_even_when_manual(self):
        self.snapshot["policy"]["auto_enabled"] = False
        gate = self.snapshot["gate"]
        gate.update(state="continuation_pending", receipt_digest="f" * 64, continuation={"run_id": "run", "action": "resume_same_session"})
        value = await self.invoke("confirmation-continuation", {"gate_id": gate["gate_id"]})
        self.assertEqual(value["claim"]["continuation"]["run_id"], "run")
        self.assertNotIn("submission", value["claim"])
        original = json.loads(json.dumps(gate))
        for change in ({"state": "confirmed"}, {"state": "continuation_unknown"}, {"task_id": "different"}, {"receipt_digest": None}, {"continuation": {"run_id": "different", "action": "resume_same_session"}}):
            self.snapshot["gate"] = {**original, **change}
            with self.subTest(change=change), self.assertRaises(broker.HTTPException) as failed:
                await self.invoke("confirmation-continuation", {"gate_id": original["gate_id"]})
            self.assertEqual(failed.exception.status_code, 409)

    async def test_continuation_request_cannot_override_session_prompt_or_choice(self):
        for body in ({"gate_id": "invalid"}, {"gate_id": "e" * 64, "session_id": "another"}, {"gate_id": "e" * 64, "prompt": "override"}, {"gate_id": "e" * 64, "auto_enabled": True}):
            with self.assertRaises(broker.HTTPException) as failed:
                await self.invoke("confirmation-continuation", body)
            self.assertEqual(failed.exception.status_code, 400)
        self.repo.get.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
