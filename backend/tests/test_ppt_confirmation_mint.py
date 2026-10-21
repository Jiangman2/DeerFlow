"""The capability endpoint opts into the new protocol only after SQL admission."""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_ppt_confirmation_service_authority import broker


class ConfirmationMintTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, enabled=True, snapshot=None):
        request = SimpleNamespace(headers={"Authorization": "Bearer fixture-internal"})
        row = {"status": "running"}
        with (
            patch.dict(os.environ, {"SANDBOX_API_KEY": "fixture-internal", "PPT_CONFIRMATION_TRANSPORT_ENABLED": "true" if enabled else "false"}),
            patch.object(broker, "bounded_body", AsyncMock(return_value={"run_id": "11111111-1111-4111-8111-111111111111", "thread_id": "22222222-2222-4222-8222-222222222222"})),
            patch.object(broker, "_service_run_owner", AsyncMock(return_value=(row, "owner"))),
            patch.object(broker, "encode_scope", return_value="fixture.capability"),
            patch.object(broker, "admit_confirmation", AsyncMock(return_value=snapshot), create=True) as admit,
        ):
            response = await broker.mint(request)
        return json.loads(response.body), admit, row

    async def test_disabled_deployment_keeps_legacy_capability_contract(self):
        value, admit, _ = await self.invoke(enabled=False)
        self.assertEqual(value, {"capability": "fixture.capability"})
        admit.assert_not_awaited()

    async def test_enabled_protocol_requires_committed_admission(self):
        value, admit, row = await self.invoke(snapshot={"policy": {"auto_enabled": True}, "cancelled": False})
        self.assertEqual(value, {"capability": "fixture.capability", "confirmation_protocol": "ppt-confirmation-v1"})
        self.assertEqual(admit.await_count, 1)
        self.assertIs(admit.await_args.args[1], row)
        self.assertEqual(admit.await_args.args[2], "owner")

    async def test_legacy_task_does_not_receive_activation_metadata(self):
        value, admit, _ = await self.invoke(snapshot=None)
        self.assertNotIn("confirmation_protocol", value)
        admit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
