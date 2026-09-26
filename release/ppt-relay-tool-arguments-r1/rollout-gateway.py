#!/usr/bin/env python3
"""Guarded Gateway-only release for the verified PPT relay candidate.

Host Python 3.7+. Run as root on datax-01. The candidate and rollback images
must already be imported on ecs-zdz-pro-0022. No other workload is patched.
"""

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path


NAMESPACE = "deer-flow"
EVIDENCE_ROOT = Path("/opt/deerflow-hotfix-evidence")
EXPECTED_PACKAGE_SHA = "adadc3fb4d787ed12d357dd8f6cbd5f6aeda757f80420098c6543259a1d1f358"
EXPECTED_CANARY_ARCHIVE_SHA = "dbc155b7df3bbc27b35102f8585efb651f7c062319a3660137a0a00479a9c4b7"
EXPECTED_IMAGE = "deerflow.local/deer-flow-gateway:20260924-ppt-relay-r1-adadc3fb4d787ed1"
PYTHON = "/app/backend/.venv/bin/python"
HEALTH_PROBE = r'''
import hashlib, json, pathlib, urllib.request
actual=hashlib.sha256(pathlib.Path("/app/backend/app/gateway/codex_responses_adapter.py").read_bytes()).hexdigest()
assert actual=="__EXPECTED_HASH__", "adapter_hash_mismatch"
client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with client.open("http://gateway:8001/health", timeout=8) as response:
    assert response.status==200, "service_http_status"
    body=response.read(8193)
    assert len(body)<8193, "service_health_too_large"
    assert json.loads(body).get("status") in ("healthy", "ok"), "service_health_not_ready"
print("gateway_relay_service_probe=PASS")
'''


