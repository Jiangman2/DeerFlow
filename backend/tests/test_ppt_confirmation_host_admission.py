"""Protocol activation is task-local, authenticated and cannot upgrade legacy turns."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from test_ppt_confirmation_host_native import load_host


class HostAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="ppt-confirm-admission-")
        self.addCleanup(self.folder.cleanup)
        self.host, self.launches = load_host(self.folder.name)
        self.host._enterprise_relay_required = lambda env: True
        self.manager = self.host.CodexTaskManager()
        self.policy = dict(owner_id="owner", task_id="new", thread_id="thread", admitted=True, auto_enabled=True)
        self.network = patch.object(self.manager.confirmation_control, "request_json", return_value=self.policy)
        self.request = self.network.start()
        self.addCleanup(self.network.stop)

    def start(self, **extra):
        return self.manager.start(task_id="new", thread_id="thread", prompt="fixture", resume=False, runtime_capability="fixture.capability", **extra)

    def test_protocol_requires_gateway_policy_and_is_not_a_global_default(self):
        with patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "1"}):
            task = self.start()
        self.assertFalse("PPT_MASTER_CONFIRMATION_PROTOCOL" in task.env)
        self.request.assert_not_called()

    def test_new_task_rechecks_matching_gateway_policy_before_launch(self):
        task = self.start(confirmation_protocol="ppt-confirmation-v1")
        self.assertEqual(task.env["PPT_MASTER_CONFIRMATION_PROTOCOL"], "1")
        self.assertEqual(task.state["confirmation_protocol"], "ppt-confirmation-v1")
        self.assertIs(task.state["confirmation_auto_enabled"], True)
        self.assertEqual(len(self.launches), 1)
        args, kwargs = self.request.call_args
        self.assertEqual(args[:2], ("gateway", 8001))
        self.assertEqual(kwargs["body"], {"task_id": "new"})
        self.assertEqual(kwargs["headers"]["X-Ppt-Runtime-Operation"], "confirmation-policy")
        self.assertEqual(kwargs["headers"]["X-Ppt-Capability"], "fixture.capability")
        self.assertNotIn("fixture.capability", json.dumps(task.state))
        self.assertIs(self.start(confirmation_protocol="ppt-confirmation-v1"), task)
        self.assertEqual(self.request.call_count, 1)
        self.assertEqual(len(self.launches), 1)

    def test_manual_new_task_enables_protocol_not_automatic_consent(self):
        self.policy["auto_enabled"] = False
        task = self.start(confirmation_protocol="ppt-confirmation-v1")
        self.assertEqual(task.env["PPT_MASTER_CONFIRMATION_PROTOCOL"], "1")
        self.assertIs(task.state["confirmation_auto_enabled"], False)

    def test_bad_policy_fails_before_reservation_or_process_start(self):
        for field, value in (("admitted", False), ("admitted", 1), ("auto_enabled", "true"), ("task_id", "other"), ("thread_id", "other"), ("owner_id", None)):
            original = self.policy[field]
            self.policy[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                self.start(confirmation_protocol="ppt-confirmation-v1")
            self.policy[field] = original
            self.assertFalse((self.host.HOST_STATE_ROOT / "tasks/new").exists())
        self.assertEqual(self.launches, [])

    def test_policy_failure_does_not_downgrade_to_unmanaged_launch(self):
        self.request.side_effect = RuntimeError("confirmation_transport_unconfirmed")
        with self.assertRaises(RuntimeError):
            self.start(confirmation_protocol="ppt-confirmation-v1")
        self.assertEqual(self.launches, [])

    def test_unknown_protocol_and_legacy_history_cannot_be_upgraded(self):
        with self.assertRaises(ValueError):
            self.start(confirmation_protocol=True)
        self.host._atomic_json(self.host.HOST_STATE_ROOT / "thread.json", {"thread_id": "thread", "session_id": "old"})
        with self.assertRaises(RuntimeError):
            self.start(confirmation_protocol="ppt-confirmation-v1")
        self.request.assert_not_called()
        self.assertEqual(self.launches, [])

    def test_existing_unmanaged_task_cannot_be_relabelled_after_ack_loss(self):
        task = self.start()
        with self.assertRaises(RuntimeError):
            self.start(confirmation_protocol="ppt-confirmation-v1")
        self.assertFalse("PPT_MASTER_CONFIRMATION_PROTOCOL" in task.env)
        self.assertEqual(len(self.launches), 1)


if __name__ == "__main__":
    unittest.main()
