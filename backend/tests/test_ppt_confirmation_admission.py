"""Consent comes from the authenticated initial Human input, never Agent prose."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from deerflow.persistence.ppt_confirmation.sql import Scope
from deerflow.ppt_confirmation.admission import admit_initial_task
from deerflow.ppt_confirmation.lifecycle import ConsentConflict


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = Scope("owner", "thread", "run")
        self.row = {
            "run_id": "run",
            "thread_id": "thread",
            "user_id": "owner",
            "assistant_id": "ppt-master-runner",
            "status": "running",
            "kwargs": {"input": {"messages": [{"type": "human", "content": "/ppt-master test", "additional_kwargs": {"ppt_confirmation_mode": "auto_after_60s"}}]}},
        }
        self.runs = SimpleNamespace(list_by_thread=AsyncMock(return_value=[self.row]))
        self.repo = SimpleNamespace(get=AsyncMock(return_value=None), admit=AsyncMock(return_value={"policy": {"auto_enabled": True}, "cancelled": False}))

    async def invoke(self):
        return await admit_initial_task(self.scope, self.row, run_store=self.runs, repository=self.repo)

    async def test_explicit_new_human_consent_is_owner_scoped_and_persisted_once(self):
        result = await self.invoke()
        self.assertTrue(result["policy"]["auto_enabled"])
        self.runs.list_by_thread.assert_awaited_once_with("thread", user_id="owner", limit=2)
        self.repo.admit.assert_awaited_once_with(self.scope, runtime_profile="ppt-master-runner", skill_name="ppt-master", opted_in=True, new_task=True)

    async def test_missing_optin_defaults_manual_and_does_not_read_context_or_config(self):
        self.row["kwargs"]["input"]["messages"][0]["additional_kwargs"] = {}
        self.row["kwargs"]["config"] = {"ppt_confirmation_mode": "auto_after_60s"}
        self.row["metadata"] = {"ppt_confirmation_mode": "auto_after_60s"}
        await self.invoke()
        self.assertIs(self.repo.admit.await_args.kwargs["opted_in"], False)

    async def test_existing_consent_is_immutable_even_when_input_changes(self):
        self.repo.get.return_value = {"policy": {"auto_enabled": False}, "cancelled": False}
        self.assertFalse((await self.invoke())["policy"]["auto_enabled"])
        self.repo.admit.assert_not_awaited()
        self.runs.list_by_thread.assert_not_awaited()

    async def test_prior_runs_or_continuation_cannot_opt_in(self):
        for kind in ("history", "continue", "wrong_run", "empty_history", "cancelled", "other_owner", "other_assistant"):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == "history":
                    self.runs.list_by_thread.return_value = [self.row, {"run_id": "previous"}]
                elif kind == "continue":
                    self.row["kwargs"]["input"]["messages"][0]["content"] = "继续"
                elif kind == "wrong_run":
                    self.runs.list_by_thread.return_value = [{**self.row, "run_id": "other"}]
                elif kind == "empty_history":
                    self.runs.list_by_thread.return_value = []
                elif kind == "cancelled":
                    self.row["cancel_action"] = "interrupt"
                elif kind == "other_owner":
                    self.row["user_id"] = "other"
                else:
                    self.row["assistant_id"] = "default"
                with self.assertRaises(ConsentConflict):
                    await self.invoke()
                self.repo.admit.assert_not_awaited()

    async def test_legacy_continuation_without_optin_is_not_admitted(self):
        self.row["kwargs"]["input"]["messages"][0] = {"type": "human", "content": "继续"}
        self.assertIsNone(await self.invoke())
        self.repo.admit.assert_not_awaited()

    async def test_ambiguous_model_or_malformed_marker_cannot_enable_policy(self):
        original = copy.deepcopy(self.row)
        for kind in ("ai", "tool", "two_humans", "forged_role", "null", "boolean", "unknown"):
            self.row = copy.deepcopy(original)
            message = self.row["kwargs"]["input"]["messages"][0]
            if kind in {"ai", "tool"}:
                message["type"] = kind
            elif kind == "two_humans":
                self.row["kwargs"]["input"]["messages"].append(copy.deepcopy(message))
            elif kind == "forged_role":
                message["role"] = "assistant"
            else:
                message["additional_kwargs"]["ppt_confirmation_mode"] = {"null": None, "boolean": True, "unknown": "automatic"}[kind]
            with self.subTest(kind=kind), self.assertRaises(ConsentConflict):
                await self.invoke()
        self.repo.admit.assert_not_awaited()

    async def test_explicit_manual_and_text_parts_are_accepted(self):
        message = self.row["kwargs"]["input"]["messages"][0]
        message["additional_kwargs"]["ppt_confirmation_mode"] = "manual"
        message["content"] = [{"type": "text", "text": "/ppt-master test"}]
        await self.invoke()
        self.assertIs(self.repo.admit.await_args.kwargs["opted_in"], False)


if __name__ == "__main__":
    unittest.main()
