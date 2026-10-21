"""Production diagnostic has a narrow, read-only, credential-free contract."""

import ast
import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/enterprise/portal-production-probe.py"


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location("portal_production_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python37_controller_and_remote_scripts_parse(probe):
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 7))
    ast.parse(probe.PROBE)
    ast.parse(probe.bounded_probe_source(), feature_version=(3, 7))


def test_errors_never_print_provider_or_kubectl_output(probe, monkeypatch, capsys):
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="secret-value", stderr="secret-value"))
    with pytest.raises(RuntimeError, match="kubernetes_command_failed"):
        probe.command(["get", "pods", "-o", "json"])
    assert "secret-value" not in capsys.readouterr().out


def test_source_classifiers_are_redacted_without_network(probe):
    tree = ast.parse(probe.PROBE)
    helpers = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ("classify", "origin")], type_ignores=[])
    from urllib.parse import urlsplit

    scope = {"urlsplit": urlsplit}
    exec(compile(helpers, "probe-helpers", "exec"), scope)
    assert scope["origin"]("https://u:secret@open.w.cnooc.com.cn/a?token=secret#secret") == "https://open.w.cnooc.com.cn"
    assert scope["classify"](RuntimeError("SSL: EE_KEY_TOO_WEAK secret")) == "TLS_WEAK_KEY_OR_SIGNATURE"
    assert scope["classify"](RuntimeError("net::ERR_NAME_NOT_RESOLVED")) == "DNS"


def test_inventory_only_selects_owned_ready_non_deleting_replicas(probe, monkeypatch, capsys):
    monkeypatch.setattr(probe.socket, "gethostname", lambda: "datax-01")
    deployment = {"metadata": {"uid": "gateway-owner", "generation": 1}, "spec": {"replicas": 70, "template": {"spec": {"containers": [{"name": "gateway", "image": "baseline"}]}}}, "status": {"readyReplicas": 70}}

    def pod(name, node, *, owner="owned-rs", ready=True, deleting=False):
        return {
            "metadata": {"name": name, "ownerReferences": [{"uid": owner, "controller": True}], **({"deletionTimestamp": "now"} if deleting else {})},
            "spec": {"nodeName": node},
            "status": {"containerStatuses": [{"name": "gateway", "ready": ready, "imageID": "pinned"}]},
        }

    calls = []

    def command(args, stdin=None, timeout=25):
        calls.append(args)
        if args[:2] == ["get", "deployment"]:
            return copy.deepcopy(deployment)
        if args[:2] == ["get", "replicasets"]:
            return {"items": [{"metadata": {"uid": "owned-rs", "ownerReferences": [{"uid": "gateway-owner", "controller": True}]}}]}
        if args[:2] == ["get", "pods"]:
            return {"items": [pod("foreign", "n3", owner="other"), pod("dead", "n3", ready=False), pod("deleting", "n3", deleting=True), pod("a", "n1"), pod("b", "n1"), pod("c", "n2")]}
        assert args[0] == "--request-timeout=95s" and args[1] == "exec"
        assert stdin == probe.bounded_probe_source()
        return {"fixture": True}

    monkeypatch.setattr(probe, "command", command)
    probe.main()
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert [sample["pod"] for sample in report["samples"]] == ["a", "c"]
    assert report["baseline_unchanged"] and not report["workloads_mutated"] and not report["ready_for_rollout"]
    assert len([call for call in calls if "exec" in call]) == 2


@pytest.mark.skipif(__import__("os").name == "nt", reason="Remote supervisor targets Linux")
def test_remote_supervisor_suppresses_raw_failed_child_output(probe, monkeypatch):
    import sys

    monkeypatch.setattr(probe, "PROBE", "print('secret-value'); raise RuntimeError('secret-value')")
    result = subprocess.run([sys.executable, "-B", "-c", probe.bounded_probe_source()], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert json.loads(result.stdout)["child_exit_nonzero"] is True
    assert "secret-value" not in result.stdout + result.stderr
