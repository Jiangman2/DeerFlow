#!/usr/bin/env python3
"""Read-only, host-Python-3.7-compatible audit; never prints Secret/env values."""

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

SOURCE = "/app/backend/app/gateway/codex_responses_adapter.py"
EXPECTED_SOURCE = "be67018d948af473d32db24611b875fa3cb641855b472a94237c140e309892d7"
EXPECTED_IMAGE = "deerflow.local/deer-flow-gateway:20260923-enterprise-arm64-rc15-channel-pdf-preview-r2"
EXPECTED_ID = "sha256:a18f04d64152ea0b1272863e2dd57c2706573a4642bc06943da36a9518fb0856"
THREAD = "636f2a5a-3a85-4885-9df1-cd50179580ba"
RUNTIME_PROBE = r"""
import hashlib, json, os, sys
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError
p = Path("/app/backend/app/gateway/codex_responses_adapter.py")
raw = p.read_bytes()
packages = {}
for name in ("httpx", "fastapi", "jsonschema", "referencing"):
    try: packages[name] = version(name)
    except PackageNotFoundError: packages[name] = None
print(json.dumps({"source_sha256": hashlib.sha256(raw).hexdigest(),
    "python": list(sys.version_info[:3]), "packages": packages,
    "model": os.getenv("PPT_MASTER_DEEPSEEK_MODEL", "DeepSeek-V4-Flash")[:128]}))
"""


def kube(args):
    process = subprocess.run(
        ["kubectl", "--request-timeout=15s", "-n", "deer-flow"] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=25,
        env=dict(os.environ, KUBECONFIG="/etc/kubernetes/admin.conf"),
    )
    if process.returncode:
        # stderr may contain operator configuration. Only report operation class.
        raise RuntimeError(
            "kubectl_{}_failed_rc_{}".format(args[0], process.returncode)
        )
    if len(process.stdout) > 8 * 1024 * 1024:
        raise RuntimeError("audit_output_limit")
    return process.stdout


def overlaps(container):
    result = []
    for mount in container.get("volumeMounts", []):
        path = mount.get("mountPath", "").rstrip("/")
        if mount.get("mountPath") == "/" or (
            path and (SOURCE == path or SOURCE.startswith(path + "/"))
        ):
            result.append(
                {
                    key: mount[key]
                    for key in ("name", "mountPath", "subPath", "readOnly")
                    if key in mount
                }
            )
    return result


def summary(deployment):
    return {
        "uid": deployment["metadata"]["uid"],
        "generation": deployment["metadata"]["generation"],
        "spec_sha256": hashlib.sha256(
            json.dumps(
                deployment["spec"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "replicas": deployment["spec"].get("replicas", 1),
        "ready": deployment.get("status", {}).get("readyReplicas", 0),
        "updated": deployment.get("status", {}).get("updatedReplicas", 0),
        "images": {
            c["name"]: c["image"]
            for c in deployment["spec"]["template"]["spec"]["containers"]
        },
    }


def collect():
    report = {
        "kind": "ppt-relay-runtime-audit-v1",
        "thread_id": THREAD,
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "production_workloads_mutated": False,
        "deployments": {},
        "gateway_pods": [],
    }
    for name in ("gateway", "ppt-master-runner", "provisioner"):
        deployment = json.loads(kube(["get", "deployment", name, "-o", "json"]))
        report["deployments"][name] = summary(deployment)
        if name == "gateway":
            container = next(
                c
                for c in deployment["spec"]["template"]["spec"]["containers"]
                if c["name"] == "gateway"
            )
            report["source_mount_overlaps"] = overlaps(container)
    pods = json.loads(kube(["get", "pods", "-l", "app=gateway", "-o", "json"]))["items"]
    if not pods or len(pods) > 8:
        raise RuntimeError("unexpected_gateway_pod_count")
    for pod in pods:
        name = pod["metadata"]["name"]
        container = next(c for c in pod["spec"]["containers"] if c["name"] == "gateway")
        status = next(
            (
                s
                for s in pod.get("status", {}).get("containerStatuses", [])
                if s["name"] == "gateway"
            ),
            {},
        )
        record = {
            "name": name,
            "uid": pod["metadata"]["uid"],
            "node": pod["spec"].get("nodeName"),
            "image": container["image"],
            "image_id": status.get("imageID"),
            "ready": status.get("ready", False),
            "container_id": status.get("containerID"),
            "restarts": status.get("restartCount"),
            "deleting": bool(pod["metadata"].get("deletionTimestamp")),
            "source_mount_overlaps": overlaps(container),
        }
        if record["ready"] and not record["deleting"]:
            record["runtime"] = json.loads(
                kube(
                    [
                        "exec",
                        name,
                        "-c",
                        "gateway",
                        "--",
                        "/app/backend/.venv/bin/python",
                        "-B",
                        "-c",
                        RUNTIME_PROBE,
                    ]
                )
            )
            current = json.loads(kube(["get", "pod", name, "-o", "json"]))
            current_status = next(
                s
                for s in current["status"]["containerStatuses"]
                if s["name"] == "gateway"
            )
            if (
                current["metadata"]["uid"] != record["uid"]
                or current_status.get("containerID") != record["container_id"]
            ):
                raise RuntimeError("gateway_changed_during_audit")
        report["gateway_pods"].append(record)
    report["log_signals"] = {}
    for name in ("gateway", "ppt-master-runner"):
        try:
            logs = kube(
                [
                    "logs",
                    "deployment/" + name,
                    "-c",
                    name,
                    "--since=4h",
                    "--tail=1000",
                    "--limit-bytes=262144",
                ]
            ).decode("utf-8", "replace")
            report["log_signals"][name] = {
                "thread_mentions": logs.count(THREAD),
                "malformed_tool_arguments": logs.count("malformed tool arguments"),
                "missing_cmd": len(re.findall(r"missing field [`']cmd[`']", logs)),
                "parse_function_arguments": logs.count(
                    "failed to parse function arguments"
                ),
            }
        except (RuntimeError, subprocess.TimeoutExpired):
            report["log_signals"][name] = {"available": False}
    # Compare the deployment template again without ever serializing env values.
    after = summary(json.loads(kube(["get", "deployment", "gateway", "-o", "json"])))
    report["stable_gateway_spec"] = (
        after["spec_sha256"] == report["deployments"]["gateway"]["spec_sha256"]
    )
    report["baseline_match"] = (
        report["stable_gateway_spec"]
        and not report["source_mount_overlaps"]
        and report["deployments"]["gateway"]["images"].get("gateway") == EXPECTED_IMAGE
        and len(report["gateway_pods"]) == report["deployments"]["gateway"]["replicas"]
        and all(
            p["ready"]
            and not p["deleting"]
            and not p["source_mount_overlaps"]
            and p["image"] == EXPECTED_IMAGE
            and str(p["image_id"]).endswith(EXPECTED_ID)
            and p.get("runtime", {}).get("source_sha256") == EXPECTED_SOURCE
            and p["runtime"]["python"][:2] >= [3, 11]
            and all(p["runtime"]["packages"].values())
            for p in report["gateway_pods"]
        )
    )
    report["production_rollout_authorized"] = False
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = collect()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(args.output, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(
            "ppt_relay_audit=COMPLETE baseline_match={} production_workloads_mutated=no".format(
                str(report["baseline_match"]).lower()
            )
        )
        print("audit_file=" + os.path.abspath(args.output))
        return 0
    except Exception as exc:
        print("ppt_relay_audit=FAIL error_type=" + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
