"""Offline coverage for the server package; no Kubernetes/provider access."""

import ast
import importlib.util
import json
import os
import select
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy/enterprise"
SOURCE_PATHS = {
    "featured": "backend/app/gateway/employee_featured_store.py",
    "channels": "backend/app/channels/runtime_config_store.py",
    "robust": "backend/packages/harness/deerflow/models/robust_openai.py",
    "glm": "backend/packages/harness/deerflow/models/enterprise_glm.py",
    "glm_probe": "backend/app/gateway/enterprise_glm_probe.py",
}


@pytest.fixture
def control():
    spec = importlib.util.spec_from_file_location("acceptance_controller_test", DEPLOY / "run-acceptance.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_controller_parses_on_python37(control):
    ast.parse((DEPLOY / "run-acceptance.py").read_text(), feature_version=(3, 7))
    ast.parse(control.supervisor("print('{}')", 3), feature_version=(3, 7))


def test_shared_only_is_explicit_and_cannot_call_glm(control):
    args = control.parse_arguments(["--output", "/tmp/report", "--shared-only", "--shared-fixture", "--shared-parent", "/shared"])
    assert args.shared_only and not args.glm
    for options in (["--shared-only"], ["--shared-only", "--shared-fixture", "--shared-parent", "/shared", "--glm"]):
        with pytest.raises(SystemExit) as failure:
            control.parse_arguments(["--output", "/tmp/report"] + options)
        assert failure.value.code == 2


def test_remote_key_is_stdin_only_and_no_failure_output(control, monkeypatch, capsys):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1, stdout="fixture-secret", stderr="fixture-secret")

    monkeypatch.setattr(control.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="remote_command_failed"):
        control.remote("fixture-pod", {"action": "glm", "key": "fixture-secret"})
    assert "fixture-secret" not in repr(calls[0][0])
    assert json.loads(calls[0][1]["input"])["key"] == "fixture-secret"
    assert "fixture-secret" not in capsys.readouterr().out


def test_no_getpass_echo_fallback(control, monkeypatch):
    import warnings

    def fail(*_):
        warnings.warn("no tty", control.getpass.GetPassWarning)
        pytest.fail("must not echo secret")

    monkeypatch.setattr(control.getpass, "getpass", fail)
    with pytest.raises(control.getpass.GetPassWarning):
        control.hidden_key()


def storage_pod(volume=None, **mount):
    return {"spec": {"containers": [{"name": "gateway", "volumeMounts": [dict(name="data", mountPath="/shared", **mount)]}], "volumes": [dict(name="data", **(volume if volume is not None else {"nfs": {"server": "fixture", "path": "/"}}))]}}


def test_shared_mount_rejects_container_local_readonly_and_dynamic_subpath(control):
    with pytest.raises(RuntimeError, match="not_a_shared"):
        control.shared_mount(storage_pod({"emptyDir": {}}), "/shared")
    for kwargs in ({"readOnly": True}, {"subPathExpr": "$(POD_NAME)"}):
        with pytest.raises(RuntimeError, match="needs_static_writable"):
            control.shared_mount(storage_pod(**kwargs), "/shared")


@pytest.mark.parametrize("subpath", ["/absolute", "../escape", "a/../b", "a//b", "a\\b", "."])
def test_subpath_traversal_rejected(control, subpath):
    with pytest.raises(RuntimeError, match="unsafe_static_subpath"):
        control.shared_mount(storage_pod(subPath=subpath), "/shared")


def test_directory_subpath_is_part_of_shared_identity(control):
    first = control.shared_mount(storage_pod(subPath="runtime/home"), "/shared")
    assert first == control.shared_mount(storage_pod(subPath="runtime/home"), "/shared")
    assert first != control.shared_mount(storage_pod(subPath="another/home"), "/shared")
    assert first != control.shared_mount(storage_pod(), "/shared")


