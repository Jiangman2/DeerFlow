"""Owned confirmation-page transport; no Agent/provider/production requests."""

import importlib.util
import json
import shlex
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docker/sandbox-ppt-master"


def module():
    spec = importlib.util.spec_from_file_location("confirmation_page_test", SOURCE / "ppt_confirmation_pages.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def pages():
    return module()


@pytest.fixture
def fixture(pages, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = workspace / ".ppt-master-runtime"
    project = workspace / "我的演示"
    project.mkdir()
    (project / "confirm_ui").mkdir()
    candidate = {"project": str(project), "port": 5050, "item_id": "command1"}
    item = {
        "id": "command1",
        "type": "command_execution",
        "exit_code": 0,
        "command": 'python3 -B "' + str(runtime / "scripts/confirm_ui/server.py") + '" "' + str(project) + '" --daemon --no-browser',
        "aggregated_output": "running at http://127.0.0.1:5050",
    }
    health = {
        "service": "confirm_ui",
        "status": "ok",
        "project": str(project),
        "pid": 123,
        "stage": 1,
        "recommendations": True,
        "session": {"phase": "strategist", "status": "ready_user", "current_stage": "stage1", "recommendation_version": "a" * 64, "template_error": None, "recommendation_error": None},
    }
    return SimpleNamespace(workspace=workspace, runtime=runtime, project=project, candidate=candidate, item=item, health=health)


def test_only_actual_successful_native_daemon_command_discovers_owner(pages, fixture):
    assert pages.candidate_from_event({"type": "item.completed", "item": fixture.item}, fixture.workspace, fixture.runtime) == fixture.candidate
    for changes in ({"exit_code": 1}, {"exit_code": True}, {"type": "agent_message"}, {"command": "echo " + fixture.item["command"]}, {"command": fixture.item["command"] + "; echo injected"}, {"aggregated_output": "http://127.0.0.1:6060"}):
        item = {**fixture.item, **changes}
        assert pages.candidate_from_event({"type": "item.completed", "item": item}, fixture.workspace, fixture.runtime) is None


def test_discovery_cannot_use_an_unrelated_script_or_project(pages, fixture):
    for command in (fixture.item["command"].replace("server.py", "other.py"), fixture.item["command"].replace(str(fixture.project), str(fixture.workspace.parent)), fixture.item["command"] + " --reset", fixture.item["command"] + " --wait"):
        assert pages.candidate_from_event({"type": "item.completed", "item": {**fixture.item, "command": command}}, fixture.workspace, fixture.runtime) is None


def test_codex_single_shell_envelope_is_discovery_only(pages, fixture):
    for shell in ("/bin/bash", "/usr/bin/bash", "/bin/sh"):
        command = shell + " -lc " + shlex.quote(fixture.item["command"])
        event = {"type": "item.completed", "item": {**fixture.item, "command": command}}
        assert pages.candidate_from_event(event, fixture.workspace, fixture.runtime) == fixture.candidate
    for command in ("bash -lc " + shlex.quote(fixture.item["command"] + "; echo fake"), "bash -lc " + shlex.quote("bash -lc " + shlex.quote(fixture.item["command"]))):
        event = {"type": "item.completed", "item": {**fixture.item, "command": command}}
        assert pages.candidate_from_event(event, fixture.workspace, fixture.runtime) is None


def test_only_host_installed_skill_alias_can_discover_page(pages, fixture):
    fixture.runtime.mkdir()
    alias = fixture.workspace / "codex-skills/ppt-master"
    alias.parent.mkdir()
    try:
        alias.symlink_to(fixture.runtime, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink privilege unavailable")
    item = {**fixture.item, "command": fixture.item["command"].replace(str(fixture.runtime), str(alias))}
    event = {"type": "item.completed", "item": item}
    assert pages.candidate_from_event(event, fixture.workspace, fixture.runtime, skill_alias=alias) == fixture.candidate
    assert pages.candidate_from_event(event, fixture.workspace, fixture.runtime) is None
    alias.unlink()
    alias.symlink_to(fixture.project, target_is_directory=True)
    assert pages.candidate_from_event(event, fixture.workspace, fixture.runtime, skill_alias=alias) is None


def test_health_is_not_model_prose_and_does_not_expose_raw_errors(pages, fixture):
    result = pages.health_view(fixture.candidate, fixture.health)
    assert result["state"] == "waiting_user"
    assert result["port"] == 5050
    assert "project" not in result
    bad = {**fixture.health, "session": {**fixture.health["session"], "template_error": "private secret"}}
    result = pages.health_view(fixture.candidate, bad)
    assert result["state"] == "owner_error"
    assert "private secret" not in json.dumps(result)
    for change in ({"pid": True}, {"service": "svg_editor"}, {"project": "/wrong"}):
        with pytest.raises(RuntimeError, match="confirmation_owner_identity"):
            pages.health_view(fixture.candidate, {**fixture.health, **change})


def test_redirect_and_non_loopback_target_are_not_followed(pages, fixture):
    with patch.object(pages.urllib.request, "build_opener") as opener:
        with pytest.raises(RuntimeError, match="confirmation_health_unavailable"):
            pages.probe(fixture.candidate)
        handlers = opener.call_args.args
        assert any(isinstance(handler, pages.urllib.request.ProxyHandler) and handler.proxies == {} for handler in handlers)
        assert any(isinstance(handler, pages.NoRedirect) for handler in handlers)
    with pytest.raises(RuntimeError, match="invalid_confirmation_port"):
        pages.probe({**fixture.candidate, "port": 8788})


def test_restore_is_exact_native_daemon_not_confirmation_or_agent(pages, fixture, tmp_path):
    records = []
    receipts = SimpleNamespace(atomic_json=lambda path, data: records.append((path, data.copy())), read_bytes=lambda *args: b"")
    task = SimpleNamespace(task_id="original", state={"confirmation_owner_candidate": fixture.candidate}, task_dir=tmp_path / "task")
    task.task_dir.mkdir()
    run = Mock(return_value=SimpleNamespace(returncode=0))
    with patch.object(pages, "verify_source"), patch.object(pages, "probe", side_effect=[None, fixture.health]), patch.object(pages.subprocess, "run", run):
        result = pages.restore(task, workspace=fixture.workspace, source_root=ROOT / "vendor/ppt-master-v6.6.0", receipts=receipts)
    assert result["state"] == "waiting_user"
    assert len(records) == 2 and records[0][1]["state"] == "requested" and records[1][1]["state"] == "ready"
    command = run.call_args.args[0]
    assert command[1] == "-B"
    assert Path(command[2]).as_posix().endswith("scripts/confirm_ui/server.py")
    assert command[3:] == [str(fixture.project), "--daemon", "--no-browser", "--port", "5050"]
    assert run.call_args.kwargs["timeout"] == 15
    assert run.call_args.kwargs["shell"] is False
    assert "--reset" not in command and "--wait" not in command and "--shutdown" not in command


def test_unknown_restore_is_not_replayed_even_after_restart(pages, fixture, tmp_path):
    task = SimpleNamespace(task_id="original", state={"confirmation_owner_candidate": fixture.candidate}, task_dir=tmp_path)
    (tmp_path / "confirmation-recovery.json").write_text('{"schema":1,"task_id":"original","state":"requested"}')
    with patch.object(pages, "verify_source"), patch.object(pages, "probe", return_value=None), patch.object(pages.subprocess, "run") as run:
        result = pages.restore(task, workspace=fixture.workspace, source_root=ROOT, receipts=SimpleNamespace())
    assert result["state"] == "recovery_unconfirmed"
    assert not run.called


def test_timeout_redacts_and_retains_uncertain_operation(pages, fixture, tmp_path):
    records = []
    task = SimpleNamespace(task_id="original", state={"confirmation_owner_candidate": fixture.candidate}, task_dir=tmp_path)
    receipts = SimpleNamespace(atomic_json=lambda path, data: records.append(data.copy()))
    with patch.object(pages, "verify_source"), patch.object(pages, "probe", return_value=None), patch.object(pages.subprocess, "run", side_effect=subprocess.TimeoutExpired("private", 15, output="secret")):
        result = pages.restore(task, workspace=fixture.workspace, source_root=ROOT, receipts=receipts)
    assert records[0]["state"] == "requested"
    assert result["state"] == "recovery_unconfirmed"
    assert "secret" not in json.dumps(result)


def test_recovery_ack_loss_prevents_spawn(pages, fixture, tmp_path):
    task = SimpleNamespace(task_id="original", state={"confirmation_owner_candidate": fixture.candidate}, task_dir=tmp_path)
    receipts = SimpleNamespace(atomic_json=Mock(side_effect=OSError("lost save ack")))
    with patch.object(pages, "verify_source"), patch.object(pages, "probe", return_value=None), patch.object(pages.subprocess, "run") as run:
        with pytest.raises(OSError):
            pages.restore(task, workspace=fixture.workspace, source_root=ROOT, receipts=receipts)
    assert not run.called


def test_real_official_source_is_pinned(pages):
    pages.verify_source(ROOT / "vendor/ppt-master-v6.6.0")
    with tempfile.TemporaryDirectory() as folder:
        with pytest.raises(RuntimeError, match="confirmation_source_unavailable"):
            pages.verify_source(Path(folder))


def test_later_expiry_is_a_new_restore_only_after_previous_success(pages, fixture, tmp_path):
    task = SimpleNamespace(task_id="original", state={"confirmation_owner_candidate": fixture.candidate}, task_dir=tmp_path)
    marker = tmp_path / "confirmation-recovery.json"
    marker.write_text('{"schema":1,"task_id":"original","state":"ready"}')
    records = []
    receipts = SimpleNamespace(atomic_json=lambda path, data: records.append(data.copy()))
    with patch.object(pages, "verify_source"), patch.object(pages, "probe", side_effect=[None, fixture.health]), patch.object(pages.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
        result = pages.restore(task, workspace=fixture.workspace, source_root=ROOT, receipts=receipts)
    assert result["state"] == "waiting_user" and run.call_count == 1
    assert [record["state"] for record in records] == ["requested", "ready"]


def test_invalid_recovery_record_never_authorizes_launch(pages, fixture, tmp_path):
    task = SimpleNamespace(task_id="original", state={"confirmation_owner_candidate": fixture.candidate}, task_dir=tmp_path)
    (tmp_path / "confirmation-recovery.json").write_text('{"schema":1,"task_id":"different-task","state":"ready"}')
    with patch.object(pages, "verify_source"), patch.object(pages, "probe", return_value=None), patch.object(pages.subprocess, "run") as run:
        with pytest.raises(RuntimeError, match="unsafe_confirmation_recovery_record"):
            pages.restore(task, workspace=fixture.workspace, source_root=ROOT, receipts=SimpleNamespace())
    assert not run.called
