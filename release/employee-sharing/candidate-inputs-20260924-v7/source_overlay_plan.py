"""Local source-overlay review and reversible TEMPLATE rehearsal, not rollout.

No Kubernetes client, file writes, database calls or process execution. Keep the
old shared scheduler ConfigMap untouched. A new immutable, content-addressed
ConfigMap supplies only Gateway's employee-aware app.py. Other mounts and PPT
Runner retain their original sources. This does NOT authorize old-image data
rollback, replace maintenance/backup gates, or claim an image has been built.
"""

import base64
import copy
import hashlib
import json
import re

APP = "/app/backend/app/gateway/app.py"
RELATIVE = "app/gateway/app.py"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def source(binding):
    raw = base64.b64decode(binding["base64"], validate=True)
    if (
        len(raw) > 2 * 1024**2
        or binding["bytes"] != len(raw)
        or hashlib.sha256(raw).hexdigest() != binding["sha256"]
        or binding.get("read_only") is not True
        or not binding.get("configmap_uid")
        or not binding.get("configmap_resource_version")
    ):
        raise ValueError("invalid mounted-source evidence")
    return raw


def make_plan(report, gateway_delta):
    if (
        report.get("schema") != 1
        or report.get("audit") != "employee-sharing-source-overlays-v1"
        or report.get("ready_for_rollout") is not False
        or report.get("production_mutation") != "NONE"
        or report.get("secrets_collected") is not False
        or report.get("employee_data_collected") is not False
        or report.get("coverage") != "FIXED_READONLY_PYTHON_SOURCE_MOUNTS_ONLY"
        or set(report.get("workloads", {}))
        != {"gateway", "frontend", "provisioner", "ppt-master-runner"}
        or gateway_delta.get("component") != "gateway"
        or gateway_delta.get("ready_for_rollout") is not False
    ):
        raise ValueError("complete reviewed overlay/candidate evidence required")
    gateway = report["workloads"]["gateway"]
    if (
        gateway["image"] != gateway_delta["source_image"]
        or not gateway["instances"]
        or any(
            instance["image_id"].split("://")[-1].split("@")[-1]
            != gateway_delta["source_image_id"]
            for instance in gateway["instances"]
        )
    ):
        raise ValueError("overlay report belongs to a different Gateway image")
    entries = {e["path"]: e for e in gateway_delta["entries"]}
    if len(entries) != len(gateway_delta["entries"]) or RELATIVE not in entries:
        raise ValueError("candidate startup delta absent or ambiguous")
    aliases, changed, retained = {}, [], []
    for workload, state in report["workloads"].items():
        paths = set()
        for binding in state["source_bindings"]:
            source(binding)
            path = binding["mount_path"]
            if path in paths:
                raise ValueError("duplicate mounted-source path")
            paths.add(path)
            key = (
                binding["configmap_name"],
                binding["configmap_uid"],
                binding["configmap_key"],
            )
            receipt = (binding["sha256"], binding["configmap_resource_version"])
            if key in aliases and aliases[key] != receipt:
                raise ValueError("shared source key has conflicting receipts")
            aliases[key] = receipt
            if workload == "gateway":
                relative = (
                    path[len("/app/backend/") :]
                    if path.startswith("/app/backend/")
                    else path
                )
                expected = entries.get(relative, {}).get(
                    "source_sha256"
                ) or gateway_delta["anchors"].get(relative)
                if expected is not None and expected != binding["sha256"]:
                    raise ValueError(
                        "mounted bytes changed since reviewed source baseline"
                    )
                if relative in entries and path != APP:
                    raise ValueError(
                        "additional changed source mount needs a separate reviewed plan"
                    )
            if workload == "gateway" and path == APP:
                changed.append(binding)
            else:
                retained.append(
                    {
                        "workload": workload,
                        **{k: v for k, v in binding.items() if k != "base64"},
                    }
                )
    if len(changed) != 1:
        raise ValueError("exactly one Gateway startup file mount required")
    binding = changed[0]
    entry = entries[RELATIVE]
    if entry["source_sha256"] != binding["sha256"]:
        raise ValueError("candidate was not composed from the mounted startup source")
    raw = base64.b64decode(entry["content_base64"], validate=True)
    if (
        len(raw) > 900 * 1024
        or hashlib.sha256(raw).hexdigest() != entry["target_sha256"]
    ):
        raise ValueError("candidate startup content invalid or too large for ConfigMap")
    if binding["sub_path"] != "gateway_app.py":
        raise ValueError("unexpected Gateway startup subPath; no guessed mapping")
    name = "employee-gateway-bootstrap-" + entry["target_sha256"][:32]
    return {
        "schema": 1,
        "scope": "EMPLOYEE_SOURCE_OVERLAY_REVIEW_ONLY",
        "source_report_canonical_sha256": digest(report),
        "deployment_uid": gateway["deployment_uid"],
        "deployment_spec_sha256": gateway["deployment_spec_sha256"],
        "base_image": gateway["image"],
        "original_binding": copy.deepcopy(binding),
        "retained_bindings": retained,
        "new_configmap": {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": "deer-flow"},
            "immutable": True,
            "data": {"gateway_app.py": raw.decode("utf-8")},
        },
        "target_source_sha256": entry["target_sha256"],
        "production_mutation": "NONE",
        "ready_for_rollout": False,
        "data_rollback_authorized": False,
        "open_gates": [
            "exact_OCI_effective_source_build",
            "live_overlay_drift_checks",
            "maintenance_and_consistent_backup",
            "real_multiuser_NFS_acceptance",
        ],
    }


