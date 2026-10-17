"""Execute the exact release Runner's mint function with an in-memory HTTP peer."""

import ast
import asyncio
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

SOURCE = Path(__file__).resolve().parents[2] / "docker/ppt-master-runner/production-source/backend/packages/harness/deerflow/agents/ppt_master_runner/agent.py"


class RunnerContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_renewal_outage_defers_usage_without_advancing_cursor(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PptProviderReceipts")
        for status in (429, 500, 503, 401, 403, 409):
            with self.subTest(status=status):
                request = httpx.Request("POST", "http://gateway/fixture")
                response = httpx.Response(status, request=request)
                error = httpx.HTTPStatusError("fixture", request=request, response=response)
                mint = AsyncMock(side_effect=error)
                namespace = {"asyncio": asyncio, "httpx": httpx, "logger": logging.getLogger("fixture"), "_mint_ppt_capability": mint}
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
                receipts = namespace["PptProviderReceipts"]("old.capability", "run", "thread", renewal_key="fixture")
                receipts.renew_at, receipts.cursor = 0, 7
                receipts.records = {"known": {"total_tokens": 10}}
                if status in (401, 403, 409):
                    with self.assertRaises(httpx.HTTPStatusError):
                        await receipts.merge({"status": "running"})
                else:
                    result = await receipts.merge({"status": "running"})
                    self.assertEqual(result["status"], "running")
                    self.assertEqual(result["provider_usage_sync_state"], "delayed")
                    self.assertEqual(result["usage_records"], [{"total_tokens": 10}])
                    await receipts.merge({"status": "running"})
                mint.assert_awaited_once()
                self.assertEqual(receipts.cursor, 7)
                self.assertEqual(receipts.capability, "old.capability")
                self.assertEqual(receipts.renew_at, 0)

    async def invoke(self, reply, **kwargs):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_mint_ppt_capability")
        requests = []

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, *, json):
                requests.append((url, json))
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: reply)

        def client(**options):
            self.assertIs(options["trust_env"], False)
            self.assertEqual(options["headers"]["X-Ppt-Runtime-Operation"],
                             "confirmation-capability-refresh" if kwargs.get("refresh_confirmation") else "capability")
            return Client()

        namespace = {"httpx": SimpleNamespace(AsyncClient=client, Timeout=lambda value: value), "_auth_headers": lambda key: {"Authorization": "Bearer fixture"}}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
        result = await namespace["_mint_ppt_capability"]("run", "thread", "fixture", **kwargs)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0], ("http://gateway:8001/api/codex/deepseek/v1/responses", {"run_id": "run", "thread_id": "thread"}))
        return result

    async def test_legacy_contract_unchanged(self):
        self.assertEqual(await self.invoke({"capability": "fixture.capability"}), "fixture.capability")

    async def test_admitted_protocol_is_forwarded_but_caller_cannot_supply_consent(self):
        result = await self.invoke({"capability": "fixture.capability", "confirmation_protocol": "ppt-confirmation-v1", "auto_enabled": True}, with_confirmation=True)
        self.assertEqual(result, {"capability": "fixture.capability", "confirmation_protocol": "ppt-confirmation-v1"})

    async def test_disabled_gateway_is_compatible_with_new_runner(self):
        result = await self.invoke({"capability": "fixture.capability"}, with_confirmation=True)
        self.assertEqual(result, {"capability": "fixture.capability"})

    async def test_unknown_protocol_is_not_silently_downgraded(self):
        for value in (True, 1, "future-version"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                await self.invoke({"capability": "fixture.capability", "confirmation_protocol": value}, with_confirmation=True)

    async def test_confirmation_renewal_cannot_change_task_or_admit_legacy(self):
        value = {"capability": "renewed.capability", "confirmation_protocol": "ppt-confirmation-v1", "task_id": "run", "thread_id": "thread"}
        self.assertEqual(await self.invoke(value, refresh_confirmation=True), "renewed.capability")
        for change in ({"task_id": "other"}, {"thread_id": "other"}, {"confirmation_protocol": None}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                await self.invoke({**value, **change}, refresh_confirmation=True)


if __name__ == "__main__":
    unittest.main()
