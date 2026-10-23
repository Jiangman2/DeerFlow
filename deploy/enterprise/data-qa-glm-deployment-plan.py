"""Pure paired image/config/Secret plan. No cluster calls or file writes.

Activation must first bind the field canaries, five node preload receipts,
immutable resources and a candidate runtime check. These helpers do not grant
activation or rollback permission and never submit or retry a patch.
"""

import copy
import hashlib
import json
import re

UID = "a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1"
OLD = "deerflow.local/deer-flow-gateway@sha256:58a50175cfae4a141dd1c03bbd4362a3a3f672545e2d5c08cbc2f18816a204a3"
NEW = "deerflow.local/deer-flow-gateway@sha256:eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5"
CONFIG = "deer-flow-config-hot-glm-20261019-f003c309f97d"
KEY = "APPFORGE_DATA_QA_API_KEY"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def compare_and_swap_prefix(deployment):
    require(deployment.get("apiVersion") == "apps/v1" and deployment.get("kind") == "Deployment", "DEPLOYMENT_REQUIRED")
    metadata = deployment["metadata"]
    require(metadata.get("uid") == UID and metadata.get("name") == "gateway" and metadata.get("namespace") == "deer-flow", "GATEWAY_IDENTITY_CHANGED")
    require(isinstance(metadata.get("resourceVersion"), str) and type(metadata.get("generation")) is int, "DEPLOYMENT_VERSION_REQUIRED")
    patch = [{"op": "test", "path": "/apiVersion", "value": "apps/v1"}, {"op": "test", "path": "/kind", "value": "Deployment"}]
    patch.extend({"op": "test", "path": "/metadata/" + key, "value": metadata[key]} for key in ("uid", "resourceVersion", "generation"))
    patch.append({"op": "test", "path": "/spec", "value": copy.deepcopy(deployment["spec"])})
    return patch


def activation_plan(deployment, *, baseline, config_receipt, secret_receipt):
    """Plan exactly three changes against immutable prepared resources.

    Receipt validation here uses only names/UIDs/metadata, never Secret values.
    The controller must freshly read back those resources before patching.
    """
    patch = compare_and_swap_prefix(deployment)
    metadata, spec = deployment["metadata"], deployment["spec"]
    observed = {"uid": metadata["uid"], "generation": metadata["generation"], "spec_sha256": digest(spec)}
    require(observed == baseline, "GATEWAY_BASELINE_CHANGED")
    require(spec.get("replicas") == 70, "GATEWAY_REPLICAS_CHANGED")
    for receipt, prefix in ((config_receipt, "deer-flow-config-data-qa-glm-"), (secret_receipt, "deer-flow-data-qa-")):
        require(receipt.get("immutable") is True and receipt.get("uid") and receipt.get("namespace") == "deer-flow", "IMMUTABLE_RESOURCE_RECEIPT_REQUIRED")
        require(re.fullmatch(re.escape(prefix) + r"[a-f0-9]{12}", receipt.get("name", "")), "PREPARED_RESOURCE_NAME_INVALID")
    require(secret_receipt.get("keys") == [KEY], "DEDICATED_SECRET_KEYS_REQUIRED")
    require(re.fullmatch(r"[a-f0-9]{64}", config_receipt.get("data_sha256", "")), "CONFIG_DATA_HASH_REQUIRED")
    pod = spec["template"]["spec"]
    containers = [i for i, c in enumerate(pod["containers"]) if c["name"] == "gateway"]
    volumes = [i for i, v in enumerate(pod.get("volumes", [])) if v["name"] == "config"]
    require(len(containers) == len(volumes) == 1, "GATEWAY_CONFIG_LAYOUT_CHANGED")
    ci, vi = containers[0], volumes[0]
    container, volume = pod["containers"][ci], pod["volumes"][vi]
    require(container.get("image") == OLD and container.get("imagePullPolicy") == "Never", "GATEWAY_IMAGE_CHANGED")
    require(volume.get("configMap", {}).get("name") == CONFIG, "GATEWAY_CONFIG_CHANGED")
    require(not any(e.get("name") == KEY for e in container.get("env", [])), "DATA_QA_ENV_ALREADY_PRESENT")
    expected = copy.deepcopy(spec)
    target = expected["template"]["spec"]
    target["containers"][ci]["image"] = NEW
    target["volumes"][vi]["configMap"]["name"] = config_receipt["name"]
    env = copy.deepcopy(container.get("env", []))
    env.append({"name": KEY, "valueFrom": {"secretKeyRef": {"name": secret_receipt["name"], "key": KEY}}})
    target["containers"][ci]["env"] = env
    patch.extend(
        [
            {"op": "replace", "path": f"/spec/template/spec/containers/{ci}/image", "value": NEW},
            {"op": "replace", "path": f"/spec/template/spec/volumes/{vi}/configMap/name", "value": config_receipt["name"]},
            {"op": "replace" if "env" in container else "add", "path": f"/spec/template/spec/containers/{ci}/env", "value": env},
        ]
    )
    return patch, expected


def rollback_plan(deployment, *, accepted_new_spec, saved_old_spec):
    """Explicit paired rollback only if nothing changed after activation.

    Caller must authenticate both specs against its sealed preparation report,
    revalidate old image availability and resources, and obtain a drained window.
    """
    patch = compare_and_swap_prefix(deployment)
    require(deployment["spec"] == accepted_new_spec, "GATEWAY_CHANGED_SINCE_ACTIVATION")
    before = [c for c in saved_old_spec["template"]["spec"]["containers"] if c["name"] == "gateway"]
    require(len(before) == 1 and before[0]["image"] == OLD, "SAVED_BASE_IMAGE_INVALID")
    config = [v for v in saved_old_spec["template"]["spec"]["volumes"] if v["name"] == "config"]
    require(len(config) == 1 and config[0].get("configMap", {}).get("name") == CONFIG, "SAVED_BASE_CONFIG_INVALID")
    patch.append({"op": "replace", "path": "/spec", "value": copy.deepcopy(saved_old_spec)})
    return patch
