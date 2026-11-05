"""Read-only host probe bound to the no-patch activation failure. Python 3.7."""

# ruff: noqa: F821 -- PROBE is supplied by the package builder.
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys

EXPECTED = dict(uid="a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1", generation=381, spec_sha256="5a84f74566171e588433779a59b9adec1ae752c2dc3c42706ff55db1e295fbda")
REPORT = "/var/lib/portal-gateway-data-qa-glm-activation-r1-efa5e03631e046b08eb30497198936bd/REPORT.json"
REPORT_SHA = "fe2959adb97e8bf4f78c0a6a93f7d9d740785ce0e068c77a73be5e1900b21fd8"
POD = "gateway-8649cdbc5b-26q9l"
POD_UID = "b5451481-1577-4656-a1ed-e28decdabc7a"
BASE = ["kubectl", "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=15s", "-n", "deer-flow"]


def require(ok, code):
    if not ok:
        raise ValueError(code)


def query(args, limit=8388608):
    p = subprocess.run(BASE + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35)
    require(p.returncode == 0 and len(p.stdout) <= limit, "READ_QUERY_FAILED")
    return json.loads(p.stdout)


def binding(d):
    raw = json.dumps(d["spec"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return dict(uid=d["metadata"]["uid"], generation=d["metadata"]["generation"], spec_sha256=hashlib.sha256(raw).hexdigest())


def receipt():
    p = pathlib.Path(REPORT).parent
    require(not p.is_symlink() and p.stat().st_uid == 0 and stat.S_IMODE(p.stat().st_mode) == 0o700, "REPORT_DIRECTORY")
    fd = os.open(REPORT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == 26307 and info.st_nlink == 1, "REPORT_METADATA")
        raw = f.read(26308)
    require(len(raw) == 26307 and hashlib.sha256(raw).hexdigest() == REPORT_SHA, "REPORT_HASH")
    value = json.loads(raw)
    require(value.get("error_code") == "persisted_agent_old_references_or_incomplete" and value.get("patch_requested") is False and value.get("patch_may_have_applied") is False and value.get("patch_request_count") == 0, "REPORT_NOT_NO_PATCH")
    return value


def pod_identity(p, d):
    require(p["metadata"]["name"] == POD and p["metadata"]["uid"] == POD_UID and not p["metadata"].get("deletionTimestamp"), "POD_IDENTITY_CHANGED")
    container = next(x for x in p["spec"]["containers"] if x["name"] == "gateway")
    expected = next(x for x in d["spec"]["template"]["spec"]["containers"] if x["name"] == "gateway")
    state = next(x for x in p["status"]["containerStatuses"] if x["name"] == "gateway")
    require(container["image"] == expected["image"] and state.get("ready") is True, "POD_NOT_READY_BASELINE")
    return (p["metadata"]["uid"], state.get("containerID"), state.get("imageID"), state.get("restartCount"))


def main():
    require(sys.argv[1:] == ["--inspect-readonly"] and sys.flags.isolated and sys.dont_write_bytecode and os.geteuid() == 0, "ISOLATED_ROOT_READONLY_REQUIRED")
    failed = receipt()
    d = query(["get", "deployment", "gateway", "-o", "json"])
    require(binding(d) == EXPECTED, "GATEWAY_BASELINE_CHANGED")
    p = query(["get", "pod", POD, "-o", "json"])
    before = pod_identity(p, d)
    observed = query(["exec", POD, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-c", PROBE], 65536)
    require(observed.get("status") in ("PASS", "OLD_REFERENCES_FOUND"), "REFERENCE_SCAN_FAILED")
    require(pod_identity(query(["get", "pod", POD, "-o", "json"]), d) == before, "POD_CHANGED_DURING_SCAN")
    require(binding(query(["get", "deployment", "gateway", "-o", "json"])) == EXPECTED, "GATEWAY_CHANGED_DURING_SCAN")
    previous = failed["agent_reference_observations"][0]["observation"]
    print(json.dumps(dict(schema="portal-data-qa-agent-reference-detail.v1", status="READONLY_REFERENCE_DETAILS_COLLECTED", gateway=EXPECTED, sample=dict(pod=POD, uid=POD_UID), matches_failed_inventory=observed["inventory_sha256"] == previous["inventory_sha256"], observation=observed, files_modified=False, workload_modified=False, credentials_exported=False, provider_requests=0), sort_keys=True, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        code = str(exc)
        print(json.dumps(dict(status="INCOMPLETE", error_class=type(exc).__name__, error_code=code if isinstance(exc, ValueError) and re.fullmatch("[A-Z_]{1,80}", code) else "READONLY_INSPECTION_FAILED", files_modified=False, workload_modified=False, credentials_exported=False, provider_requests=0)))
        raise SystemExit(1)
