"""Offline regression of the shared PPT research failure circuit breaker."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "docker/sandbox-ppt-master/ppt_research_guard.py"


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location("ppt_research_guard_test", SOURCE)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def ledger(module, tmp_path):
    return module.ResearchGuard.create(tmp_path / "research.sqlite", "capability-for-this-task")


def fail(ledger, key="a" * 64, category="gateway_unavailable"):
    ticket = ledger.reserve(key)
    ledger.finish(ticket, category)


def test_three_identical_failures_stop_all_workers_and_parent(module, ledger):
    fail(ledger)
    fail(ledger)
    assert ledger.snapshot()["blocked"] is False
    fail(ledger)
    peer = module.ResearchGuard(ledger.path, "capability-for-this-task")
    assert peer.snapshot()["reason"] == "repeated_research_failure"
    with pytest.raises(module.ResearchStopped):
        peer.reserve("b" * 64)
    assert peer.snapshot()["attempts"] == 3


def test_different_failed_queries_cannot_evade_the_shared_bound(ledger):
    for i in range(8):
        fail(ledger, format(i, "064x"))
    assert ledger.snapshot()["reason"] == "consecutive_research_failures"


def test_real_success_resets_failures_but_not_attempt_count(ledger):
    fail(ledger)
    fail(ledger)
    ledger.finish(ledger.reserve("a" * 64), None)
    fail(ledger)
    assert ledger.snapshot()["blocked"] is False
    assert ledger.snapshot()["attempts"] == 4


def test_duplicate_completion_is_idempotent_and_success_does_not_unblock(ledger):
    ticket = ledger.reserve("a" * 64)
    ledger.finish(ticket, "tls_verification")
    ledger.finish(ticket, "tls_verification")
    assert ledger.snapshot()["consecutive_failures"] == 1
    fail(ledger)
    fail(ledger)
    ledger.finish(ticket, None)
    assert ledger.snapshot()["blocked"] is True


def test_parallel_admission_is_bounded_in_shared_store(module, ledger):
    def reserve(_):
        try:
            return module.ResearchGuard(ledger.path, "capability-for-this-task").reserve("c" * 64)
        except module.ResearchStopped:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        tickets = list(pool.map(reserve, range(80)))
    assert len([ticket for ticket in tickets if ticket is not None]) == 64
    assert ledger.snapshot()["attempts"] == 64
    assert ledger.snapshot()["reason"] == "research_attempt_budget"


def test_ledger_never_stores_queries_credentials_or_raw_errors(ledger):
    fail(ledger, category="secret-key-and-raw-url-must-not-survive")
    assert ledger.snapshot()["last_error"] == "unknown_research_failure"
    raw = ledger.path.read_bytes()
    assert b"capability-for-this-task" not in raw
    assert b"secret-key" not in raw


def test_scope_mismatch_and_existing_file_fail_closed(module, ledger):
    with pytest.raises(ValueError, match="scope"):
        module.ResearchGuard(ledger.path, "another-task").snapshot()
    with pytest.raises(FileExistsError):
        module.ResearchGuard.create(ledger.path, "another-task")


def test_fresh_task_has_separate_budget_and_old_evidence_remains(module, ledger, tmp_path):
    for _ in range(3):
        fail(ledger)
    fresh = module.ResearchGuard.create(tmp_path / "next-turn.sqlite", "next-run-capability")
    assert fresh.snapshot()["attempts"] == 0
    assert fresh.snapshot()["blocked"] is False
    assert ledger.snapshot()["blocked"] is True


def test_idle_user_confirmation_without_research_calls_does_not_trip(ledger):
    assert ledger.snapshot()["blocked"] is False
    assert ledger.snapshot()["attempts"] == 0


def test_invalid_fingerprint_cannot_bloat_ledger(ledger):
    with pytest.raises(ValueError):
        ledger.reserve("unbounded-or-raw-query")
    assert ledger.snapshot()["attempts"] == 0


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE.parent))
    spec = importlib.util.spec_from_file_location("ppt_research_stdio_test", SOURCE.with_name("ppt_research_stdio.py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_adapter_failures_are_shared_and_fourth_does_not_call_gateway(adapter, ledger, monkeypatch):
    import urllib.error
    from types import SimpleNamespace

    monkeypatch.setenv("PPT_RUNTIME_CAPABILITY", "capability-for-this-task")
    monkeypatch.setenv("PPT_RESEARCH_GUARD_PATH", str(ledger.path))
    calls = []

    def fail_open(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError("http://must-not-leak", 502, "secret-message", {}, None)

    monkeypatch.setattr(adapter.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=fail_open))
    for _ in range(4):
        response = adapter.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "web_search", "arguments": {"query": "private topic"}}})
        assert response["result"]["isError"] is True
        assert "secret-message" not in str(response)
        assert "private topic" not in str(response)
    assert len(calls) == 3
    assert ledger.snapshot()["blocked"] is True


def test_adapter_missing_required_guard_does_not_call_gateway(adapter, monkeypatch):
    monkeypatch.setenv("PPT_RUNTIME_CAPABILITY", "test")
    monkeypatch.setenv("PPT_RESEARCH_GUARD_REQUIRED", "1")
    monkeypatch.delenv("PPT_RESEARCH_GUARD_PATH", raising=False)
    monkeypatch.setattr(adapter.urllib.request, "build_opener", lambda *a: pytest.fail("network touched"))
    result = adapter.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "web_search", "arguments": {"query": "topic"}}})
    assert result["result"]["isError"] is True
    assert "guard_unavailable" in str(result)


def test_mcp_progress_metadata_cannot_reset_identical_call_failures(adapter, ledger, monkeypatch):
    monkeypatch.setenv("PPT_RUNTIME_CAPABILITY", "capability-for-this-task")
    monkeypatch.setenv("PPT_RESEARCH_GUARD_PATH", str(ledger.path))
    calls = []

    def failed(params):
        calls.append(1)
        raise TimeoutError("private")

    monkeypatch.setattr(adapter, "call_tool", failed)
    for i in range(4):
        adapter.guarded_call({"name": "web_search", "arguments": {"query": "same"}, "_meta": {"progressToken": i}})
    assert len(calls) == 3
    assert ledger.snapshot()["reason"] == "repeated_research_failure"


def test_host_supervisor_stops_blocked_worker_despite_parent_chatter(monkeypatch, tmp_path):
    import io
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("host_research_guard_test", SOURCE.with_name("agent_host.py"))
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    task = host.AgentTask("test-task", "test-thread", "", False, {}, tmp_path, {"PPT_RUNTIME_CAPABILITY": "test-cap"})
    task.prepare_research_guard(local_root=tmp_path)
    task.set_state(session_id="session-to-resume")
    for _ in range(3):
        fail(task.research_guard)
    task.record_public_progress({"type": "item.completed", "item": {"type": "agent_message", "text": "waiting"}})
    process = SimpleNamespace(stdout=io.StringIO(""))
    with pytest.raises(RuntimeError, match="研究工具"):
        list(host.iter_supervised_agent_stdout(task, process))
    assert task.state["stop_reason"] == "repeated_research_failure"
    assert task.state["resumable"] is True
    assert task.state["research_guard"]["attempts"] == 3


def test_host_mcp_environment_propagates_shared_guard():
    spec = importlib.util.spec_from_file_location("host_guard_env_test", SOURCE.with_name("agent_host.py"))
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    args = " ".join(host._ppt_research_config_args())
    assert "PPT_RESEARCH_GUARD_PATH" in args
    assert "PPT_RESEARCH_GUARD_REQUIRED" in args


@pytest.mark.parametrize("content,is_error", [("# Successful page", False), ("Error: raw provider failure", True)])
def test_adapter_handles_legacy_gateway_fetch_failure(adapter, ledger, monkeypatch, content, is_error):
    import io
    import json
    from types import SimpleNamespace

    monkeypatch.setenv("PPT_RUNTIME_CAPABILITY", "capability-for-this-task")
    monkeypatch.setenv("PPT_RESEARCH_GUARD_PATH", str(ledger.path))
    stream = io.BytesIO(json.dumps({"name": "web_fetch", "content": content}).encode())
    stream.status = 200
    monkeypatch.setattr(adapter.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **k: stream))
    result = adapter.guarded_call({"name": "web_fetch", "arguments": {"url": "https://example.com"}})
    assert result["isError"] == is_error
    assert ledger.snapshot()["consecutive_failures"] == int(is_error)
    if is_error:
        assert "raw provider failure" not in str(result)


def test_adapter_accepts_only_allowlisted_gateway_error_category(adapter):
    import io
    import urllib.error

    for text, expected in [(b'{"detail":{"research_error":"tls_verification"}}', "tls_verification"), (b'{"detail":{"research_error":"secret"}}', "gateway_unavailable")]:
        exc = urllib.error.HTTPError("http://private", 502, "raw", {}, io.BytesIO(text))
        assert adapter.error_category(exc) == expected


def test_guard_store_missing_is_fail_closed(module, ledger):
    ledger.path.unlink()  # Test fixture only; snapshot must not recreate it.
    with pytest.raises(FileNotFoundError):
        ledger.snapshot()
    assert not ledger.path.exists()


def test_host_failure_uses_existing_finally_cleanup_and_retains_session(monkeypatch, tmp_path):
    import io
    import threading
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("host_cleanup_test", SOURCE.with_name("agent_host.py"))
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    for name in ("WORKSPACE_ROOT", "HOST_STATE_ROOT", "CODEX_HOME", "TEMP_ROOT", "RUNTIME_ROOT"):
        monkeypatch.setattr(host, name, tmp_path)
    monkeypatch.setattr(host, "ensure_user_data_directories", lambda: [])
    monkeypatch.setattr(host, "validate_ppt_master_source_contract", lambda value: None)
    monkeypatch.setattr(host, "prepare_writable_skill_snapshot", lambda *a: None)
    monkeypatch.setattr(host, "_prepare_codex_home", lambda *a: None)
    monkeypatch.setattr(host, "validate_codex_provider_contract", lambda *a: {})
    monkeypatch.setattr(host, "has_codex_credentials", lambda *a: True)
    monkeypatch.setattr(host.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    task = host.AgentTask("task", "thread", "prompt", False, {}, tmp_path, {"PPT_RUNTIME_CAPABILITY": "cap"})
    original_prepare = task.prepare_research_guard

    def prepare():
        original_prepare(local_root=tmp_path)
        for _ in range(3):
            fail(task.research_guard)

    monkeypatch.setattr(task, "prepare_research_guard", prepare)
    monkeypatch.setattr(task, "observe_codex_session_usage", lambda **kw: None)
    process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(""), stderr=io.StringIO(""))
    monkeypatch.setattr(host.subprocess, "Popen", lambda *a, **k: process)
    cleaned = []
    monkeypatch.setattr(host, "stop_agent_process", lambda value, **kw: cleaned.append((value, kw)))
    # Exercise the actual durable admission guard now used by every launch.
    manager = host.CodexTaskManager()
    manager.lock = threading.RLock()
    manager.active_task_id = "task"
    monkeypatch.setattr(manager, "_session_for", lambda task: "retained-session")
    manager._run(task)
    assert task.state["status"] == "failed"
    assert task.state["session_id"] == "retained-session"
    assert task.state["resumable"] is True
    assert task.state["process_cleanup_complete"] is True
    assert cleaned == [(process, {"include_descendants": True})]
    assert manager.active_task_id is None
    assert task.process is None
    assert "研究工具" in task.events_path.read_text(encoding="utf-8")


def test_cancellation_takes_priority_over_research_guard(monkeypatch):
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("host_cancel_test", SOURCE.with_name("agent_host.py"))
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)

    class QuietStream:
        def readline(self, limit):
            return ""

    task = SimpleNamespace(task_id="cancel", cancel_requested=True, check_research_guard=lambda: pytest.fail("guard obscured user cancellation"))
    process = SimpleNamespace(stdout=QuietStream())
    cleaned = []
    monkeypatch.setattr(host, "stop_agent_process", lambda value, **kw: cleaned.append((value, kw)))
    assert list(host.iter_supervised_agent_stdout(task, process)) == []
    assert cleaned == [(process, {"include_descendants": True})]
