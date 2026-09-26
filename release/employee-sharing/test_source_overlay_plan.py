"""Immutable bootstrap replacement, without deleting existing scheduler fixes."""

import base64
import copy
import hashlib

import pytest

import source_overlay_plan as planner
from test_source_overlays import audit as _audit_fixture, fake_reader

audit = _audit_fixture


def fixture(audit, monkeypatch):
    deployments, pods, cm, _ = fake_reader(audit, monkeypatch)
    # Same old ConfigMap has a second scheduler patch, also used by the runner.
    cm["data"]["scheduler-key"] = "# scheduler must be preserved\n"
    for name in ("gateway", "ppt-master-runner"):
        spec = deployments[name]["spec"]["template"]["spec"]
        spec["volumes"][0]["configMap"]["items"].append(
            {"key": "scheduler-key", "path": "scheduler.py"}
        )
        spec["containers"][0]["volumeMounts"].append(
            {
                "name": "scheduler-repair-volume",
                "mountPath": "/app/backend/app/scheduler/service.py",
                "subPath": "scheduler.py",
                "readOnly": True,
            }
        )
    # Test collection separately; synthesize the extra mount's known reader
    # receipts here so this unit focuses on complete template preservation.
    report = {
        "schema": 1,
        "audit": "employee-sharing-source-overlays-v1",
        "ready_for_rollout": False,
        "production_mutation": "NONE",
        "secrets_collected": False,
        "employee_data_collected": False,
        "coverage": "FIXED_READONLY_PYTHON_SOURCE_MOUNTS_ONLY",
        "workloads": {},
    }
    for name, deployment in deployments.items():
        bindings = []
        for binding in audit.bindings(name, deployment["spec"]["template"]["spec"]):
            raw = cm["data"][binding["configmap_key"]].encode()
            bindings.append(
                {
                    **binding,
                    "base64": base64.b64encode(raw).decode(),
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "configmap_uid": "cm-uid",
                    "configmap_resource_version": "4",
                }
            )
        report["workloads"][name] = {
            "image": "exact:" + name,
            "deployment_uid": deployment["metadata"]["uid"],
            "deployment_spec_sha256": planner.digest(deployment["spec"]),
            "instances": [{"image_id": "sha256:" + "a" * 64}],
            "source_bindings": bindings,
        }
    old = cm["data"]["python-key"].encode()
    new = old + b"# exact reviewed employee bootstrap additions\n"
    delta = {
        "component": "gateway",
        "ready_for_rollout": False,
        "source_image": "exact:gateway",
        "source_image_id": "sha256:" + "a" * 64,
        "entries": [
            {
                "path": planner.RELATIVE,
                "source_sha256": hashlib.sha256(old).hexdigest(),
                "target_sha256": hashlib.sha256(new).hexdigest(),
                "content_base64": base64.b64encode(new).decode(),
            }
        ],
        "anchors": {
            "app/scheduler/service.py": hashlib.sha256(
                cm["data"]["scheduler-key"].encode()
            ).hexdigest()
        },
    }
    return report, delta, deployments, cm


def test_only_gateway_startup_mount_and_image_change_and_inverse_is_exact(
    audit, monkeypatch
):
    report, delta, deployments, cm = fixture(audit, monkeypatch)
    before = copy.deepcopy((report, delta, deployments, cm))
    plan = planner.make_plan(report, delta)
    assert plan["new_configmap"]["immutable"] is True
    assert set(plan["new_configmap"]["data"]) == {"gateway_app.py"}
    assert len(plan["retained_bindings"]) == 3
    rehearsal = planner.rehearse_template(
        deployments["gateway"],
        plan,
        "deerflow.local/deer-flow-gateway@sha256:" + "b" * 64,
    )
    proposed = rehearsal["proposed_spec"]["template"]["spec"]
    original = deployments["gateway"]["spec"]["template"]["spec"]
    assert proposed["volumes"][:-1] == original["volumes"]
    assert proposed["containers"][0]["env"] == original["containers"][0]["env"]
    assert (
        proposed["containers"][0]["volumeMounts"][1]
        == original["containers"][0]["volumeMounts"][1]
    )
    assert proposed["containers"][0]["volumeMounts"][0]["mountPath"] == planner.APP
    assert (
        proposed["containers"][0]["volumeMounts"][0]["name"]
        != "scheduler-repair-volume"
    )
    assert rehearsal["template_inverse_exact"] is True
    assert rehearsal["ready_for_rollout"] is False
    assert rehearsal["data_rollback_authorized"] is False
    assert (report, delta, deployments, cm) == before


