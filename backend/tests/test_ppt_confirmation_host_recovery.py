"""Real private files + real Host admission, without a CLI/provider/task launch."""

import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("confirmation_recovery_host", ROOT / "docker/sandbox-ppt-master/agent_host.py")
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    for name, relative in (("WORKSPACE_ROOT", "workspace"), ("UPLOADS_ROOT", "uploads"), ("OUTPUTS_ROOT", "outputs"), ("HOST_STATE_ROOT", "workspace/.ppt-master-agent"), ("TEMP_ROOT", "workspace/.ppt-master-agent/tmp")):
        monkeypatch.setattr(host, name, tmp_path / relative)
    monkeypatch.setattr(host, "_enterprise_relay_required", lambda env: False)
    starts = []
    monkeypatch.setattr(host, "threading", SimpleNamespace(RLock=host.threading.RLock, Thread=lambda *a, **kw: Mock(start=lambda: starts.append(kw))))
    manager = host.CodexTaskManager()
    return manager, starts


def persist(task="old-task", thread="thread-1", status="completed", cleanup=True, session="original-session", **extra):
    directory = host.HOST_STATE_ROOT / "tasks" / task
    directory.mkdir(parents=True, exist_ok=True)
    state = dict(
        task_id=task,
        thread_id=thread,
        status=status,
        process_cleanup_complete=cleanup,
        session_id=session,
        last_seq=1,
        created_at=1000.0,
        updated_at=1010.0,
        final_message="fixture",
        artifacts=[],
        error="",
        usage={},
        usage_records=[],
        **extra,
    )
    host._atomic_json(directory / "state.json", state)
    (directory / "events.jsonl").write_text(json.dumps(dict(seq=1, kind="host", payload={"message": "persisted progress"}, created_at=1001)) + "\n", encoding="utf-8")
    return state


def start(manager, task="new-task", thread="thread-1", **extra):
    return manager.start(task_id=task, thread_id=thread, prompt="fixture prompt", resume=False, **extra)


def test_restart_reads_existing_receipts_without_reconstructing_execution(environment):
    manager, starts = environment
    persist(worker_session_ids=["private-worker"], runtime_capability="never-public", prompt="never-public")
    before = (host.HOST_STATE_ROOT / "tasks/old-task/state.json").read_bytes()
    recovered = manager.get("old-task")
    assert recovered is not None
    snapshot = recovered.snapshot()
    assert snapshot["status"] == "completed"
    assert snapshot["session_id"] == "original-session"
    assert snapshot["events"][0]["payload"]["message"] == "persisted progress"
    assert snapshot["host_recovery"] == "persisted_terminal_receipt"
    assert "private-worker" not in json.dumps(snapshot)
    assert "never-public" not in json.dumps(snapshot)
    assert (host.HOST_STATE_ROOT / "tasks/old-task/state.json").read_bytes() == before
    assert starts == []


def test_resubmitting_same_task_after_restart_never_launches_it_again(environment):
    manager, starts = environment
    persist()
    assert start(manager, task="old-task").snapshot()["status"] == "completed"
    assert starts == []


@pytest.mark.parametrize("disk", [False, True])
def test_idempotency_cannot_return_a_different_threads_task(environment, disk):
    manager, starts = environment
    if disk:
        persist(task="same-task")
    else:
        start(manager, task="same-task")
    count = len(starts)
    with pytest.raises(RuntimeError, match="task_thread_mismatch"):
        start(manager, task="same-task", thread="other-thread")
    assert len(starts) == count


@pytest.mark.parametrize("status,cleanup", [("running", False), ("pending", False), ("completed", False)])
def test_recovered_unknown_process_is_not_reported_alive_or_clean(environment, status, cleanup):
    manager, starts = environment
    persist(status=status, cleanup=cleanup)
    value = manager.get("old-task").snapshot()
    assert value["persisted_status"] == status
    assert value["status"] == "failed"
    assert value["host_recovery"] == "process_reconciliation_required"
    assert value["process_cleanup_complete"] is False
    assert value["resumable"] is False
    with pytest.raises(RuntimeError, match="prior_task_reconciliation_required"):
        start(manager)
    assert not starts


