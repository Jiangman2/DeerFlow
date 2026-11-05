"""Pinned native WeKnora resource bootstrap. Host Python 3.7+.

The packaged version embeds CHILD. This repository template is not executable
until packaged. It never patches deployments, creates accounts or resets keys.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

CHILD = None
BASE = ["kubectl", "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=15s", "-n", "deer-flow"]
DIRECTORY = Path("/var/lib/portal-weknora-company-bootstrap-20261024-r1")
PINS = {
    "gateway": ("a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1", 386, "890cc7b2aa7fc8bff6abaebfe99f6f7149ac17e17405bc7e056ecd24263516b4"),
    "weknora-app": ("44aca5b9-8fc1-44d6-af61-37c6d62723d5", 82, "e7f481fc2b552098b8a8249beb2eb064212218cc48d5bbade2807446dc7ee83c"),
}
IMAGE = "deerflow.local/deer-flow-gateway@sha256:0a47db87acd2b219ca7bd548b8635b6f4cabd85ce34161b1935817786811006c"


class Refused(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def query(args):
    result = subprocess.run(BASE + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    require(result.returncode == 0 and len(result.stdout) <= 16 * 1024 * 1024, "READ_QUERY_FAILED")
    return json.loads(result.stdout)


def pin(deployment, name):
    expected = PINS[name]
    require((deployment["metadata"]["uid"], deployment["metadata"]["generation"], digest(deployment["spec"])) == expected, "DEPLOYMENT_BASELINE_CHANGED_" + name)
    n = deployment["spec"]["replicas"]
    state = deployment.get("status", {})
    require(not deployment["spec"].get("paused") and state.get("observedGeneration") == expected[1] and all(state.get(k, 0) == n for k in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")) and not state.get("terminatingReplicas"), "DEPLOYMENT_NOT_STABLE_" + name)


def claim():
    require(not DIRECTORY.exists() and not DIRECTORY.is_symlink(), "PRIOR_ATTEMPT_EXISTS_INSPECT_REPORT")
    DIRECTORY.mkdir(mode=0o700)
    with (DIRECTORY / "STARTED.json").open("x") as output:
        json.dump({"administrator_email": "jiangman2@cnooc.com.cn", "automatic_retry": False}, output)


def execute(report):
    require(CHILD is not None, "PACKAGED_SCRIPT_REQUIRED")
    require(socket.gethostname().split(".")[0] == "datax-01" and os.geteuid() == 0, "DATAX01_ROOT_REQUIRED")
    before = {name: query(["get", "deployment", name, "-o", "json"]) for name in PINS}
    for name, deployment in before.items():
        pin(deployment, name)
    pods = query(["get", "pods", "-l", "app=gateway", "-o", "json"])["items"]
    pods = [p for p in pods if not p["metadata"].get("deletionTimestamp") and any(c.get("type") == "Ready" and c.get("status") == "True" for c in p.get("status", {}).get("conditions", [])) and any(c.get("name") == "gateway" and c.get("image") == IMAGE for c in p["spec"]["containers"])]
    require(bool(pods), "NO_READY_GATEWAY")
    pod = sorted(pods, key=lambda p: p["metadata"]["name"])[0]
    name = pod["metadata"]["name"]
    report["sample"] = {"pod": name, "uid": pod["metadata"]["uid"]}
    claim()
    report["once_claimed"] = True
    report["operation_may_have_applied"] = True
    try:
        process = subprocess.run(BASE + ["exec", name, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-c", CHILD], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
    except subprocess.TimeoutExpired:
        raise Refused("CHILD_TIMEOUT_DO_NOT_REPLAY") from None
    require(len(process.stdout) <= 256 * 1024, "CHILD_OUTPUT_TOO_LARGE")
    rows = [json.loads(line) for line in process.stdout.decode("utf-8").splitlines() if line.strip()]
    require(bool(rows) and all(isinstance(row, dict) for row in rows), "CHILD_RECEIPT_MISSING_DO_NOT_REPLAY")
    for row in rows[:-1]:
        print(json.dumps(row, sort_keys=True), flush=True)
    observation = rows[-1]
    require(observation.get("schema") == "portal-weknora-company-bootstrap.v1", "CHILD_RECEIPT_INVALID")
    report["observation"] = observation
    report["operation_may_have_applied"] = observation.get("resource_write_may_have_applied", True)
    for deployment_name in PINS:
        pin(query(["get", "deployment", deployment_name, "-o", "json"]), deployment_name)
    after_pod = query(["get", "pod", name, "-o", "json"])
    require(pod["metadata"]["uid"] == after_pod["metadata"]["uid"] and pod.get("status", {}).get("containerStatuses") == after_pod.get("status", {}).get("containerStatuses"), "SAMPLED_CONTAINER_CHANGED")
    require(process.returncode == 0 and observation.get("status") == "ADMIN_RESOURCES_CREATED_AUTO_ENROLLMENT_NOT_DEPLOYED", "NATIVE_BOOTSTRAP_INCOMPLETE")
    report["status"] = observation["status"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-company-knowledge-once", action="store_true", required=True)
    parser.parse_args()
    report = {"schema": "portal-weknora-company-bootstrap-host.v1", "status": "INCOMPLETE", "once_claimed": False, "operation_may_have_applied": False, "automatic_retry": False, "credentials_exported": False, "production_workload_mutation": False, "provider_requests": 0, "auto_enrollment_enabled": False}
    try:
        execute(report)
    except Exception as exc:
        report.update({"error_class": type(exc).__name__, "error_code": str(exc) if isinstance(exc, Refused) else "BOOTSTRAP_INCOMPLETE", "raw_errors_exported": False})
    if report["once_claimed"]:
        data = json.dumps(report, ensure_ascii=True, sort_keys=True).encode()
        path = DIRECTORY / "REPORT.json"
        with path.open("xb") as output:
            output.write(data)
        report.update({"report_path": str(path), "report_bytes": len(data), "report_sha256": hashlib.sha256(data).hexdigest()})
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "ADMIN_RESOURCES_CREATED_AUTO_ENROLLMENT_NOT_DEPLOYED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
