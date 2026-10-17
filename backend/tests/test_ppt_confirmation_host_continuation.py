"""Real private admission files; mocked thread launcher, no Agent/model execution."""

import json
import tempfile
import unittest
from unittest.mock import patch

from test_ppt_confirmation_host_native import load_host


class HostContinuationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="ppt-host-continue-")
        self.addCleanup(self.folder.cleanup)
        self.host, self.launches = load_host(self.folder.name)
        self.manager = self.host.CodexTaskManager()
        self.manager.confirmation_control.initial_policy = lambda task: {"auto_enabled": True}
        self.task = self.manager.start(task_id="original", thread_id="thread", prompt="fixture", resume=False, confirmation_protocol="ppt-confirmation-v1")
        self.task.set_state(status="completed", process_cleanup_complete=True, session_id="exact-session", artifacts=[])
        self.manager.active_task_id = None
        self.host._atomic_json(self.task.thread_state_path, {"thread_id": "thread", "session_id": "exact-session"})
        self.gate_id = "a" * 64
        self.body = {"gate_id": self.gate_id, "session_id": "exact-session"}
        self.authority = patch.object(self.manager.confirmation_control, "continuation_authority", return_value={"receipt_digest": "b" * 64})
        self.claim = self.authority.start()
        self.addCleanup(self.authority.stop)

    def test_exact_session_is_reserved_but_thread_start_is_not_process_receipt(self):
        result = self.manager.confirmation_continue("original", self.body)
        self.assertEqual(result["state"], "dispatch_intent")
        self.assertEqual(len(self.launches), 2)
        self.assertEqual(self.manager._session_for(self.task), "exact-session")
        self.assertEqual(self.task.state["status"], "pending")
        self.assertEqual(self.manager.active_task_id, "original")
        self.assertEqual(self.task.env["PPT_MASTER_CONFIRMATION_PROTOCOL"], "1")
        self.assertNotIn("PPT_RUNTIME_CAPABILITY", json.dumps(self.task.state))

    def test_repeated_request_after_unknown_start_never_starts_another_thread(self):
        self.manager.confirmation_continue("original", self.body)
        self.manager.confirmation_continue("original", self.body)
        self.assertEqual(len(self.launches), 2)
        self.claim.assert_called_once()

    def test_cancelled_failed_busy_delivered_or_unclean_original_cannot_continue(self):
        original = dict(self.task.state)
        for change in ({"status": "failed"}, {"cancel_requested": True}, {"status": "running"},
                       {"artifacts": ["deck.pptx"]}, {"process_cleanup_complete": False}):
            self.task.state = dict(original)
            self.task.set_state(**change)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.manager.confirmation_continue("original", self.body)
        self.assertEqual(len(self.launches), 1)
        self.claim.assert_not_called()

    def test_later_thread_session_cannot_replace_original(self):
        self.host._atomic_json(self.task.thread_state_path, {"thread_id": "thread", "session_id": "later-session"})
        with self.assertRaises(RuntimeError):
            self.manager.confirmation_continue("original", self.body)
        self.claim.assert_not_called()
        self.assertEqual(len(self.launches), 1)

    def test_host_restart_cannot_reconstruct_an_automatic_launch(self):
        recovered = self.host.CodexTaskManager()
        with self.assertRaises(RuntimeError):
            recovered.confirmation_continue("original", self.body)
        self.assertEqual(len(self.launches), 1)

    def test_mismatched_session_or_arbitrary_prompt_refused(self):
        for body in ({**self.body, "session_id": "wrong"}, {**self.body, "prompt": "arbitrary"}, {**self.body, "gate_id": None}):
            with self.subTest(body=body), self.assertRaises((RuntimeError, ValueError)):
                self.manager.confirmation_continue("original", body)
        self.assertEqual(len(self.launches), 1)


if __name__ == "__main__":
    unittest.main()