def test_recovered_cancel_is_durable_intent_not_a_claim_process_was_killed(environment, monkeypatch):
    manager, starts = environment
    persist(status="running", cleanup=False)
    signal = Mock(side_effect=AssertionError("must not signal an unverified/reused PID"))
    monkeypatch.setattr(host.os, "killpg", signal, raising=False)
    snapshot = manager.cancel("old-task").snapshot()
    assert snapshot["cancel_requested"] is True
    assert snapshot["process_cleanup_complete"] is False
    assert snapshot["status"] == "failed"
    state = json.loads((host.HOST_STATE_ROOT / "tasks/old-task/state.json").read_text())
    assert state["cancel_requested"] is True
    assert state["status"] == "running"
    assert not signal.called and not starts


def test_explicit_resume_uses_original_task_not_latest_thread_pointer(environment):
    manager, starts = environment
    persist()
    host._atomic_json(host.HOST_STATE_ROOT / "thread.json", {"thread_id": "thread-1", "session_id": "later-session"})
    task = manager.start(task_id="next-task", thread_id="thread-1", prompt="continue", resume=True, resume_from_task_id="old-task", expected_session_id="original-session")
    assert manager._session_for(task) == "original-session"
    assert task.snapshot()["resume_from_task_id"] == "old-task"
    assert len(starts) == 1


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"thread": "other-thread"}, "resume_source_thread_mismatch"),
        ({"session": "other-session"}, "resume_session_mismatch"),
        ({"status": "cancelled"}, "resume_source_not_completed"),
        ({"cleanup": False}, "resume_source_not_quiescent"),
    ],
)
def test_explicit_resume_rejects_wrong_source_or_unfinished_cleanup(environment, change, expected):
    manager, starts = environment
    persist(**change)
    with pytest.raises(RuntimeError, match=expected):
        manager.start(task_id="next-task", thread_id="thread-1", prompt="continue", resume=True, resume_from_task_id="old-task", expected_session_id="original-session")
    assert not starts
    assert not (host.HOST_STATE_ROOT / "tasks/next-task").exists()


def test_explicit_resume_still_requires_completed_source_at_launch(environment):
    manager, starts = environment
    persist()
    task = manager.start(task_id="next-task", thread_id="thread-1", prompt="continue", resume=True, resume_from_task_id="old-task", expected_session_id="original-session")
    persist(status="cancelled")
    with pytest.raises(RuntimeError, match="resume_source_not_completed"):
        manager._session_for(task)


def test_legacy_resume_validates_thread_owner_pointer(environment):
    manager, starts = environment
    host._atomic_json(host.HOST_STATE_ROOT / "thread.json", {"thread_id": "other-thread", "session_id": "other-session"})
    task = start(manager)
    task.resume = True
    with pytest.raises(RuntimeError, match="persisted_session_thread_mismatch"):
        manager._session_for(task)


@pytest.mark.parametrize("fault", ["invalid_json", "wrong_task", "symlink", "hardlink", "oversized", "incomplete"])
def test_unsafe_or_incomplete_persisted_receipts_never_allow_launch(environment, fault):
    manager, starts = environment
    persist()
    path = host.HOST_STATE_ROOT / "tasks/old-task/state.json"
    if fault == "invalid_json":
        path.write_text("{broken", encoding="utf-8")
    elif fault == "wrong_task":
        state = json.loads(path.read_text())
        state["task_id"] = "wrong-task"
        host._atomic_json(path, state)
    elif fault == "hardlink":
        (path.parent / "hardlink").hardlink_to(path)
    elif fault == "symlink":
        original = path.with_name("original.json")
        path.rename(original)
        try:
            path.symlink_to(original)
        except OSError:
            pytest.skip("symlink privilege unavailable")
    elif fault == "oversized":
        path.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    else:
        path.unlink()
    with pytest.raises(RuntimeError):
        start(manager, task="old-task")
    assert not starts


def test_cross_manager_admission_has_one_durable_winner(environment):
    first, starts = environment
    second = host.CodexTaskManager()

    def attempt(manager, task):
        try:
            return start(manager, task)
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, first, "first"), pool.submit(attempt, second, "second")]
        assert sum(f.result() is not None for f in futures) == 1
    assert len(starts) == 1


def test_missing_task_read_does_not_create_an_execution(environment):
    manager, starts = environment
    assert manager.get("unknown") is None
    assert not (host.HOST_STATE_ROOT / "tasks/unknown").exists()
    assert not starts


