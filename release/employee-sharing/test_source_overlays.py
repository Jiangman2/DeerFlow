"""No live Kubernetes calls: read-only collector scope and drift failures."""

import ast
import base64
import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def audit():
    path = Path(__file__).with_name("collect-source-overlays.py")
    spec = importlib.util.spec_from_file_location("employee_overlay_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def objects(audit):
    deployments, pods = {}, []
    source = "# existing scheduler repair\nVALUE = 1\n"
    cm = {
        "metadata": {
            "name": "actual-configmap-not-volume-name",
            "uid": "cm-uid",
            "resourceVersion": "4",
        },
        "data": {"python-key": source, "UNRELATED_SECRET_TEXT": "NEVER_EXPORT"},
    }
    for name in audit.WORKLOADS:
        container = {
            "name": name,
            "image": "exact:" + name,
            "env": [{"name": "API_KEY", "value": "NEVER_EXPORT"}],
        }
        template = {"containers": [container], "volumes": []}
        if name in ("gateway", "ppt-master-runner"):
            template["volumes"] = [
                {
                    "name": "scheduler-repair-volume",
                    "configMap": {
                        "name": cm["metadata"]["name"],
                        "items": [{"key": "python-key", "path": "gateway_app.py"}],
                    },
                }
            ]
            container["volumeMounts"] = [
                {
                    "name": "scheduler-repair-volume",
                    "mountPath": "/app/backend/app/gateway/app.py",
                    "subPath": "gateway_app.py",
                    "readOnly": True,
                }
            ]
        deployment = {
            "metadata": {"name": name, "uid": "deployment-" + name},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": name}},
                "template": {"spec": template},
            },
        }
        deployments[name] = deployment
        pods.append(
            {
                "metadata": {
                    "name": name + "-pod",
                    "uid": "pod-" + name,
                    "labels": {"app": name},
                },
                "spec": copy.deepcopy(template),
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "name": name,
                            "ready": True,
                            "restartCount": 0,
                            "imageID": "sha256:" + "a" * 64,
                            "containerID": "runtime:" + name,
                        }
                    ],
                },
            }
        )
    return deployments, pods, cm, source


