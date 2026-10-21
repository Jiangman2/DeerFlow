"""Stdlib-only subprocess acceptance; no provider, CLI, credentials or network.

Run on Linux with unittest to exercise real owned process groups. Windows can
run the cross-process ledger/MCP tests; process-group cases are Linux-only.
"""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "docker/sandbox-ppt-master"


def load(name):
    spec = importlib.util.spec_from_file_location("process_test_" + name, SOURCE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def live(pid):
    try:
        # Zombies have exited, even if the container's init has not reaped them.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[0] not in {"Z", "X"}
    except FileNotFoundError:
        return False


class ProcessTests(unittest.TestCase):
    def test_parallel_process_admission_never_exceeds_64(self):
        guard_module = load("ppt_research_guard")
        with tempfile.TemporaryDirectory() as folder:
            ledger = guard_module.ResearchGuard.create(Path(folder) / "guard.sqlite", "fixture-capability")
            code = """
import sys
sys.path.insert(0, sys.argv[1])
from ppt_research_guard import ResearchGuard, ResearchStopped
ledger = ResearchGuard(sys.argv[2], 'fixture-capability')
count = 0
for _ in range(12):
    try:
        ledger.reserve('c' * 64)
        count += 1
    except ResearchStopped:
        pass
print(count)
"""
            children = []
            try:
                for _ in range(8):
                    children.append(subprocess.Popen([sys.executable, "-B", "-c", code, str(SOURCE), str(ledger.path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
                admitted = 0
                for child in children:
                    out, err = child.communicate(timeout=20)
                    self.assertEqual(child.returncode, 0, err)
                    admitted += int(out)
                self.assertEqual(admitted, 64)
                self.assertEqual(ledger.snapshot()["reason"], "research_attempt_budget")
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)
                    child.stdout.close()
                    child.stderr.close()

    def test_separate_mcp_processes_share_budget_and_do_not_retry_gateway(self):
        guard_module = load("ppt_research_guard")
        with tempfile.TemporaryDirectory() as folder:
            ledger = guard_module.ResearchGuard.create(Path(folder) / "guard.sqlite", "fixture-capability")
            env = {**os.environ, "PPT_RUNTIME_CAPABILITY": "fixture-capability", "PPT_RESEARCH_GUARD_PATH": str(ledger.path), "PPT_RESEARCH_GUARD_REQUIRED": "1"}
            # Real adapter stdio and real process inheritance; fixed in-process
            # HTTP fixture instead of any network access or deployed service.
            code = """
import sys, urllib.error
sys.path.insert(0, sys.argv[1])
import ppt_research_stdio as adapter
def fail(params):
    print('fixture_gateway_call', file=sys.stderr, flush=True)
    raise urllib.error.HTTPError('http://fixture', 502, 'private detail', {}, None)
adapter.call_tool = fail
adapter.main()
"""
            request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "web_search", "arguments": {"query": "public fixture"}}}) + "\n"
            actual_calls = 0
            for _ in range(4):
                child = subprocess.run([sys.executable, "-B", "-c", code, str(SOURCE)], input=request, text=True, capture_output=True, env=env, timeout=15)
                self.assertEqual(child.returncode, 0, child.stderr)
                self.assertTrue(json.loads(child.stdout)["result"]["isError"])
                self.assertNotIn("private detail", child.stdout)
                actual_calls += child.stderr.count("fixture_gateway_call")
            self.assertEqual(actual_calls, 3)
            self.assertEqual(ledger.snapshot()["reason"], "repeated_research_failure")

    @unittest.skipUnless(sys.platform == "linux", "owned POSIX process-group test")
    def test_failure_cleans_child_even_when_leader_already_exited(self):
        self.group_case(leader_exits=True)

    @unittest.skipUnless(sys.platform == "linux", "owned POSIX process-group test")
    def test_failure_cleans_term_resistant_child_after_leader_terminates(self):
        self.group_case(leader_exits=False)

    @unittest.skipUnless(sys.platform == "linux", "owned POSIX process-group test")
    def test_normal_completion_preserves_background_confirmation_owner(self):
        self.group_case(leader_exits=True, normal=True)

    def group_case(self, *, leader_exits, normal=False):
        host = load("agent_host")
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "ready"
            child_code = f"import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path({str(marker)!r}).write_text('ready'); time.sleep(60)"
            code = """
import subprocess, sys, time
p = subprocess.Popen([sys.executable, '-c', sys.argv[1]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(p.pid, flush=True)
if sys.argv[2] == 'wait': time.sleep(60)
"""
            process = subprocess.Popen([sys.executable, "-c", code, child_code, "exit" if leader_exits else "wait"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            pid = None
            try:
                pid = int(process.stdout.readline())
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists())
                if leader_exits:
                    process.wait(timeout=5)
                if normal:
                    host.stop_agent_process(process)
                    self.assertTrue(live(pid), "normal confirmation owner was stopped")
                else:
                    host.stop_agent_process(process, include_descendants=True)
                    deadline = time.monotonic() + 3
                    while live(pid) and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertFalse(live(pid), "failed task left an executing child")
                    self.assertIsNotNone(process.poll())
            finally:
                # Only this fixture's freshly-created session, never user apps.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
