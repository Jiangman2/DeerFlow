"""Combined authorized research/confirmation patch, never the paused switch."""

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0044d221d4338ddf455f57c46435920d896a5824"


def definitions(source):
    result = {}
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result[node.name + "." + method.name] = ast.dump(method, include_attributes=False)
    return result


def test_only_approved_runtime_functions_changed():
    for path, allowed in {
        "backend/app/gateway/ppt_runtime_broker.py": {
            "research", "research_error_category", "_research_outcome", "admit_confirmation",
            "confirmation_authority", "dispatch_runtime", "mint", "refresh_confirmation_capability",
        },
        "docker/sandbox-ppt-master/agent_host.py": {
            "stop_agent_process", "iter_supervised_agent_stdout", "_ppt_research_config_args",
            "AgentTask.__init__", "AgentTask.prepare_research_guard", "AgentTask.check_research_guard", "CodexTaskManager._run",
            "_atomic_json", "AgentTask.record_public_progress", "AgentTask.snapshot",
            "CodexTaskManager.__init__", "CodexTaskManager._cancel_locked", "CodexTaskManager._require_confirmation_observer",
            "CodexTaskManager._session_for", "CodexTaskManager.cancel", "CodexTaskManager.confirmation",
            "CodexTaskManager.confirmation_continuation_receipt", "CodexTaskManager.confirmation_continue",
            "CodexTaskManager.confirmation_observe", "CodexTaskManager.confirmation_receipt", "CodexTaskManager.confirmation_submit",
            "CodexTaskManager.get", "CodexTaskManager.start", "AgentHostHandler.do_GET", "AgentHostHandler.do_POST",
        },
        "docker/sandbox-ppt-master/ppt_research_stdio.py": {"error_category", "guarded_call", "call_tool", "dispatch"},
    }.items():
        original = subprocess.run(["git", "show", BEFORE + ":" + path], cwd=ROOT, capture_output=True, check=True, timeout=15).stdout
        before, after = definitions(original), definitions((ROOT / path).read_bytes())
        changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
        assert changed <= allowed, (path, changed - allowed)
        assert not set(before) - set(after), (path, "existing function removed")
