"""Read-only follow-up to the accepted viewer canary. Host Python 3.7+.

No login, join, native API write, deployment patch or local report rewrite.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

CHILD = None
PINS = None
RECEIPTS = None
IMAGE = "deerflow.local/deer-flow-gateway@sha256:0a47db87acd2b219ca7bd548b8635b6f4cabd85ce34161b1935817786811006c"
BASE = ["kubectl", "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=15s", "-n", "deer-flow"]


class Refused(Exception):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def query(args):
    process = subprocess.run(BASE + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    require(process.returncode == 0 and len(process.stdout) <= 16 * 1024 * 1024, "READ_QUERY_FAILED")
    return json.loads(process.stdout)


def check_receipts():
    result = []
    for expected in RECEIPTS:
        path = Path(expected["path"])
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == expected["bytes"], "PRIOR_RECEIPT_MISSING_OR_SIZE_CHANGED")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected["sha256"], "PRIOR_RECEIPT_CHANGED")
        result.append(expected)
    return result


def check_deployment(obj, name):
    expected = PINS[name]
    require([obj["metadata"]["uid"], obj["metadata"]["generation"], digest(obj["spec"])] == expected, "DEPLOYMENT_BASELINE_CHANGED")
    count = obj["spec"]["replicas"]
    state = obj.get("status", {})
    require(count > 0 and not obj["spec"].get("paused") and state.get("observedGeneration") == expected[1] and all(state.get(key, 0) == count for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")) and not state.get("terminatingReplicas"), "DEPLOYMENT_NOT_STABLE")


def eligible(pod):
    return not pod["metadata"].get("deletionTimestamp") and any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", [])) and any(c.get("name") == "gateway" and c.get("image") == IMAGE for c in pod["spec"]["containers"])


def execute(report):
    require(isinstance(CHILD, str) and PINS and RECEIPTS, "PACKAGED_SCRIPT_REQUIRED")
    require(socket.gethostname().split(".")[0] == "datax-01" and os.geteuid() == 0, "DATAX01_ROOT_REQUIRED")
    report["prior_receipts"] = check_receipts()
    for name in PINS:
        check_deployment(query(["get", "deployment", name, "-o", "json"]), name)
    pods = query(["get", "pods", "-l", "app=gateway", "-o", "json"])["items"]
    pods = sorted([p for p in pods if eligible(p)], key=lambda p: p["metadata"]["name"])
    require(bool(pods), "NO_READY_GATEWAY")
    selected = pods[0]
    name = selected["metadata"]["name"]
    pod = query(["get", "pod", name, "-o", "json"])
    require(eligible(pod) and pod["metadata"]["uid"] == selected["metadata"]["uid"], "SAMPLED_CONTAINER_CHANGED")
    report["sample"] = {"pod": name, "uid": pod["metadata"]["uid"]}
    process = subprocess.run(BASE + ["exec", name, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-c", CHILD], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    require(len(process.stdout) <= 256 * 1024, "CHILD_OUTPUT_LIMIT")
    observation = json.loads(process.stdout)
    require(isinstance(observation, dict) and observation.get("schema") == "portal-weknora-company-members-diagnosis.v2", "INVALID_CHILD_REPORT")
    report["observation"] = observation
    after = query(["get", "pod", name, "-o", "json"])
    require(eligible(after) and pod["metadata"]["uid"] == after["metadata"]["uid"] and pod.get("status", {}).get("containerStatuses") == after.get("status", {}).get("containerStatuses"), "SAMPLED_CONTAINER_CHANGED")
    for name in PINS:
        check_deployment(query(["get", "deployment", name, "-o", "json"]), name)
    require(process.returncode == 0 and observation.get("status") == "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED", "MEMBER_INVENTORY_INCOMPLETE_SEE_OBSERVATION")
    report["status"] = observation["status"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-members-readonly", action="store_true", required=True)
    parser.parse_args()
    report = {"schema": "portal-weknora-company-members-host.v2", "status": "INCOMPLETE", "files_modified": False, "database_modified": False, "workload_modified": False, "membership_grants": 0, "login_requests": 0, "provider_requests": 0, "credentials_exported": False, "automatic_retry": False}
    try:
        execute(report)
    except Exception as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else "READONLY_INSPECTION_FAILED", raw_errors_exported=False)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
