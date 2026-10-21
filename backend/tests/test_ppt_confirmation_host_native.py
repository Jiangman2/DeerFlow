"""Stdlib-only isolated Host transport/process tests, no real Agent/provider.

PPT_CONFIRMATION_HOST_TEST_SOURCE permits a read-only /host mount in a disposable,
network-disabled Linux container. All task files are temporary private fixtures.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(os.environ.get("PPT_CONFIRMATION_HOST_TEST_SOURCE") or (Path(__file__).resolve().parents[2] / "docker/sandbox-ppt-master"))


def load_host(folder):
    spec = importlib.util.spec_from_file_location("native_recovery_host", SOURCE / "agent_host.py")
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    host.WORKSPACE_ROOT = Path(folder) / "workspace"
    host.UPLOADS_ROOT = Path(folder) / "uploads"
    host.OUTPUTS_ROOT = Path(folder) / "outputs"
    host.HOST_STATE_ROOT = host.WORKSPACE_ROOT / ".ppt-master-agent"
    host.RUNTIME_ROOT = host.WORKSPACE_ROOT / ".ppt-master-runtime"
    host.TEMP_ROOT = host.HOST_STATE_ROOT / "tmp"
    host._enterprise_relay_required = lambda env: False
    launches = []

    class NoAgent:
        def __init__(self, **kw):
            self.kw = kw

        def start(self):
            launches.append(self.kw)

    host.threading = SimpleNamespace(RLock=threading.RLock, Thread=NoAgent)
    return host, launches


class HostNativeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ppt-host-native-")
        self.addCleanup(self.directory.cleanup)
        self.host, self.launches = load_host(self.directory.name)
        self.manager = self.host.CodexTaskManager()

    def persist(self, status="completed", cleanup=True):
        directory = self.host.HOST_STATE_ROOT / "tasks/old"
        directory.mkdir(parents=True, exist_ok=True)
        state = dict(
            task_id="old", thread_id="thread", status=status, process_cleanup_complete=cleanup, session_id="original", last_seq=0, created_at=1000, updated_at=1001, worker_session_ids=["not-public"], runtime_capability="not-public"
        )
        self.host._atomic_json(directory / "state.json", state)
        return directory / "state.json"

    def server(self):
        handler = type("FixtureHandler", (self.host.AgentHostHandler,), {"manager": self.manager, "api_key": "local-fixture-only", "log_message": lambda *args: None})
        server = self.host.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(cleanup)
        return "http://127.0.0.1:" + str(server.server_address[1])

    def request(self, base, path, payload=None, authorized=True):
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["Authorization"] = "Bearer local-fixture-only"
        req = urllib.request.Request(base + path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as response:
            return response.code, json.loads(response.read())

    def test_authenticated_http_recovers_history_and_does_not_relaunch(self):
        self.persist()
        base = self.server()
        self.assertEqual(self.request(base, "/v1/tasks/old", authorized=False)[0], 401)
        status, body = self.request(base, "/v1/tasks/old")
        self.assertEqual(status, 200)
        self.assertEqual(body["host_recovery"], "persisted_terminal_receipt")
        self.assertNotIn("not-public", json.dumps(body))
        payload = dict(task_id="old", thread_id="thread", prompt="retry", resume=False)
        self.assertEqual(self.request(base, "/v1/tasks", payload)[0], 202)
        self.assertEqual(self.launches, [])
        payload["thread_id"] = "wrong-thread"
        self.assertEqual(self.request(base, "/v1/tasks", payload)[0], 409)

    def test_authenticated_http_cancellation_persists_intent_without_signalling(self):
        path = self.persist(status="running", cleanup=False)
        status, body = self.request(self.server(), "/v1/tasks/old/cancel", {})
        self.assertEqual(status, 202)
        self.assertTrue(body["cancel_requested"])
        self.assertFalse(body["process_cleanup_complete"])
        self.assertEqual(json.loads(path.read_text())["status"], "running")
        self.assertEqual(self.launches, [])

    def test_authenticated_exact_resume_keeps_original_session(self):
        self.persist()
        self.host._atomic_json(self.host.HOST_STATE_ROOT / "thread.json", {"thread_id": "thread", "session_id": "later"})
        status, body = self.request(self.server(), "/v1/tasks", dict(task_id="continuation", thread_id="thread", prompt="fixture", resume=True, resume_from_task_id="old", expected_session_id="original"))
        self.assertEqual(status, 202)
        self.assertEqual(body["expected_session_id"], "original")
        self.assertEqual(self.manager._session_for(self.manager.get("continuation")), "original")
        self.assertEqual(len(self.launches), 1)

    def test_http_resume_flag_is_not_string_truthiness(self):
        self.persist()
        status, body = self.request(self.server(), "/v1/tasks", dict(task_id="next", thread_id="thread", prompt="fixture", resume="false", resume_from_task_id="old", expected_session_id="original"))
        self.assertEqual(status, 400)
        self.assertEqual(self.launches, [])

    def test_confirmation_api_is_authenticated_and_read_does_not_start_owner(self):
        self.persist()
        base = self.server()
        self.assertEqual(self.request(base, "/v1/tasks/old/confirmation", authorized=False)[0], 401)
        status, body = self.request(base, "/v1/tasks/old/confirmation")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "not_discovered")
        self.assertEqual(self.launches, [])
        status, body = self.request(base, "/v1/tasks/old/confirmation/recover", {"project": "/arbitrary"})
        self.assertEqual(status, 400)

    def test_conditional_submit_requires_auth_current_owned_task_and_cancel_guard(self):
        task = self.manager.start(task_id="live", thread_id="thread", prompt="fixture", resume=False)
        task.set_state(status="running")
        calls = []
        self.manager.confirmation_control.submit = lambda *args, **kw: calls.append(args) or {"state": "receipt_required"}
        base = self.server()
        path = "/v1/tasks/live/confirmation/submit"
        body = {"operation_id": "op", "payload": {"stage": "stage1"}}
        self.assertEqual(self.request(base, path, body, authorized=False)[0], 401)
        self.assertEqual(calls, [])
        self.assertEqual(self.request(base, path, body)[0], 200)
        self.assertEqual(len(calls), 1)

    def test_observation_http_is_authenticated_managed_task_only_and_never_launches_agent(self):
        task = self.manager.start(task_id="live", thread_id="thread", prompt="fixture", resume=False)
        task.set_state(status="running")
        calls = []
        self.manager.confirmation_control.observe = lambda *args, **kw: (
            calls.append(args)
            or {
                "task_id": "live",
                "thread_id": "thread",
                "confirmation_submitted": False,
                "version": "a" * 64,
                "owner_version": "b" * 64,
                "source_digest": "c" * 64,
                "payload_digest": "d" * 64,
                "project_id": "project",
                "stage": "stage1",
            }
        )
        base = self.server()
        path = "/v1/tasks/live/confirmation/observation"
        self.assertEqual(self.request(base, path, authorized=False)[0], 401)
        self.assertEqual(self.request(base, path)[0], 409)
        task.set_state(confirmation_protocol="ppt-confirmation-v1")
        status, body = self.request(base, path)
        self.assertEqual(status, 200)
        self.assertIs(body["confirmation_submitted"], False)
        self.assertEqual(task.state["confirmation_observed_gate"]["version"], "a" * 64)
        self.assertNotIn("confirmation_observed_gate", task.snapshot())
        self.assertEqual(len(calls), 1)
        self.manager.cancel("live")
        self.assertEqual(self.request(base, path)[0], 409)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.launches), 1)

    def test_cancel_does_not_wait_for_observation_calculation(self):
        task = self.manager.start(task_id="live", thread_id="thread", prompt="fixture", resume=False)
        task.set_state(status="running", confirmation_protocol="ppt-confirmation-v1")
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        outcomes = []

        def slow(*args, **kw):
            entered.set()
            release.wait(timeout=4)
            return {"task_id": "live", "confirmation_submitted": False}

        def observe():
            try:
                self.manager.confirmation_observe("live")
                outcomes.append("incorrect_success")
            except RuntimeError:
                outcomes.append("cancelled")

        self.manager.confirmation_control.observe = slow
        thread = threading.Thread(target=observe)
        thread.start()
        self.assertTrue(entered.wait(timeout=1))

        def cancel():
            self.manager.cancel("live")
            cancelled.set()

        canceller = threading.Thread(target=cancel)
        canceller.start()
        try:
            self.assertTrue(cancelled.wait(timeout=1))
        finally:
            release.set()
            thread.join(timeout=3)
            canceller.join(timeout=3)
        self.assertEqual(outcomes, ["cancelled"])

    def test_conditional_submit_cancel_guard_after_successful_request(self):
        task = self.manager.start(task_id="live", thread_id="thread", prompt="fixture", resume=False)
        task.set_state(status="running")
        calls = []
        self.manager.confirmation_control.submit = lambda *args, **kw: calls.append(args) or {}
        base = self.server()
        path = "/v1/tasks/live/confirmation/submit"
        body = {"operation_id": "op", "payload": {}}
        self.assertEqual(self.request(base, path, body)[0], 200)
        self.manager.cancel("live")
        self.assertEqual(self.request(base, path, body)[0], 409)
        self.assertEqual(len(calls), 1)

    def test_conditional_submit_never_reconstructs_a_recovered_task(self):
        self.persist()
        calls = []
        self.manager.confirmation_control.submit = lambda *args, **kw: calls.append(args) or {}
        status, body = self.request(self.server(), "/v1/tasks/old/confirmation/submit", {"operation_id": "op", "payload": {}})
        self.assertEqual(status, 409)
        self.assertEqual(calls, [])
        self.assertEqual(self.launches, [])

    def test_cancel_ack_is_serialized_after_an_already_dispatched_native_effect(self):
        task = self.manager.start(task_id="live", thread_id="thread", prompt="fixture", resume=False)
        task.set_state(status="running")
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        failures = []

        def effect(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fixture_timeout")
            return {"state": "receipt_required"}

        def submit():
            try:
                self.manager.confirmation_submit("live", {"operation_id": "op", "payload": {}})
            except Exception as error:
                failures.append(type(error).__name__)

        def cancel():
            try:
                self.manager.cancel("live")
                cancelled.set()
            except Exception as error:
                failures.append(type(error).__name__)

        self.manager.confirmation_control.submit = effect
        dispatcher = threading.Thread(target=submit)
        cancellation = threading.Thread(target=cancel)
        dispatcher.start()
        try:
            self.assertTrue(entered.wait(3))
            cancellation.start()
            self.assertFalse(cancelled.wait(0.1))
        finally:
            release.set()
            dispatcher.join(5)
            if cancellation.ident is not None:
                cancellation.join(5)
        self.assertEqual(failures, [])
        self.assertTrue(cancelled.is_set())
        with self.assertRaisesRegex(RuntimeError, "confirmation_task_cancelled_or_inactive"):
            self.manager.confirmation_submit("live", {"operation_id": "op2", "payload": {}})

    def test_native_receipt_rpc_is_authenticated_and_never_a_submit(self):
        task = self.manager.start(task_id="live", thread_id="thread", prompt="fixture", resume=False)
        task.set_state(status="running")
        calls = []
        self.manager.confirmation_control.receipt = lambda *args, **kw: calls.append(args) or {"state": "not_found"}
        base = self.server()
        path = "/v1/tasks/live/confirmation/receipts/by-version/" + "a" * 64
        self.assertEqual(self.request(base, path, authorized=False)[0], 401)
        self.assertEqual(self.request(base, path)[0], 200)
        self.assertEqual(calls[0][1:], ("by-version", "a" * 64))

    def test_confirmation_restore_refuses_unknown_process_and_historical_task(self):
        self.persist(status="running", cleanup=False)
        base = self.server()
        status, body = self.request(base, "/v1/tasks/old/confirmation/recover", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["detail"], "confirmation_task_not_quiescent")
        self.persist()
        self.host._atomic_json(self.host.HOST_STATE_ROOT / "admission-head.json", {"task_id": "newer", "thread_id": "thread"})
        status, body = self.request(base, "/v1/tasks/old/confirmation/recover", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["detail"], "confirmation_task_not_current")
        self.assertEqual(self.launches, [])

    def test_confirmation_recovery_uses_committed_task_not_browser_path(self):
        path = self.persist()
        state = json.loads(path.read_text())
        state["confirmation_owner_candidate"] = {"project": "private-project", "port": 5050, "item_id": "cmd"}
        self.host._atomic_json(path, state)
        self.host._atomic_json(self.host.HOST_STATE_ROOT / "admission-head.json", {"task_id": "old", "thread_id": "thread"})
        calls = []
        self.manager.pages.restore = lambda task, **kwargs: calls.append(task.state["confirmation_owner_candidate"]) or {"state": "waiting_user"}
        status, body = self.request(self.server(), "/v1/tasks/old/confirmation/recover", {})
        self.assertEqual(status, 200)
        self.assertEqual(calls, [state["confirmation_owner_candidate"]])
        self.assertEqual(self.launches, [])
        self.manager.cancel("old")
        status, body = self.request(self.server(), "/v1/tasks/old/confirmation/recover", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["detail"], "confirmation_task_cancelled")
        self.assertEqual(len(calls), 1)

    def test_executed_daemon_receipt_is_persisted_privately_across_host_restart(self):
        project = self.host.WORKSPACE_ROOT / "project"
        (project / "confirm_ui").mkdir(parents=True)
        task = self.manager.start(task_id="discovered", thread_id="thread", prompt="fixture", resume=False)
        event = {
            "type": "item.completed",
            "item": {
                "id": "cmd1",
                "type": "command_execution",
                "exit_code": 0,
                "command": 'python3 "' + str(self.host.RUNTIME_ROOT / "scripts/confirm_ui/server.py") + '" "' + str(project) + '" --daemon',
                "aggregated_output": "running at http://127.0.0.1:5050",
            },
        }
        task.record_public_progress(event)
        self.assertEqual(task.state["confirmation_owner_candidate"]["port"], 5050)
        self.assertNotIn("confirmation_owner_candidate", task.snapshot())
        recovered = self.host.CodexTaskManager().get("discovered")
        self.assertEqual(recovered.state["confirmation_owner_candidate"], task.state["confirmation_owner_candidate"])
        self.assertNotIn("confirmation_owner_candidate", recovered.snapshot())

    def test_finished_deck_does_not_reopen_an_old_confirmation_gate(self):
        path = self.persist()
        state = json.loads(path.read_text())
        state["artifacts"] = ["/mnt/user-data/outputs/delivered.pptx"]
        self.host._atomic_json(path, state)
        status, body = self.request(self.server(), "/v1/tasks/old/confirmation/recover", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["detail"], "confirmation_task_already_delivered")

    @unittest.skipUnless(os.name == "posix", "Linux flock and process-death qualification")
    def test_kernel_lock_released_after_crash_but_reservation_never_replayed(self):
        code = """
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("fixture", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
store = module.TaskReceipts(Path(sys.argv[2]), None)
with store.admission():
    store.directory("crashed").mkdir(parents=True)
    os._exit(0)
"""
        result = subprocess.run([sys.executable, "-B", "-c", code, str(SOURCE / "ppt_task_receipts.py"), str(self.host.HOST_STATE_ROOT)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.manager.receipts.admission():
            with self.assertRaisesRegex(RuntimeError, "receipt_read_unavailable"):
                self.manager.receipts.require_quiescent()
        with self.assertRaises(RuntimeError):
            self.manager.start(task_id="crashed", thread_id="thread", prompt="fixture", resume=False)
        self.assertEqual(self.launches, [])

    @unittest.skipUnless(os.name == "posix", "POSIX link/permissions qualification")
    def test_posix_receipt_rejects_symlinks_and_writable_files(self):
        path = self.persist()
        original = path.with_name("private.json")
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaisesRegex(RuntimeError, "unsafe_receipt_path"):
            self.manager.get("old")
        path.unlink()
        original.rename(path)
        path.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError, "unsafe_receipt_permissions"):
            self.manager.get("old")

    def test_same_task_historical_state_is_unchanged_by_read(self):
        path = self.persist()
        before = path.read_bytes()
        self.manager.get("old").snapshot()
        self.assertEqual(path.read_bytes(), before)

    def test_four_separate_host_processes_admit_one_execution(self):
        code = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("fixture_test", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
host, launches = module.load_host(sys.argv[2])
try:
    host.CodexTaskManager().start(task_id=sys.argv[3], thread_id="thread", prompt="fixture", resume=False)
    print("admitted")
except RuntimeError:
    print("refused")
"""
        children = []
        try:
            for index in range(4):
                children.append(subprocess.Popen([sys.executable, "-B", "-c", code, str(Path(__file__).resolve()), self.directory.name, "process-" + str(index)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            outcomes = []
            for child in children:
                out, err = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, err)
                outcomes.append(out.strip())
            self.assertEqual(outcomes.count("admitted"), 1)
            self.assertEqual(outcomes.count("refused"), 3)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=3)
                child.stdout.close()
                child.stderr.close()


if __name__ == "__main__":
    unittest.main()