def fake_reader(audit, monkeypatch, *, drift=None):
    deployments, pods, cm, source = objects(audit)
    calls, counts = [], {}

    def read(args):
        calls.append(args)
        token = tuple(args[:3])
        counts[token] = counts.get(token, 0) + 1
        if args[:2] == ["get", "deployment"]:
            result = copy.deepcopy(deployments[args[2]])
            if drift == "deployment" and args[2] == "gateway" and counts[token] == 2:
                result["spec"]["replicas"] = 2
            return result
        if args[:2] == ["get", "pods"]:
            return {"items": copy.deepcopy(pods)}
        if args[:2] == ["get", "pod"]:
            result = copy.deepcopy(
                next(p for p in pods if p["metadata"]["name"] == args[2])
            )
            if drift == "pod" and args[2] == "gateway-pod":
                result["status"]["containerStatuses"][0]["restartCount"] += 1
            return result
        if args[:2] == ["get", "configmap"]:
            assert args[2] == cm["metadata"]["name"]
            result = copy.deepcopy(cm)
            if drift == "configmap" and counts[token] == 2:
                result["metadata"]["resourceVersion"] = "5"
            return result
        assert args[0] == "exec"
        assert args[4:9] == [
            "--",
            "/app/backend/.venv/bin/python",
            "-B",
            "-c",
            audit.READER,
        ]
        assert json.loads(args[9]) == ["/app/backend/app/gateway/app.py"]
        raw = (source + "# stale" if drift == "mounted_bytes" else source).encode()
        return {
            "/app/backend/app/gateway/app.py": {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        }

    monkeypatch.setattr(audit, "read", read)
    return deployments, pods, cm, calls


def test_collects_actual_binding_source_and_pod_receipt_without_other_values(
    audit, monkeypatch
):
    deployments, pods, cm, calls = fake_reader(audit, monkeypatch)
    before = copy.deepcopy((deployments, pods, cm))
    report = audit.collect()
    binding = report["workloads"]["gateway"]["source_bindings"][0]
    assert binding["configmap_name"] == "actual-configmap-not-volume-name"
    assert binding["volume_name"] == "scheduler-repair-volume"
    assert binding["configmap_key"] == "python-key"
    assert binding["configmap_uid"] == "cm-uid"
    assert base64.b64decode(binding["base64"]).decode() == cm["data"]["python-key"]
    assert report["workloads"]["frontend"]["source_bindings"] == []
    assert "NEVER_EXPORT" not in json.dumps(report)
    assert "UNRELATED_SECRET_TEXT" not in json.dumps(report)
    assert report["ready_for_rollout"] is False
    assert report["production_mutation"] == "NONE"
    assert report["employee_data_collected"] is False
    assert all(a[0] in ("get", "exec") for a in calls)
    assert (deployments, pods, cm) == before


@pytest.mark.parametrize("drift", ["deployment", "pod", "configmap", "mounted_bytes"])
def test_drift_never_produces_complete_evidence(audit, monkeypatch, drift):
    fake_reader(audit, monkeypatch, drift=drift)
    with pytest.raises(ValueError):
        audit.collect()


@pytest.mark.parametrize(
    "problem",
    [
        "secret",
        "directory",
        "dynamic",
        "writable",
        "missing_key",
        "duplicate_path",
        "optional",
    ],
)
def test_unsupported_source_mount_fails_without_secret_lookup(audit, problem):
    deployments, _, _, _ = objects(audit)
    spec = deployments["gateway"]["spec"]["template"]["spec"]
    mount = spec["containers"][0]["volumeMounts"][0]
    volume = spec["volumes"][0]
    if problem == "secret":
        volume["secret"] = {"secretName": "not-read"}
        del volume["configMap"]
    elif problem == "directory":
        mount["mountPath"] = "/app/backend/app"
    elif problem == "dynamic":
        mount["subPathExpr"] = "$(UNKNOWN)"
    elif problem == "writable":
        mount["readOnly"] = False
    elif problem == "missing_key":
        mount["subPath"] = "absent.py"
    elif problem == "duplicate_path":
        volume["configMap"]["items"].append(
            {"key": "different", "path": "gateway_app.py"}
        )
    else:
        volume["configMap"]["optional"] = True
    with pytest.raises(ValueError):
        audit.bindings("gateway", spec)


def test_unrelated_configuration_and_data_mounts_are_not_collected(audit):
    spec = {
        "containers": [
            {
                "name": "gateway",
                "volumeMounts": [
                    {"name": "config", "mountPath": "/app/backend/config.yaml"},
                    {"name": "data", "mountPath": "/app/backend/.deer-flow"},
                    {"name": "skills", "mountPath": "/app/skills"},
                    {"name": "secret", "mountPath": "/etc/deerflow-ca"},
                ],
            }
        ]
    }
    assert audit.bindings("gateway", spec) == []


def test_binary_key_and_no_items_mount_mapping(audit):
    deployments, _, cm, _ = objects(audit)
    spec = deployments["gateway"]["spec"]["template"]["spec"]
    del spec["volumes"][0]["configMap"]["items"]
    assert audit.bindings("gateway", spec)[0]["configmap_key"] == "gateway_app.py"
    cm["binaryData"] = {"binary": base64.b64encode(b"source").decode()}
    assert audit.config_key(cm, "binary") == b"source"
    cm["data"]["binary"] = "other"
    with pytest.raises(ValueError):
        audit.config_key(cm, "binary")


def test_reader_host_and_collector_parse_with_python37(audit):
    ast.parse(Path(audit.__file__).read_text(encoding="utf-8"), feature_version=(3, 7))
    ast.parse(audit.READER, feature_version=(3, 7))


def test_new_report_refuses_overwrite(audit, tmp_path):
    path = tmp_path / "report.json"
    audit.write_report(path, {"ready_for_rollout": False})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        audit.write_report(path, {"changed": True})
    assert path.read_bytes() == before
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_kubernetes_wrapper_has_read_scope_and_does_not_echo_errors(audit, monkeypatch):
    from types import SimpleNamespace

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 30
        return SimpleNamespace(
            returncode=1, stdout=b"NEVER_EXPORT", stderr=b"NEVER_EXPORT"
        )

    monkeypatch.setattr(audit.subprocess, "run", run)
    with pytest.raises(RuntimeError) as e:
        audit.read(["get", "configmap", "fixed", "-o", "json"])
    assert "NEVER_EXPORT" not in str(e.value)
    for args in (
        ["get", "secret", "forbidden"],
        ["patch", "deployment", "gateway"],
        ["exec", "gateway-pod", "--", "sh", "-c", "forbidden"],
    ):
        with pytest.raises(ValueError):
            audit.read(args)
    assert len(calls) == 1


def test_only_collected_pods_need_no_runtime_exec(audit, monkeypatch):
    deployments, pods, _, calls = fake_reader(audit, monkeypatch)
    for name in deployments:
        deployments[name]["spec"]["template"]["spec"]["containers"][0].pop(
            "volumeMounts", None
        )
    for pod in pods:
        pod["spec"]["containers"][0].pop("volumeMounts", None)
    report = audit.collect()
    assert not any(a[0] == "exec" or a[1] == "configmap" for a in calls)
    assert all(not v["source_bindings"] for v in report["workloads"].values())


@pytest.mark.parametrize(
    "changed_stat", [None, "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"]
)
def test_embedded_reader_hashes_real_file_without_source_or_environment_output(
    audit, monkeypatch, tmp_path, capsys, changed_stat
):
    from types import SimpleNamespace

    path = tmp_path / "app.py"
    content = b"# controlled source fixture\n"
    path.write_bytes(content)
    original_path = pathlib.Path
    # Only remap the fixed Linux name into a disposable local file. The reader
    # itself performs the real lstat/open/fstat/read/hash; no production paths.
    monkeypatch.setattr(
        pathlib,
        "Path",
        lambda value: (
            original_path(path)
            if value == "/app/backend/app/gateway/app.py"
            else pytest.fail("unexpected reader path")
        ),
    )
    if os.name == "nt":
        monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)
    real_fstat = os.fstat

    def opened_stat(fd):
        actual = real_fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        values = {name: getattr(actual, name) for name in fields}
        if os.name == "nt":
            # CPython 3.12 Windows lstat still exposes creation time as ctime,
            # while fstat exposes change time. Normalize this fixture only;
            # this does NOT exercise Linux O_NOFOLLOW or real ctime semantics.
            values["st_ctime_ns"] = actual.st_birthtime_ns
        if changed_stat is not None:
            values[changed_stat] += 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(os, "fstat", opened_stat)
    monkeypatch.setattr(sys, "argv", ["reader", '["/app/backend/app/gateway/app.py"]'])
    monkeypatch.setenv("API_KEY", "NEVER_EXPORT")
    if changed_stat is not None:
        with pytest.raises(ValueError, match="^source changed$"):
            exec(compile(audit.READER, "source-hash-reader", "exec"), {})
        assert capsys.readouterr().out == ""
        assert path.read_bytes() == content
        return
    exec(compile(audit.READER, "source-hash-reader", "exec"), {})
    result = capsys.readouterr().out
    assert "NEVER_EXPORT" not in result
    assert "controlled source" not in result
    assert json.loads(result) == {
        "/app/backend/app/gateway/app.py": {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
    }
    assert path.read_bytes() == content


@pytest.mark.parametrize(
    "problem", ["unready", "terminating", "recreated", "unmounted", "mixed_image_ids"]
)
def test_unstable_or_mismatched_runtime_pod_set_fails(audit, monkeypatch, problem):
    deployments, pods, _, _ = fake_reader(audit, monkeypatch)
    pod = pods[0]
    if problem == "unready":
        pod["status"]["containerStatuses"][0]["ready"] = False
    elif problem == "terminating":
        pod["metadata"]["deletionTimestamp"] = "now"
    elif problem == "recreated":
        pods.append(copy.deepcopy(pod))
    elif problem == "unmounted":
        pod["spec"]["containers"][0]["volumeMounts"] = []
    else:
        deployments["gateway"]["spec"]["replicas"] = 2
        other = copy.deepcopy(pod)
        other["metadata"]["name"] = "gateway-second"
        other["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "b" * 64
        pods.append(other)
    with pytest.raises(ValueError):
        audit.collect()
