"""Exercise the production inventory wrapper using synthetic Kubernetes objects."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def inventory(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "deploy/enterprise/inspect-weknora-company-knowledge.py"
    spec = importlib.util.spec_from_file_location("company_inventory", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(path), "--inspect-readonly"])
    return module


def setup_queries(monkeypatch, inventory, *, mutate=None, child_status="CATALOG_AND_NAMED_RESOURCES_OBSERVED"):
    deployments = {}
    for name in ("gateway", "weknora-app", "weknora-frontend"):
        deployments[name] = {
            "metadata": {"uid": inventory.GATEWAY_UID if name == "gateway" else name, "generation": 386},
            "spec": {"replicas": 70, "template": {"spec": {"containers": [{"name": "gateway", "image": inventory.GATEWAY_IMAGE}]}}},
            "status": {"observedGeneration": 386, "replicas": 70, "readyReplicas": 70, "updatedReplicas": 70, "availableReplicas": 70},
        }
    pod = {
        "metadata": {"name": "gateway-synthetic", "uid": "pod-uid"},
        "spec": deployments["gateway"]["spec"]["template"]["spec"],
        "status": {"conditions": [{"type": "Ready", "status": "True"}], "containerStatuses": [{"name": "gateway", "containerID": "old"}]},
    }
    calls = []

    def query(args):
        calls.append(args)
        assert args[0] in {"get", "exec"}
        if args[0] == "exec":
            assert args[-1] == inventory.CHILD
            return {"status": child_status, "catalog": {}, "named_resources": {}}
        if args[1] == "pods":
            result = {"items": [copy.deepcopy(pod)]}
        elif args[1] == "pod":
            result = copy.deepcopy(pod)
        else:
            result = copy.deepcopy(deployments[args[2]])
        if mutate:
            mutate(args, calls, result)
        return result

    monkeypatch.setattr(inventory, "query", query)
    return calls


def test_inventory_accepts_stable_readonly_sample(inventory, monkeypatch, capsys):
    calls = setup_queries(monkeypatch, inventory)
    assert inventory.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "READONLY_BASELINE_COLLECTED_NOT_ENABLED"
    assert report["database_modified"] is False
    assert report["workload_modified"] is False
    assert report["api_contract_acceptance_pending"] is True
    assert len([c for c in calls if c[0] == "exec"]) == 1


def test_source_change_does_not_report_acceptance(inventory, monkeypatch, capsys):
    setup_queries(monkeypatch, inventory, child_status="SOURCE_CHANGED")
    assert inventory.main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "INCOMPLETE"


def test_unstable_gateway_does_not_exec_child(inventory, monkeypatch):
    def mutate(args, calls, result):
        if args[1:3] == ["deployment", "gateway"]:
            result["status"]["updatedReplicas"] = 69

    calls = setup_queries(monkeypatch, inventory, mutate=mutate)
    with pytest.raises(inventory.Refused, match="GATEWAY_NOT_STABLE"):
        inventory.main()
    assert all(c[0] == "get" for c in calls)


def test_container_restart_invalidates_observation(inventory, monkeypatch):
    def mutate(args, calls, result):
        if args[1] == "pod" and len([c for c in calls if c[1] == "pod"]) == 2:
            result["status"]["containerStatuses"][0]["containerID"] = "restarted"

    setup_queries(monkeypatch, inventory, mutate=mutate)
    with pytest.raises(inventory.Refused, match="SAMPLED_CONTAINER_CHANGED"):
        inventory.main()


def test_other_deployment_change_invalidates_observation(inventory, monkeypatch):
    def mutate(args, calls, result):
        if args[1:3] == ["deployment", "weknora-app"] and len([c for c in calls if c[1:3] == ["deployment", "weknora-app"]]) == 2:
            result["metadata"]["generation"] += 1

    setup_queries(monkeypatch, inventory, mutate=mutate)
    with pytest.raises(inventory.Refused, match="DEPLOYMENT_CHANGED"):
        inventory.main()