@pytest.mark.parametrize(
    "problem",
    [
        "old_source",
        "changed_dependency",
        "wrong_image",
        "wrong_id",
        "alias_drift",
        "other_change",
        "corrupt_payload",
        "other_subpath",
        "missing_mount",
        "incomplete",
    ],
)
def test_unreviewed_or_inconsistent_evidence_fails(audit, monkeypatch, problem):
    report, delta, _, _ = fixture(audit, monkeypatch)
    gateway = report["workloads"]["gateway"]
    binding = next(
        b for b in gateway["source_bindings"] if b["mount_path"] == planner.APP
    )
    if problem == "old_source":
        delta["entries"][0]["source_sha256"] = "0" * 64
    elif problem == "changed_dependency":
        delta["anchors"]["app/scheduler/service.py"] = "0" * 64
    elif problem == "wrong_image":
        gateway["image"] = "wrong:image"
    elif problem == "wrong_id":
        gateway["instances"][0]["image_id"] = "sha256:" + "0" * 64
    elif problem == "alias_drift":
        report["workloads"]["ppt-master-runner"]["source_bindings"][0][
            "configmap_resource_version"
        ] = "different"
    elif problem == "other_change":
        delta["entries"].append(
            {
                "path": "app/scheduler/service.py",
                "source_sha256": delta["anchors"]["app/scheduler/service.py"],
            }
        )
    elif problem == "corrupt_payload":
        delta["entries"][0]["content_base64"] = base64.b64encode(
            b"not reviewed"
        ).decode()
    elif problem == "other_subpath":
        binding["sub_path"] = "other.py"
    elif problem == "missing_mount":
        gateway["source_bindings"].remove(binding)
    else:
        report["workloads"].pop("ppt-master-runner")
    with pytest.raises(ValueError):
        planner.make_plan(report, delta)


@pytest.mark.parametrize(
    "problem",
    [
        "live_uid",
        "live_env",
        "tag",
        "configmap_content",
        "configmap_name",
        "mutable",
        "volume_collision",
    ],
)
def test_stale_or_unsafe_template_rehearsal_fails(audit, monkeypatch, problem):
    report, delta, deployments, _ = fixture(audit, monkeypatch)
    plan = planner.make_plan(report, delta)
    deployment = deployments["gateway"]
    image = "deerflow.local/deer-flow-gateway@sha256:" + "b" * 64
    if problem == "live_uid":
        deployment["metadata"]["uid"] = "recreated"
    elif problem == "live_env":
        deployment["spec"]["template"]["spec"]["containers"][0]["env"].append(
            {"name": "UNRELATED", "value": "new"}
        )
    elif problem == "tag":
        image = "deerflow.local/deer-flow-gateway:latest"
    elif problem == "configmap_content":
        plan["new_configmap"]["data"]["gateway_app.py"] += "# unreviewed\n"
    elif problem == "configmap_name":
        plan["new_configmap"]["metadata"]["name"] = "actual-configmap-not-volume-name"
    elif problem == "mutable":
        plan["new_configmap"]["immutable"] = False
    else:
        deployment["spec"]["template"]["spec"]["volumes"].append(
            {"name": plan["new_configmap"]["metadata"]["name"], "emptyDir": {}}
        )
        plan["deployment_spec_sha256"] = planner.digest(deployment["spec"])
    with pytest.raises(ValueError):
        planner.rehearse_template(deployment, plan, image)
