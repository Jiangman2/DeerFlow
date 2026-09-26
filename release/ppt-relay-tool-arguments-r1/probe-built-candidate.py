#!/usr/bin/env python3
"""Run one bounded, non-executing DeepSeek tool-call canary in the built image.

Host Python 3.7+. Run on datax-01 after importing the exact candidate image.
The Job receives the Gateway's existing Secret reference, never its value.
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import uuid
from pathlib import Path


PROBE = r"""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path

path = Path("/app/backend/app/gateway/codex_responses_adapter.py")
if hashlib.sha256(path.read_bytes()).hexdigest() != "__TARGET_HASH__":
    raise SystemExit("candidate_adapter_digest_mismatch")
spec = importlib.util.spec_from_file_location("ppt_relay_live_probe", path)
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)

async def check():
    request = {
        "model": os.environ.get("PPT_MASTER_DEEPSEEK_MODEL", "DeepSeek-V4-Flash"),
        "stream": True,
        "max_output_tokens": 256,
        "reasoning": {"effort": "low"},
        "instructions": "Call the specified function once. Supply exactly one JSON string field named cmd. Do not execute any command yourself.",
        "input": "Call functions.exec_command with cmd equal to printf 2. This is only a protocol check; the tool will not be executed.",
        "tools": [{
            "type": "namespace", "name": "functions",
            "tools": [{
                "type": "function", "name": "exec_command",
                "description": "Protocol probe only. The tool is not executed.",
                "parameters": {
                    "type": "object", "properties": {"cmd": {"type": "string"}},
                    "required": ["cmd"], "additionalProperties": False,
                },
            }],
        }],
        "tool_choice": {"type": "function", "name": "exec_command", "namespace": "functions"},
        "parallel_tool_calls": False,
    }
    converted = module.convert_responses_request(request)
    calls = []
    terminal = None
    async for frame in module.stream_deepseek_responses(converted):
        for line in frame.splitlines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("type") == "response.output_item.done":
                calls.append(event["item"])
            if event.get("type") in ("response.completed", "response.failed"):
                terminal = event
    if terminal is None:
        raise RuntimeError("no_terminal_response")
    if terminal["type"] != "response.completed":
        code = terminal.get("response", {}).get("error", {}).get("code", "unknown")
        raise RuntimeError("model_response_failed_" + str(code)[:80])
    tools = [item for item in calls if item.get("type") == "function_call"]
    if len(tools) != 1:
        raise RuntimeError("expected_one_tool_call")
    tool = tools[0]
    if tool.get("name") != "exec_command" or tool.get("namespace") != "functions":
        raise RuntimeError("tool_namespace_mismatch")
    arguments = json.loads(tool["arguments"])
    if set(arguments) != {"cmd"} or not isinstance(arguments["cmd"], str) or not arguments["cmd"]:
        raise RuntimeError("required_cmd_missing")
    usage = terminal.get("response", {}).get("usage", {})
    print("ppt_relay_real_model=PASS required_cmd=PASS namespace=functions calls=1 executed=0 total_tokens=" + str(usage.get("total_tokens", "unavailable")))

try:
    asyncio.run(check())
except Exception as exc:
    print("ppt_relay_real_model=FAIL error_type=" + type(exc).__name__ + " reason=" + str(exc)[:160])
    raise SystemExit(1)
