"""Load the complete candidate module over known local dependencies, then test."""

import importlib
import importlib.util
import hashlib
import json
import os
import platform
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
BASE = REPO / ".codex-post44-sync-20261113" / "backend"
sys.dont_write_bytecode = True
sys.path[:0] = [str(BASE / "packages/harness"), str(BASE), str(ROOT)]


def load(name, relative):
    parent, _, child = name.rpartition(".")
    package = importlib.import_module(parent)
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    setattr(package, child, module)
    spec.loader.exec_module(module)


load("deerflow.utils.conversation_url", "backend/packages/harness/deerflow/utils/conversation_url.py")
load("deerflow.sandbox.tools", "backend/packages/harness/deerflow/sandbox/tools.py")

import pytest

os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
result = pytest.main([str(ROOT / "test_task_conversation_context.py"), "-q", "-p", "no:cacheprovider", "--junitxml=" + str(ROOT / "TEST-RESULTS.xml"), *sys.argv[1:]])
suites = ET.parse(ROOT / "TEST-RESULTS.xml").getroot().findall("testsuite")
validation = {
    "schema": "task-conversation-context-offline-tests.v1",
    "status": "OFFLINE_TESTS_PASSED_NOT_DEPLOYED" if result == 0 else "OFFLINE_TESTS_FAILED",
    "python": platform.python_version(),
    "platform": platform.system(),
    "complete_candidate_module_loaded": True,
    "candidate_tools_sha256": hashlib.sha256((ROOT / "backend/packages/harness/deerflow/sandbox/tools.py").read_bytes()).hexdigest(),
    "helper_sha256": hashlib.sha256((ROOT / "backend/packages/harness/deerflow/utils/conversation_url.py").read_bytes()).hexdigest(),
    "integrated_skill_delivery_text_sha256": hashlib.sha256((ROOT.parents[1] / "candidates/energy-market-daily-2.1.2/scripts/delivery_text.py").read_bytes()).hexdigest(),
    "tests": sum(int(s.get("tests", 0)) for s in suites),
    "failures": sum(int(s.get("failures", 0)) for s in suites),
    "errors": sum(int(s.get("errors", 0)) for s in suites),
    "skipped": sum(int(s.get("skipped", 0)) for s in suites),
    "production_requests": 0,
    "provider_requests": 0,
    "messages_sent": 0,
    "runtime_parent_source_verified": False,
    "real_tool_transports_executed": False,
    "real_local_posix_shell_context_probes": True,
    "real_skill_212_context_subprocess_probes": True,
    "source_inventory_modified": False,
}
(ROOT / "VALIDATION.json").write_text(json.dumps(validation, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
raise SystemExit(result)
