"""Read-only historical 503 evidence; no model requests, keys or raw logs."""

import datetime
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys

RECEIPTS = (
    ("r2", "11a8655d26394f249766f7d2a77063e4", 1781, "baa2aff6a6c750f98d72c97e693aceac3bb00c210ef094861faa9238efb7c73a", "flash", 1),
    ("r3", "f6dbf7f90154471789b2b42ddca90ecc", 2247, "d710b3124e9cab0627d80fdd2961d94348fd570de14715d175109517357f0609", "tool_resume", 4),
    ("r4", "b63817e832e74c5da7b70c88fd83ffca", 1795, "a7da7831f6b0aa434103de8fa18cfde15f705894b3c40fc6fbb8396cab24e788", "flash", 1),
)
POD = "gateway-8649cdbc5b-26q9l"
POD_UID = "b5451481-1577-4656-a1ed-e28decdabc7a"
SHANGHAI = datetime.timezone(datetime.timedelta(hours=8))


class Refused(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def read_report(root, pin):
    revision, operation, size, digest, stage, attempts = pin
    parent = os.open("portal-deepseek-v41-preparation-" + operation, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    try:
        info = os.fstat(parent)
        require(info.st_uid == 0 and not info.st_mode & 0o077, "report_directory_permissions")
        fd = os.open("REPORT.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077 and info.st_size == size, "report_file_identity")
            raw = stream.read(65537)
            after = os.fstat(stream.fileno())
            require((info.st_ino, info.st_size, info.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns), "report_changed_during_read")
    finally:
        os.close(parent)
    require(len(raw) == size and hashlib.sha256(raw).hexdigest() == digest, "report_digest_mismatch")
    report = json.loads(raw)
    require(report.get("operation") == operation and report.get("status") == "INCOMPLETE", "report_identity")
    provider = report.get("provider", {})
    require(provider.get("http_status") == 503 and provider.get("stage") == stage and provider.get("provider_requests_attempted") == attempts, "report_provider_identity")
    require(report.get("secret_creation_requested") is False and report.get("workload_modified") is False, "unexpected_mutation")
    return {
        "revision": revision,
        "operation": operation,
        "report_sha256": digest,
        "report_bytes": size,
        "report_file_mtime_utc": datetime.datetime.fromtimestamp(info.st_mtime, datetime.timezone.utc).isoformat(),  # noqa: UP017 -- host Python 3.7
        "report_file_mtime_shanghai": datetime.datetime.fromtimestamp(info.st_mtime, SHANGHAI).isoformat(),
        "timestamp_meaning": "REPORT_FILE_MTIME_APPROXIMATE_END_NOT_REQUEST_START",
        "http_status": 503,
        "failed_stage": stage,
        "provider_requests_attempted": attempts,
        "completed_checks": [{k: c.get(k) for k in ("stage", "elapsed_seconds", "finish_reason", "reasoning_present")} for c in provider.get("checks", [])],
        "stage_results": provider.get("stage_results"),
        "error_class": provider.get("error_class"),
        "error_code": provider.get("error_code"),
        "gateway": report.get("gateway_before"),
        "sample": report.get("sample"),
        "profile_sha256": report.get("profile_sha256"),
        "runtime_sources": provider.get("runtime_sources"),
        "upstream_request_id": "NOT_RECORDED",
        "response_headers": "NOT_RECORDED",
        "response_body": "NOT_RECORDED",
    }


def current_pod():
    binary = shutil.which("kubectl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    require(binary in ("/usr/bin/kubectl", "/usr/local/bin/kubectl"), "kubectl_missing")
    for path in (binary, os.path.dirname(binary)):
        info = os.lstat(path)
        require(info.st_uid == 0 and not info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode), "kubectl_permissions")
    p = subprocess.run([binary, "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=15s", "-n", "deer-flow", "get", "pod", POD, "-o", "json"], capture_output=True, timeout=20)
    if p.returncode:
        return {"status": "CURRENT_POD_QUERY_UNAVAILABLE", "returncode": p.returncode}
    require(len(p.stdout) <= 2097152, "pod_output_limit")
    pod = json.loads(p.stdout)
    if pod.get("metadata", {}).get("uid") != POD_UID:
        return {"status": "POD_IDENTITY_CHANGED_ADDRESSES_WITHHELD"}
    s = pod.get("status", {})
    return {
        "status": "CURRENT_SAME_POD_METADATA_ONLY",
        "pod": POD,
        "uid": POD_UID,
        "node": pod.get("spec", {}).get("nodeName"),
        "pod_ip": s.get("podIP"),
        "node_ip": s.get("hostIP"),
        "address_meaning": "CURRENT_ADDRESSES_NOT_HISTORICAL_EGRESS_OR_NAT_PROOF",
    }


def main():
    result = {
        "schema": "portal-deepseek-v41-incident-evidence.v1",
        "status": "INCOMPLETE",
        "read_only": True,
        "provider_requests": 0,
        "files_modified": False,
        "workload_modified": False,
        "credentials_exported": False,
        "raw_logs_exported": False,
        "reports": [],
    }
    try:
        require(sys.argv[1:] == ["--collect-existing-readonly"], "explicit_flag_required")
        require(os.geteuid() == 0 and socket.gethostname().split(".")[0] == "datax-01" and sys.flags.isolated and sys.flags.dont_write_bytecode, "isolated_root_datax01_required")
        root = os.open("/var/lib", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(root)
            require(info.st_uid == 0 and not info.st_mode & 0o022, "report_root_permissions")
            for pin in RECEIPTS:
                result["reports"].append(read_report(root, pin))
        finally:
            os.close(root)
        result["current_pod"] = current_pod()
        result["status"] = "HISTORICAL_503_EVIDENCE_COLLECTED"
    except Exception as exc:
        result.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else "LOCAL_OR_QUERY_FAILURE")
    result["exitcode"] = 0 if result["status"] == "HISTORICAL_503_EVIDENCE_COLLECTED" else 1
    print(json.dumps(result, sort_keys=True))
    return result["exitcode"]


if __name__ == "__main__":
    raise SystemExit(main())