def test_separate_host_processes_cannot_admit_two_launches(environment):
    manager, starts = environment
    code = r"""
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("fixture_host", sys.argv[1])
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
host.HOST_STATE_ROOT = Path(sys.argv[2])
host.WORKSPACE_ROOT = host.HOST_STATE_ROOT.parent
host.UPLOADS_ROOT = host.WORKSPACE_ROOT.parent / "uploads"
host.OUTPUTS_ROOT = host.WORKSPACE_ROOT.parent / "outputs"
host.TEMP_ROOT = host.HOST_STATE_ROOT / "tmp"
host._enterprise_relay_required = lambda env: False
class NoAgent:
    def __init__(self, **kw): pass
    def start(self): pass
host.threading.Thread = NoAgent
try:
    host.CodexTaskManager().start(task_id=sys.argv[3], thread_id="thread-1", prompt="fixture", resume=False)
    print("admitted")
except RuntimeError:
    print("refused")
"""
    children = []
    try:
        for index in range(4):
            children.append(subprocess.Popen([sys.executable, "-B", "-c", code, str(SPEC.origin), str(host.HOST_STATE_ROOT), "process-" + str(index)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        outcomes = []
        for child in children:
            out, err = child.communicate(timeout=30)
            assert child.returncode == 0, err
            outcomes.append(out.strip())
        assert outcomes.count("admitted") == 1
        assert outcomes.count("refused") == 3
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()


def test_commit_ack_loss_never_grants_replay_permission(environment, monkeypatch):
    manager, starts = environment
    atomic = host._atomic_json

    def lost_ack(path, data):
        atomic(path, data)
        raise RuntimeError("fixture_lost_ack")

    monkeypatch.setattr(host, "_atomic_json", lost_ack)
    with pytest.raises(RuntimeError, match="fixture_lost_ack"):
        start(manager)
    monkeypatch.setattr(host, "_atomic_json", atomic)
    restored = host.CodexTaskManager()
    task = start(restored)
    assert task.snapshot()["host_recovery"] == "process_reconciliation_required"
    assert task.snapshot()["persisted_status"] == "pending"
    assert not starts


def test_recovered_replay_reads_only_committed_events_and_honors_cursor(environment):
    manager, starts = environment
    persist()
    events = host.HOST_STATE_ROOT / "tasks/old-task/events.jsonl"
    with events.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(seq=2, kind="host", payload={"message": "not committed"})) + "\n")
        stream.write('{"seq":3')
    task = manager.get("old-task")
    assert len(task.snapshot()["events"]) == 1
    assert task.snapshot(1)["events"] == []


def test_uncommitted_cancelled_source_is_never_a_resume_target(environment):
    manager, starts = environment
    persist(cancel_requested=True)
    with pytest.raises(RuntimeError, match="resume_source_not_completed"):
        manager.start(task_id="next", thread_id="thread-1", prompt="continue", resume=True, resume_from_task_id="old-task", expected_session_id="original-session")
    assert not starts


def test_receipt_tampering_is_a_fixed_error_not_a_python_type_error(environment):
    manager, starts = environment
    state = persist()
    state["status"] = ["running"]
    host._atomic_json(host.HOST_STATE_ROOT / "tasks/old-task/state.json", state)
    with pytest.raises(RuntimeError, match="invalid_persisted_status"):
        manager.get("old-task")


def test_current_task_cancel_intent_is_saved_before_process_signal(environment):
    manager, starts = environment
    task = start(manager)
    manager.cancel(task.task_id)
    assert task.state["cancel_requested"] is True
    state = json.loads(task.state_path.read_text())
    assert state["cancel_requested"] is True
    assert state["status"] == "pending"


def test_duplicate_id_cannot_be_rebound_to_another_resume_source(environment):
    manager, starts = environment
    persist()
    with pytest.raises(RuntimeError, match="resume_admission_identity_mismatch"):
        manager.start(task_id="old-task", thread_id="thread-1", prompt="fixture", resume=True, resume_from_task_id="different-task", expected_session_id="different-session")
    assert not starts


def test_new_task_refuses_mixed_thread_storage(environment):
    manager, starts = environment
    persist(thread="other-thread")
    with pytest.raises(RuntimeError, match="thread_receipt_scope_mismatch"):
        start(manager)
    assert not starts