def test_pvc_requires_bound_rwx_and_equal_claim_identity(control, monkeypatch):
    pvc = {"metadata": {"uid": "fixture-pvc"}, "status": {"phase": "Bound"}, "spec": {"accessModes": ["ReadWriteMany"]}}
    monkeypatch.setattr(control.network, "command", lambda _: pvc)
    pod = storage_pod({"persistentVolumeClaim": {"claimName": "shared"}}, subPath="runtime")
    assert "fixture-pvc" in control.shared_mount(pod, "/shared")
    pvc["spec"]["accessModes"] = ["ReadWriteOnce"]
    with pytest.raises(RuntimeError, match="requires_bound_rwx"):
        control.shared_mount(pod, "/shared")


def test_private_report_never_overwrites(control, tmp_path, monkeypatch):
    monkeypatch.setattr(control, "safe_parent", lambda _: None)
    path = tmp_path / "result.json"
    control.save(path, {"result": "fixture"})
    with pytest.raises(FileExistsError):
        control.save(path, {})
    assert json.loads(path.read_text()) == {"result": "fixture"}


def test_package_has_only_allowlisted_sources_and_verified_manifest(tmp_path):
    import hashlib
    import tarfile

    output = tmp_path / "diagnostic.tar.gz"
    subprocess.run([sys.executable, "-B", str(DEPLOY / "package-acceptance.py"), "--output", str(output)], check=True, capture_output=True, text=True)
    with tarfile.open(output) as archive:
        assert len(archive.getmembers()) == 11
        assert all(item.isfile() and "/" not in item.name and item.mode == 0o600 for item in archive.getmembers())
        for line in archive.extractfile("PACKAGE.SHA256").read().decode().splitlines():
            expected, name = line.split("  ")
            assert hashlib.sha256(archive.extractfile(name).read()).hexdigest() == expected
        assert b"\r\n" not in archive.extractfile("run-datax01-acceptance.sh").read()
    assert hashlib.sha256(output.read_bytes()).hexdigest() == Path(str(output) + ".sha256").read_text().split()[0]


def test_storage_controller_sequence_includes_two_writers_and_no_production_config(control, monkeypatch):
    names = [{"metadata": {"name": "a"}}, {"metadata": {"name": "b"}}]
    monkeypatch.setattr(control, "shared_mount", lambda *_: "same-rwx-mount")
    calls = []

    def remote(pod, payload):
        calls.append((pod, payload))
        if payload["action"] == "shared_parent":
            assert payload["parent"] == "/shared"
            return {"result": "PASS"}
        assert payload["fixture"].startswith("/shared/portal-acceptance-")
        return {"result": "PASS", "fixture_sha256": "same"}

    monkeypatch.setattr(control, "remote", remote)
    ready = set()

    class Gate:
        def __init__(self, pod, payload):
            self.pod, self.payload, self.events = pod, payload, []
            calls.append((pod, payload))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def receive(self, event):
            if event == "ready":
                ready.add(self.pod)
            return {"result": "PASS"}

        def command(self, command):
            if command == "go":
                assert ready == {"a", "b"}, "both ready before any GO"

    monkeypatch.setattr(control, "GatedRemote", Gate)
    report = control.storage_test(names, {"a": {"runtime_home": "/shared"}, "b": {"runtime_home": "/shared"}}, {"featured": "fixture", "channels": "fixture"}, None)
    assert report["result"] == "PASS" and not report["production_config_touched"]
    assert sorted((p, v["index"]) for p, v in calls if v["action"] == "write") == [("a", 0), ("b", 1)]
    assert len(calls) == 9
    assert [v["action"] for _, v in calls[:2]] == ["shared_parent", "shared_parent"]

    # A real data verification failure must remain blocking even though control
    # signalling and both writes completed successfully.
    normal_remote = remote

    def stale_remote(pod, payload):
        if payload["action"] == "verify":
            return {"result": "FAIL", "failed_check": "lost_concurrent_update"}
        return normal_remote(pod, payload)

    monkeypatch.setattr(control, "remote", stale_remote)
    failed = control.storage_test(names, {}, {"featured": "fixture", "channels": "fixture"}, "/shared")
    assert failed["result"] == "INCOMPLETE"
    assert failed["failed_check"] == "shared_fixture_failed_verify"
    assert len([r for r in failed["receipts"] if r["action"] == "write"]) == 2
    assert failed["receipts"][-1]["receipt"]["failed_check"] == "lost_concurrent_update"


