#!/usr/bin/env python3
"""Render guarded Runner overlay and exact rollback patches."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path


MODULE_PATH = "/app/backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py"
VOLUME_NAME = "ppt-runner-execution-lease-fd"


def _container_index(document: dict) -> int:
    containers = document["spec"]["template"]["spec"]["containers"]
    matches = [index for index, row in enumerate(containers) if row.get("name") == "ppt-master-runner"]
    if len(matches) != 1:
        raise ValueError("expected exactly one ppt-master-runner container")
    return matches[0]


def _assert_no_conflicting_mount(document: dict) -> None:
    spec = document["spec"]["template"]["spec"]
    if any(row.get("name") == VOLUME_NAME for row in spec.get("volumes") or []):
        raise ValueError("target volume already exists")
    container = spec["containers"][_container_index(document)]
    for mount in container.get("volumeMounts") or []:
        mount_path = str(mount.get("mountPath") or "").rstrip("/")
        if MODULE_PATH == mount_path or MODULE_PATH.startswith(mount_path + "/"):
            raise ValueError("existing mount masks target module: %s" % mount_path)


def render_configmap(source: str) -> dict:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "ppt-runner-execution-lease-fd-%s" % digest,
            "namespace": "deer-flow",
            "labels": {
                "app.kubernetes.io/name": "ppt-master-runner",
                "app.kubernetes.io/component": "execution-lease-fd-hotfix",
            },
        },
        "immutable": True,
        "data": {"aio_sandbox_provider.py": source},
    }


def render_apply_patch(document: dict, configmap_name: str) -> list[dict]:
    _assert_no_conflicting_mount(document)
    spec = document["spec"]["template"]["spec"]
    container_index = _container_index(document)
    operations: list[dict] = [
        {"op": "test", "path": "/metadata/resourceVersion", "value": document["metadata"]["resourceVersion"]}
    ]
    if "volumes" not in spec:
        operations.append({"op": "add", "path": "/spec/template/spec/volumes", "value": []})
    operations.append(
        {
            "op": "add",
            "path": "/spec/template/spec/volumes/-",
            "value": {"name": VOLUME_NAME, "configMap": {"name": configmap_name, "defaultMode": 420}},
        }
    )
    container = spec["containers"][container_index]
    if "volumeMounts" not in container:
        operations.append(
            {"op": "add", "path": "/spec/template/spec/containers/%d/volumeMounts" % container_index, "value": []}
        )
    operations.append(
        {
            "op": "add",
            "path": "/spec/template/spec/containers/%d/volumeMounts/-" % container_index,
            "value": {
                "name": VOLUME_NAME,
                "mountPath": MODULE_PATH,
                "subPath": "aio_sandbox_provider.py",
                "readOnly": True,
            },
        }
    )
    return operations


def expected_after(document: dict, configmap_name: str) -> dict:
    result = copy.deepcopy(document)
    spec = result["spec"]["template"]["spec"]
    spec.setdefault("volumes", []).append(
        {"name": VOLUME_NAME, "configMap": {"name": configmap_name, "defaultMode": 420}}
    )
    container = spec["containers"][_container_index(result)]
    container.setdefault("volumeMounts", []).append(
        {
            "name": VOLUME_NAME,
            "mountPath": MODULE_PATH,
            "subPath": "aio_sandbox_provider.py",
            "readOnly": True,
        }
    )
    return result


def render_rollback_patch(before: dict, live: dict, configmap_name: str) -> list[dict]:
    expected = expected_after(before, configmap_name)
    if live["spec"] != expected["spec"]:
        raise ValueError("live Runner spec diverged from the exact hotfix target; refusing automatic rollback")
    return [
        {"op": "test", "path": "/metadata/resourceVersion", "value": live["metadata"]["resourceVersion"]},
        {"op": "replace", "path": "/spec/template", "value": before["spec"]["template"]},
    ]


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "configmap" and len(sys.argv) == 4:
        source = Path(sys.argv[2]).read_text(encoding="utf-8")
        Path(sys.argv[3]).write_text(json.dumps(render_configmap(source), sort_keys=True) + "\n", encoding="utf-8")
        return
    if mode == "apply" and len(sys.argv) == 5:
        Path(sys.argv[4]).write_text(
            json.dumps(render_apply_patch(load(sys.argv[2]), sys.argv[3]), sort_keys=True) + "\n", encoding="utf-8"
        )
        return
    if mode == "rollback" and len(sys.argv) == 6:
        Path(sys.argv[5]).write_text(
            json.dumps(render_rollback_patch(load(sys.argv[2]), load(sys.argv[3]), sys.argv[4]), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    raise SystemExit(
        "usage: render_runner_overlay.py configmap SOURCE OUT | apply DEPLOYMENT CM OUT | rollback BEFORE LIVE CM OUT"
    )


if __name__ == "__main__":
    main()
