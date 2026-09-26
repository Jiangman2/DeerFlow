"""Read-only release evidence, not a write fence or a backup."""

import ast
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def audit():
    path = Path(__file__).with_name("collect-storage-topology.py")
    spec = importlib.util.spec_from_file_location("employee_storage_topology", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pod():
    return {
        "metadata": {
            "name": "gateway-one",
            "uid": "pod-uid",
            "labels": {"app": "gateway"},
        },
        "spec": {
            "nodeName": "ecs",
            "volumes": [
                {"name": "data", "persistentVolumeClaim": {"claimName": "user-data"}}
            ],
            "containers": [
                {
                    "name": "gateway",
                    "image": "exact:image",
                    "env": [{"name": "API_KEY", "value": "NEVER_RETURN"}],
                    "volumeMounts": [
                        {"name": "data", "mountPath": "/state", "readOnly": False}
                    ],
                }
            ],
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "gateway",
                    "imageID": "sha256:exact",
                    "image": "exact:image",
                    "containerID": "containerd://one",
                    "restartCount": 0,
                    "ready": True,
                }
            ],
        },
    }


def test_pod_projection_keeps_mounts_but_no_env_or_commands(audit):
    item = pod()
    item["spec"]["containers"][0]["args"] = ["password=NEVER_RETURN"]
    result = audit.project_pod(item)
    assert result["containers"][0]["mounts"] == [
        {"name": "data", "mountPath": "/state", "readOnly": False}
    ]
    assert result["volumes"][0]["claim"] == "user-data"
    assert "NEVER_RETURN" not in json.dumps(result)


def test_pv_projection_does_not_copy_secret_refs_or_csi_attributes(audit):
    item = {
        "metadata": {"name": "pv-one", "uid": "pv-uid"},
        "spec": {
            "csi": {
                "driver": "nfs.csi.k8s.io",
                "volumeHandle": "PRIVATE_HANDLE",
                "volumeAttributes": {"token": "NEVER_RETURN"},
                "nodePublishSecretRef": {"name": "NEVER_RETURN"},
            },
            "accessModes": ["ReadWriteMany"],
            "persistentVolumeReclaimPolicy": "Retain",
        },
    }
    result = audit.project_pv(item)
    assert result["driver"] == "nfs.csi.k8s.io"
    assert result["access_modes"] == ["ReadWriteMany"]
    assert "NEVER_RETURN" not in json.dumps(result)
    assert "PRIVATE_HANDLE" not in json.dumps(result)


@pytest.mark.parametrize(
    "change",
    ["uid", "containerID", "imageID", "restartCount", "ready", "deletionTimestamp"],
)
def test_replaced_restarted_or_unready_gateway_cannot_attest(audit, change):
    before, after = pod(), pod()
    if change == "uid":
        after["metadata"]["uid"] = "replacement"
    elif change == "deletionTimestamp":
        after["metadata"][change] = "now"
    else:
        after["status"]["containerStatuses"][0][change] = (
            False if change == "ready" else "changed"
        )
    with pytest.raises(RuntimeError, match="instance changed"):
        audit.require_same_instance(before, after)