def test_unsafe_second_parent_fails_before_any_fixture_write(control, monkeypatch):
    names = [{"metadata": {"name": "a"}}, {"metadata": {"name": "b"}}]
    monkeypatch.setattr(control, "shared_mount", lambda *_: "same")
    calls = []

    def remote(pod, payload):
        calls.append(payload["action"])
        return {"result": "PASS" if pod == "a" else "FAIL", "failed_check": "unsafe_fixture_parent"}

    monkeypatch.setattr(control, "remote", remote)
    with pytest.raises(RuntimeError, match="shared_parent_preflight_failed"):
        control.storage_test(names, {}, {}, "/shared")
    assert calls == ["shared_parent", "shared_parent"]


def test_remote_shared_parent_rejects_single_file_mount(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}")
    result = subprocess.run([sys.executable, "-B", str(DEPLOY / "acceptance-remote.py")], input=json.dumps({"action": "shared_parent", "parent": str(path)}), text=True, capture_output=True, timeout=5)
    assert json.loads(result.stdout)["failed_check"] == "unsafe_fixture_parent"
    assert path.read_text() == "{}"


@pytest.mark.skipif(os.name != "posix", reason="Server supervisor/real flock contract requires Linux")
def test_remote_child_secret_output_suppressed_and_timeout_bounded(control):
    for source, expected in (("print('fixture-secret'); raise RuntimeError('fixture-secret')", "CHILD_EXIT"), ("import time; time.sleep(60)", "DEADLINE")):
        result = subprocess.run([sys.executable, "-B", "-c", control.supervisor(source, 1)], input="{}", capture_output=True, text=True, timeout=6)
        assert json.loads(result.stdout)["error_class"] == expected
        assert "fixture-secret" not in result.stdout + result.stderr


@pytest.mark.skipif(os.name != "posix", reason="Actual deployed filesystem/lock contract requires Linux")
def test_remote_storage_sequence_real_processes(control, tmp_path, monkeypatch):
    sources = {k: (REPO / v).read_text() for k, v in SOURCE_PATHS.items() if k in ("featured", "channels")}

    def remote(pod, payload):
        result = subprocess.run([sys.executable, "-B", str(DEPLOY / "acceptance-remote.py")], input=json.dumps(payload), text=True, capture_output=True, timeout=30)
        value = json.loads(result.stdout)
        assert value.get("result") == "PASS", value
        return value

    monkeypatch.setattr(control, "remote", remote)
    monkeypatch.setattr(control, "shared_mount", lambda *_: "same-fixture-mount")

    def command(pod, code):
        # Directory-marker visibility is deliberately unusable; shared data
        # operations and flock still run unchanged in actual child processes.
        guard = """
from pathlib import Path
_exists = Path.exists
def checked_exists(path):
    if path.name in ("lock-held", "release-lock", "writer-0", "writer-1"):
        raise RuntimeError("filesystem_marker_barrier_must_not_be_used")
    return _exists(path)
Path.exists = checked_exists
"""
        return [sys.executable, "-B", "-c", guard + code, "--gated"]

    monkeypatch.setattr(control, "gated_command", command)
    names = [{"metadata": {"name": name}} for name in ("a", "b")]
    report = control.storage_test(names, {}, sources, str(tmp_path))
    assert report["result"] == "PASS", report
    verifications = [r["receipt"] for r in report["receipts"] if r["action"] == "verify"]
    assert verifications[0]["fixture_sha256"] == verifications[1]["fixture_sha256"]
    assert all(r["revision"] == 3 and r["cas"] == "PASS" and r["atomic_replace_failure"] == "PASS" for r in verifications)
    root = Path(report["fixture"])
    assert not any((root / name).exists() for name in ("lock-held", "release-lock", "writer-0", "writer-1"))
    assert len(report["gated_sessions"]) == 3
    assert all(s["events"][-1]["event"] == "result" for s in report["gated_sessions"])


