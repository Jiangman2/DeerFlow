import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), HERE / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_audit(monkeypatch, *, wrong_source=False, source_mount=False):
    module = load("collect-runtime")
    calls = []
    container = {
        "name": "gateway",
        "image": module.EXPECTED_IMAGE,
        "env": [{"name": "API_KEY", "value": "DO-NOT-EXPORT"}],
    }
    if source_mount:
        container["volumeMounts"] = [
            {
                "mountPath": module.SOURCE,
                "name": "overlay",
                "subPath": "source",
                "readOnly": True,
            }
        ]
    deployment = {
        "metadata": {"uid": "deployment-uid", "generation": 2},
        "spec": {"replicas": 1, "template": {"spec": {"containers": [container]}}},
        "status": {"readyReplicas": 1, "updatedReplicas": 1},
    }
    pod = {
        "metadata": {"name": "gateway-one", "uid": "pod-uid"},
        "spec": {"nodeName": "ecs-zdz-pro-0022", "containers": [container]},
        "status": {
            "containerStatuses": [
                {
                    "name": "gateway",
                    "ready": True,
                    "imageID": module.EXPECTED_ID,
                    "containerID": "container-id",
                    "restartCount": 0,
                }
            ]
        },
    }

    def kube(args):
        calls.append(args)
        if args[0] == "exec":
            assert args[-1] == module.RUNTIME_PROBE
            return json.dumps(
                {
                    "source_sha256": "bad" if wrong_source else module.EXPECTED_SOURCE,
                    "python": [3, 12, 13],
                    "packages": {"jsonschema": "4.26.0", "referencing": "0.36"},
                }
            ).encode()
        if args[0] == "logs":
            return ("DO-NOT-EXPORT missing field `cmd` " + module.THREAD).encode()
        assert args[0] == "get" and args[1] in ("deployment", "pod", "pods")
        return json.dumps(
            deployment
            if args[1] == "deployment"
            else pod
            if args[1] == "pod"
            else {"items": [pod]}
        ).encode()

    monkeypatch.setattr(module, "kube", kube)
    return module, calls


def test_read_only_audit_redacts_config_and_logs(monkeypatch):
    module, calls = fixture_audit(monkeypatch)
    report = module.collect()
    assert report["baseline_match"] is True
    assert report["production_rollout_authorized"] is False
    assert "DO-NOT-EXPORT" not in json.dumps(report)
    assert report["log_signals"]["gateway"]["missing_cmd"] == 1
    assert all(args[0] in ("get", "logs", "exec") for args in calls)


@pytest.mark.parametrize("kwargs", [{"wrong_source": True}, {"source_mount": True}])
def test_audit_refuses_unreviewed_source_or_mount(monkeypatch, kwargs):
    module, _ = fixture_audit(monkeypatch, **kwargs)
    assert module.collect()["baseline_match"] is False


@pytest.mark.parametrize("name", ["collect-runtime", "apply-candidate", "package"])
def test_host_scripts_parse_on_python37(name):
    ast.parse(
        (HERE / (name + ".py")).read_text(encoding="utf-8"), feature_version=(3, 7)
    )


def test_apply_refuses_baseline_mismatch_before_any_write(tmp_path):
    module = load("apply-candidate")
    source = tmp_path / module.SOURCE
    source.parent.mkdir(parents=True)
    source.write_text("production_changes = True\n", encoding="utf-8")
    before = source.read_bytes()
    with pytest.raises(ValueError, match="baseline mismatch"):
        module.apply(tmp_path, tmp_path)
    assert source.read_bytes() == before


def test_candidate_roundtrip_has_only_scoped_files_and_applies_exact_base(tmp_path):
    module = load("package")
    module.package(tmp_path)
    import tarfile

    archive = tmp_path / (module.NAME + ".tar.gz")
    root = tmp_path / "expanded"
    root.mkdir()
    with tarfile.open(archive) as bundle:
        bundle.extractall(root, filter="data")
    package = root / module.NAME
    expected = set(module.FILES) | {
        "codex_responses_adapter.py",
        "PATCH.json",
        "PACKAGE.SHA256",
    }
    assert {p.name for p in package.iterdir()} == expected
    assert (
        json.loads((package / "PATCH.json").read_text())["production_rollout_ready"]
        is False
    )
    baseline = (
        module.REPO
        / ".codex-tmp/ppt-v660-r16-build/deerflow-ppt-master-v660-20260923-r16/payload/backend/app/gateway/codex_responses_adapter.py"
    )
    raw = baseline.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == module.BASE_SHA
    backend = tmp_path / "backend"
    target = backend / "app/gateway/codex_responses_adapter.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    apply_module = load("apply-candidate")
    apply_module.apply(backend, package)
    assert target.read_bytes() == (package / "codex_responses_adapter.py").read_bytes()
    result = subprocess.run(
        [sys.executable, str(package / "smoke.py"), str(target)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert "ppt_relay_offline_smoke=PASS" in result.stdout
