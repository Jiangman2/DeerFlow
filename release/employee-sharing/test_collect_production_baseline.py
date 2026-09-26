import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def audit():
    path = Path(__file__).with_name("collect-production-baseline.py")
    spec = importlib.util.spec_from_file_location("employee_baseline_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_reads(*, changed=False, files=None):
    deployment = {"metadata": {"resourceVersion": "1", "generation": 2}, "spec": {"replicas": 1, "template": {"spec": {"containers": [{"name": "gateway", "image": "local/exact", "env": [{"name": "SECRET", "value": "never-copy"}]}]}}}}
    pod = {"metadata": {"name": "gateway-ready", "uid": "original-pod"}, "spec": {"nodeName": "ecs"}, "status": {"phase": "Running", "containerStatuses": [{"name": "gateway", "image": "local/exact", "ready": True, "imageID": "sha256:exact", "containerID": "containerd://original", "restartCount": 0}]}}
    source = {"app/gateway/app.py": {"sha256": "digest", "base64": "eA=="}} if files is None else files
    return iter([deployment, {"items": [pod]}, {"files": source}, {**deployment, "metadata": {"resourceVersion": "2" if changed else "1", "generation": 2}}, pod])


def test_audit_is_read_only_and_never_returns_environment_values(audit, monkeypatch):
    answers = fake_reads()
    calls = []
    def read(args, **kw):
        calls.append(args)
        return next(answers)
    monkeypatch.setattr(audit, "kubectl", read)
    result = audit.collect_component("gateway", "gateway", "/app/backend", ["app/gateway/app.py"])
    assert result["environment_names_only"] == ["SECRET"]
    assert result["source_coverage"] == "COMPLETE"
    assert result["source_paths_missing"] == []
    assert "never-copy" not in repr(result)
    assert [call[0] for call in calls] == ["get", "get", "exec", "get", "get"]
    assert not any(path.endswith((".env", "config.yaml")) for path in audit.BACKEND + audit.FRONTEND)


def test_concurrent_deployment_change_is_not_a_stable_baseline(audit, monkeypatch):
    answers = fake_reads(changed=True)
    monkeypatch.setattr(audit, "kubectl", lambda *a, **kw: next(answers))
    with pytest.raises(RuntimeError, match="changed"):
        audit.collect_component("gateway", "gateway", "/app/backend", [])


def test_script_parses_on_host_python37(audit):
    import ast
    ast.parse(Path(audit.__file__).read_text(encoding="utf-8"), feature_version=(3, 7))
    ast.parse(audit.PYTHON_READER, feature_version=(3, 7))


def test_missing_source_tree_is_not_reported_as_success(audit, monkeypatch):
    answers = fake_reads(files={"app/gateway/app.py": {"missing": True}})
    monkeypatch.setattr(audit, "kubectl", lambda *a, **kw: next(answers))
    with pytest.raises(RuntimeError, match="source"):
        audit.collect_component("gateway", "gateway", "/wrong/root", ["app/gateway/app.py"])


def test_partial_source_report_names_missing_paths(audit, monkeypatch):
    answers = fake_reads(files={"app/gateway/app.py": {"sha256": "digest", "base64": "eA=="}, "old-path.py": {"missing": True}})
    monkeypatch.setattr(audit, "kubectl", lambda *a, **kw: next(answers))
    result = audit.collect_component("gateway", "gateway", "/app/backend", ["app/gateway/app.py", "old-path.py"])
    assert result["source_coverage"] == "PARTIAL"
    assert result["source_files_collected"] == 1
    assert result["source_paths_missing"] == ["old-path.py"]


def test_frontend_runtime_version_cannot_overwrite_kubernetes_node(audit, monkeypatch):
    answers = list(fake_reads())
    answers[2].update(node="v22.23.1", image="untrusted-reader-value")
    reads = iter(answers)
    monkeypatch.setattr(audit, "kubectl", lambda *a, **kw: next(reads))
    result = audit.collect_component("frontend", "gateway", "/app/frontend", ["app/gateway/app.py"], frontend=True)
    assert result["node"] == "ecs"
    assert result["image"] == "local/exact"
    assert result["runtime"] == {"node": "v22.23.1"}


def test_supplement_scope_is_fixed_code_only_and_keeps_anchors(audit):
    backend = audit.SUPPLEMENT_BACKEND
    frontend = audit.SUPPLEMENT_FRONTEND
    assert "app/gateway/app.py" in backend
    assert "packages/harness/deerflow/runtime/context_compaction.py" in backend
    assert "app/gateway/routers/employee_resources.py" in backend
    assert "packages/harness/deerflow/persistence/agents/drafts.py" in backend
    assert "package.json" in frontend
    assert "src/components/workspace/input-box.tsx" in frontend
    assert "src/core/skills/invocation.ts" in frontend
    for paths in (backend, frontend):
        assert len(paths) == len(set(paths))
        assert all(not path.startswith("/") and ".." not in path.split("/") for path in paths)
        assert all(path.endswith((".py", ".ts", ".tsx", "package.json")) for path in paths)


@pytest.mark.parametrize("frontend", [False, True])
def test_reader_refuses_parent_directory_symlink(audit, tmp_path, frontend):
    import json
    import shutil
    import subprocess
    import sys
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.py").write_text("not application source")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host cannot create symlinks; execute this case on Linux")
    command = [sys.executable, "-c", audit.PYTHON_READER]
    if frontend:
        node = shutil.which("node")
        if not node:
            pytest.skip("Node reader requires Node runtime")
        command = [node, "-e", audit.NODE_READER]
    result = subprocess.run(command + [str(root), json.dumps(["link/private.py"])], capture_output=True, timeout=10)
    assert result.returncode != 0
    assert b"not application source" not in result.stdout