def run_reader(audit, tmp_path, text, extra=None):
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    data = tmp_path / "state"
    data.mkdir(exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DEER_FLOW_") and key != "PYTHONPATH"
    }
    env.update(DEER_FLOW_CONFIG_PATH=str(config), DEER_FLOW_HOME=str(data))
    env.update(extra or {})
    return subprocess.run(
        [sys.executable, "-B", "-c", audit.READER],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_reader_only_reports_storage_enums_not_secrets_or_employee_contents(
    audit, tmp_path
):
    data = tmp_path / "state"
    (data / "users" / "private-user" / "agents").mkdir(parents=True)
    (data / "users" / "private-user" / "agents" / "secret.txt").write_text(
        "employee PRIVATE_CONTENT"
    )
    result = run_reader(
        audit,
        tmp_path,
        "agent_storage:\n  backend: db\ndatabase:\n  backend: postgres\n  url: postgres://PRIVATE_CONTENT\nmodels: [PRIVATE_CONTENT]\n",
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["agent_storage"] == "db"
    assert report["database"] == "postgres"
    assert report["data_home"] == str(data)
    assert report["database_queried"] is False
    assert "PRIVATE_CONTENT" not in result.stdout
    assert "private-user" not in result.stdout
    assert report["roots"]["users"]["kind"] == "directory"


def test_reader_missing_env_reference_is_unknown_not_file_fallback(audit, tmp_path):
    result = run_reader(
        audit,
        tmp_path,
        "agent_storage:\n  backend: $UNSET_STORAGE_REFERENCE\ndatabase:\n  backend: postgres\n",
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["agent_storage"] == "UNKNOWN"


def test_reader_does_not_assume_config_path_or_data_home(audit, tmp_path):
    result = run_reader(
        audit, tmp_path, "{}", {"DEER_FLOW_CONFIG_PATH": "", "DEER_FLOW_HOME": ""}
    )
    report = json.loads(result.stdout)
    assert report["agent_storage"] == "UNKNOWN"
    assert report["data_home"] is None
    assert report["config_selection"] == "UNRESOLVED"


def test_reader_fails_closed_on_yaml_duplicates_without_printing_values(
    audit, tmp_path
):
    result = run_reader(
        audit,
        tmp_path,
        "agent_storage: {backend: file}\nagent_storage: {backend: db}\nsecret: PRIVATE_CONTENT\n",
    )
    assert result.returncode != 0
    assert "PRIVATE_CONTENT" not in result.stdout + result.stderr
    assert "storage_reader=FAIL" in result.stdout


def test_reader_refuses_data_directory_symlink(audit, tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "linked-state"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("Linux exercises the symlink boundary")
    result = run_reader(
        audit, tmp_path, "agent_storage: {backend: file}", {"DEER_FLOW_HOME": str(link)}
    )
    report = json.loads(result.stdout)
    assert report["data_home_state"] == "LINKED_OR_UNSAFE"
    assert report["roots"] == {}


def test_reader_can_read_projected_configmap_symlink_without_exporting_contents(
    audit, tmp_path
):
    real = tmp_path / "projected.yaml"
    real.write_text(
        "agent_storage: {backend: db}\ndatabase: {backend: postgres}\napi_key: PRIVATE_CONTENT\n"
    )
    link = tmp_path / "visible.yaml"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("Linux exercises ConfigMap symlinks")
    result = run_reader(audit, tmp_path, "{}", {"DEER_FLOW_CONFIG_PATH": str(link)})
    assert result.returncode == 0
    assert json.loads(result.stdout)["agent_storage"] == "db"
    assert "PRIVATE_CONTENT" not in result.stdout + result.stderr


def test_cli_creates_only_private_local_evidence_and_refuses_repeat(
    audit, monkeypatch, tmp_path, capsys
):
    target = tmp_path / "topology.json"
    monkeypatch.setattr(audit.socket, "gethostname", lambda: "datax-01")
    monkeypatch.setattr(audit.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", [audit.__file__, "--output", str(target)])
    monkeypatch.setattr(
        audit,
        "collect",
        lambda: {"ready_for_rollout": False, "production_mutation": "NONE"},
    )
    audit.main()
    before = target.read_bytes()
    with pytest.raises(RuntimeError, match="overwritten"):
        audit.main()
    assert target.read_bytes() == before
    assert list(tmp_path.iterdir()) == [target]
    assert "production_workloads_mutated=no" in capsys.readouterr().out


def test_pvc_consumers_include_ppt_runner_and_other_writers(audit):
    a, b, c = pod(), pod(), pod()
    b["metadata"]["name"] = "ppt-master-runner-one"
    c["metadata"]["name"] = "unknown-background-writer"
    c["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = True
    result = audit.consumers([a, b, c], {"user-data"})
    assert [x["pod"] for x in result] == [
        "gateway-one",
        "ppt-master-runner-one",
        "unknown-background-writer",
    ]
    assert [x["read_only"] for x in result] == [False, False, True]


def test_report_is_not_a_consistent_backup_or_write_fence(audit, monkeypatch):
    deployments = {
        "metadata": {"name": "gateway", "uid": "dep", "generation": 1},
        "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "gateway"}}},
    }
    pvc = {
        "items": [
            {
                "metadata": {"name": "user-data", "uid": "pvc"},
                "spec": {"volumeName": "pv", "accessModes": ["ReadWriteMany"]},
                "status": {"phase": "Bound"},
            }
        ]
    }
    calls = []

    def read(args):
        calls.append(args)
        if args[:3] == ["get", "deployment", "gateway"]:
            return copy.deepcopy(deployments)
        if args[:2] == ["get", "pods"]:
            return {"items": [pod()]}
        if args[:2] == ["get", "pvc"]:
            return copy.deepcopy(pvc)
        if args[:2] == ["get", "pv"]:
            return {
                "metadata": {"name": "pv", "uid": "pv"},
                "spec": {
                    "accessModes": ["ReadWriteMany"],
                    "nfs": {"server": "internal", "path": "/exports/data"},
                },
            }
        if args[0] == "exec":
            return {
                "agent_storage": "file",
                "database": "postgres",
                "data_home": "/state",
            }
        if args[:3] == ["get", "pod", "gateway-one"]:
            return pod()
        raise AssertionError(args)

    monkeypatch.setattr(audit, "kubectl", read)
    result = audit.collect()
    assert result["ready_for_rollout"] is False
    assert result["consistent_backup"] is False
    assert result["writers_quiesced"] is False
    assert result["external_writers"] == "UNKNOWN"
    assert all(x[0] in ("get", "exec") for x in calls)
    assert not any("secrets" in x for x in calls)
    assert result["data_mounts"][0]["claim"] == "user-data"


def test_write_evidence_is_exclusive_and_private(audit, tmp_path):
    destination = tmp_path / "report.json"
    audit.write_report(destination, {"ready_for_rollout": False})
    with pytest.raises(FileExistsError):
        audit.write_report(destination, {"overwrite": True})
    assert json.loads(destination.read_text()) == {"ready_for_rollout": False}
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600


def test_host_and_embedded_reader_parse_on_python37(audit):
    ast.parse(Path(audit.__file__).read_text(), feature_version=(3, 7))
    ast.parse(audit.READER, feature_version=(3, 7))
