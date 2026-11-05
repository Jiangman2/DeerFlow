"""Pure validation of the already prepared immutable resources; no writes.

The caller must first verify the exact saved report bytes/hash and current
Gateway baseline. This does not authorize another probe or a rollout.
"""

import hashlib
import json

OPERATION = "2e0fd403b23a4cf5b880daaee1b8e504"
CONFIG = dict(name="deer-flow-config-data-qa-glm-2e0fd403b23a", uid="5aefbbbc-fee1-4745-99f9-54d092b6ad4d", data_sha256="046185f02faccb041f6ce0f853d9847c84d6f01674e812210662aed4da57c9f6", immutable=True, namespace="deer-flow")
SECRET = dict(name="deer-flow-data-qa-2e0fd403b23a", uid="b2f728c8-8129-4d4f-98ba-48ce9b730f1f", keys=["APPFORGE_DATA_QA_API_KEY"], immutable=True, namespace="deer-flow")
JOB = dict(name="portal-data-qa-check-2e0fd403b23a-0", uid="25e9005f-ff83-41c2-8463-99effaa1cca8")


def require(condition, code):
    if not condition:
        raise ValueError(code)


def validate_reuse(receipt, config, secret_metadata):
    require(receipt.get("schema") == "portal-data-qa-glm-preparation.v1" and receipt.get("operation") == OPERATION and receipt.get("status") == "INCOMPLETE" and receipt.get("error_code") == "candidate_pod_owner", "REUSE_RECEIPT_MISMATCH")
    require(receipt.get("deployment_patch_requested") is False and receipt.get("gateway_restarted") is False and receipt.get("provider_requests") == 0 and receipt.get("candidate_checks") == [], "REUSE_OPERATION_NOT_QUALIFIED")
    require(receipt.get("prepared_config") == CONFIG and receipt.get("prepared_secret") == SECRET, "REUSE_RESOURCE_BINDINGS_CHANGED")
    intents = receipt.get("resource_intents", [])
    require(len(intents) == 3, "REUSE_RESOURCE_COUNT")
    for item, kind, expected in zip(intents, ("secret", "configmap", "job"), (SECRET, CONFIG, JOB)):
        require(item.get("kind") == kind and item.get("name") == expected["name"] and item.get("uid") == expected["uid"] and item.get("creation_confirmed") is True, "REUSE_INTENT_MISMATCH")
    metadata = config.get("metadata", {})
    require(config.get("kind") == "ConfigMap" and config.get("immutable") is True and all(metadata.get(k) == CONFIG[k] for k in ("name", "uid", "namespace")) and not config.get("binaryData"), "REUSE_CONFIG_IDENTITY")
    actual_hash = hashlib.sha256(json.dumps(config.get("data"), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    require(actual_hash == CONFIG["data_sha256"], "REUSE_CONFIG_HASH")
    # A controller must request this metadata projection only, never Secret data.
    require(secret_metadata == SECRET, "REUSE_SECRET_METADATA")
    return dict(config=dict(CONFIG), secret=dict(SECRET), create_config=False, create_secret=False, provider_requests=0, ready_for_rollout=False)