def rehearse_template(deployment, plan, candidate_image):
    """Return a proposed spec and prove inverse layout without applying either.

    The image must be digest-pinned, but its existence/build is NOT verified
    here. No old shared ConfigMap contents or unrelated Deployment field can
    change. Real rollout must independently enforce live ConfigMap identities,
    exact built images, maintained data compatibility and workload gates.
    """
    if (
        plan.get("scope") != "EMPLOYEE_SOURCE_OVERLAY_REVIEW_ONLY"
        or plan.get("ready_for_rollout") is not False
        or deployment["metadata"]["name"] != "gateway"
        or deployment["metadata"]["uid"] != plan["deployment_uid"]
        or digest(deployment["spec"]) != plan["deployment_spec_sha256"]
        or not re.fullmatch(
            r"deerflow\.local/deer-flow-gateway@sha256:[a-f0-9]{64}", candidate_image
        )
    ):
        raise ValueError("stale deployment, invalid plan or unpinned candidate image")
    configmap = plan["new_configmap"]
    if (
        configmap.get("immutable") is not True
        or set(configmap.get("data", {})) != {"gateway_app.py"}
        or hashlib.sha256(configmap["data"]["gateway_app.py"].encode()).hexdigest()
        != plan["target_source_sha256"]
        or configmap["metadata"]["name"]
        != "employee-gateway-bootstrap-" + plan["target_source_sha256"][:32]
        or configmap["metadata"]["namespace"] != "deer-flow"
    ):
        raise ValueError("candidate ConfigMap differs from reviewed source")
    original = plan["original_binding"]
    proposed = copy.deepcopy(deployment["spec"])
    pod = proposed["template"]["spec"]
    containers = [c for c in pod["containers"] if c["name"] == "gateway"]
    if len(containers) != 1 or containers[0]["image"] != plan["base_image"]:
        raise ValueError("runtime image/container differs")
    container = containers[0]
    mounts = container.get("volumeMounts", [])
    selected = [i for i, m in enumerate(mounts) if m.get("mountPath") == APP]
    if len(selected) != 1:
        raise ValueError("live startup mount absent or ambiguous")
    index = selected[0]
    mount = mounts[index]
    if (
        mount.get("name") != original["volume_name"]
        or mount.get("subPath") != original["sub_path"]
        or mount.get("subPathExpr")
        or mount.get("readOnly") is not True
    ):
        raise ValueError("startup mount differs from reviewed binding")
    volumes = pod.get("volumes", [])
    selected_volumes = [v for v in volumes if v["name"] == original["volume_name"]]
    if (
        len(selected_volumes) != 1
        or selected_volumes[0].get("configMap", {}).get("name")
        != original["configmap_name"]
    ):
        raise ValueError("live source ConfigMap reference differs")
    if any(v["name"] == configmap["metadata"]["name"] for v in volumes):
        raise ValueError("candidate volume name already exists")
    container["image"] = candidate_image
    mount["name"] = configmap["metadata"]["name"]
    volumes.append(
        {
            "name": configmap["metadata"]["name"],
            "configMap": {
                "name": configmap["metadata"]["name"],
                "items": [{"key": "gateway_app.py", "path": original["sub_path"]}],
                "defaultMode": 0o444,
            },
        }
    )
    # Undo only the three intended layout changes in a private copy. This is a
    # structural proof, not a data rollback operation or a production command.
    inverse = copy.deepcopy(proposed)
    inverse_pod = inverse["template"]["spec"]
    inverse_container = next(
        c for c in inverse_pod["containers"] if c["name"] == "gateway"
    )
    inverse_container["image"] = plan["base_image"]
    inverse_container["volumeMounts"][index]["name"] = original["volume_name"]
    inverse_pod["volumes"].pop()
    if inverse != deployment["spec"]:
        raise ValueError("unexpected Deployment mutation")
    return {
        "proposed_spec": proposed,
        "template_inverse_exact": True,
        "ready_for_rollout": False,
        "data_rollback_authorized": False,
    }
