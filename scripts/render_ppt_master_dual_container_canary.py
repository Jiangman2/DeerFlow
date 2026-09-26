#!/usr/bin/env python3
"""Render an isolated dual-container canary from a live, Ready R9 Sandbox Pod.

Input is ``kubectl get pod POD -o json`` on stdin. The live manifest supplies
the precise AIO init/probe/Secret/mount contract, but user-data is replaced by
an emptyDir and the output has a non-production label and unique identity.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path


def _replace_literal(env: list[dict], name: str, value: str) -> None:
    env[:] = [row for row in env if row.get("name") != name]
    env.append({"name": name, "value": value})


def render(
    live: dict,
    *,
    name: str,
    node: str,
    aio_image: str,
    agent_image: str,
    codex_config: str,
    model_catalog: str,
) -> dict:
    if aio_image == agent_image:
        raise ValueError("AIO and PPT Agent Host images must be distinct")
    metadata = live.get("metadata") or {}
    spec = live.get("spec") or {}
    if (metadata.get("labels") or {}).get("app") != "deer-flow-sandbox":
        raise ValueError("source must be a live DeerFlow sandbox Pod")
    if metadata.get("deletionTimestamp") or not any(
        row.get("type") == "Ready" and row.get("status") == "True"
        for row in (live.get("status") or {}).get("conditions") or []
    ):
        raise ValueError("source sandbox Pod must be Ready and non-terminating")
    source_containers = spec.get("containers") or []
    if sorted(row.get("name") for row in source_containers) != [
        "ppt-master-agent-host",
        "sandbox",
    ]:
        raise ValueError("source must have the two expected sandbox containers")
    if next(row for row in source_containers if row["name"] == "sandbox").get("image") != aio_image:
        raise ValueError("source AIO image differs from the pinned production baseline")
    source_init = spec.get("initContainers") or []
    if not source_init or any(row.get("image") != aio_image for row in source_init):
        raise ValueError("source init containers must use the pinned AIO image")
    if not codex_config.strip() or not model_catalog.strip():
        raise ValueError("the enterprise Agent Host profile must not be empty")
    json.loads(model_catalog)

    clone = copy.deepcopy(spec)
    clone["nodeName"] = node
    clone["restartPolicy"] = "Never"
    clone["activeDeadlineSeconds"] = 1800
    clone["automountServiceAccountToken"] = False
    for field in (
        "serviceAccountName",
        "hostname",
        "subdomain",
        "readinessGates",
    ):
        clone.pop(field, None)
    volumes = clone.get("volumes") or []
    user_data = [volume for volume in volumes if volume.get("name") == "user-data"]
    if len(user_data) != 1:
        raise ValueError("source must have exactly one user-data volume")
    if "persistentVolumeClaim" not in user_data[0]:
        raise ValueError("enterprise canary requires a user-data PVC source")
    for volume in volumes:
        if "hostPath" in volume:
            raise ValueError("enterprise canary refuses hostPath volumes")
        if volume is not user_data[0] and "persistentVolumeClaim" in volume:
            if volume["persistentVolumeClaim"].get("readOnly") is not True:
                raise ValueError("enterprise canary refuses other writable PVC volumes")
    # All user-data mounts, including PVC subPaths, now refer only to this
    # disposable emptyDir. No production thread directory is mounted.
    user_data[0].clear()
    user_data[0].update({"name": "user-data", "emptyDir": {}})

    # The live R9 Host may predate the enterprise relay key. Preflight must
    # model the Secret reference that the R16 Provisioner will add, without
    # ever reading or embedding the credential itself.
    aio = next(row for row in source_containers if row["name"] == "sandbox")
    sandbox_key = next(
        (row for row in aio.get("env") or [] if row.get("name") == "SANDBOX_API_KEY"),
        None,
    )
    secret_ref = ((sandbox_key or {}).get("valueFrom") or {}).get("secretKeyRef") or {}
    secret_name = secret_ref.get("name")
    if not secret_name or secret_ref.get("key") != "SANDBOX_API_KEY":
        raise ValueError("source must reference the sandbox API key Secret")

    for container in clone["containers"]:
        container["imagePullPolicy"] = "Never"
        if container["name"] != "ppt-master-agent-host":
            continue
        container["image"] = agent_image
        env = container.setdefault("env", [])
        _replace_literal(env, "CODEX_CONFIG_TOML", codex_config)
        _replace_literal(env, "CODEX_MODEL_CATALOG_JSON", model_catalog)
        _replace_literal(env, "PPT_MASTER_ENTERPRISE_RELAY_REQUIRED", "true")
        _replace_literal(env, "QWEN_TTS_BASE_URL", "https://dashscope.aliyuncs.com")
        env[:] = [row for row in env if row.get("name") != "PPT_MASTER_RESPONSES_RELAY_KEY"]
        env.append(
            {
                "name": "PPT_MASTER_RESPONSES_RELAY_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": secret_name,
                        "key": "PPT_MASTER_RESPONSES_RELAY_KEY",
                        "optional": False,
                    }
                },
            }
        )
    for container in clone["initContainers"]:
        container["imagePullPolicy"] = "Never"

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": metadata.get("namespace", "deer-flow"),
            "labels": {
                "app": "ppt-master-dual-container-canary",
                "deerflow.hotfix/temporary": "true",
            },
            "annotations": {
                "deerflow.io/user-id": "hotfix-canary",
                "deerflow.io/thread-id": name,
                "deerflow.io/include-legacy-skills": "false",
            },
        },
        "spec": clone,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--aio-image", required=True)
    parser.add_argument("--agent-image", required=True)
    parser.add_argument("--codex-config", type=Path, required=True)
    parser.add_argument("--model-catalog", type=Path, required=True)
    args = parser.parse_args()
    output = render(
        json.load(sys.stdin),
        name=args.name,
        node=args.node,
        aio_image=args.aio_image,
        agent_image=args.agent_image,
        codex_config=args.codex_config.read_text(encoding="utf-8"),
        model_catalog=args.model_catalog.read_text(encoding="utf-8"),
    )
    json.dump(output, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
