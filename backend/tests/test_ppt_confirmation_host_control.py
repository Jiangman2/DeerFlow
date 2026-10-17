"""Host effect guard: private files and local owner, no production/model calls."""

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_ppt_confirmation_proposal import OfficialFixture

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docker/sandbox-ppt-master"))


class HostControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ppt-host-control-")
        self.addCleanup(self.temp.cleanup)
        import os

        with patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "1"}):
            self.fixture = OfficialFixture(self.temp.name)
        spec = importlib.util.spec_from_file_location("host_control_fixture", ROOT / "docker/sandbox-ppt-master/ppt_confirmation_control.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        from deerflow.ppt_confirmation import proposal

        self.module.proposal = proposal
        self.snapshot = self.fixture.snapshot()
        rec = self.fixture.get("/api/recommendations")
        self.payload = {"stage": "stage1", "primary_language": "zh-CN", "canvas": "ppt169", "template_selection": {"mode": "free_design", "selection_keys": []}, "options_sha256": rec["template_options"]["options_sha256"]}
        directory = Path(self.temp.name) / "task"
        directory.mkdir()
        self.task = SimpleNamespace(
            task_id="task", thread_id="thread", task_dir=directory, env={"PPT_RUNTIME_CAPABILITY": "fixture-only"}, state={"status": "running", "confirmation_owner_candidate": {"project": str(self.fixture.project), "port": 5050}}
        )
        self.claim = {
            "owner_id": "owner",
            "thread_id": "thread",
            "task_id": "task",
            "state": "submission_pending",
            "stage": "stage1",
            "project_id": hashlib.sha256(str(self.fixture.project.resolve()).encode()).hexdigest(),
            "version": self.snapshot.version,
            "owner_version": self.snapshot.owner_version,
            "source_digest": self.snapshot.source_digest,
            "payload_digest": hashlib.sha256(json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "submission": {"id": "op-one", "mode": "automatic"},
        }
        self.calls = []
        self.native_posts = 0
        self.lose_ack = False
        self.receipts = SimpleNamespace(atomic_json=self.save)

    def save(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def transport(self, host, port, path, *, body=None, headers=None):
        self.calls.append((host, path))
        if host == "gateway":
            return {"task_id": "task", "thread_id": "thread", "owner_id": "owner", "claim": self.claim}
        self.assertEqual(host, "127.0.0.1")
        if body is None:
            return self.fixture.get(path)
        self.native_posts += 1
        reply = self.fixture.submit(body)
        if reply.status_code != 200:
            raise RuntimeError("native_confirmation_refused")
        if self.lose_ack:
            raise TimeoutError("private")
        return reply.json

    def submit(self):
        with patch.object(self.module, "request_json", self.transport):
            return self.module.submit(self.task, {"operation_id": "op-one", "payload": self.payload}, receipts=self.receipts, workspace=Path(self.temp.name), runtime=ROOT / "vendor/ppt-master-v6.6.0")

    def observe(self, capture, cache):
        with patch.object(self.module, "request_json", self.transport):
            return self.module.observe(self.task, workspace=Path(self.temp.name), runtime=ROOT / "vendor/ppt-master-v6.6.0", capture=capture, cache=cache)

    def proposal(self, snapshot):
        from deerflow.ppt_confirmation.proposal import CapturedProposal

        raw = self.module.encoded(self.payload)
        return CapturedProposal(snapshot.version, snapshot.source_digest, hashlib.sha256(raw).hexdigest(), raw, "Official", 0, 1, owner_version=snapshot.owner_version)

    def test_observation_caches_only_unchanged_native_snapshot_and_never_posts(self):
        calls, cache = [], {}

        def capture(snapshot):
            calls.append(snapshot.version)
            return self.proposal(snapshot)

        first = self.observe(capture, cache)
        second = self.observe(capture, cache)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        self.assertEqual(first["project_id"], self.claim["project_id"])
        self.assertEqual(first["payload"], self.payload)
        self.assertEqual(first["task_id"], "task")
        self.assertEqual(self.native_posts, 0)

    def test_capture_revalidates_owner_and_discards_stale_payload(self):
        cache = {}

        def capture(snapshot):
            self.task.state["cancel_requested"] = True
            return self.proposal(snapshot)

        with self.assertRaises(RuntimeError):
            self.observe(capture, cache)
        self.assertEqual(cache, {})
        self.assertEqual(self.native_posts, 0)

    def test_capture_errors_never_produce_a_gate_or_confirmation(self):
        cache = {}

        def capture(snapshot):
            raise RuntimeError("capture_fixture_failure")

        with self.assertRaises(RuntimeError):
            self.observe(capture, cache)
        self.assertEqual(cache, {})
        self.assertEqual(self.native_posts, 0)

    def test_real_native_post_has_one_durable_intent_and_wait_accepts_receipt(self):
        result = self.submit()
        self.assertEqual(result["state"], "receipt_required")
        self.assertEqual(self.native_posts, 1)
        self.assertEqual(self.fixture.module._wait_result_status(self.fixture.confirm / "result.json", "stage1"), 0)
        record = json.loads((self.task.task_dir / "confirmation-op-one.json").read_text())
        self.assertEqual(record["state"], "dispatch_intent")
        self.assertNotIn("payload", record)

    def test_lost_ack_and_repeated_rpc_never_post_twice(self):
        self.lose_ack = True
        self.assertEqual(self.submit()["state"], "submission_unknown")
        self.assertEqual(self.submit()["state"], "submission_unknown")
        self.assertEqual(self.native_posts, 1)
        self.assertTrue((self.fixture.confirm / "result.json").exists())

    def test_wrong_gateway_claim_payload_or_version_never_submits(self):
        original = self.claim.copy()
        for key, value in (("task_id", "other"), ("project_id", "f" * 64), ("payload_digest", "f" * 64), ("owner_version", "f" * 64), ("source_digest", "f" * 64), ("state", "submission_unknown")):
            self.claim = {**original, key: value}
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.submit()
            self.assertEqual(self.native_posts, 0)

    def test_cancelled_task_never_reads_authority_or_posts(self):
        self.task.state["cancel_requested"] = True
        with self.assertRaisesRegex(RuntimeError, "confirmation_task_inactive"):
            self.submit()
        self.assertEqual(self.calls, [])

    def test_failed_intent_save_never_dispatches(self):
        def failed(*args):
            raise OSError("uncertain save")

        self.receipts.atomic_json = failed
        with self.assertRaises(OSError):
            self.submit()
        self.assertEqual(self.native_posts, 0)

    def test_authority_change_during_snapshot_refuses_before_intent(self):
        checks = 0
        original = self.transport

        def change(host, port, path, **kwargs):
            nonlocal checks
            if host == "gateway":
                checks += 1
                if checks == 2:
                    self.claim = {**self.claim, "revision": 999}
            return original(host, port, path, **kwargs)

        with patch.object(self.module, "request_json", change):
            with self.assertRaisesRegex(RuntimeError, "confirmation_claim_changed"):
                self.module.submit(self.task, {"operation_id": "op-one", "payload": self.payload}, receipts=self.receipts, workspace=Path(self.temp.name), runtime=ROOT / "vendor/ppt-master-v6.6.0")
        self.assertFalse((self.task.task_dir / "confirmation-op-one.json").exists())
        self.assertEqual(self.native_posts, 0)

    def test_unsafe_port_is_refused_before_network(self):
        self.task.state["confirmation_owner_candidate"]["port"] = 8788
        with self.assertRaises(RuntimeError):
            self.submit()
        self.assertEqual(self.calls, [])

    def test_committed_receipt_is_readable_after_native_page_shuts_down(self):
        self.submit()
        with patch.object(self.module, "request_json", side_effect=AssertionError("must not need a live page")):
            proof = self.module.receipt(self.task, "by-version", self.snapshot.owner_version, workspace=Path(self.temp.name), runtime=ROOT / "vendor/ppt-master-v6.6.0")
        self.assertEqual(proof["state"], "committed")
        self.assertEqual(proof["operation_id"], "op-one")
        self.assertFalse(proof["retry_permitted"])


if __name__ == "__main__":
    unittest.main()