def test_unsafe_fixture_parent_not_repaired(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX permissions")
    tmp_path.chmod(0o777)
    payload = {"fixture": str(tmp_path / ("portal-acceptance-" + "a" * 32)), "nonce": "a" * 32, "action": "initialize", "sources": {}}
    result = subprocess.run([sys.executable, "-B", str(DEPLOY / "acceptance-remote.py")], input=json.dumps(payload), text=True, capture_output=True, timeout=5)
    assert json.loads(result.stdout)["result"] == "FAIL"
    assert list(tmp_path.iterdir()) == []
    assert tmp_path.stat().st_mode & 0o777 == 0o777


@pytest.mark.skipif(os.name != "posix", reason="Linux stdin control protocol and real flock")
@pytest.mark.parametrize("disconnect", [True, False])
def test_gated_writer_requires_explicit_go_without_filesystem_barrier(tmp_path, disconnect):
    nonce = "b" * 32
    root = tmp_path / ("portal-acceptance-" + nonce)
    sources = {k: (REPO / v).read_text() for k, v in SOURCE_PATHS.items() if k in ("featured", "channels")}
    base = {"fixture": str(root), "nonce": nonce, "sources": sources}
    remote = [sys.executable, "-B", str(DEPLOY / "acceptance-remote.py")]
    result = subprocess.run(remote, input=json.dumps(dict(base, action="initialize")), text=True, capture_output=True, timeout=10)
    assert json.loads(result.stdout)["result"] == "PASS"
    before = (root / "featured.json").read_bytes()
    child = subprocess.Popen(remote + ["--gated"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        child.stdin.write(json.dumps(dict(base, action="write", index=0)) + "\n")
        child.stdin.flush()
        assert select.select([child.stdout], [], [], 5)[0], "worker did not acknowledge readiness"
        frame = json.loads(child.stdout.readline())
        assert frame["event"] == "ready" and frame["nonce"] == nonce
        assert (root / "featured.json").read_bytes() == before
        assert not list(root.glob("writer-*"))
        # Neither EOF nor a different fixture's control message grants a write.
        if not disconnect:
            child.stdin.write(json.dumps({"command": "go", "nonce": "wrong", "action": "write", "writer": 0}) + "\n")
            child.stdin.flush()
        child.stdin.close()
        child.wait(timeout=5)
        terminal = json.loads(child.stdout.readline())
        assert terminal["receipt"]["result"] == "FAIL"
        assert (root / "featured.json").read_bytes() == before
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_failed_storage_keeps_completed_receipts(control, monkeypatch):
    names = [{"metadata": {"name": "a"}}, {"metadata": {"name": "b"}}]
    monkeypatch.setattr(control, "shared_mount", lambda *_: "same")

    def remote(pod, payload):
        if payload["action"] == "shared_parent":
            return {"result": "PASS"}
        return {"result": "FAIL", "failed_check": "synthetic_initialize_failed"}

    monkeypatch.setattr(control, "remote", remote)
    report = control.storage_test(names, {}, {"featured": "", "channels": ""}, "/shared")
    assert report["result"] == "INCOMPLETE"
    assert report["fixture"].startswith("/shared/portal-acceptance-")
    assert report["receipts"][0]["action"] == "initialize"
    assert report["receipts"][0]["receipt"]["failed_check"] == "synthetic_initialize_failed"


@pytest.mark.skipif(os.name != "posix", reason="Linux process lifetime and pipe deadline")
def test_gated_remote_deadline_and_raw_output_redaction(control, monkeypatch):
    monkeypatch.setattr(control, "gated_command", lambda *_: [sys.executable, "-c", "import time; time.sleep(60)"])
    payload = {"action": "write", "nonce": "a" * 32, "index": 0}
    with control.GatedRemote("fixture", payload, timeout=0.3) as child:
        with pytest.raises(control.CheckFailure, match="control_receive_deadline"):
            child.receive("ready")
    assert child.process.poll() is not None
    monkeypatch.setattr(control, "gated_command", lambda *_: [sys.executable, "-c", "import sys; sys.stdin.readline(); print('private-raw-output', flush=True)"])
    with control.GatedRemote("fixture", payload) as child:
        with pytest.raises(control.CheckFailure, match="invalid_control_receipt") as caught:
            child.receive("ready")
    assert "private-raw-output" not in str(caught.value)