"""

ENV_NAMES = frozenset(
    {
        "DeepSeek_V4_API_KEY",
        "PPT_MASTER_DEEPSEEK_MODEL",
        "PPT_MASTER_DEEPSEEK_UPSTREAM_URL",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "OPENSSL_CONF",
    }
)
MOUNT_NAMES = frozenset({"enterprise-ca", "openssl-compat"})


def run(command, input_text=None, timeout=30):
    process = subprocess.run(
        command,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        env=dict(os.environ, KUBECONFIG="/etc/kubernetes/admin.conf"),
    )
    if process.returncode:
        raise RuntimeError(
            "command_failed={} rc={}".format(command[0:3], process.returncode)
        )
    return process.stdout


def kube(arguments, input_text=None, timeout=30):
    request_timeout = "245s" if arguments and arguments[0] == "wait" else "15s"
    return run(
        ["kubectl", "--request-timeout=" + request_timeout, "-n", "deer-flow"]
        + arguments,
        input_text,
        timeout,
    )


def summary(deployment):
    spec = deployment["spec"]
    return {
        "uid": deployment["metadata"]["uid"],
        "generation": deployment["metadata"]["generation"],
        "spec_sha256": hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "replicas": spec.get("replicas", 1),
        "ready": deployment.get("status", {}).get("readyReplicas", 0),
        "updated": deployment.get("status", {}).get("updatedReplicas", 0),
        "images": {
            container["name"]: container["image"]
            for container in spec["template"]["spec"]["containers"]
        },
    }


def read_context(result_path, package):
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if (
        result.get("ppt_relay_candidate_build") != "PASS"
        or result.get("production_workloads_mutated") is not False
    ):
        raise ValueError("Candidate build receipt is not a completed build")
    manifest = package / "PACKAGE.SHA256"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != result.get(
        "package_sha256"
    ):
        raise ValueError("Candidate package digest mismatch")
    patch = json.loads((package / "PATCH.json").read_text(encoding="utf-8"))
    if (
        not isinstance(patch.get("target_sha256"), str)
        or len(patch["target_sha256"]) != 64
    ):
        raise ValueError("Candidate target digest missing")
    image = result.get("image")
    receipt = result.get("image_receipt", {})
    if (
        receipt.get("image") != image
        or receipt.get("audited_oci_base") != "PASS"
        or receipt.get("base_layer_prefix") != "PRESERVED"
        or receipt.get("runtime_config") != "PRESERVED"
    ):
        raise ValueError("Candidate image receipt mismatch")
    archive = result_path.parent / "gateway.candidate.oci.tar"
    if not archive.is_file() or archive.is_symlink():
        raise ValueError("Candidate OCI archive missing")
    return result, patch["target_sha256"], archive


def verify_archive(archive, digest):
    hasher = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(block)
    if hasher.hexdigest() != digest:
        raise ValueError("Candidate OCI archive checksum mismatch")


def current_baseline(result):
    node = json.loads(kube(["get", "node", "datax-01", "-o", "json"]))
    conditions = {
        row["type"]: row["status"]
        for row in node.get("status", {}).get("conditions", [])
    }
    for name, state in (
        ("Ready", "True"),
        ("DiskPressure", "False"),
        ("MemoryPressure", "False"),
        ("PIDPressure", "False"),
    ):
        if conditions.get(name) != state:
            raise RuntimeError("Build node not healthy: " + name)
    gateway = None
    for name, expected in result["baseline"]["deployments"].items():
        current = json.loads(kube(["get", "deployment", name, "-o", "json"]))
        if (
            summary(current) != expected
            or current["status"].get("availableReplicas") != expected["replicas"]
        ):
            raise RuntimeError("Production deployment drift: " + name)
        if name == "gateway":
            gateway = current
    pods = json.loads(kube(["get", "pods", "-l", "app=gateway", "-o", "json"]))["items"]
    if len(pods) != 1:
        raise RuntimeError("Gateway Pod count changed")
    pod = pods[0]
    status = next(
        (
            item
            for item in pod.get("status", {}).get("containerStatuses", [])
            if item["name"] == "gateway"
        ),
        {},
    )
    if (
        pod["spec"].get("nodeName") != "ecs-zdz-pro-0022"
        or pod["metadata"].get("deletionTimestamp")
        or not status.get("ready")
        or not str(status.get("imageID", "")).endswith(
            result["baseline"]["source_image_id"]
        )
    ):
        raise RuntimeError("Gateway Pod identity or health changed")
    return gateway


def render_job(result, target_hash, deployment, name):
    template = deployment["spec"]["template"]["spec"]
    container = next(
        item for item in template["containers"] if item["name"] == "gateway"
    )
    env = [dict(item) for item in container.get("env", []) if item["name"] in ENV_NAMES]
    values = {item["name"]: item for item in env}
    if (
        "DeepSeek_V4_API_KEY" not in values
        or "valueFrom" not in values["DeepSeek_V4_API_KEY"]
    ):
        raise ValueError("Gateway upstream key is not a Secret reference")
    if (
        "PPT_MASTER_DEEPSEEK_UPSTREAM_URL" not in values
        or "SSL_CERT_FILE" not in values
    ):
        raise ValueError("Gateway model URL or CA file not configured")
    env.extend(
        {"name": key, "value": value}
        for key, value in (
            ("PPT_MASTER_DEEPSEEK_ATTEMPTS", "2"),
            ("PPT_MASTER_DEEPSEEK_MAX_OUTPUT_TOKENS", "256"),
            ("PPT_MASTER_DEEPSEEK_TURN_TIMEOUT_SECONDS", "180"),
            ("PPT_MASTER_DEEPSEEK_READ_TIMEOUT_SECONDS", "90"),
        )
    )
    mounts = [
        dict(item)
        for item in container.get("volumeMounts", [])
        if item["name"] in MOUNT_NAMES
    ]
    volumes = [
        dict(item)
        for item in template.get("volumes", [])
        if item["name"] in MOUNT_NAMES
    ]
    if {item["name"] for item in mounts} != MOUNT_NAMES or {
        item["name"] for item in volumes
    } != MOUNT_NAMES:
        raise ValueError("Gateway enterprise CA/openssl mounts missing")
    if any("secret" not in item and "configMap" not in item for item in volumes):
        raise ValueError("Unexpected canary volume source")
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": "deer-flow",
            "labels": {"app": "ppt-relay-canary"},
        },
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {"labels": {"app": "ppt-relay-canary"}},
                "spec": {
                    "nodeName": "datax-01",
                    "restartPolicy": "Never",
                    "activeDeadlineSeconds": 240,
                    "automountServiceAccountToken": False,
                    "volumes": volumes,
                    "containers": [
                        {
                            "name": "probe",
                            "image": result["image"],
                            "imagePullPolicy": "Never",
                            "command": [
                                "/app/backend/.venv/bin/python",
                                "-B",
                                "-c",
                                PROBE.replace("__TARGET_HASH__", target_hash),
                            ],
                            "env": env,
                            "volumeMounts": mounts,
                            "resources": {
                                "requests": {"cpu": "250m", "memory": "512Mi"},
                                "limits": {
                                    "cpu": "1",
                                    "memory": "2Gi",
                                    "ephemeral-storage": "1Gi",
                                },
                            },
                        }
                    ],
                },
            },
        },
    }


def verify_import(image, manifest_digest):
    # containerd 2.2's `ctr images inspect` may succeed with empty stdout.
    # Its exit status confirms existence; CRI provides structured status.
    run(["ctr", "-n", "k8s.io", "images", "inspect", image])
    info = json.loads(
        run(
            [
                "crictl",
                "--runtime-endpoint",
                "unix:///run/containerd/containerd.sock",
                "--image-endpoint",
                "unix:///run/containerd/containerd.sock",
                "inspecti",
                image,
            ]
        )
    )
    status = info.get("status", {})
    if image not in status.get("repoTags", []):
        raise ValueError("Candidate image tag missing from CRI")
    digests = [value.rsplit("@", 1)[-1] for value in status.get("repoDigests", [])]
    if digests and manifest_digest not in digests:
        raise ValueError("Imported candidate digest mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--run-canary", action="store_true")
    args = parser.parse_args()
    if args.check_only == args.run_canary:
        parser.error("Select exactly one mode")
    if (
        os.name != "posix"
        or os.geteuid() != 0
        or platform.machine() not in ("aarch64", "arm64")
        or socket.gethostname().split(".")[0] != "datax-01"
    ):
        raise SystemExit("Run as root on ARM64 datax-01")
    result, target_hash, archive = read_context(args.result, args.package)
    gateway = current_baseline(result)
    if args.check_only:
        print(
            json.dumps(
                {
                    "ppt_relay_live_canary_setup": "PASS",
                    "image": result["image"],
                    "archive_present": True,
                    "production_workloads_mutated": False,
                    "ready_for_rollout": False,
                },
                sort_keys=True,
            )
        )
        return
    if args.output.exists():
        raise ValueError("Canary receipt already exists")
    verify_archive(archive, result["image_receipt"]["archive_sha256"])
    print("canary_stage=archive_checksum_pass", flush=True)
    verify_import(result["image"], result["image_receipt"]["manifest_digest"])
    print("canary_stage=cri_image_identity_pass", flush=True)
    name = "ppt-relay-canary-" + uuid.uuid4().hex[:12]
    job = render_job(result, target_hash, gateway, name)
    kube(["create", "-f", "-"], json.dumps(job), 30)
    print("canary_job=" + name, flush=True)
    completed = False
    try:
        kube(
            ["wait", "--for=condition=complete", "job/" + name, "--timeout=240s"],
            timeout=255,
        )
        completed = True
    except (RuntimeError, subprocess.TimeoutExpired):
        pass
    try:
        logs = kube(
            ["logs", "job/" + name, "-c", "probe", "--tail=30", "--limit-bytes=8192"]
        )
    except RuntimeError:
        logs = "canary_pod_logs=UNAVAILABLE"
    passed = (
        completed
        and "ppt_relay_real_model=PASS" in logs
        and "ppt_relay_real_model=FAIL" not in logs
    )
    if passed:
        current_baseline(result)
    report = {
        "kind": "ppt-relay-build2-live-model-canary",
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "job": name,
        "image": result["image"],
        "candidate_archive_sha256": result["image_receipt"]["archive_sha256"],
        "real_model_function_call": "PASS" if passed else "FAIL",
        "shell_commands_executed": 0,
        "production_workloads_mutated": False,
        "ready_for_rollout": False,
    }
    fd = os.open(
        str(args.output),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(logs.strip(), flush=True)
    print("canary_receipt=" + str(args.output), flush=True)
    if not passed:
        raise RuntimeError("Live-model canary did not pass")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "ppt_relay_live_canary=FAIL error_type="
            + type(exc).__name__
            + " reason="
            + str(exc)[:180],
            file=sys.stderr,
        )
        sys.exit(1)
