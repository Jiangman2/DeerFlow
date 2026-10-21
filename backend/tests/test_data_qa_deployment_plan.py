"""Paired rollout planning must preserve all unrelated Gateway fields."""

import copy
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "deploy/enterprise/data-qa-glm-deployment-plan.py"
SPEC = importlib.util.spec_from_file_location("data_qa_deployment_plan", PATH)
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)


def fixture():
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "gateway", "namespace": "deer-flow", "uid": plan.UID, "generation": 381, "resourceVersion": "revision"},
        "spec": {
            "replicas": 70,
            "strategy": {"type": "RollingUpdate"},
            "template": {
                "metadata": {"labels": {"app": "gateway"}},
                "spec": {
                    "containers": [
                        {"name": "gateway", "image": plan.OLD, "imagePullPolicy": "Never", "env": [{"name": "EXISTING", "valueFrom": {"secretKeyRef": {"name": "old", "key": "EXISTING"}}}], "resources": {"limits": {"memory": "4Gi"}}}
                    ],
                    "volumes": [{"name": "config", "configMap": {"name": plan.CONFIG, "items": [{"key": "config.yaml", "path": "config.yaml"}]}}],
                },
            },
        },
    }
    baseline = {"uid": plan.UID, "generation": 381, "spec_sha256": plan.digest(deployment["spec"])}
    config = {"name": "deer-flow-config-data-qa-glm-0123456789ab", "uid": "config-uid", "immutable": True, "namespace": "deer-flow", "data_sha256": "a" * 64}
    secret = {"name": "deer-flow-data-qa-0123456789ab", "uid": "secret-uid", "immutable": True, "namespace": "deer-flow", "keys": [plan.KEY]}
    return deployment, baseline, config, secret


def test_exact_three_changes_and_input_preservation():
    d, baseline, config, secret = fixture()
    original = copy.deepcopy(d)
    patch, expected = plan.activation_plan(d, baseline=baseline, config_receipt=config, secret_receipt=secret)
    assert d == original
    changes = [p for p in patch if p["op"] != "test"]
    assert len(changes) == 3
    assert next(p for p in patch if p["path"] == "/spec")["value"] == original["spec"]
    reverted = copy.deepcopy(expected)
    pod = reverted["template"]["spec"]
    pod["containers"][0]["image"] = plan.OLD
    pod["containers"][0]["env"].pop()
    pod["volumes"][0]["configMap"]["name"] = plan.CONFIG
    assert reverted == original["spec"]


@pytest.mark.parametrize("change", ["generation", "spec", "uid", "secret_mutable", "config_mutable", "wrong_key", "new_key_exists"])
def test_refuses_drift_or_unqualified_resources(change):
    d, baseline, config, secret = fixture()
    if change == "generation":
        d["metadata"]["generation"] += 1
    elif change == "spec":
        d["spec"]["replicas"] = 69
    elif change == "uid":
        d["metadata"]["uid"] = "replacement"
    elif change == "secret_mutable":
        secret["immutable"] = False
    elif change == "config_mutable":
        config["immutable"] = False
    elif change == "wrong_key":
        secret["keys"] = ["OLD_KEY"]
    else:
        d["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": plan.KEY, "value": "synthetic"})
        baseline["spec_sha256"] = plan.digest(d["spec"])
    with pytest.raises(ValueError):
        plan.activation_plan(d, baseline=baseline, config_receipt=config, secret_receipt=secret)


def test_rollback_restores_image_config_and_env_together_and_refuses_later_edits():
    d, baseline, config, secret = fixture()
    _, expected = plan.activation_plan(d, baseline=baseline, config_receipt=config, secret_receipt=secret)
    current = copy.deepcopy(d)
    current["spec"] = expected
    current["metadata"].update(generation=382, resourceVersion="next-revision")
    rollback = plan.rollback_plan(current, accepted_new_spec=expected, saved_old_spec=d["spec"])
    assert rollback[-1]["value"] == d["spec"]
    current["spec"] = copy.deepcopy(expected)
    current["spec"]["replicas"] = 71
    with pytest.raises(ValueError, match="CHANGED_SINCE_ACTIVATION"):
        plan.rollback_plan(current, accepted_new_spec=expected, saved_old_spec=d["spec"])