def emit(message):
    print(message, flush=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def execute(command, input_text=None, timeout=40):
    try:
        result = subprocess.run(
            command,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=dict(os.environ, KUBECONFIG="/etc/kubernetes/admin.conf"),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("kubectl_operation_timeout=" + command[4]) from None
    if result.returncode:
        # kubectl stderr can contain application configuration; never echo it.
        raise RuntimeError("kubectl_operation_failed={} rc={}".format(command[4], result.returncode))
    return result.stdout


def kube(*arguments, input_text=None, timeout=40):
    return execute(
        ["kubectl", "--request-timeout=30s", "-n", NAMESPACE] + list(arguments),
        input_text=input_text,
        timeout=timeout,
    )


def get(kind, name=None, selector=None):
    arguments = ["get", kind]
    if name:
        arguments.append(name)
    if selector:
        arguments.extend(["-l", selector])
    return json.loads(kube(*(arguments + ["-o", "json"])))


def summary(deployment):
    spec = deployment["spec"]
    return {
        "uid": deployment["metadata"]["uid"],
        "generation": deployment["metadata"]["generation"],
        "spec_sha256": sha(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()),
        "replicas": spec.get("replicas", 1),
        "ready": deployment.get("status", {}).get("readyReplicas", 0),
        "updated": deployment.get("status", {}).get("updatedReplicas", 0),
        "images": {c["name"]: c["image"] for c in spec["template"]["spec"]["containers"]},
    }


def image_index(spec):
    matches = [
        index
        for index, container in enumerate(spec["template"]["spec"]["containers"])
        if container["name"] == "gateway"
    ]
    if len(matches) != 1:
        raise RuntimeError("Unexpected Gateway container layout")
    return matches[0]


def image_of(spec):
    return spec["template"]["spec"]["containers"][image_index(spec)]["image"]


def with_image(spec, image):
    changed = json.loads(json.dumps(spec))
    changed["template"]["spec"]["containers"][image_index(spec)]["image"] = image
    return changed


def verify_receipts(build_path, canary_path, package):
    build = read_json(build_path)
    canary = read_json(canary_path)
    if build.get("ppt_relay_candidate_build") != "PASS" or build.get("production_workloads_mutated") is not False:
        raise RuntimeError("Build receipt invalid")
    if build.get("package_sha256") != EXPECTED_PACKAGE_SHA or sha((package / "PACKAGE.SHA256").read_bytes()) != EXPECTED_PACKAGE_SHA:
        raise RuntimeError("Package checksum does not match approved candidate")
    image_receipt = build.get("image_receipt", {})
    if build.get("image") != EXPECTED_IMAGE or image_receipt.get("image") != EXPECTED_IMAGE or image_receipt.get("archive_sha256") != EXPECTED_CANARY_ARCHIVE_SHA:
        raise RuntimeError("Built image receipt mismatch")
    if image_receipt.get("audited_oci_base") != "PASS" or image_receipt.get("base_layer_prefix") != "PRESERVED" or image_receipt.get("runtime_config") != "PRESERVED" or image_receipt.get("layers_verified") != 347:
        raise RuntimeError("Built image integrity receipt incomplete")
    if canary.get("kind") != "ppt-relay-build2-live-model-canary" or canary.get("image") != EXPECTED_IMAGE or canary.get("candidate_archive_sha256") != EXPECTED_CANARY_ARCHIVE_SHA or canary.get("real_model_function_call") != "PASS" or canary.get("shell_commands_executed") != 0 or canary.get("production_workloads_mutated") is not False:
        raise RuntimeError("Real-model canary receipt incomplete")
    canary_time = datetime.datetime.fromisoformat(canary["utc"].replace("Z", "+00:00"))
    age = datetime.datetime.now(datetime.timezone.utc) - canary_time
    if age.total_seconds() < -300 or age.total_seconds() > 24 * 3600:
        raise RuntimeError("Real-model canary is stale; rerun canary")
    patch = read_json(package / "PATCH.json")
    if patch.get("source_sha256") != build["baseline"]["source_sha256"] or patch.get("changed_image_files") != ["/app/backend/app/gateway/codex_responses_adapter.py"]:
        raise RuntimeError("Unexpected source or candidate patch scope")
    target = patch.get("target_sha256")
    if not isinstance(target, str) or not re.fullmatch(r"[a-f0-9]{64}", target):
        raise RuntimeError("Target adapter digest invalid")
    return build, target


def require_node():
    node = get("node", "ecs-zdz-pro-0022")
    conditions = {item["type"]: item["status"] for item in node.get("status", {}).get("conditions", [])}
    for kind, expected in (("Ready", "True"), ("DiskPressure", "False"), ("MemoryPressure", "False"), ("PIDPressure", "False")):
        if conditions.get(kind) != expected:
            raise RuntimeError("Gateway node unhealthy: " + kind)


def active_work():
    pods = [
        pod for pod in get("pods", selector="app=postgres")["items"]
        if pod.get("status", {}).get("phase") == "Running"
        and not pod["metadata"].get("deletionTimestamp")
        and any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", []))
    ]
    if len(pods) != 1:
        raise RuntimeError("Expected exactly one Ready postgres Pod")
    sql = (
        "BEGIN READ ONLY; SET LOCAL statement_timeout='10s'; "
        "SELECT 'active_deerflow_runs=' || count(*) FROM runs WHERE status IN ('pending','running'); "
        "SELECT 'active_oa_jobs=' || count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running'); "
        "ROLLBACK;\n"
    )
    result = kube(
        "exec", "-i", pods[0]["metadata"]["name"], "-c", "postgres", "--",
        "sh", "-ec", 'exec psql -X -q -t -A -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
        input_text=sql,
    )
    rows = [row.strip() for row in result.splitlines() if row.strip()]
    if len(rows) != 2 or not re.fullmatch(r"active_deerflow_runs=\d+", rows[0]) or not re.fullmatch(r"active_oa_jobs=\d+", rows[1]):
        raise RuntimeError("Unexpected active-work query result")
    counts = {item.split("=", 1)[0]: int(item.split("=", 1)[1]) for item in rows}
    for row in rows:
        emit(row)
    return counts


def one_gateway_pod(image, image_ids):
    pods = [
        pod for pod in get("pods", selector="app=gateway")["items"]
        if pod.get("status", {}).get("phase") not in ("Succeeded", "Failed")
    ]
    if len(pods) != 1:
        return None
    pod = pods[0]
    status = next((s for s in pod.get("status", {}).get("containerStatuses", []) if s["name"] == "gateway"), {})
    live_image = next((c["image"] for c in pod["spec"]["containers"] if c["name"] == "gateway"), "")
    if pod["spec"].get("nodeName") != "ecs-zdz-pro-0022" or pod["metadata"].get("deletionTimestamp") or not status.get("ready") or live_image != image:
        return None
    if not any(str(status.get("imageID", "")).endswith(identity) for identity in image_ids):
        raise RuntimeError("Gateway Pod image identity mismatch")
    slices = get("endpointslices", selector="kubernetes.io/service-name=gateway")["items"]
    endpoints = [
        item for slice_obj in slices for item in slice_obj.get("endpoints", [])
        if item.get("conditions", {}).get("ready") is True and not item.get("conditions", {}).get("terminating")
    ]
    if len(endpoints) != 1 or endpoints[0].get("targetRef", {}).get("uid") != pod["metadata"]["uid"]:
        return None
    return pod


def service_probe(pod, source_sha):
    if pod is None:
        raise RuntimeError("Gateway Pod or endpoint is not Ready")
    code = HEALTH_PROBE.replace("__EXPECTED_HASH__", source_sha)
    result = kube("exec", pod["metadata"]["name"], "-c", "gateway", "--", PYTHON, "-B", "-c", code, timeout=35)
    if "gateway_relay_service_probe=PASS" not in result:
        raise RuntimeError("Gateway service HTTP probe failed")


def wait_healthy(expected_spec, image_ids, source_sha, timeout=600):
    deadline = time.monotonic() + timeout
    last_progress = 0
    while time.monotonic() < deadline:
        deployment = get("deployment", "gateway")
        if deployment["spec"] != expected_spec:
            raise RuntimeError("Concurrent Gateway deployment change")
        status = deployment.get("status", {})
        ready = all(status.get(field, 0) == 1 for field in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas"))
        ready = ready and status.get("observedGeneration", 0) >= deployment["metadata"]["generation"]
        if ready:
            pod = one_gateway_pod(image_of(expected_spec), image_ids)
            if pod:
                service_probe(pod, source_sha)
                service_probe(pod, source_sha)
                return pod
        if time.monotonic() - last_progress > 20:
            emit("gateway_rollout_wait=RUNNING target=" + image_of(expected_spec))
            last_progress = time.monotonic()
        time.sleep(4)
    raise RuntimeError("Gateway readiness/endpoint deadline exceeded")


def compare_baseline(build, gateway):
    for name, expected in build["baseline"]["deployments"].items():
        current = gateway if name == "gateway" else get("deployment", name)
        if summary(current) != expected or current.get("status", {}).get("availableReplicas", 0) != expected["replicas"]:
            raise RuntimeError("Audited deployment changed: " + name)
    require_node()
    spec = gateway["spec"]
    if spec.get("replicas") != 1 or spec.get("paused") or image_of(spec) != build["baseline"]["source_image"]:
        raise RuntimeError("Unexpected Gateway topology or base image")
    template = spec["template"]["spec"]
    if template.get("nodeSelector", {}).get("kubernetes.io/hostname") != "ecs-zdz-pro-0022":
        raise RuntimeError("Gateway is not pinned to the ECS node")
    if template["containers"][image_index(spec)].get("imagePullPolicy") != "Never":
        raise RuntimeError("Gateway imagePullPolicy changed")
    for hpa in get("hpa")["items"]:
        ref = hpa.get("spec", {}).get("scaleTargetRef", {})
        if ref.get("kind") == "Deployment" and ref.get("name") == "gateway":
            raise RuntimeError("Gateway HPA controls release target")
    service = get("service", "gateway")
    if service["spec"].get("selector") != {"app": "gateway"} or not any(item["port"] == 8001 for item in service["spec"].get("ports", [])):
        raise RuntimeError("Gateway Service routing changed")
    pod = one_gateway_pod(build["baseline"]["source_image"], [build["baseline"]["source_image_id"]])
    if pod is None:
        raise RuntimeError("Gateway baseline Pod or endpoint not Ready")
    return pod


def save_bytes(directory, name, raw):
    path = directory / name
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
    return path


def save_json(directory, name, value):
    return save_bytes(directory, name, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())


def patch_image(before, target, evidence):
    old_spec = before["spec"]
    index = image_index(old_spec)
    current = get("deployment", "gateway")
    if current["metadata"]["uid"] != before["metadata"]["uid"] or current["spec"] != old_spec:
        raise RuntimeError("Gateway changed before image patch")
    operations = [
        {"op": "test", "path": "/metadata/uid", "value": before["metadata"]["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": current["metadata"]["resourceVersion"]},
        {"op": "test", "path": "/spec", "value": old_spec},
        {"op": "replace", "path": "/spec/template/spec/containers/{}/image".format(index), "value": target},
    ]
    patch_path = save_json(evidence, "patch-" + uuid.uuid4().hex[:12] + ".json", operations)
    kube("patch", "deployment", "gateway", "--type=json", "--patch-file=" + str(patch_path), "-o", "name")
    after = get("deployment", "gateway")
    if after["spec"] != with_image(old_spec, target):
        raise RuntimeError("Gateway patch changed an unrelated field")
    return after


def backup(evidence, gateway, build, canary_path):
    raw = (json.dumps(gateway, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    save_bytes(evidence, "gateway.before.json", raw)
    save_json(evidence, "state.json", {
        "gateway_backup_sha256": sha(raw),
        "source_image": build["baseline"]["source_image"],
        "source_image_id": build["baseline"]["source_image_id"],
        "source_sha256": build["baseline"]["source_sha256"],
        "candidate_image": EXPECTED_IMAGE,
        "candidate_config_digest": build["image_receipt"]["config_digest"],
        "candidate_manifest_digest": build["image_receipt"]["manifest_digest"],
        "canary_sha256": sha(canary_path.read_bytes()),
    })


def restore(evidence):
    state = read_json(evidence / "state.json")
    raw = (evidence / "gateway.before.json").read_bytes()
    if sha(raw) != state["gateway_backup_sha256"]:
        raise RuntimeError("Gateway backup checksum mismatch")
    before = json.loads(raw)
    old = before["spec"]
    if image_of(old) != state["source_image"]:
        raise RuntimeError("Gateway backup base image mismatch")
    current = get("deployment", "gateway")
    if current["metadata"]["uid"] != before["metadata"]["uid"]:
        raise RuntimeError("Gateway identity changed; manual recovery required")
    if current["spec"] == old:
        target = current
    elif current["spec"] == with_image(old, state["candidate_image"]):
        target = patch_image(current, state["source_image"], evidence)
    else:
        raise RuntimeError("Gateway changed outside this release; manual recovery required")
    wait_healthy(target["spec"], [state["source_image_id"]], state["source_sha256"])
    emit("ppt_relay_gateway_rollback=PASS image=" + state["source_image"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--rollout", action="store_true")
    group.add_argument("--rollback", type=Path)
    parser.add_argument("--build-result", type=Path)
    parser.add_argument("--canary-result", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--force-active-work", action="store_true")
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0 or socket.gethostname().split(".")[0] != "datax-01":
        raise SystemExit("Run as root on datax-01")
    os.umask(0o077)
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        os.environ.pop(name, None)
    lock = open("/run/lock/deerflow-ppt-relay-r1-release.lock", "a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.rollback:
        evidence = args.rollback.resolve(strict=True)
        if EVIDENCE_ROOT not in evidence.parents or not evidence.name.startswith("ppt-relay-r1-rollout-"):
            raise RuntimeError("Unexpected rollback evidence path")
        restore(evidence)
        return
    if not args.build_result or not args.canary_result or not args.package:
        parser.error("Build receipt, canary receipt and package are required")
    build, target_hash = verify_receipts(args.build_result, args.canary_result, args.package)
    gateway = get("deployment", "gateway")
    compare_baseline(build, gateway)
    counts = active_work()
    active = sum(counts.values())
    if args.preflight:
        emit("ppt_relay_gateway_preflight=PASS candidate=" + EXPECTED_IMAGE)
        emit("maintenance_gate_ready=" + ("yes" if active == 0 else "no"))
        emit("gateway_strategy=" + json.dumps(gateway["spec"].get("strategy", {}), sort_keys=True))
        emit("ecs_image_preload=REQUIRES_OPERATOR_VERIFICATION")
        return
    if active and (not args.force_active_work or os.environ.get("PPT_RELAY_ACTIVE_WORK_ACK") != "YES"):
        raise RuntimeError("Active work detected; wait or use the explicit controlled force gate")
    if args.force_active_work and os.environ.get("PPT_RELAY_ACTIVE_WORK_ACK") != "YES":
        raise RuntimeError("Controlled force gate requires PPT_RELAY_ACTIVE_WORK_ACK=YES")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = EVIDENCE_ROOT / ("ppt-relay-r1-rollout-" + stamp + "-" + uuid.uuid4().hex[:8])
    evidence.mkdir(mode=0o700)
    emit("rollback_state=" + str(evidence))
    backup(evidence, gateway, build, args.canary_result)
    save_json(evidence, "active-work.before.json", counts)
    service_probe(one_gateway_pod(build["baseline"]["source_image"], [build["baseline"]["source_image_id"]]), build["baseline"]["source_sha256"])
    # Race recheck immediately before the sole production mutation.
    compare_baseline(build, get("deployment", "gateway"))
    latest = active_work()
    save_json(evidence, "active-work.last-check.json", latest)
    if sum(latest.values()) and not args.force_active_work:
        raise RuntimeError("New active work appeared before rollout")
    attempted = False
    try:
        attempted = True  # Include an uncertain patch response in recovery.
        after = patch_image(gateway, EXPECTED_IMAGE, evidence)
        wait_healthy(after["spec"], [build["image_receipt"]["config_digest"], build["image_receipt"]["manifest_digest"]], target_hash)
        save_json(evidence, "rollout-result.json", {"status": "PASS", "candidate_image": EXPECTED_IMAGE, "browser_acceptance": "REQUIRED", "automatic_resume": False})
        emit("ppt_relay_gateway_rollout=PASS image=" + EXPECTED_IMAGE)
        emit("ppt_relay_browser_acceptance=REQUIRED rollback_state=" + str(evidence))
    except (Exception, KeyboardInterrupt):
        if attempted:
            emit("automatic_ppt_relay_rollback=START")
            restore(evidence)
            emit("automatic_ppt_relay_rollback=PASS")
        raise


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        emit("ppt_relay_gateway_release=FAIL error_type=" + type(exc).__name__ + " reason=" + str(exc)[:160])
        sys.exit(1)