def test_supplement_cli_creates_only_requested_report_and_never_overwrites(audit, monkeypatch, tmp_path, capsys):
    import json
    output = tmp_path / "supplement.json"
    monkeypatch.setattr(audit.socket, "gethostname", lambda: "datax-01")
    monkeypatch.setattr(audit.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(audit.sys, "argv", [audit.__file__, "--profile", "supplemental", "--output", str(output)])
    calls = []
    def collect(component, container, root, paths, frontend):
        calls.append((component, paths))
        return {"source_coverage": "PARTIAL", "source_files_collected": 1, "source_paths_missing": ["not-deployed.py"]}
    monkeypatch.setattr(audit, "collect_component", collect)
    assert audit.main() == 0
    assert calls == [("gateway", audit.SUPPLEMENT_BACKEND), ("frontend", audit.SUPPLEMENT_FRONTEND), ("provisioner", ["app.py", "requirements.txt"])]
    data = output.read_bytes()
    assert json.loads(data)["audit"] == "employee-sharing-source-supplement-v1"
    assert json.loads(data)["production_mutation"] == "NONE"
    assert "production_workloads_mutated=no" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="never overwritten"):
        audit.main()
    assert output.read_bytes() == data
    assert len(calls) == 3


@pytest.mark.parametrize("change", ["uid", "containerID", "imageID", "restartCount", "terminating"])
def test_replaced_or_restarted_pod_cannot_attest_source(audit, monkeypatch, change):
    import copy
    answers = list(fake_reads())
    after = copy.deepcopy(answers[-1])
    if change == "uid":
        after["metadata"]["uid"] = "replacement"
    elif change == "terminating":
        after["metadata"]["deletionTimestamp"] = "2026-09-24T00:00:00Z"
    else:
        after["status"]["containerStatuses"][0][change] = 1 if change == "restartCount" else "changed"
    answers[-1] = after
    values = iter(answers)
    monkeypatch.setattr(audit, "kubectl", lambda *a, **kw: next(values))
    with pytest.raises(RuntimeError, match="Pod changed"):
        audit.collect_component("gateway", "gateway", "/app/backend", ["app/gateway/app.py"])


def test_dependency_profile_is_fixed_and_preserves_existing_profiles(audit, monkeypatch, tmp_path):
    import json
    output = tmp_path / "dependencies.json"
    monkeypatch.setattr(audit.socket, "gethostname", lambda: "datax-01")
    monkeypatch.setattr(audit.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(audit.sys, "argv", [audit.__file__, "--profile", "dependencies", "--output", str(output)])
    calls = []
    def collect(component, container, root, paths, frontend):
        calls.append((component, paths))
        return {"source_coverage": "PARTIAL", "source_files_collected": 1, "source_paths_missing": []}
    monkeypatch.setattr(audit, "collect_component", collect)
    assert audit.main() == 0
    assert calls == [("gateway", audit.DEPENDENCY_BACKEND), ("frontend", audit.DEPENDENCY_FRONTEND), ("provisioner", ["app.py", "requirements.txt"])]
    document = json.loads(output.read_bytes())
    assert document["audit"] == "employee-sharing-source-supplement-v1"
    assert document["profile"] == "dependencies"
    assert document["production_mutation"] == "NONE"
    assert "app/gateway/app.py" in audit.DEPENDENCY_BACKEND
    assert "packages/harness/deerflow/skills/agent_selection.py" in audit.DEPENDENCY_BACKEND
    assert "packages/harness/deerflow/skills/skillscan/orchestrator.py" in audit.DEPENDENCY_BACKEND
    assert "package.json" in audit.DEPENDENCY_FRONTEND
    assert "src/core/threads/utils.ts" in audit.DEPENDENCY_FRONTEND
    assert "src/components/workspace/knowledge-scope-selector.tsx" in audit.DEPENDENCY_FRONTEND
    for paths in (audit.DEPENDENCY_BACKEND, audit.DEPENDENCY_FRONTEND):
        assert len(paths) == len(set(paths))
        assert all(not path.startswith("/") and ".." not in path.split("/") for path in paths)
        assert all(path.endswith((".py", ".ts", ".tsx", "package.json")) for path in paths)
    assert len(list(tmp_path.iterdir())) == 1
