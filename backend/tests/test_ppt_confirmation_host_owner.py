"""Bounded authenticated Host adapter; no provider, browser or Agent execution."""

import hashlib
import json
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace

from deerflow.persistence.ppt_confirmation.sql import Scope
from deerflow.ppt_confirmation.host_owner import HostOwner


class HostOwnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = Scope("owner", "thread", "task")
        raw = b'{"stage":"stage1"}'
        self.body = {"task_id": "task", "thread_id": "thread", "project_id": "project", "stage": "stage1", "payload": {"stage": "stage1"},
                     "payload_digest": hashlib.sha256(raw).hexdigest(), "version": "a" * 64, "owner_version": "b" * 64,
                     "source_digest": "c" * 64, "confirmation_submitted": False}
        self.calls = []
        self.status = 200
        self.raw = None

        @asynccontextmanager
        async def stream(method, url, **kwargs):
            self.calls.append((method, url, kwargs))

            async def content():
                yield self.raw if self.raw is not None else json.dumps(self.body).encode()

            yield SimpleNamespace(status_code=self.status, headers={"content-type": "application/json"}, aiter_bytes=content)

        self.owner = HostOwner(SimpleNamespace(stream=stream), "http://sandbox-fixture:8788", self.scope)

    async def test_observe_validates_all_task_and_payload_bindings(self):
        result = await self.owner.observe(self.scope)
        self.assertEqual(result.scope, self.scope)
        self.assertEqual(result.stage, "stage1")
        self.assertEqual(result.proposal.payload, {"stage": "stage1"})
        self.assertEqual(self.calls[0][0:2], ("GET", "http://sandbox-fixture:8788/v1/tasks/task/confirmation/observation"))

    async def test_not_ready_is_not_failure_or_permission_to_submit(self):
        self.status = 409
        self.assertIsNone(await self.owner.observe(self.scope))
        self.assertEqual(len(self.calls), 1)

    async def test_mismatched_or_mutated_receipt_is_rejected(self):
        for key, wrong in (("task_id", "other"), ("thread_id", "other"), ("payload_digest", "f" * 64), ("confirmation_submitted", True), ("stage", "stage3")):
            original = self.body[key]
            self.body[key] = wrong
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.owner.observe(self.scope)
            self.body[key] = original

    async def test_cross_owner_scope_never_touches_transport(self):
        with self.assertRaises(ValueError):
            await self.owner.observe(Scope("other", "thread", "task"))
        self.assertEqual(self.calls, [])

    async def test_oversized_response_is_bounded(self):
        self.raw = b" " * (400 * 1024)
        with self.assertRaises(OSError):
            await self.owner.observe(self.scope)

    async def test_submission_forwards_only_one_claimed_native_payload(self):
        result = await self.owner.observe(self.scope)
        claim = {"owner_id": "owner", "thread_id": "thread", "task_id": "task", "state": "submission_pending", "submission": {"id": "operation", "mode": "automatic"}}
        self.body = {"state": "receipt_required", "retry_permitted": False}
        await self.owner.submit_guarded(self.scope, claim, result.proposal)
        method, url, kwargs = self.calls[-1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/confirmation/submit"))
        self.assertEqual(kwargs["json"], {"operation_id": "operation", "payload": {"stage": "stage1"}})
        self.assertEqual(len(self.calls), 2)

    async def test_receipt_paths_cannot_escape_the_task(self):
        for identity in ("../other", "x?y=1", "", None):
            with self.assertRaises(ValueError):
                await self.owner.receipt(self.scope, identity)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
